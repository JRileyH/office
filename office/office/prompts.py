"""Turn the templates in prompts/ into the text a worker gets."""

from __future__ import annotations

from pathlib import Path
from string import Template

from . import plan
from .config import Settings
from .playbook import Playbook
from .store import Job, Project, Step

# Office's own steps worth telling a worker about in "Done so far".
TOLD = {"merge", "push-main", "deploy", "checkin"}


class Prompts:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.dir = settings.process_dir / "prompts"

    def render(self, template: str, **values: object) -> str:
        text = (self.dir / f"{template}.md").read_text()
        return Template(text).safe_substitute({k: str(v) for k, v in values.items()})

    def step(
        self,
        project: Project,
        job: Job,
        book: Playbook,
        step: Step,
        steps: list[Step],
        branch: str,
        feedback: list[str],
        lessons: str,
    ) -> str:
        s = self.settings
        if step.name == "fix" and step.problem:
            instructions = self.render("fix", problem=step.problem)
        else:
            instructions = book.instructions(step.name)
        earlier = ""
        if step.error:
            earlier = (
                "# An earlier attempt\n\nThis step was started before and didn't finish: "
                f"{step.error}\nWhatever it committed is on the branch; carry on from there."
            )
        return self.render(
            "step",
            name=project.name,
            slug=project.slug,
            summary=project.summary,
            mode=project.mode,
            url=s.project_url(project.slug),
            repo=s.repo_url(project.slug),
            branch=branch,
            kind="new project" if job.kind == "new" else "change to an existing project",
            request=job.request,
            issue=f"#{job.issue} ({s.repo_url(project.slug)}/issues/{job.issue})" if job.issue else "none",
            intro=book.intro,
            done=format_done(steps),
            step=step.name,
            instructions=instructions,
            earlier=earlier,
            feedback=format_feedback(feedback),
            lessons=lessons.strip() or "(none yet)",
            lessons_path=s.state_dir / "lessons.md",
            proposals_dir=s.state_dir / "proposals",
            skills=s.process_dir / "skills",
        )

    def resume(self, feedback: list[str]) -> str:
        return self.render("resume", feedback=format_feedback(feedback))

    def wrapup(self) -> str:
        return self.render("wrapup")

    def intake_path(self) -> Path:
        return self.dir / "intake.md"


def format_done(steps: list[Step]) -> str:
    lines = [
        f"- {plan.label(s)}: {s.summary}"
        for s in steps
        if s.state == "done" and s.summary and (s.kind == "agent" or s.kind in TOLD)
    ]
    return "\n".join(lines) or "(nothing yet: this is the first step)"


def format_feedback(notes: list[str]) -> str:
    if not notes:
        return "(none)"
    return "\n\n".join("\n".join(f"> {line}".rstrip() for line in note.splitlines()) for note in notes)
