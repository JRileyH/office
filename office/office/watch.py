"""What office is doing: its queue, the phase that's running, and each step its worker takes.

    python -m office.watch        the queue, then each phase as it runs, until nothing more
                                  is about to run (Ctrl-C stops sooner)
    python -m office.watch JOB    everything job JOB's runs did, start to finish

bin/watch.sh runs this in the office container. Run logs are Claude Code's stream-json
events, compressed and flushed after every event, so a phase can be read while it runs.
"""

from __future__ import annotations

import json
import os
import sys
import time
import zlib
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from . import playbook as playbooks
from .config import Settings
from .store import ACTIVE, Job, Store

POLL_SECONDS = 2
STILL_SECONDS = 120  # quiet this long: say what the worker is on
QUIET_SECONDS = 600  # nothing logged this long: it may be stuck
WAIT_SECONDS = 300  # a job should be running but no phase has started: stop watching
BACKLOG = 20  # steps shown from before you started watching
# The part of a tool call's input that says what it does, in order of preference.
SUMMARY_KEYS = ("command", "file_path", "path", "pattern", "url", "query", "skill", "description", "prompt")
HIDDEN_TOOLS = {"StructuredOutput"}  # the verdict, shown when the run ends


class Tail:
    """Reads a run log as it grows: the harness flushes the compressed stream after every event."""

    def __init__(self, path: Path):
        self.path = path
        self.offset = 0
        self.inflate = zlib.decompressobj(16 + zlib.MAX_WBITS)  # gzip
        self.partial = b""

    def events(self) -> list[dict]:
        """The events logged since the last call."""
        try:
            with open(self.path, "rb") as f:
                f.seek(self.offset)
                data = f.read()
        except FileNotFoundError:
            return []
        self.offset += len(data)
        *lines, self.partial = (self.partial + self.inflate.decompress(data)).split(b"\n")
        return [e for e in (_parse(line) for line in lines) if e]

    def modified(self) -> datetime | None:
        try:
            return datetime.fromtimestamp(self.path.stat().st_mtime, UTC)
        except FileNotFoundError:
            return None


class Steps:
    """Turns a run's events into one line per step, timed from the start of the run."""

    def __init__(self, started: datetime):
        self.started = started
        self.pending: dict[str, tuple[str, datetime | None]] = {}  # tool calls still running
        self.hidden: set[str] = set()  # calls of HIDDEN_TOOLS, whose results aren't shown either
        self.last: datetime | None = None  # the latest step
        self.ended = False  # the run's result is in

    def lines(self, events: list[dict]) -> list[str]:
        return [line for event in events for line in self.describe(event)]

    def describe(self, event: dict) -> list[str]:
        at = _time(event.get("timestamp"))
        self.last = at or self.last
        content = (event.get("message") or {}).get("content")
        blocks = [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []
        found = []
        match event.get("type"):
            case "assistant":
                for block in blocks:
                    if block.get("type") == "text" and str(block.get("text", "")).strip():
                        found.append("says: " + _short(block["text"], 300))
                    elif block.get("type") == "tool_use" and block.get("name") in HIDDEN_TOOLS:
                        self.hidden.add(block.get("id"))
                    elif block.get("type") == "tool_use":
                        call = f"{block.get('name')} {_summary(block.get('input'))}".strip()
                        self.pending[block.get("id")] = (call, at)
                        found.append("→ " + call)
            case "user":
                for block in blocks:
                    if block.get("type") == "tool_result" and block.get("tool_use_id") not in self.hidden:
                        self.pending.pop(block.get("tool_use_id"), None)
                        found.append(("✗ " if block.get("is_error") else "← ") + _output(block.get("content")))
            case "result":
                self.ended = True
                found.append(_ending(event))
            case "system":
                if event.get("subtype") == "init":
                    found.append(f"started on {event.get('model')}")
                elif event.get("subtype") == "compact_boundary":
                    found.append("context compacted")
            case "rate_limit_event":
                info = event.get("rate_limit_info") or {}
                if info.get("status") not in (None, "allowed"):
                    found.append(f"Claude usage: {info['status']} ({info.get('rateLimitType', 'limit')})")
        stamp = _clock(at - self.started) if at else ""
        nested = "┆ " if event.get("parent_tool_use_id") else ""  # a subagent's step
        return [f"{stamp:>8}  {nested}{line}" for line in found]

    def doing(self, now: datetime) -> str:
        """What the worker is on, for how long."""
        if self.pending:
            call, at = list(self.pending.values())[-1]
            return f"running {call}" + (f" for {_span(now - at)}" if at else "")
        return "thinking" + (f" for {_span(now - self.last)}" if self.last else "")


# --- the queue ---------------------------------------------------------------------


def overview(settings: Settings, store: Store, now: datetime) -> list[str]:
    lines = ["Work is paused: !resume in the inbox starts it again."] if store.get("scheduler_paused") == "1" else []
    return lines + ([job_line(settings, store, j, now) for j in store.jobs(ACTIVE)] or ["No unfinished jobs."])


def job_line(settings: Settings, store: Store, job: Job, now: datetime) -> str:
    project = store.project(job.slug)
    try:
        phases = [p.name for p in playbooks.load(settings.process_dir / "playbooks" / f"{job.playbook}.md").phases]
    except (OSError, ValueError):
        phases = []
    phase = f"{phases[job.phase]} ({job.phase + 1}/{len(phases)})" if job.phase < len(phases) else "finished"
    runs = store.runs(job.id)
    last = runs[-1] if runs else None
    state = job.state
    if job.state == "running" and last and last["outcome"] is None:
        state = f"running for {_span(now - _time(last['started_at']))}"
    elif job.state == "waiting":
        state = "waiting on you: reply in its room"
    elif job.state == "parked":
        retry = _time(job.retry_at)
        when = f"in {_span(retry - now)}" if retry and retry > now else "now"
        state = f"retrying {when} (last run: {last['outcome']})" if last else f"starting {when}"
    held = ", project paused" if project.paused else ""
    return (
        f"#{job.id} {project.name} ({job.slug}): {job.kind}, {job.playbook}, {project.model}/{project.effort}."
        f" {phase}: {state}{held}"
    )


def run_heading(run: dict) -> str:
    name = Path(run["log_path"]).name.removesuffix(".jsonl.gz")
    line = f"── job {run['job_id']} run {name} on {run['model']}, started {run['started_at'][:16].replace('T', ' ')} UTC"
    if run["outcome"]:
        took = _span(_time(run["ended_at"]) - _time(run["started_at"]))
        line += f": {run['outcome']} after {took}" + (f", {_turns(run['num_turns'])}" if run["num_turns"] else "")
    return line


def live_run(store: Store) -> dict | None:
    for job in store.jobs(("running",)):
        runs = store.runs(job.id)
        if runs and runs[-1]["outcome"] is None:
            return runs[-1]
    return None


def next_run(store: Store, after: int) -> dict | None:
    """The first run started after run `after`, finished or not: a run can fail between two looks."""
    newer = [r for r in store.runs(limit=20) if r["id"] > after]
    return min(newer, key=lambda r: r["id"]) if newer else None


def stopped(run: dict | None, job: Job | None) -> str | None:
    """How a run ended, or None while it's going."""
    if run is None or job is None:
        return "deleted"
    if run["outcome"]:
        return run["outcome"]
    if job.state == "killed":
        return "killed"
    if job.state != "running":
        return "interrupted: office restarted, and the phase runs again"
    return None


def expecting(store: Store, now: datetime) -> bool:
    """Whether a phase is running or about to: a job is running, queued, or parked until soon."""
    if store.get("scheduler_paused") == "1":
        return False
    for job in store.jobs(("running", "queued", "parked")):
        soon = job.state != "parked" or (job.retry_at and _time(job.retry_at) <= now + timedelta(seconds=WAIT_SECONDS))
        if soon and not store.project(job.slug).paused:
            return True
    return False


# --- the two views -----------------------------------------------------------------


def follow(
    settings: Settings,
    store: Store,
    out: Callable[[str], None] = print,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> None:
    """The queue, then each phase as it runs, until nothing more is about to."""
    for line in overview(settings, store, clock()):
        out(line)
    live = live_run(store)
    seen = live["id"] - 1 if live else max((r["id"] for r in store.runs(limit=1)), default=0)
    watched, waiting_since = False, None
    while True:
        run = next_run(store, seen)
        if run:
            watch_run(store, run, out, sleep, clock, backlog=None if watched else BACKLOG)
            seen, watched, waiting_since = run["id"], True, None
            continue
        now = clock()
        waiting_since = waiting_since or now
        if not expecting(store, now) or (now - waiting_since).total_seconds() >= WAIT_SECONDS:
            if watched:
                out("")
                for line in overview(settings, store, now):
                    out(line)
            return
        sleep(POLL_SECONDS)


def watch_run(
    store: Store,
    run: dict,
    out: Callable[[str], None],
    sleep: Callable[[float], None],
    clock: Callable[[], datetime],
    backlog: int | None,
) -> None:
    """Print a run's steps as they're logged, until it ends."""
    out("")
    out(run_heading(run))
    tail, steps = Tail(Path(run["log_path"])), Steps(_time(run["started_at"]))
    lines = steps.lines(tail.events())
    if backlog is not None and len(lines) > backlog:
        out(f"{'':>8}  … {len(lines) - backlog} earlier steps (bin/watch.sh {run['job_id']} shows them all)")
        lines = lines[-backlog:]
    said = clock()
    while True:
        for line in lines:
            out(line)
        now = clock()
        if lines:
            said = now
        ended = stopped(store.run(run["id"]), store.job(run["job_id"]))
        if ended:
            for line in steps.lines(tail.events()):
                out(line)
            if not steps.ended:
                out(f"{'':>8}  ■ {ended}")
            return
        if (now - said).total_seconds() >= STILL_SECONDS:
            modified = tail.modified()
            quiet = now - modified if modified else timedelta(0)
            warning = ""
            if quiet.total_seconds() >= QUIET_SECONDS:
                warning = f"; nothing logged for {_span(quiet)}, it may be stuck (!kill in its room stops the job)"
            out(f"{'':>8}  … {steps.doing(now)}{warning}")
            said = now
        sleep(POLL_SECONDS)
        lines = steps.lines(tail.events())


def transcript(settings: Settings, store: Store, job_id: int, out: Callable[[str], None] = print) -> int:
    """Everything a job's runs did, start to finish."""
    job = store.job(job_id)
    if job is None:
        out(f"No job {job_id}.")
        return 1
    out(job_line(settings, store, job, datetime.now(UTC)))
    out(f"Asked for: {_short(job.request, 500)}")
    for run in store.runs(job.id):
        out("")
        out(run_heading(run))
        for line in Steps(_time(run["started_at"])).lines(Tail(Path(run["log_path"])).events()):
            out(line)
    return 0


# --- formatting --------------------------------------------------------------------


def _summary(data) -> str:
    if not isinstance(data, dict):
        return ""
    for key in SUMMARY_KEYS:
        if isinstance(data.get(key), str) and data[key].strip():
            return _short(data[key], 200)
    return _short(json.dumps(data), 200)


def _output(content) -> str:
    if isinstance(content, list):
        content = "\n".join(str(b.get("text", "")) for b in content if isinstance(b, dict))
    lines = [line for line in str(content or "").splitlines() if line.strip()]
    if not lines:
        return "(no output)"
    if len(lines) == 1:
        return _short(lines[0], 160)
    return f"{_short(lines[0], 80)} … {_short(lines[-1], 80)} ({len(lines)} lines)"


def _ending(event: dict) -> str:
    turns = _turns(event.get("num_turns"))
    if event.get("subtype") == "success" and not event.get("is_error"):
        verdict = event.get("structured_output") or {}
        said = f": {verdict['verdict']}: {_short(str(verdict.get('message', '')), 300)}" if verdict.get("verdict") else ""
        return f"■ finished after {turns}{said}"
    return f"■ stopped after {turns}: {event.get('subtype')} {_short(str(event.get('result') or ''), 200)}".rstrip()


def _turns(count) -> str:
    return "1 turn" if count == 1 else f"{count} turns"


def _short(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _time(value) -> datetime | None:
    try:
        return datetime.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        return None


def _clock(delta: timedelta) -> str:
    """Time into the run: 4:05, or 1:02:03."""
    minutes, seconds = divmod(max(int(delta.total_seconds()), 0), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"


def _span(delta: timedelta) -> str:
    """A duration in words: 40s, 14m, 2h05m."""
    seconds = max(int(delta.total_seconds()), 0)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    return f"{seconds // 3600}h{seconds % 3600 // 60:02d}m"


def _parse(line: bytes) -> dict:
    try:
        event = json.loads(line)
    except ValueError:
        return {}
    return event if isinstance(event, dict) else {}


def main(argv: list[str]) -> int:
    if len(argv) > 1 or (argv and not argv[0].lstrip("#").isdigit()):
        print(__doc__, file=sys.stderr)
        return 2
    settings = Settings.from_env()
    store = Store(settings.state_dir / "office.db")
    try:
        if argv:
            return transcript(settings, store, int(argv[0].lstrip("#")))
        follow(settings, store)
    except KeyboardInterrupt:
        print()
    except BrokenPipeError:  # whoever was reading went away
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
