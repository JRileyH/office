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
from datetime import timedelta

from . import intake, plan, teardown
from .config import Settings
from .deployer_client import Deployer, DeployerError
from .harness import Harness
from .matrix import Chat, Message
from .prompts import Prompts
from .scheduler import WORKER_ENV_DENY, Scheduler
from .store import ACTIVE, EFFORTS, MODELS, MODES, Job, Project, Store, later
from .workspace import Workspace, WorkspaceError

log = logging.getLogger(__name__)

HELP = """Commands (in a project's room they apply to that project; elsewhere, put the project's slug first):
!queue — what's queued, running and waiting
!status [project] — a project's state, settings and links
!mode reckless|standard|rigorous — how carefully it's built
!model fable|opus|sonnet|haiku — which Claude model the workers use
!effort low|medium|high|xhigh|max — how hard they think
!rename <name> — change the project's name
!slug <new-slug> — move it to a new address and repo
!pause / !resume — hold a project while you tinker; with no project in the inbox, all work
!kill [job] — drop a job
!delete — delete a project for good: site, data, checkout, history and room (asks you to confirm)

In the inbox you can also say the settings when you dictate ("rigorous, opus, max effort").
Anything else: in the inbox, a new idea; in a project's room, feedback or a change."""

STARTING = "Starting now."
INBOX_WELCOME = """This is office's inbox. Dictate an idea here and I'll turn it into a project with its own room, where I'll post check-ins and you can ask for changes. !help lists commands."""
SETTINGS = {"mode": MODES, "model": MODELS, "effort": EFFORTS}


class Bot:
    # New projects wait this long before work starts, so Riggy can change their settings.
    start_delay = timedelta(minutes=1)

    def __init__(
        self,
        settings: Settings,
        store: Store,
        chat: Chat,
        scheduler: Scheduler,
        harness: Harness,
        workspace: Workspace,
        prompts: Prompts,
        deployer: Deployer,
    ):
        self.settings = settings
        self.store = store
        self.chat = chat
        self.scheduler = scheduler
        self.harness = harness
        self.workspace = workspace
        self.prompts = prompts
        self.deployer = deployer

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
            slug = await intake.unique_slug(brief.slug, self.slug_taken)
            mode = brief.mode or self.settings.default_mode
            project = self.store.add_project(
                slug,
                brief.name,
                brief.summary,
                mode=mode,
                model=brief.model or self.settings.default_model,
                effort=brief.effort or self.settings.default_effort,
            )
            url, repo = self.settings.project_url(slug), self.settings.repo_url(slug)
            room = await self.chat.create_room(brief.name, f"{url} · {repo}", alias=slug)
            self.store.set_room(slug, room)
            job = self.store.add_job(slug, "new", msg.body, mode)
            self.store.update_job(job.id, state="parked", retry_at=later(self.start_delay))
        finally:
            await self.chat.typing(msg.room_id, False)

        await self.chat.send(
            room,
            f"{brief.name}\n\n{brief.summary}\n\nYou said: “{msg.body}”\n\n"
            f"It'll live at {url} (code: {repo}). I'll post progress here and check in when there's "
            "an MVP worth looking at. Reply here any time with feedback or changes.\n\n"
            f"{settings_line(project)}. Starting in a minute: to change that first, say "
            "!mode, !model or !effort here (!help for the options).",
        )
        await self.chat.send(
            msg.room_id,
            f"New project: {brief.name} ({slug}), {settings_line(project).lower()}. "
            f"I've invited you to its room: https://matrix.to/#/{room}",
        )
        self.scheduler.wake()

    async def slug_taken(self, slug: str) -> bool:
        return self.store.project(slug) is not None or await self.workspace.repo_exists(slug)

    async def triage(self, text: str) -> intake.Brief:
        if self.settings.harness == "fake":
            return intake.fallback_brief(text)
        return await intake.triage(
            self.harness,
            self.prompts.intake_path(),
            text,
            cwd=self.settings.state_dir / "triage",
            log_path=self.settings.state_dir / "triage" / "last.jsonl.gz",
            env={k: v for k, v in os.environ.items() if k not in WORKER_ENV_DENY},
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
        elif job.state == "running" or self.store.steps(job.id):  # under way, maybe waiting on CI
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
        room = msg.room_id
        if here and self.store.project_by_room(room) is None:  # deleted, room and all
            room = self.store.get("inbox_room")
        await self.chat.send(room, reply, notice=True)

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
                    return "Paused all work. The current step finishes first." if paused else "Resumed."
                project = self.pick_project(args, here)
                if not project:
                    return f"No project called {args[0]}."
                self.store.set_paused(project.slug, paused)
                self.scheduler.wake()
                if paused:
                    return f"Paused {project.name}. A step already running finishes first; nothing new starts until !resume."
                return f"Resumed {project.name}."
            case "kill":
                job = self.pick_job(args, here)
                if job is None:
                    return "Which job? !kill <number> (see !queue)."
                if not self.scheduler.kill(job.id):
                    return f"Job {job.id} has already finished."
                return f"Killed job {job.id} ({self.store.project(job.slug).name})."
            case "mode" | "model" | "effort":
                return self.set_setting(name, args, here)
            case "rename":
                project, words = (here, args) if here else (self.pick_project(args[:1], None), args[1:])
                if not project or not words:
                    return "Usage: !rename <new name> in the project's room, or !rename <slug> <new name>."
                new = " ".join(words)[:60]
                self.store.update_project(project.slug, name=new)
                await self.chat.set_name(project.room_id, new)
                return f"Renamed {project.name} to {new}. The address stays {project.slug}; !slug changes that."
            case "slug" | "move":
                project, rest = (here, args) if here else (self.pick_project(args[:1], None), args[1:])
                if not project or len(rest) != 1:
                    return "Usage: !slug <new-slug> in the project's room, or !slug <old-slug> <new-slug>."
                return await self.move(project, rest[0].lower())
            case "delete":
                return await self.delete(args, here)
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
        steps = plan.planned(self.settings, self.store, job)
        step = plan.current(steps)
        what = "new project" if job.kind == "new" else "change"
        held = " (project paused)" if project.paused else ""
        state = job.state
        if state == "parked":  # a grace period before a new project starts, CI running, or a retry
            if not self.store.steps(job.id):
                state = "starting shortly"
            elif step and step.kind in ("checks", "deploy") and not step.error:
                state = "waiting for CI"
            else:
                state = "retrying soon"
        return f"#{job.id} {project.name}: {what}, {state}, {plan.where(steps)}{held}"

    def status_text(self, project: Project) -> str:
        job = self.store.active_job(project.slug)
        state = self.job_line(job) if job else "Idle."
        return (
            f"{project.name} ({project.slug})\n{settings_line(project)}\n{state}\n"
            f"{self.settings.project_url(project.slug)}\n{self.settings.repo_url(project.slug)}"
        )

    # --- settings -------------------------------------------------------------

    def set_setting(self, setting: str, args: list[str], here: Project | None) -> str:
        allowed = SETTINGS[setting]
        project, rest = (here, args) if here and len(args) == 1 else (self.pick_project(args[:1], None), args[1:])
        if not project or len(rest) != 1 or rest[0].lower() not in allowed:
            where = "" if here else " <project>"
            return f"Usage: !{setting}{where} {'|'.join(allowed)}"
        value = rest[0].lower()
        project = self.store.update_project(project.slug, **{setting: value})
        reply = f"{project.name}: {settings_line(project).lower()}."
        job = self.store.active_job(project.slug)
        if setting == "mode" and job and self.store.steps(job.id):
            reply += f" The job already under way keeps its steps; later jobs follow {value} rules."
        elif setting == "mode" and job and job.kind == "new" and job.playbook != value:
            self.store.update_job(job.id, playbook=value)
            reply += f" The new-project job will follow the {value} playbook."
        elif setting != "mode" and job:
            reply += " Takes effect from the next step."
        return reply

    async def move(self, project: Project, new: str) -> str:
        """A new slug means a new address and a new repo: office moves the code, a job fixes the references."""
        old = project.slug
        if new == old:
            return f"{project.name} is already at {old}."
        if not intake.valid_slug(new):
            return f"{new} won't work as an address: 2 to 31 lowercase letters, digits and hyphens, not reserved."
        if await self.slug_taken(new):
            return f"{new} is taken (by a project or an existing repo)."
        job = self.store.active_job(old)
        if job and job.state == "running":
            return "A step is running. Try again when it finishes, or !pause the project first."
        try:
            await self.workspace.move_repo(old, new, project.summary)
        except WorkspaceError as e:
            return f"Couldn't move the repo: {e}"
        await self.deployer.stop(old)
        project = self.store.move_project(old, new)
        url, repo = self.settings.project_url(new), self.settings.repo_url(new)
        await self.chat.set_topic(project.room_id, f"{url} · {repo}")
        await self.chat.add_alias(project.room_id, new)

        request = (
            f"This project moved from `{old}` to `{new}`. The code is now in {repo} (pushed by office, with "
            f"the full history) and it will live at {url}. Update every reference to the old slug (the image "
            f"in deploy/compose.yml, README, AGENTS.md, docs, code), wait for CI on main, deploy, and check "
            f"the new URL works. The old site has been stopped."
        )
        if job:
            self.store.add_note(job.id, request)
            follow = "The current job will update the references and redeploy."
        else:
            self.store.add_job(new, "change", request, "feature")
            self.scheduler.wake()
            follow = "A job is updating the references and will redeploy."
        return (
            f"Moved {project.name} to {new}: {repo}, soon live at {url}. {follow} "
            f"The old repo {self.settings.repo_url(old)} is still there; delete it on GitHub when you like "
            "(my token can't)."
        )

    async def delete(self, args: list[str], here: Project | None) -> str:
        """All of a project but its repo. Riggy confirms by typing the slug."""
        if not args:
            if not here:
                return "Usage: !delete <slug>. It deletes the project for good."
            return (
                f"This deletes {here.name} for good: the site and its data, the code checkout, every job and "
                f"its logs, and this room. The repo stays on GitHub for you to delete. "
                f"To go ahead, say !delete {here.slug}"
            )
        project = self.store.project(args[0].lower())
        if not project:
            return f"No project called {args[0]}."
        await self.scheduler.drop_project(project.slug)
        try:
            await teardown.delete_project(project, self.settings, self.store, self.chat, self.workspace, self.deployer)
        except (DeployerError, WorkspaceError) as e:
            return (
                f"Couldn't delete all of {project.name}: {e}. Its jobs are stopped; "
                f"say !delete {project.slug} to try again."
            )
        return (
            f"Deleted {project.name}: its site and data, checkout, jobs, logs and room are gone. "
            f"Delete the repo yourself on GitHub (Settings, at the bottom): {self.settings.repo_url(project.slug)}/settings"
        )


def settings_line(project: Project) -> str:
    return f"Mode {project.mode}, model {project.model}, {project.effort} effort"
