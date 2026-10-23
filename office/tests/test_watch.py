"""bin/watch.sh: reading run logs while they're written, and what it shows."""

from __future__ import annotations

import asyncio
import gzip
import json
import stat
import sys
from datetime import UTC, datetime, timedelta

from office.harness import ClaudeHarness
from office.watch import Steps, Tail, expecting, follow, overview, transcript, watch_run

T0 = datetime(2026, 10, 22, 13, 30, tzinfo=UTC)
RESULT = {
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "num_turns": 12,
    "structured_output": {"verdict": "done", "message": "Spec written."},
}


def at(seconds: float, start: datetime = T0) -> str:
    return (start + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")  # as Claude Code writes it


def said(text: str, seconds: float, start: datetime = T0) -> dict:
    content = [{"type": "thinking", "thinking": ""}, {"type": "text", "text": text}]
    return {"type": "assistant", "timestamp": at(seconds, start), "message": {"content": content}}


def call(tool_id: str, name: str, seconds: float, start: datetime = T0, **tool_input) -> dict:
    content = [{"type": "tool_use", "id": tool_id, "name": name, "input": tool_input}]
    return {"type": "assistant", "timestamp": at(seconds, start), "message": {"content": content}}


def answer(tool_id: str, output, seconds: float, error: bool = False) -> dict:
    content = [{"type": "tool_result", "tool_use_id": tool_id, "content": output, "is_error": error}]
    return {"type": "user", "timestamp": at(seconds), "message": {"content": content}}


def running_job(office, started: datetime = T0):
    """A rigorous job in its first phase; returns it, its run's id and the open log."""
    store = office.store
    store.add_project("trees", "Trees", "Flashcards", mode="rigorous", model="opus", effort="max")
    job = store.update_job(store.add_job("trees", "new", "a flashcard game", "rigorous").id, state="running")
    log = office.settings.job_logs(job.id) / "001-spec.jsonl.gz"
    log.parent.mkdir(parents=True)
    run_id = store.start_run(job.id, "spec", "opus/max", str(log))
    store.db.execute("UPDATE runs SET started_at = ? WHERE id = ?", (started.isoformat(timespec="seconds"), run_id))
    return job, run_id, gzip.open(log, "wt")


def write(log, *events: dict) -> None:
    for event in events:
        log.write(json.dumps(event) + "\n")
    log.flush()


def test_a_log_can_be_read_while_it_is_written(tmp_path):
    path = tmp_path / "001-spec.jsonl.gz"
    tail = Tail(path)
    assert tail.events() == []
    with gzip.open(path, "wt") as log:
        write(log, {"type": "a"})
        assert tail.events() == [{"type": "a"}]
        log.write(json.dumps({"type": "b"}) + '\n{"type": ')
        log.flush()
        assert tail.events() == [{"type": "b"}]
        log.write('"c"}\n')
    assert tail.events() == [{"type": "c"}]
    assert tail.events() == []


def test_the_harness_log_can_be_read_while_claude_runs(tmp_path):
    go = tmp_path / "go"
    binary = tmp_path / "claude"
    binary.write_text(
        f"#!{sys.executable}\nimport json, os, sys, time\nsys.stdin.read()\n"
        "print(json.dumps({'type': 'system', 'subtype': 'init', 'session_id': 's'}), flush=True)\n"
        f"while not os.path.exists({str(go)!r}):\n    time.sleep(0.01)\n"
        "print(json.dumps({'type': 'result', 'subtype': 'success', 'is_error': False,"
        " 'structured_output': {'verdict': 'done', 'message': 'ok'}}))\n"
    )
    binary.chmod(binary.stat().st_mode | stat.S_IEXEC)
    log_path = tmp_path / "001-build.jsonl.gz"

    async def scenario():
        harness = ClaudeHarness(str(binary))
        run = asyncio.create_task(
            harness.run(cwd=tmp_path, prompt="p", model="opus", max_turns=5, log_path=log_path, env={})
        )
        tail, seen = Tail(log_path), []
        for _ in range(500):
            seen += tail.events()
            if seen:
                break
            await asyncio.sleep(0.01)
        go.touch()
        return seen, await run

    seen, result = asyncio.run(scenario())
    assert [e.get("subtype") for e in seen] == ["init"]
    assert result.outcome == "verdict"


def test_each_step_gets_a_line():
    steps = Steps(T0)
    subagent = {"parent_tool_use_id": "t2"}
    lines = steps.lines(
        [
            {"type": "system", "subtype": "init", "model": "claude-opus-5-5"},
            {"type": "system", "subtype": "thinking_tokens", "estimated_tokens": 50},
            said("Reading the brief.\nThen the spec.", 3),
            call("t1", "Bash", 65, command="npm test", description="Run the tests"),
            answer("t1", "> test\n\n42 passed", 70),
            call("t2", "Task", 80, description="Review the spec", prompt="Read SPEC.md and …"),
            call("t3", "Read", 81, file_path="/workspace/trees/SPEC.md") | subagent,
            answer("t3", [{"type": "text", "text": "# Spec"}], 82) | subagent,
            answer("t2", "", 90, error=True),
            {"type": "rate_limit_event", "rate_limit_info": {"status": "allowed"}},
            {"type": "rate_limit_event", "rate_limit_info": {"status": "allowed_warning", "rateLimitType": "five_hour"}},
            call("t4", "StructuredOutput", 95, verdict="done", message="Spec written."),
            answer("t4", "Structured output provided successfully", 95),
            RESULT,
        ]
    )
    assert lines == [
        "          started on claude-opus-5-5",
        "    0:03  says: Reading the brief. Then the spec.",
        "    1:05  → Bash npm test",
        "    1:10  ← > test … 42 passed (2 lines)",
        "    1:20  → Task Review the spec",
        "    1:21  ┆ → Read /workspace/trees/SPEC.md",
        "    1:22  ┆ ← # Spec",
        "    1:30  ✗ (no output)",
        "          Claude usage: allowed_warning (five_hour)",
        "          ■ finished after 12 turns: done: Spec written.",
    ]
    assert steps.ended


def test_steps_know_what_the_worker_is_on():
    steps = Steps(T0)
    steps.lines([call("t1", "Bash", 60, command="npm run dev")])
    assert steps.doing(T0 + timedelta(minutes=4)) == "running Bash npm run dev for 3m"
    steps.lines([answer("t1", "listening on :3000", 61)])
    assert steps.doing(T0 + timedelta(minutes=4)) == "thinking for 2m"


def test_watch_follows_a_run_until_nothing_more_is_running(office):
    store = office.store
    job, run_id, log = running_job(office)
    write(log, said("Reading the brief.", 2))
    worker = [
        lambda: write(log, call("t1", "Bash", 65, command="npm test"), answer("t1", "42 passed", 70)),
        lambda: write(log, RESULT),
        lambda: (store.finish_run(run_id, "verdict", num_turns=12), store.update_job(job.id, state="waiting")),
    ]
    out = []
    follow(office.settings, store, out.append, sleep=lambda _: worker.pop(0)(), clock=lambda: T0 + timedelta(minutes=5))
    assert out == [
        "#1 Trees (trees): new, rigorous, opus/max. spec (2/26): running for 5m",
        "",
        "── job 1 run 001-spec on opus/max, started 2026-10-22 13:30 UTC",
        "    0:02  says: Reading the brief.",
        "    1:05  → Bash npm test",
        "    1:10  ← 42 passed",
        "          ■ finished after 12 turns: done: Spec written.",
        "",
        "#1 Trees (trees): new, rigorous, opus/max. spec (2/26): waiting on you: reply in its room",
    ]


def test_watch_says_what_a_quiet_worker_is_on(office):
    store = office.store
    start = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=2)
    job, run_id, log = running_job(office, start)
    write(log, call("t1", "Bash", 60, start, command="npm run dev"))
    now = [start + timedelta(minutes=2)]  # "nothing logged" goes by the log's real modification time

    def sleep(_):
        if now[0] - start < timedelta(minutes=5):
            now[0] += timedelta(minutes=11, seconds=30)
        else:
            store.finish_run(run_id, "killed")
            store.update_job(job.id, state="killed")

    out = []
    follow(office.settings, store, out.append, sleep=sleep, clock=lambda: now[0])
    assert [line.strip() for line in out[-5:]] == [
        "1:00  → Bash npm run dev",
        "… running Bash npm run dev for 12m; nothing logged for 11m, it may be stuck (!kill in its room stops the job)",
        "■ killed",
        "",
        "No unfinished jobs.",
    ]


def test_watch_shows_runs_that_end_between_looks(office):
    store = office.store
    store.add_project("trees", "Trees", "Flashcards")
    job = store.add_job("trees", "new", "a flashcard game", "standard")
    store.update_job(job.id, state="parked", retry_at=(T0 + timedelta(seconds=30)).isoformat())

    def fails_fast(_):
        run_id = store.start_run(job.id, "spec", "opus/high", str(office.settings.job_logs(job.id) / "001-spec.jsonl.gz"))
        store.finish_run(run_id, "error")
        times = (T0.isoformat(), (T0 + timedelta(seconds=3)).isoformat(), run_id)
        store.db.execute("UPDATE runs SET started_at = ?, ended_at = ? WHERE id = ?", times)
        store.update_job(job.id, state="parked", attempts=1, retry_at=(T0 + timedelta(minutes=20)).isoformat())

    out = []
    follow(office.settings, store, out.append, sleep=fails_fast, clock=lambda: T0)
    assert out == [
        "#1 Trees (trees): new, standard, opus/high. set up (1/24): starting in 30s",
        "",
        "── job 1 run 001-spec on opus/high, started 2026-10-22 13:30 UTC: error after 3s",
        "          ■ error",
        "",
        "#1 Trees (trees): new, standard, opus/high. spec (2/24): retrying in 20m (last run: error)",
    ]


def test_a_run_cut_short_by_a_restart_is_not_waited_on(office):
    job, run_id, log = running_job(office)
    write(log, said("Reading the brief.", 2))
    office.store.requeue_interrupted()
    out = []
    watch_run(office.store, office.store.run(run_id), out.append, sleep=None, clock=lambda: T0, backlog=20)
    assert out[-2:] == [
        "    0:02  says: Reading the brief.",
        "          ■ interrupted: office restarted, and the step runs again",
    ]


def test_watch_starts_with_the_latest_steps(office):
    job, run_id, log = running_job(office)
    write(log, *(said(f"step {i}", i) for i in range(25)))
    office.store.finish_run(run_id, "verdict")
    out = []
    watch_run(office.store, office.store.run(run_id), out.append, sleep=None, clock=lambda: T0, backlog=20)
    assert out[2] == "          … 5 earlier steps (bin/watch.sh 1 shows them all)"
    assert out[3].endswith("says: step 5") and out[-2].endswith("says: step 24")
    assert out[-1] == "          ■ verdict"


def test_overview_says_why_jobs_wait(office):
    store = office.store
    for slug in ("a", "b", "c"):
        store.add_project(slug, slug.upper(), "s")
    a = store.add_job("a", "new", "x", "standard")
    store.update_job(a.id, state="waiting")
    b = store.add_job("b", "change", "y", "standard")
    store.finish_run(store.start_run(b.id, "spec", "opus/high", "/nowhere"), "limit")
    store.update_job(b.id, state="parked", retry_at=(T0 + timedelta(minutes=23)).isoformat())
    c = store.add_job("c", "new", "z", "standard")
    store.update_job(c.id, state="parked", retry_at=(T0 + timedelta(seconds=40)).isoformat())
    store.set_paused("c", True)
    store.put("scheduler_paused", "1")
    assert overview(office.settings, store, T0) == [
        "Work is paused: !resume in the inbox starts it again.",
        "#1 A (a): new, standard, opus/high. set up (1/24): waiting on you: reply in its room",
        "#2 B (b): change, standard, opus/high. spec (2/24): retrying in 23m (last run: limit)",
        "#3 C (c): new, standard, opus/high. set up (1/24): starting in 40s, project paused",
    ]
    assert not expecting(store, T0)
    store.put("scheduler_paused", "0")
    assert not expecting(store, T0)  # b isn't due for a while, c's project is paused
    store.update_job(b.id, retry_at=(T0 + timedelta(minutes=1)).isoformat())
    assert expecting(store, T0)


def test_transcript_shows_every_run_of_a_job(office):
    store = office.store
    job, run_id, log = running_job(office)
    write(log, said("Writing the spec.", 5), RESULT)
    log.close()
    store.finish_run(run_id, "verdict", num_turns=12)
    store.db.execute("UPDATE runs SET ended_at = ? WHERE id = ?", ((T0 + timedelta(minutes=14)).isoformat(), run_id))
    store.update_job(job.id, state="done", phase=8)
    out = []
    assert transcript(office.settings, store, job.id, out.append) == 0
    assert out == [
        "#1 Trees (trees): new, rigorous, opus/max. finished: done",
        "Asked for: a flashcard game",
        "",
        "── job 1 run 001-spec on opus/max, started 2026-10-22 13:30 UTC: verdict after 14m, 12 turns",
        "    0:05  says: Writing the spec.",
        "          ■ finished after 12 turns: done: Spec written.",
    ]
    assert transcript(office.settings, store, 99, out.append) == 1
