"""What Riggy can change from the status page, with the rules that keep a job sound.

- Skip an upcoming step the playbook marks optional (a review, docs, the MVP check-in),
  or undo that while nothing after it has started. The work itself and office's
  push-to-deploy steps can't be skipped, so a job still always ends deployed.
- Move a job to the front or the back of the queue. It takes the worker at the next
  step boundary: a running step isn't interrupted.
- Pause and resume a project, or all work; stop a job.
- Choose the model and effort: for an agent step that hasn't started, for a project, or
  as the default for a kind of step (all docs steps, say). The most specific wins
  (plan.engine).
- Send a job a note, as if said in its room: feedback for the next agent step, or the
  answer to a question or a check-in, which picks the job back up. Or give one upcoming
  agent step some context, which only that step gets.

Each change is posted in the project's room, so the room stays the record of the job.
"""

from __future__ import annotations

from . import plan
from .scheduler import Scheduler
from .store import ACTIVE, EFFORTS, MODELS, Job, Project, Step, Store

SKIPPED = "Skipped from the status page."


class ControlError(Exception):
    """A change that isn't allowed, with a reason to show."""


class Control:
    def __init__(self, store: Store, scheduler: Scheduler):
        self.store = store
        self.scheduler = scheduler

    def _job(self, job_id: int) -> tuple[Job, Project]:
        job = self.store.job(job_id)
        if job is None or job.state not in ACTIVE:
            raise ControlError("That job has finished.")
        return job, self.store.project(job.slug)

    def _project(self, slug: str) -> Project:
        project = self.store.project(slug)
        if project is None:
            raise ControlError(f"No project called {slug}.")
        return project

    def step(self, job: Job, step_id: int, position: int = 0) -> Step:
        """A job's step by id, or by position for a job that hasn't started (its steps are written then)."""
        if not step_id and position:
            steps = self.scheduler.ensure_steps(job)
            step_id = next((s.id for s in steps if s.position == position), 0)
        step = self.store.step(step_id) if step_id else None
        if step is None or step.job_id != job.id:
            raise ControlError("No such step.")
        return step

    def configurable(self, job: Job, step: Step) -> bool:
        """Whether a step's model, effort and context can still change: an agent step not yet started."""
        return job.state in ACTIVE and step.kind == "agent" and step.state == "pending" and step.started_at is None

    def kinds(self) -> list[str]:
        """The kinds of agent step, by name, in the order the playbooks use them; fix last."""
        names: list[str] = []
        for book in ("standard", "rigorous", "reckless", "feature"):
            for spec in plan.load(self.scheduler.settings, book).steps:
                if spec.kind == "agent" and spec.name not in names:
                    names.append(spec.name)
        return [*names, "fix"]

    def skippable(self, job: Job, step_id: int) -> bool:
        """Whether an upcoming step can be skipped: optional, and not under way."""
        step = self.store.step(step_id)
        if step is None or step.job_id != job.id or not step.optional:
            return False
        if step.kind == "checkin" and step.state == "waiting":  # waiting on Riggy: skipping is "looks good"
            return True
        return step.state == "pending" and step.started_at is None

    def restorable(self, job: Job, step_id: int) -> bool:
        """Whether a skip can be undone: nothing after the step has started."""
        step = self.store.step(step_id)
        if step is None or step.job_id != job.id or step.state != "skipped" or step.summary != SKIPPED:
            return False
        later = [s for s in self.store.steps(job.id) if s.position > step.position]
        return all(s.state == "pending" and s.started_at is None for s in later)

    async def skip(self, job_id: int, step_id: int) -> str:
        job, project = self._job(job_id)
        if not self.skippable(job, step_id):
            raise ControlError("That step can't be skipped: only optional steps that haven't started can.")
        step = self.store.update_step(step_id, state="skipped", summary=SKIPPED)
        if job.state == "waiting" and step.kind == "checkin":
            self.store.update_job(job.id, state="queued", attempts=0)
        self.scheduler.wake()
        what = "your check-in (carrying on as if you'd said it looks good)" if step.kind == "checkin" else step.name
        await self.scheduler.say(project, f"Skipped {what}, from the status page.", notice=True)
        return f"Skipped {plan.label(step)}."

    async def restore(self, job_id: int, step_id: int) -> str:
        job, project = self._job(job_id)
        if not self.restorable(job, step_id):
            raise ControlError("That skip can't be undone any more: the steps after it have started.")
        step = self.store.update_step(step_id, state="pending", summary=None)
        await self.scheduler.say(project, f"Put {plan.label(step)} back in, from the status page.", notice=True)
        return f"Put {plan.label(step)} back."

    async def move(self, job_id: int, where: str) -> str:
        job, project = self._job(job_id)
        others = [j.priority for j in self.store.jobs(ACTIVE) if j.id != job.id]
        if where == "first":
            priority = max(others, default=job.priority) + 1
        elif where == "last":
            priority = min(others, default=job.priority) - 1
        else:
            raise ControlError("Move it first or last.")
        self.store.update_job(job.id, priority=priority)
        self.scheduler.wake()
        return f"{project.name} goes {where} in the queue, from the next step."

    async def pause(self, slug: str, paused: bool) -> str:
        project = self._project(slug)
        self.store.set_paused(slug, paused)
        self.scheduler.wake()
        word = "Paused" if paused else "Resumed"
        await self.scheduler.say(project, f"{word} {project.name}, from the status page.", notice=True)
        return f"{word} {project.name}."

    def pause_all(self, paused: bool) -> str:
        self.scheduler.set_paused(paused)
        return "Paused all work: a running step finishes first." if paused else "Resumed all work."

    async def kill(self, job_id: int) -> str:
        job, project = self._job(job_id)
        self.scheduler.kill(job.id)
        await self.scheduler.say(project, f"Stopped job {job.id}, from the status page.", notice=True)
        return f"Stopped job {job.id}."

    async def setting(self, slug: str, name: str, value: str) -> str:
        """A project's model or effort for all its agent steps; "" for each step's default."""
        project = self._project(slug)
        _check(name, value)
        self.store.update_project(slug, **{name: value})
        said = f"{name.title()} is now {value}" if value else f"{name.title()} is each step's default again"
        await self.scheduler.say(project, f"{said}, from the status page.", notice=True)
        return f"{project.name}: {name} {value or 'by step'}, from the next agent step."

    async def step_setting(self, job_id: int, step_id: int, position: int, name: str, value: str) -> str:
        """One upcoming agent step's model or effort; "" to go back to the defaults."""
        job, project = self._job(job_id)
        step = self.step(job, step_id, position)
        if not self.configurable(job, step):
            raise ControlError("Only agent steps that haven't started can change.")
        _check(name, value)
        step = self.store.update_step(step.id, **{name: value or None})
        engine = plan.engine(self.scheduler.settings, self.store, project, step)
        await self.scheduler.say(project, f"{step.name} will run on {engine}, from the status page.", notice=True)
        return f"{step.name} will run on {engine}."

    def default_setting(self, kind: str, name: str, value: str) -> str:
        """The model or effort for every step of one kind (or "*", all of them); "" to unset."""
        if kind != "*" and kind not in self.kinds():
            raise ControlError(f"No kind of step called {kind}.")
        _check(name, value)
        self.store.set_default(kind, name, value)
        what = "Every step" if kind == "*" else f"Every {kind} step"
        return f"{what}: {name} {value or 'unset'}, unless a project or step says otherwise. Starts with the next one."

    async def note(self, job_id: int, text: str, step_id: int = 0, position: int = 0) -> str:
        job, project = self._job(job_id)
        text = text.strip()
        if not text:
            raise ControlError("Write something first.")
        if step_id or position:  # context for one upcoming step
            step = self.step(job, step_id, position)
            if not self.configurable(job, step):
                raise ControlError("That step has started: send a note to the job instead.")
            self.store.add_note(job.id, text[:4000], step.id)
            await self.scheduler.say(project, f"For {step.name}, from the status page: {text[:4000]}", notice=True)
            return f"{step.name} gets that when it starts."
        self.store.add_note(job.id, text[:4000])
        await self.scheduler.say(project, f"From the status page: {text[:4000]}", notice=True)
        if job.state == "waiting":
            self.store.update_job(job.id, state="queued", attempts=0)
            self.scheduler.wake()
            return "Sent. Picking the job back up."
        return "Sent. The next agent step gets it."


def _check(name: str, value: str) -> None:
    allowed = {"model": MODELS, "effort": EFFORTS}.get(name)
    if allowed is None or (value and value not in allowed):
        raise ControlError(f"{name} can't be {value}.")
