"""A job's steps: what it will do when it starts, where it is, and what comes next.

Shared by the scheduler (which writes the steps when a job starts), the bot, the status
page and bin/watch.sh.
"""

from __future__ import annotations

from . import playbook as playbooks
from .config import Settings
from .store import ACTIVE, OPEN, Job, Step, Store

# How office's own steps read in messages and on the status page.
LABELS = {
    "setup": "set up",
    "push": "push",
    "pr": "PR",
    "checks": "CI",
    "merge": "merge",
    "push-main": "push to main",
    "deploy": "deploy",
    "checkin": "your check-in",
    "alert": "done",
}
# Before steps were tracked, a job was a list of phases and an index into it. Jobs still
# under way from then pick up at the agent step their phase became (ship became docs).
LEGACY_PHASES = {
    "reckless": ["build"],
    "standard": ["spec", "scaffold", "mvp", "buildout", "review", "docs"],
    "rigorous": ["spec", "spec-review", "scaffold", "mvp", "buildout", "review", "second-pass", "docs"],
    "feature": ["change", "review", "docs"],
}
LEGACY_DONE = "Done before office tracked steps."


def load(settings: Settings, name: str) -> playbooks.Playbook:
    return playbooks.load(settings.process_dir / "playbooks" / f"{name}.md")


def planned(settings: Settings, store: Store, job: Job) -> list[Step]:
    """The job's steps; for a job that hasn't started, the steps it will get (id 0)."""
    steps = store.steps(job.id)
    if steps or job.state not in ACTIVE:  # a job that ended before office tracked steps has none
        return steps
    project = store.project(job.slug)
    specs = load(settings, job.playbook).for_mode(project.mode)
    steps = [Step(0, job.id, i, s.kind, s.name, s.max_turns or None) for i, s in enumerate(specs, start=1)]
    if job.phase or store.runs(job.id):
        names = LEGACY_PHASES.get(job.playbook, [])[job.phase :]
        resume = next((i for i, s in enumerate(steps) if s.kind == "agent" and s.name in names), 0)
        for s in steps[:resume]:
            s.state, s.summary = "done", LEGACY_DONE
    return steps


def current(steps: list[Step]) -> Step | None:
    return next((s for s in steps if s.state in OPEN), None)


def label(step: Step) -> str:
    return step.name if step.kind == "agent" else LABELS.get(step.kind, step.kind)


def where(steps: list[Step]) -> str:
    """The current step and how far along the job is: "mvp (9/24)", or "finished"."""
    step = current(steps)
    if step is None:
        return "finished"
    return f"{label(step)} ({steps.index(step) + 1}/{len(steps)})"


def upcoming(steps: list[Step], after: Step) -> str:
    """What happens after `after`, up to the next agent step: "PR, CI, merge, deploy, then mvp"."""
    ahead = [s for s in steps if s.position > after.position and s.state in OPEN]
    names = []
    for s in ahead:
        if s.kind == "agent":
            return ", ".join(names + [f"then {s.name}"]) if names else s.name
        if s.kind != "push":
            names.append(label(s))
    return ", ".join(names) or "nothing"


def last_said(steps: list[Step]) -> str:
    """The latest finished agent step's message: what the work so far amounts to, for Riggy."""
    for s in reversed(steps):
        if s.kind == "agent" and s.state == "done" and s.summary and s.summary != LEGACY_DONE:
            return s.summary
    return ""
