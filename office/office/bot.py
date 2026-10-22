"""The Matrix side: what Riggy's messages turn into.

- In the inbox (or any room that isn't a project's), free text is a new idea: it's
  triaged into a brief, gets a project, its own room and a job.
- In a project's room, free text is about that project: feedback for the job that's
  waiting on him, a note for the job that's running, or else a change request.
- `!commands` work anywhere; in a project room they default to that project.
"""

from __future__ import annotations

import logging
import os

from . import intake
from .config import Settings
from .harness import Harness
from .matrix import Chat, Message
from .prompts import Prompts
from .scheduler import Scheduler
from .store import ACTIVE, Job, Project, Store
from .workspace import Workspace

log = logging.getLogger(__name__)

HELP = """Commands (in a project's room they apply to that project):
!queue — what's queued, running and waiting
!status [project] — a project's state and links
!pause [project] / !resume [project] — hold a project while you tinker; with no project in the inbox, all work
!kill [job] — drop a job

Anything else: in the inbox, a new idea; in a project's room, feedback or a change."""

STARTING = "Starting now."
INBOX_WELCOME = """This is office's inbox. Dictate an idea here and I'll turn it into a project with its own room, where I'll post check-ins and you can ask for changes. !help lists commands."""


class Bot:
    def __init__(
        self,
        settings: Settings,
        store: Store,
        chat: Chat,
        scheduler: Scheduler,
        harness: Harness,
        workspace: Workspace,
        prompts: Prompts,
    ):
        self.settings = settings
        self.store = store
        self.chat = chat
        self.scheduler = scheduler
        self.harness = harness
        self.workspace = workspace
        self.prompts = prompts

    async def ensure_rooms(self) -> None:
        """The inbox and status rooms, created once and remembered."""
        status_url = self.settings.url("office") + "/"
        if not self.store.get("inbox_room"):
            room = await self.chat.create_room("office inbox", "Dictate ideas here. " + status_url, alias="inbox")
            self.store.put("inbox_room", room)
            await self.chat.send(room, INBOX_WELCOME)
        if not self.store.get("status_room"):
            room = await self.chat.create_room("office status", "Queue changes, limits, finished jobs. " + status_url, alias="status")
            self.store.put("status_room", room)

    async def handle(self, msg: Message) -> None:
        if not msg.body:
            return
        if msg.body.startswith("!"):
            await self.command(msg)
            return
        project = self.store.project_by_room(msg.room_id)
        if project:
            await self.project_message(project, msg)
        elif msg.room_id == self.store.get("status_room"):
            await self.chat.send(msg.room_id, "This room is for updates. Dictate ideas in the inbox.", notice=True)
        else:
            await self.new_idea(msg)

    # --- new ideas ------------------------------------------------------------

    async def new_idea(self, msg: Message) -> None:
        await self.chat.typing(msg.room_id, True)
        try:
            brief = await self.triage(msg.body)

            async def taken(slug: str) -> bool:
                return self.store.project(slug) is not None or await self.workspace.repo_exists(slug)

            slug = await intake.unique_slug(brief.slug, taken)
            project = self.store.add_project(slug, brief.name, brief.summary)
            url, repo = self.settings.project_url(slug), self.settings.repo_url(slug)
            room = await self.chat.create_room(brief.name, f"{url} · {repo}", alias=slug)
            self.store.set_room(slug, room)
            job = self.store.add_job(slug, "new", msg.body, "standard")
        finally:
            await self.chat.typing(msg.room_id, False)

        await self.chat.send(
            room,
            f"{brief.name}\n\n{brief.summary}\n\nYou said: “{msg.body}”\n\n"
            f"It'll live at {url} (code: {repo}). I'll post progress here and check in when there's "
            "an MVP worth looking at. Reply here any time with feedback or changes.",
        )
        await self.chat.send(
            msg.room_id,
            f"New project: {brief.name} ({slug}). {self.queue_position(job)} "
            f"I've invited you to its room: https://matrix.to/#/{room}",
        )
        self.scheduler.wake()

    async def triage(self, text: str) -> intake.Brief:
        if self.settings.harness == "fake":
            return intake.fallback_brief(text)
        return await intake.triage(
            self.harness,
            self.prompts.intake_path(),
            text,
            cwd=self.settings.state_dir / "triage",
            log_path=self.settings.state_dir / "triage" / "last.jsonl.gz",
            env={k: v for k, v in os.environ.items() if k != "OFFICE_MATRIX_PASSWORD"},
        )

    # --- project rooms --------------------------------------------------------

    async def project_message(self, project: Project, msg: Message) -> None:
        job = self.store.active_job(project.slug)
        if job is None:
            job = self.store.add_job(project.slug, "change", msg.body, "feature")
            position = self.queue_position(job)
            if position != STARTING:  # otherwise "Working on it" follows straight away
                await self.chat.send(msg.room_id, f"Got it. {position}", notice=True)
            self.scheduler.wake()
            return
        self.store.add_note(job.id, msg.body)
        if job.state == "waiting":
            self.store.update_job(job.id, state="queued")
            await self.chat.send(msg.room_id, f"Thanks, picking it back up. {self.queue_position(job)}", notice=True)
            self.scheduler.wake()
        elif job.state == "running":
            await self.chat.send(msg.room_id, "Noted. I'll work that in at the next step of the current job.", notice=True)
        else:
            await self.chat.send(msg.room_id, "Noted. I'll include that when the job starts.", notice=True)

    def queue_position(self, job: Job) -> str:
        if self.scheduler.paused:
            return "Work is paused (!resume to start again)."
        if self.store.project(job.slug).paused:
            return "This project is paused (!resume to start again)."
        current = self.scheduler.busy_with()
        if current is None or current.id == job.id:
            return STARTING
        ahead = [j for j in self.store.jobs(("queued",)) if j.id != job.id and (j.priority, -j.id) >= (job.priority, -job.id)]
        busy = self.store.project(current.slug)
        others = f" and {len(ahead)} more queued" if ahead else ""
        return f"Queued: I'm on {busy.name}{others} first."

    # --- commands -------------------------------------------------------------

    async def command(self, msg: Message) -> None:
        name, *args = msg.body[1:].split()
        here = self.store.project_by_room(msg.room_id)
        reply = await self.run_command(name.lower(), args, here)
        await self.chat.send(msg.room_id, reply, notice=True)

    async def run_command(self, name: str, args: list[str], here: Project | None) -> str:
        match name:
            case "help":
                return HELP
            case "queue":
                return self.queue_text()
            case "status":
                project = self.pick_project(args, here)
                if project:
                    return self.status_text(project)
                return self.queue_text() if not args else f"No project called {args[0]}."
            case "pause" | "resume":
                paused = name == "pause"
                if not args and not here:
                    self.scheduler.set_paused(paused)
                    return "Paused all work. The current phase finishes first." if paused else "Resumed."
                project = self.pick_project(args, here)
                if not project:
                    return f"No project called {args[0]}."
                self.store.set_paused(project.slug, paused)
                self.scheduler.wake()
                if paused:
                    return f"Paused {project.name}. A phase already running finishes first; nothing new starts until !resume."
                return f"Resumed {project.name}."
            case "kill":
                job = self.pick_job(args, here)
                if job is None:
                    return "Which job? !kill <number> (see !queue)."
                if not self.scheduler.kill(job.id):
                    return f"Job {job.id} has already finished."
                return f"Killed job {job.id} ({self.store.project(job.slug).name})."
            case _:
                return f"Unknown command !{name}.\n\n{HELP}"

    def pick_project(self, args: list[str], here: Project | None) -> Project | None:
        return self.store.project(args[0].lower()) if args else here

    def pick_job(self, args: list[str], here: Project | None) -> Job | None:
        if args:
            return self.store.job(int(args[0].lstrip("#"))) if args[0].lstrip("#").isdigit() else None
        return self.store.active_job(here.slug) if here else None

    def queue_text(self) -> str:
        jobs = self.store.jobs(ACTIVE)
        lines = [self.job_line(j) for j in jobs]
        head = "Work is paused.\n" if self.scheduler.paused else ""
        return head + ("\n".join(lines) if lines else "Nothing queued.")

    def job_line(self, job: Job) -> str:
        project = self.store.project(job.slug)
        book = self.scheduler.playbook(job.playbook)
        phase = book.phases[job.phase].name if job.phase < len(book.phases) else "finished"
        what = "new project" if job.kind == "new" else "change"
        held = " (project paused)" if project.paused else ""
        return f"#{job.id} {project.name}: {what}, {job.state}, {phase}{held}"

    def status_text(self, project: Project) -> str:
        job = self.store.active_job(project.slug)
        state = self.job_line(job) if job else "Idle."
        return (
            f"{project.name} ({project.slug})\n{state}\n"
            f"{self.settings.project_url(project.slug)}\n{self.settings.repo_url(project.slug)}"
        )
