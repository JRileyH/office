"""Playbooks: markdown files with the phase list in front matter and one `## <phase>` section per phase.

    ---
    phases:
      - {name: spec, model: sonnet, max_turns: 60}
    ---
    Text here applies to every phase.

    ## spec
    What the spec phase does.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

SECTION_RE = re.compile(r"^## +(\S+) *$", re.M)


@dataclass(frozen=True)
class Phase:
    name: str
    model: str
    max_turns: int
    instructions: str


@dataclass(frozen=True)
class Playbook:
    name: str
    intro: str
    phases: tuple[Phase, ...]

    def overview(self, current: int) -> str:
        return "\n".join(
            f"{i + 1}. {p.name}{'  <- this phase' if i == current else ''}" for i, p in enumerate(self.phases)
        )


def load(path: Path) -> Playbook:
    text = path.read_text()
    if not text.startswith("---\n"):
        raise ValueError(f"{path}: missing front matter")
    front, body = text[4:].split("\n---\n", 1)
    meta = yaml.safe_load(front)
    parts = SECTION_RE.split(body)
    intro, sections = parts[0].strip(), dict(zip(parts[1::2], (s.strip() for s in parts[2::2])))
    phases = []
    for spec in meta["phases"]:
        name = spec["name"]
        if name not in sections:
            raise ValueError(f"{path}: phase {name} has no '## {name}' section")
        phases.append(Phase(name, spec["model"], int(spec["max_turns"]), sections[name]))
    extra = set(sections) - {p.name for p in phases}
    if extra:
        raise ValueError(f"{path}: sections without a phase: {', '.join(sorted(extra))}")
    return Playbook(path.stem, intro, tuple(phases))
