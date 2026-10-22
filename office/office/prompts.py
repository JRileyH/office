"""Turn the templates in prompts/ into the text a worker gets."""

from __future__ import annotations

from pathlib import Path
from string import Template

from .config import Settings
from .playbook import Playbook
from .store import Job, Project


class Prompts:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.dir = settings.process_dir / "prompts"

    def render(self, template: str, **values: object) -> str:
        text = (self.dir / f"{template}.md").read_text()
        return Template(text).safe_substitute({k: str(v) for k, v in values.items()})

    def phase(self, project: Project, job: Job, playbook: Playbook, feedback: list[str], lessons: str) -> str:
        phase = playbook.phases[job.phase]
        s = self.settings
        return self.render(
            "phase",
            name=project.name,
            slug=project.slug,
            summary=project.summary,
            mode=project.mode,
            url=s.project_url(project.slug),
            repo=s.repo_url(project.slug),
            org=s.org,
            kind="new project" if job.kind == "new" else "change to an existing project",
            request=job.request,
            issue=f"#{job.issue} ({s.repo_url(project.slug)}/issues/{job.issue})" if job.issue else "none",
            playbook=playbook.name,
            playbook_intro=playbook.intro,
            phases=playbook.overview(job.phase),
            phase=phase.name,
            phase_number=job.phase + 1,
            phase_count=len(playbook.phases),
            last_phase="yes" if job.phase == len(playbook.phases) - 1 else "no",
            instructions=phase.instructions,
            feedback=format_feedback(feedback),
            lessons=lessons.strip() or "(none yet)",
            lessons_path=s.state_dir / "lessons.md",
            skills=s.process_dir / "skills",
        )

    def resume(self, feedback: list[str]) -> str:
        return self.render("resume", feedback=format_feedback(feedback))

    def wrapup(self) -> str:
        return self.render("wrapup")

    def intake_path(self) -> Path:
        return self.dir / "intake.md"


def format_feedback(notes: list[str]) -> str:
    if not notes:
        return "(none)"
    return "\n\n".join("\n".join(f"> {line}".rstrip() for line in note.splitlines()) for note in notes)
