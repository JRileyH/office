"""Playbooks: markdown files listing a job's steps in front matter, with one `## <name>`
section of instructions per agent step.

    ---
    steps:
      - setup
      - {agent: spec, turns: 80}
      - {agent: review, turns: 150, modes: [standard, rigorous]}
      - push
      - pr
      - checks
      - merge
      - deploy
      - alert
    ---
    Text here is given to every agent step.

    ## spec
    What the spec step does.

An agent step is one headless Claude Code run that does a piece of work and commits it.
Every other step is office's own code (CODE_STEPS), so pushing, pull requests, CI,
merging, deploying and telling Riggy happen the same way every time. `modes` limits a
step to projects in those modes. The model and effort come from the project.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from .store import MODES

SECTION_RE = re.compile(r"^## +(\S+) *$", re.M)
CODE_STEPS = ("setup", "push", "pr", "checks", "merge", "push-main", "deploy", "checkin", "alert")
FIRST, LAST = ("setup",), ("deploy", "alert")  # every job starts and ends this way, in every mode


@dataclass(frozen=True)
class StepSpec:
    kind: str  # "agent", or one of CODE_STEPS
    name: str  # an agent step's section; for code steps, the kind
    max_turns: int = 0
    instructions: str = ""
    modes: tuple[str, ...] = ()  # empty: every mode


@dataclass(frozen=True)
class Playbook:
    name: str
    intro: str
    steps: tuple[StepSpec, ...]

    def for_mode(self, mode: str) -> list[StepSpec]:
        return [s for s in self.steps if not s.modes or mode in s.modes]

    def instructions(self, name: str) -> str:
        for step in self.steps:
            if step.kind == "agent" and step.name == name:
                return step.instructions
        raise KeyError(f"{self.name} has no agent step {name}")


def load(path: Path) -> Playbook:
    text = path.read_text()
    if not text.startswith("---\n"):
        raise ValueError(f"{path}: missing front matter")
    front, body = text[4:].split("\n---\n", 1)
    meta = yaml.safe_load(front)
    parts = SECTION_RE.split(body)
    intro, sections = parts[0].strip(), dict(zip(parts[1::2], (s.strip() for s in parts[2::2])))
    steps = [_step(path, entry, sections) for entry in meta["steps"]]
    extra = set(sections) - {s.name for s in steps if s.kind == "agent"}
    if extra:
        raise ValueError(f"{path}: sections without an agent step: {', '.join(sorted(extra))}")
    book = Playbook(path.stem, intro, tuple(steps))
    for mode in MODES:
        kinds = [s.kind for s in book.for_mode(mode)]
        if tuple(kinds[: len(FIRST)]) != FIRST or tuple(kinds[-len(LAST) :]) != LAST:
            raise ValueError(f"{path}: steps must start with {', '.join(FIRST)} and end with {', '.join(LAST)}")
    return book


def _step(path: Path, entry: str | dict, sections: dict[str, str]) -> StepSpec:
    if isinstance(entry, str):
        entry = {"do": entry}
    modes = tuple(entry.get("modes") or ())
    if set(modes) - set(MODES):
        raise ValueError(f"{path}: modes are {', '.join(MODES)}, not {', '.join(sorted(set(modes) - set(MODES)))}")
    if "agent" in entry:
        name = entry["agent"]
        if name not in sections:
            raise ValueError(f"{path}: agent step {name} has no '## {name}' section")
        return StepSpec("agent", name, int(entry["turns"]), sections[name], modes)
    kind = entry.get("do")
    if kind not in CODE_STEPS:
        raise ValueError(f"{path}: unknown step {kind!r}; office's steps are {', '.join(CODE_STEPS)}")
    return StepSpec(kind, kind, modes=modes)
