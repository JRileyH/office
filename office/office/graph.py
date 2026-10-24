"""A job drawn as a line through its steps, like a git graph, for the status page.

Two tracks: the upper one is work on the job's branch (agent steps, pushing, the PR and
its CI), the lower one is main, which is what's live (setting up, merging, deploying,
Riggy's check-in and the end). The line runs solid in the project's color as far as the
job has got and dashed after; it bends down at each merge and back up for the next
piece of work. Every step is a link to its details.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from html import escape

from . import plan
from .store import Step

TOP, BOTTOM, HEIGHT = 58, 112, 162  # the tracks' y, and the drawing's height
MAJOR, MINOR, TURN = 72, 42, 18  # space between big steps, small ones, and extra for a bend
SMALL = {"push", "pr", "checks", "merge", "push-main"}  # drawn as small dots
LOWER = {"setup", "merge", "push-main", "deploy", "checkin", "alert"}  # on main
NAMES = {"checkin": "you try it", "alert": "finished", "setup": "set up"}
GLYPHS = {"done": "✓", "failed": "✕", "waiting": "?", "skipped": "–", "running": ""}


@dataclass
class Node:
    step: Step
    x: float
    y: float
    href: str
    meta: str  # under the name: how long it took, how long so far, or when it should start
    at: str | None  # for a start time: the ISO time, shown in the viewer's clock by the page's script


Meta = Callable[[Step], tuple[str, str | None]]


def layout(steps: list[Step], href: Callable[[Step], str], meta: Meta) -> list[Node]:
    nodes: list[Node] = []
    x = 36.0
    for s in steps:
        y = BOTTOM if s.kind in LOWER else TOP
        if nodes:
            prev = nodes[-1]
            small = (s.kind in SMALL) + (prev.step.kind in SMALL)
            gap = (MAJOR, (MAJOR + MINOR) / 2, MINOR)[small]
            gap = max(gap, 3.4 * (len(_name(prev.step)) + len(_name(s))) + 8)  # room for both names
            x += gap + (TURN if y != prev.y else 0)
        nodes.append(Node(s, x, y, href(s), *meta(s)))
    return nodes


def draw(steps: list[Step], href: Callable[[Step], str], meta: Meta, label: str) -> str:
    """The SVG for a job's steps. `href` and `meta` give each step's link and the words under it."""
    nodes = layout(steps, href, meta)
    if not nodes:
        return ""
    width = nodes[-1].x + 48
    lines, marks = [], []
    for a, b in zip(nodes, nodes[1:]):
        reached = b.step.state != "pending"
        if a.y == b.y:
            d = f"M{a.x:.0f} {a.y} H{b.x:.0f}"
        else:
            mid = (a.x + b.x) / 2
            d = f"M{a.x:.0f} {a.y} C{mid:.0f} {a.y} {mid:.0f} {b.y} {b.x:.0f} {b.y}"
        lines.append(f"<path class='seg{' reached' if reached else ''}' d='{d}'/>")
    for n in nodes:
        marks.append(_node(n))
    return (
        f"<svg class=graph viewBox='0 0 {width:.0f} {HEIGHT}' width='{width:.0f}' height='{HEIGHT}' role='img'"
        f" aria-label='{escape(label)}'>{''.join(lines)}{''.join(marks)}</svg>"
    )


def _node(n: Node) -> str:
    s = n.step
    small = s.kind in SMALL
    name = _name(s)
    now = " now" if s.state in ("running", "waiting") else ""
    classes = f"node {s.state}{now} k-{s.kind}{' small' if small else ''}{' fix' if s.name == 'fix' and s.kind == 'agent' else ''}"
    tip = f"{plan.label(s)}: {_state_words(s)}" + (f" · {n.meta}" if n.meta else "")
    hit = f"<rect class=hit x='{n.x - 22:.0f}' y='{n.y - 44 if n.y == TOP else n.y - 16}' width='44' height='62' rx='8'/>"
    if small:
        shape = f"<circle class=dot cx='{n.x:.0f}' cy='{n.y}' r='5'/>"
        ty = n.y - 13 if n.y == TOP else n.y + 22
        text = f"<text class=name x='{n.x:.0f}' y='{ty}'>{escape(name)}</text>"
    else:
        if s.kind in ("deploy", "setup"):
            shape = f"<rect class=dot x='{n.x - 9:.0f}' y='{n.y - 9}' width='18' height='18' rx='5'/>"
        elif s.kind == "alert":
            shape = f"<circle class=ring cx='{n.x:.0f}' cy='{n.y}' r='13'/><circle class=dot cx='{n.x:.0f}' cy='{n.y}' r='9'/>"
        else:
            shape = f"<circle class=dot cx='{n.x:.0f}' cy='{n.y}' r='10'/>"
        if s.state == "running":
            shape = f"<circle class=pulse cx='{n.x:.0f}' cy='{n.y}' r='10'/>" + shape
        glyph = GLYPHS.get(s.state, "")
        if glyph:
            shape += f"<text class=glyph x='{n.x:.0f}' y='{n.y + 4}'>{glyph}</text>"
        name_y, meta_y = (n.y - 34, n.y - 20) if n.y == TOP else (n.y + 28, n.y + 42)
        text = f"<text class=name x='{n.x:.0f}' y='{name_y}'>{escape(name)}</text>"
        if n.meta:
            at = f" data-at='{escape(n.at)}'" if n.at else ""
            text += f"<text class=meta x='{n.x:.0f}' y='{meta_y}'{at}>{escape(n.meta)}</text>"
    return (
        f"<a href='{escape(n.href)}' class='{classes}' data-sheet aria-label='{escape(tip)}'>"
        f"<title>{escape(tip)}</title>{hit}{shape}{text}</a>"
    )


def _name(s: Step) -> str:
    return s.name if s.kind == "agent" else NAMES.get(s.kind, plan.label(s))


def _state_words(s: Step) -> str:
    return {"pending": "to come", "running": "now", "waiting": "waiting for you"}.get(s.state, s.state)
