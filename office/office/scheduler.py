"""The scheduler: one worker slot, one phase at a time.

Each phase is one headless run that ends in a verdict. The job's position in its
playbook is saved after every phase, so a crash or a usage limit costs at most one phase.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from . import playbook as playbooks
from .config import Settings
from .harness import Harness, RunResult
from .matrix import Chat
from .prompts import Prompts
from .store import ACTIVE, Job, Project, Store, later
from .watch import Steps, Tail, span
from .workspace import Workspace, WorkspaceError

log = logging.getLogger(__name__)

WRAPUP_TURNS = 15
MAX_ATTEMPTS = 3  # failed runs of one phase before asking Riggy for help
LIMIT_RETRY = timedelta(minutes=30)
ERROR_RETRY = timedelta(minutes=1)
IDLE_POLL_SECONDS = 30
MONITOR_SECONDS = 15  # how often a running phase is checked on
HEARTBEAT_SECONDS = 20 * 60  # no news from a phase this long: say what it's on
# Nothing logged this long and the run is stopped and retried. Claude Code logs while it
# thinks, and its commands time out after ten minutes, so a live run is never this quiet.
STUCK_SECONDS = 30 * 60
# Never handed to workers: they talk to Riggy through verdicts, not as the bot,
# and only Riggy's !delete deletes a project's data.
WORKER_ENV_DENY = frozenset({"OFFICE_MATRIX_PASSWORD", "DEPLOYER_ADMIN_SECRET"})


class Scheduler:
    def __init__(
        self,
        settings: Settings,
        store: Store,
        chat: Chat,
        harness: Harness,
        workspace: Workspace,
        prompts: Prompts,
    ):
        self.settings = settings
        self.store = store
        self.chat = chat
        self.harness = harness
        self.workspace = workspace
        self.prompts = prompts
        self.current: tuple[int, asyncio.Task] | None = None
        self._wake = asyncio.Event()

    # --- control --------------------------------------------------------------

    @property
    def paused(self) -> bool:
        return self.store.get("scheduler_paused") == "1"

    def set_paused(self, paused: bool) -> None:
        self.store.put("scheduler_paused", "1" if paused else "0")
        self.wake()

    def wake(self) -> None:
        self._wake.set()

    def busy_with(self) -> Job | None:
        return self.store.job(self.current[0]) if self.current else None

    def kill(self, job_id: int) -> bool:
        """Stop a job for good. Returns False if it had already finished."""
        job = self.store.job(job_id)
        if job is None or job.state in ("done", "failed", "killed"):
            return False
        self.store.update_job(job_id, state="killed")
        if self.current and self.current[0] == job_id:
            self.current[1].cancel()
        return True

    async def drop_project(self, slug: str) -> None:
        """Kill the project's unfinished jobs and wait for a running phase to stop."""
        current = self.current
        for job in self.store.jobs(ACTIVE):
            if job.slug == slug:
                self.kill(job.id)
        if current and self.store.job(current[0]).slug == slug:
            await asyncio.wait({current[1]})

    # --- loop -----------------------------------------------------------------

    async def run_forever(self) -> None:
        for job in self.store.requeue_interrupted():
            log.info("job %s was interrupted; it reruns phase %s", job.id, job.phase)
        while True:
            job = None if self.paused else self.store.next_job()
            if job is None:
                try:
                    await asyncio.wait_for(self._wake.wait(), IDLE_POLL_SECONDS)
                except TimeoutError:
                    pass
                self._wake.clear()
                continue
            task = asyncio.create_task(self.step(job))
            self.current = (job.id, task)
            try:
                await asyncio.wait({task})
            except asyncio.CancelledError:  # office is shutting down; the job reruns this phase on start
                task.cancel()
                raise
            finally:
                self.current = None
            if task.cancelled():
                log.info("job %s killed", job.id)
            elif task.exception():
                log.error("job %s crashed", job.id, exc_info=task.exception())
                await self.retry_or_ask(job, f"office error: {task.exception()!r}")

    async def step(self, job: Job) -> None:
        """Run the job's current phase and act on its verdict."""
        project = self.store.project(job.slug)
        book = self.playbook(job.playbook)
        if job.phase >= len(book.phases):
            self.store.update_job(job.id, state="done")
            return
        job = self.store.update_job(job.id, state="running")
        if job.phase == 0 and not self.store.runs(job.id) and not job.resume_session:
            try:
                job = await self.start_job(job, project)
            except WorkspaceError as e:
                await self.retry_or_ask(job, str(e))
                return

        phase = book.phases[job.phase]
        notes = self.store.pending_notes(job.id)
        feedback = [text for _, text in notes]
        if job.resume_session:
            prompt, resume = self.prompts.resume(feedback), job.resume_session
        else:
            lessons = self.lessons_path.read_text() if self.lessons_path.exists() else ""
            prompt, resume = self.prompts.phase(project, job, book, feedback, lessons), None

        result = await self.execute(job, project, phase.name, phase.max_turns, prompt, resume)
        if result.outcome == "max_turns" and result.session_id:
            result = await self.execute(
                job, project, f"{phase.name}-wrapup", WRAPUP_TURNS, self.prompts.wrapup(), result.session_id
            )
        if self.store.job(job.id).state == "killed":
            return
        await self.handle(job, project, book, result, [i for i, _ in notes])

    async def start_job(self, job: Job, project: Project) -> Job:
        """First phase of a job: repo and checkout ready, an issue to hang the work on."""
        await self.workspace.ensure_repo(project.slug, project.summary)
        await self.workspace.sync_main(project.slug)
        if job.issue is None:
            title = project.name if job.kind == "new" else _first_line(job.request)
            body = f"Requested in Matrix:\n\n> {job.request}\n\nWorked on by office (job {job.id})."
            issue = await self.workspace.open_issue(project.slug, title, body)
            job = self.store.update_job(job.id, issue=issue)
        if job.kind == "change":
            await self.say(project, f"Working on it: {_first_line(job.request)}", notice=True)
        return job

    async def execute(
        self, job: Job, project: Project, label: str, max_turns: int, prompt: str, resume: str | None
    ) -> RunResult:
        """One run of the phase, with the project's current model and effort."""
        project = self.store.project(project.slug)
        log_path = self.settings.job_logs(job.id) / f"{len(self.store.runs(job.id)) + 1:03d}-{label}.jsonl.gz"
        run_id = self.store.start_run(job.id, label, f"{project.model}/{project.effort}", str(log_path))
        notes = self.progress_path(job)
        seen = notes.stat().st_size if notes.exists() else 0  # notes from earlier phases were passed on then
        run = asyncio.create_task(
            self.harness.run(
                cwd=self.workspace.path(project.slug),
                prompt=prompt,
                model=project.model,
                effort=project.effort,
                max_turns=max_turns,
                log_path=log_path,
                env=self.worker_env(project, job),
                resume=resume,
                label=label,
            )
        )
        monitor = asyncio.create_task(self.monitor(job, project, label, log_path, seen, run))
        try:
            await asyncio.wait({run})
        except asyncio.CancelledError:
            run.cancel()
            await asyncio.wait({run})
            self.store.finish_run(run_id, "killed")
            raise
        finally:
            monitor.cancel()
        if run.cancelled():  # the monitor stopped it
            result = RunResult("error", error=f"no sign of life for {STUCK_SECONDS // 60} minutes")
        else:
            result = run.result()
        self.store.finish_run(run_id, result.outcome, session_id=result.session_id, **result.usage)
        return result

    async def monitor(
        self, job: Job, project: Project, label: str, log_path: Path, seen: int, run: asyncio.Task
    ) -> None:
        """While a phase runs, keep Riggy posted: the worker's progress notes (past byte `seen`) as it
        writes them, what it's on when there's been no news for a while, and a restart if it stops."""
        notes = self.progress_path(job)
        tail, started = Tail(log_path), datetime.now(UTC)
        steps, news = Steps(started), started
        phases = len(self.playbook(job.playbook).phases)
        while True:
            await asyncio.sleep(MONITOR_SECONDS)
            steps.lines(tail.events())
            now = datetime.now(UTC)
            new, seen = _new_lines(notes, seen)
            for note in new[-3:]:
                await self.say(project, f"▸ {_first_line(note, 300)}", notice=True)
                news = now
            quiet = now - (tail.modified() or started)
            if quiet.total_seconds() >= STUCK_SECONDS:
                await self.say(project, f"No sign of life in {label} for {span(quiet)}, so I'm restarting it.", notice=True)
                run.cancel()
                return
            if (now - news).total_seconds() >= HEARTBEAT_SECONDS:
                said = f' Last said: "{steps.said}"' if steps.said else ""
                where = f"{label} ({job.phase + 1}/{phases})"
                await self.say(project, f"Still on {where}, {span(now - started)} in.{said} Now {steps.doing(now)}.", notice=True)
                news = now

    async def handle(self, job: Job, project: Project, book: playbooks.Playbook, result: RunResult, note_ids: list[int]) -> None:
        if result.outcome == "limit":
            self.store.update_job(job.id, state="parked", retry_at=later(LIMIT_RETRY))
            if self.store.get("limit_notice") != "1":
                self.store.put("limit_notice", "1")
                await self.status(f"Hit the Claude usage limit during {project.name}; parked it and retrying every 30 minutes.")
            return
        self.store.put("limit_notice", "0")
        if result.outcome != "verdict":
            await self.retry_or_ask(job, result.error or result.outcome)
            return

        self.store.consume_notes(note_ids)
        v = result.verdict
        message, link = v.get("message", "").strip(), (v.get("link") or "").strip()
        nxt = job.phase + 1
        match v["verdict"]:
            case "continue" if nxt < len(book.phases):
                self.store.update_job(job.id, phase=nxt, state="queued", attempts=0, resume_session=None)
                upcoming = f"Next: {book.phases[nxt].name} ({nxt + 1}/{len(book.phases)})."
                await self.say(project, _join(f"✓ {book.phases[job.phase].name}: {message}", link) + f"\n{upcoming}", notice=True)
            case "continue" | "done":
                self.store.update_job(job.id, phase=len(book.phases), state="done", attempts=0, resume_session=None)
                await self.say(project, _join(f"Done: {message}", link or self.settings.project_url(project.slug)))
                await self.status(f"Finished job {job.id} on {project.name}: {self.settings.project_url(project.slug)}", notice=True)
            case "checkin":
                self.store.update_job(job.id, phase=min(nxt, len(book.phases)), state="waiting", attempts=0, resume_session=None)
                await self.say(project, _join(message, link) + "\n\nReply here: say it looks good, or what to change.")
            case "blocked":
                self.store.update_job(job.id, state="waiting", attempts=0, resume_session=result.session_id)
                await self.say(project, _join(f"Question: {message}", link) + "\n\nReply here and I'll carry on.")

    async def retry_or_ask(self, job: Job, error: str) -> None:
        job = self.store.job(job.id)
        if job.state == "killed":
            return
        attempts = job.attempts + 1
        project = self.store.project(job.slug)
        if attempts < MAX_ATTEMPTS:
            self.store.update_job(job.id, state="parked", attempts=attempts, retry_at=later(ERROR_RETRY), resume_session=None)
            log.warning("job %s attempt %s failed: %s", job.id, attempts, error)
            return
        # Stuck after retries is one of the few reasons to interrupt Riggy.
        self.store.update_job(job.id, state="waiting", attempts=0, resume_session=None)
        book = self.playbook(job.playbook)
        phase = book.phases[min(job.phase, len(book.phases) - 1)].name
        await self.say(
            project,
            f"I'm stuck in {phase} after {MAX_ATTEMPTS} tries: {error[:400]}\n\n"
            "Reply with a hint and I'll try again, or `!kill` to drop the job.",
        )

    # --- helpers --------------------------------------------------------------

    def playbook(self, name: str) -> playbooks.Playbook:
        return playbooks.load(self.settings.process_dir / "playbooks" / f"{name}.md")

    @property
    def lessons_path(self) -> Path:
        return self.settings.state_dir / "lessons.md"

    def progress_path(self, job: Job) -> Path:
        """Where the worker appends progress notes for Riggy, one line each (prompts/phase.md)."""
        return self.settings.job_logs(job.id) / "progress.txt"

    def worker_env(self, project: Project, job: Job) -> dict[str, str]:
        env = {k: v for k, v in os.environ.items() if k not in WORKER_ENV_DENY}
        env.update(
            OFFICE_SLUG=project.slug,
            OFFICE_JOB=str(job.id),
            OFFICE_PROGRESS=str(self.progress_path(job)),
            OFFICE_URL=self.settings.project_url(project.slug),
            GITHUB_ORG=self.settings.org,
            DEPLOYER_URL=self.settings.deployer_url,
        )
        return env

    async def say(self, project: Project, text: str, notice: bool = False) -> None:
        if project.room_id:
            await self.chat.send(project.room_id, text, notice=notice)

    async def status(self, text: str, notice: bool = False) -> None:
        room = self.store.get("status_room")
        if room:
            await self.chat.send(room, text, notice=notice)


def _first_line(text: str, limit: int = 80) -> str:
    line = text.strip().splitlines()[0] if text.strip() else ""
    return line if len(line) <= limit else line[: limit - 1] + "…"


def _new_lines(path: Path, offset: int) -> tuple[list[str], int]:
    """Whole lines added to the file since `offset`, and the offset after them."""
    try:
        with open(path, "rb") as f:
            f.seek(offset)
            data = f.read()
    except FileNotFoundError:
        return [], offset
    complete = data[: data.rfind(b"\n") + 1]
    lines = [line.strip() for line in complete.decode(errors="replace").splitlines()]
    return [line for line in lines if line], offset + len(complete)


def _join(message: str, link: str) -> str:
    return f"{message}\n{link}" if link else message
