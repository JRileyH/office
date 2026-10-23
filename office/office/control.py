"""What Riggy can change from the status page, with the rules that keep a job sound.

- Skip an upcoming step the playbook marks optional (a review, docs, the MVP check-in),
  or undo that while nothing after it has started. The work itself and office's
  push-to-deploy steps can't be skipped, so a job still always ends deployed.
- Move a job to the front or the back of the queue. It takes the worker at the next
  step boundary: a running step isn't interrupted.
- Pause and resume a project, or all work; stop a job.
- Change a project's model and effort (from the next agent step on).
- Send a job a note, as if said in its room: feedback for the next agent step, or the
  answer to a question or a check-in, which picks the job back up.

Each change is posted in the project's room, so the room stays the record of the job.
"""

from __future__ import annotations

from . import plan
from .scheduler import Scheduler
from .store import ACTIVE, EFFORTS, MODELS, Job, Project, Store

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
        project = self._project(slug)
        allowed = {"model": MODELS, "effort": EFFORTS}.get(name)
        if allowed is None or value not in allowed:
            raise ControlError(f"{name} can't be {value}.")
        self.store.update_project(slug, **{name: value})
        await self.scheduler.say(project, f"{name.title()} is now {value}, from the status page.", notice=True)
        return f"{project.name}: {name} {value}, from the next agent step."

    async def note(self, job_id: int, text: str) -> str:
        job, project = self._job(job_id)
        text = text.strip()
        if not text:
            raise ControlError("Write something first.")
        self.store.add_note(job.id, text[:4000])
        await self.scheduler.say(project, f"From the status page: {text[:4000]}", notice=True)
        if job.state == "waiting":
            self.store.update_job(job.id, state="queued", attempts=0)
            self.scheduler.wake()
            return "Sent. Picking the job back up."
        return "Sent. The next agent step gets it."
