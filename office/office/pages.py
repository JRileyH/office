"""The status page's HTML, phone first.

The overview says in words what happens next and when ("Coming up"), then has a lane per
project: its job drawn as a line through its steps (graph.py), a sentence on where it is
and what's next, and what the running step is doing right now. Each step opens a sheet:
what it did (with its runs and transcript), what it's doing (a live tail), or, before it
starts, when it should and what it will run on, with the changes allowed (control.py).
The insights page shows where the time and Claude usage go, and sets the defaults.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from html import escape
from pathlib import Path

from . import graph, plan
from .control import SKIPPED
from .insights import Eta, Report, Tally, estimate
from .live import Line, view
from .store import ACTIVE, EFFORTS, MODELS, OPEN, Job, Project, Step
from .watch import span

SHIP = {"push", "pr", "checks", "merge", "push-main", "deploy"}  # shown together as one "ship" row
ICONS = {"done": "✓", "skipped": "–", "running": "▶", "waiting": "?", "failed": "✗", "pending": ""}
AGENDA_SHOWN = 7
# What office's own steps do, for their sheets.
DOES = {
    "setup": "Makes sure the repo, the checkout and the job's issue exist.",
    "push": "Commits anything left and pushes the job's branch to GitHub.",
    "pr": "Opens a pull request for the work since the last merge.",
    "checks": "Waits for CI to pass on the pull request. If it fails, a fix step goes in and the shipping steps run again.",
    "merge": "Squash-merges the pull request into main.",
    "push-main": "Pushes the work straight to main (reckless mode: no pull request).",
    "deploy": "Waits for CI on main, then deploys it. Done only once the site answers; if the deployer refuses it, a fix step goes in.",
    "checkin": "Shows you the live site and waits for your reply. Skipping it means “looks good, carry on”.",
    "alert": "Tells you it's live. Always the last step, after the last deploy.",
}


@dataclass
class LaneData:
    """Everything one project's lane shows, gathered by the web app."""

    project: Project
    job: Job | None  # the active job, or the latest finished one
    steps: list[Step]
    state: str  # a few words: "working", "waiting for CI", "waiting for you"…
    live: list[Line]  # the running agent step's latest lines
    notes: list[str]  # the worker's progress notes, latest last
    skippable: set[int]
    restorable: set[int]
    position: int | None  # place in the queue, 1 = next to run
    site: str
    repo: str
    color: int = 1  # the project's color, 1-8, by when it was made
    etas: dict[int, Eta] = field(default_factory=dict)  # by step position
    tallies: dict[int, Tally] = field(default_factory=dict)  # each agent step's runs, by step id
    known: dict[str, float] = field(default_factory=dict)  # seconds each kind of step usually takes


@dataclass
class SheetData:
    """One step, for its sheet."""

    lane: LaneData
    step: Step
    href: str
    engine: plan.Engine | None  # agent steps
    inherited: plan.Engine | None  # what it would run on without its own choice
    does: str  # what the step does, in a sentence or two
    runs: list[dict]  # its runs, oldest first
    transcripts: dict[int, list[Line]]  # by run id, for finished runs
    notes: list[str]  # context Riggy gave this step
    configurable: bool


# --- the page -------------------------------------------------------------------------


def page(title: str, body: str, signed_in: bool, paused: bool, flash: str = "", back: str = "/") -> str:
    auth = (
        f"<form method=post action=/logout class=inline><input type=hidden name=back value='{escape(back)}'>"
        "<button class=quiet>Sign out</button></form>"
        if signed_in
        else "<details class=signin><summary class=quiet>Sign in</summary>"
        f"<form method=post action=/login><input type=hidden name=back value='{escape(back)}'>"
        "<input type=password name=password placeholder=Password autocomplete=current-password required>"
        "<button>Sign in</button></form></details>"
    )
    pause = ""
    if signed_in:
        pause = action_form(
            "resume-all" if paused else "pause-all", "Resume all" if paused else "Pause all", back=back
        )
    banner = "<div class='banner paused'>Work is paused. Nothing new starts until it's resumed.</div>" if paused else ""
    flash_html = f"<div class=flash role=status>{escape(flash)}</div>" if flash else ""
    return f"""<!doctype html><html lang=en><meta charset=utf-8>
<meta name=viewport content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name=theme-color content="#0f172a">
<title>{escape(title)}</title><style>{STYLE}</style>
<header class=top><a href=/ class=brand>office</a><nav><a href=/insights class=navlink>Insights</a>{pause}{auth}</nav></header>
{banner}{flash_html}<main id=lanes>{body}</main>
<dialog id=sheet aria-label='Step'><button class='quiet close' aria-label=Close>✕</button><div class=sheet-body></div></dialog>
<script>{SCRIPT}</script></html>"""


def overview(lanes: list[LaneData], idle: list[LaneData], signed_in: bool, now: datetime) -> str:
    active = "".join(lane_html(lane, signed_in, now) for lane in lanes)
    if not active:
        active = "<p class=empty>Nothing under way. Dictate an idea in the office inbox.</p>"
    idle_html = ""
    if idle:
        idle_html = "<section class=idle><h2>Other projects</h2><div class=grid>" + "".join(
            idle_card(lane, signed_in, now) for lane in idle
        ) + "</div></section>"
    return agenda(lanes, now) + active + (LEGEND if lanes else "") + idle_html


def agenda(lanes: list[LaneData], now: datetime) -> str:
    """What happens next and when, in words, across projects: the queue as a timeline."""
    needs = [lane for lane in lanes if lane.job.state == "waiting"]
    items: list[tuple[datetime, LaneData, str, str]] = []  # (when, lane, what, detail)
    for lane in sorted((lane for lane in lanes if lane.position), key=lambda lane: lane.position):
        for s in lane.steps:
            eta = lane.etas.get(s.position)
            if eta is None:
                continue
            if s.started_at and s.state in OPEN:
                items.append((now, lane, _doing(lane, s, now), ""))
            elif s.kind == "agent":
                items.append((eta.start, lane, s.name, ""))
            elif s.kind == "deploy":
                items.append((eta.end, lane, "goes live", ", ".join(_agents_before(lane.steps, s)) or "the fix"))
            elif s.kind == "checkin":
                items.append((eta.start, lane, "your turn: try it", ""))
            elif s.kind == "alert":
                items.append((eta.end, lane, "finished", ""))
    html = ""
    if needs:
        rows = "".join(
            f"<li class='c{lane.color}'><a href='#p-{escape(lane.project.slug)}'><span class=swatch></span>"
            f"<b>{escape(lane.project.name)}</b> {escape(_waiting_words(lane))}</a></li>"
            for lane in needs
        )
        html += f"<section class=needs><h2>Needs you</h2><ol>{rows}</ol></section>"
    if items:
        rows = [
            f"<li class='c{lane.color}'><span class=when>{'now' if at <= now else when(at, now)}</span>"
            f"<a href='#p-{escape(lane.project.slug)}'><span class=swatch></span><b>{escape(lane.project.name)}</b> "
            f"{escape(what)}{f' <span class=muted>({escape(detail)})</span>' if detail else ''}</a></li>"
            for at, lane, what, detail in items
        ]
        shown, rest = rows[:AGENDA_SHOWN], rows[AGENDA_SHOWN:]
        more = f"<details data-key=agenda-more><summary>{len(rest)} more</summary><ol>{''.join(rest)}</ol></details>" if rest else ""
        html += (
            f"<section class=agenda><h2>Coming up</h2><ol>{''.join(shown)}</ol>{more}"
            "<p class=hint>Times are estimates from how long steps like these have taken.</p></section>"
        )
    return html


# --- a lane ---------------------------------------------------------------------------


def lane_html(lane: LaneData, signed_in: bool, now: datetime, full: bool = False) -> str:
    p, job, steps = lane.project, lane.job, lane.steps
    tone = _tone(lane)
    what = "New project" if job.kind == "new" else "Change"
    paused = "<span class='badge paused'>Paused</span>" if p.paused else ""
    count = sum(1 for s in steps if s.state in ("done", "skipped"))
    progress = f"{count} of {len(steps)} steps" if steps else "Not started"
    place = f"<span class=place>#{lane.position} in the queue</span>" if lane.position and lane.position > 1 else ""
    spent = _spent(lane)
    return f"""<article class="lane {tone} c{lane.color}" id="p-{escape(p.slug)}">
<header class=lane-head><div><h2><span class=swatch></span><a href='/jobs/{job.id}'>{escape(p.name)}</a> {paused}</h2>
<p class=sub>{what} · {escape(p.mode)} · {escape(_engine_words(p))} · job #{job.id}</p></div>
<div class=out>{_out_links(lane)}</div></header>
<p class=request>“{escape(_short(job.request, 220))}”</p>
<p class=progress><span class="badge {tone}">{escape(lane.state)}</span> {progress}{place}</p>
{track(lane, now)}
<p class=story>{story(lane, now)}</p>
{f"<p class=spent>{spent}</p>" if spent else ""}
{now_card(lane, signed_in, now) if plan.current(steps) and _show_now(lane) else ""}
{steps_lists(lane, signed_in, now) if full else ""}
{controls(lane) if signed_in else ""}
</article>"""


def track(lane: LaneData, now: datetime) -> str:
    """The job's steps as a line (graph.py), scrolled to where it is by the page's script."""
    if not lane.steps:
        return ""

    def meta(s: Step) -> tuple[str, str | None]:
        if s.state in ("done", "failed"):
            return span(timedelta(seconds=_took(lane, s))), None
        if s.state == "skipped":
            return "skipped", None
        if s.state == "waiting":
            return "your turn", None
        if s.started_at:
            return f"{span(now - _dt(s.started_at))} so far", None
        eta = lane.etas.get(s.position)
        if eta:
            return f"in {span(eta.start - now)}", eta.start.isoformat()
        return "", None

    svg = graph.draw(lane.steps, lambda s: step_href(lane.job, s), meta, f"{lane.project.name}: {plan.where(lane.steps)}")
    return f"<div class=map data-key=map-{lane.job.id}>{svg}</div>"


def story(lane: LaneData, now: datetime) -> str:
    """Where the job is and what happens next, in a sentence or three."""
    steps, job = lane.steps, lane.job
    cur = plan.current(steps)
    if cur is None:
        return {"done": "Finished.", "killed": "Stopped.", "failed": "Failed."}.get(job.state, "Finished.")
    if job.state == "waiting":
        first = "Waiting for you to try it." if cur.kind == "checkin" else "Waiting for you: see its room."
    elif lane.project.paused:
        first = "Paused."
    elif cur.started_at:
        first = f"Now: {escape(_doing(lane, cur, now))}."
    elif cur.position in lane.etas and lane.position:
        first = f"Starts {when(lane.etas[cur.position].start, now)} with {escape(plan.label(cur))}."
    else:
        first = f"Next: {escape(plan.label(cur))}."
    parts, agents, chapters = [first], [], 0
    for s in steps:
        if s.position <= cur.position or s.state not in OPEN:
            continue
        if s.kind == "agent":
            agents.append(s.name)
            continue
        eta = lane.etas.get(s.position)
        at = f" {when(eta.end if s.kind != 'checkin' else eta.start, now)}" if eta else ""
        work = f"Then {escape(_and(agents))}, " if agents else ""
        if s.kind == "deploy":
            parts.append(f"{work}going live{at}." if agents else f"Goes live{at}.")
        elif s.kind == "checkin":
            parts.append(f"{work}then you try it{at}." if agents else f"Then you try it{at}.")
        elif s.kind == "alert":
            parts.append(f"{work}finished{at}." if agents else f"Finished{at}.")
        else:
            continue
        agents, chapters = [], chapters + 1
        if chapters == 2:
            left = sum(1 for x in steps if x.position > s.position and x.state in OPEN and x.kind == "agent")
            if left:
                parts.append(f"{left} more agent step{'s' if left != 1 else ''} after that.")
            break
    return " ".join(parts)


def now_card(lane: LaneData, signed_in: bool, now: datetime) -> str:
    step = plan.current(lane.steps)
    job = lane.job
    since = f" for {span(now - _dt(step.started_at))}" if step.started_at else ""
    head = (
        f"<div class=now-head><a class=label href='{escape(step_href(job, step))}' data-sheet>{escape(plan.label(step))}</a>"
        f"<span class=muted>{since}</span></div>"
    )
    body = ""
    if step.kind == "checkin" and step.state == "waiting":
        said = plan.last_said(lane.steps)
        body = (
            f"<p>{escape(said)}</p><p><a class=button href='{escape(lane.site)}'>Try it ↗</a></p>"
            + (
                "<p class=muted>Reply in its Matrix room, or here:</p>"
                + reply_form(job, "Looks good, or what to change…")
                + skip_button(job, step, "Looks good, carry on")
                if signed_in
                else "<p class=muted>Reply in its Matrix room.</p>"
            )
        )
    elif job.state == "waiting":
        body = f"<p>{escape(step.summary or 'It needs your help: see its Matrix room.')}</p>"
        if step.error:
            body += f"<p class=muted>{escape(_short(step.error, 300))}</p>"
        if signed_in:
            body += reply_form(job, "Your answer or a hint…")
    elif step.kind == "agent" and step.started_at:
        said = next((line.text for line in reversed(lane.live) if line.kind == "says"), "")
        body = f"<p class=said>{escape(said)}</p>" if said else ""
        if step.error:
            body += f"<p class=muted>Trying again: {escape(_short(step.error, 200))}</p>"
        if lane.notes:
            body += "<ul class=notes>" + "".join(f"<li>{escape(n)}</li>" for n in lane.notes[-4:]) + "</ul>"
        body += tail(lane.live, signed_in, f"tail-{job.id}")
    else:
        body = f"<p>{escape(step.summary or _doing(lane, step, now))}</p>" + _links(step.links)
        if step.error:
            body += f"<p class=muted>Last try: {escape(_short(step.error, 200))}</p>"
    return f"<section class='now {_tone(lane)}'>{head}{body}</section>"


def tail(lines: list[Line], signed_in: bool, key: str, limit: int = 14, title: str = "Live") -> str:
    shown = view(lines, signed_in, limit)
    if not shown:
        return ""
    rows = "".join(
        f"<div class='t {line.kind}{' nested' if line.nested else ''}'><time>{escape(line.at)}</time>"
        f"<span>{_mark(line)} {escape(line.text)}</span></div>"
        for line in shown
    )
    return f"<details class=tail data-key={key} open><summary>{escape(title)}</summary><div class=log>{rows}</div></details>"


def controls(lane: LaneData) -> str:
    p, job = lane.project, lane.job
    if job.state not in ACTIVE:
        return f"<section class=controls>{_pause(p)}</section>"
    back = f"/#p-{p.slug}"
    selects = "".join(
        select_form(name, getattr(p, name), values, "the defaults", action="setting", slug=p.slug, back=back)
        for name, values in (("model", MODELS), ("effort", EFFORTS))
    )
    first = "" if lane.position == 1 else action_form("move", "Run next", job=job.id, value="first", back=back)
    return (
        "<section class=controls>"
        + first
        + action_form("move", "Run last", job=job.id, value="last", back=back)
        + _pause(p)
        + action_form("kill", "Stop job", job=job.id, back=back, confirm=f"Stop job {job.id} on {p.name} for good?", danger=True)
        + f"<span class=engine-pick>All steps: {selects}</span>"
        + "<details class=note-form><summary class=quiet>Note for the next step</summary>"
        + reply_form(job, "Feedback the next agent step gets…")
        + "</details></section>"
    )


def idle_card(lane: LaneData, signed_in: bool, now: datetime) -> str:
    p, job = lane.project, lane.job
    last = "No jobs yet."
    if job:
        ago = f"{span(now - _dt(job.updated_at))} ago" if job.updated_at else ""
        word = {"done": "Done", "killed": "Stopped", "failed": "Failed"}.get(job.state, job.state)
        last = f"<a href='/jobs/{job.id}'>{word} {ago}</a>: {escape(_short(job.request, 90))}"
    paused = "<span class='badge paused'>Paused</span>" if p.paused else ""
    return (
        f"<article class='card c{lane.color}' id='p-{escape(p.slug)}'><h3><span class=swatch></span>{escape(p.name)} {paused}</h3>"
        f"<p class=sub>{escape(_short(p.summary, 120))}</p><p class=sub>{last}</p>"
        f"<div class=out>{_out_links(lane)}</div>"
        + (f"<div class=controls>{_pause(p)}</div>" if signed_in else "")
        + "</article>"
    )


# --- a step's sheet -------------------------------------------------------------------


def sheet(d: SheetData, signed_in: bool, now: datetime) -> str:
    lane, s, job = d.lane, d.step, d.lane.job
    words = {"pending": "to come", "running": "now", "waiting": "waiting for you"}.get(s.state, s.state)
    kind = "Agent step" if s.kind == "agent" else "Office's own step"
    head = (
        f"<header class='sheet-head c{lane.color}'><p class=sub><span class=swatch></span>{escape(lane.project.name)} · job #{job.id} · "
        f"step {s.position} of {len(lane.steps)}</p><h2>{escape(plan.label(s))} <span class='badge {_state_tone(s)}'>"
        f"{ICONS.get(s.state, '')} {escape(words)}</span></h2><p class=muted>{kind}{' · optional' if s.optional else ''}</p></header>"
    )
    body = f"<p>{escape(d.does)}</p>" if d.does else ""
    if s.name == "fix" and s.problem and signed_in:
        body += f"<details><summary>The problem it was given</summary><pre class=problem>{escape(_short(s.problem, 3000))}</pre></details>"
    facts = []
    if s.state in OPEN and not s.started_at:
        eta = lane.etas.get(s.position)
        usual = estimate(lane.known, s)
        if eta:
            facts.append(("Starts", f"{when(eta.start, now)}, if the queue goes as planned"))
        facts.append(("Usually takes", span(timedelta(seconds=usual)) if usual >= 1 else "a moment"))
    if s.started_at:
        facts.append(("Started", when(_dt(s.started_at), now)))
    if s.ended_at:
        facts.append(("Took", span(timedelta(seconds=_took(lane, s)))))
    elif s.started_at:
        facts.append(("So far", span(now - _dt(s.started_at))))
    if d.engine:
        facts.append(("Runs on", f"{d.engine} <span class=muted>(model from {escape(d.engine.model_from)}, effort from {escape(d.engine.effort_from)})</span>"))
    tally = lane.tallies.get(s.id)
    if tally and tally.runs:
        facts.append(("Usage", f"{_money(tally.cost)} · {_count(tally.tokens)} tokens · {tally.runs} run{'s' if tally.runs != 1 else ''}"))
    if facts:
        body += "<dl class=facts>" + "".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in facts) + "</dl>"
    if s.summary and s.state != "pending":
        body += f"<p class=summary>{escape(s.summary)}</p>"
    if s.error:
        body += f"<p class=muted>Last try: {escape(_short(s.error, 400))}</p>"
    body += _links(s.links)
    if d.notes:
        body += "<h3>Your context for it</h3><ul class=notes>" + "".join(f"<li>{escape(n)}</li>" for n in d.notes) + "</ul>"
    if s.state in ("running", "waiting") and s.kind == "agent":
        body += f"<div data-live>{tail(lane.live, signed_in, f'sheet-tail-{s.id}', limit=30)}</div>"
    for run in d.runs:
        lines = d.transcripts.get(run["id"])
        if lines:
            title = f"{run['phase']} on {run['model']}: {_outcome(run)}, {span(timedelta(seconds=_seconds(run)))}"
            body += tail(lines, signed_in, f"run-{run['id']}", limit=400, title=title).replace(" open>", ">", 1)
    if signed_in:
        body += sheet_controls(d)
    elif s.state == "pending":
        body += "<p class=hint>Sign in to change this step.</p>"
    return head + body


def sheet_controls(d: SheetData) -> str:
    lane, s, job = d.lane, d.step, d.lane.job
    back = f"/#{d.href}"
    ids = {"job": job.id, "step": s.id, "value": ""}
    if not s.id:
        ids["position"] = s.position
    out = []
    if d.configurable and d.inherited:
        pick = "".join(
            select_form(name, getattr(s, name) or "", values, f"default: {getattr(d.inherited, name)}",
                        action="step-setting", back=back, **ids)  # fmt: skip
            for name, values in (("model", MODELS), ("effort", EFFORTS))
        )
        out.append(f"<div class=row-form><span>This step only:</span>{pick}</div>")
        out.append(
            "<form method=post action=/act class=reply><input type=hidden name=action value=note>"
            + _hidden(job=job.id, step=s.id or "", position="" if s.id else s.position, back=back)
            + "<textarea name=value rows=2 placeholder='Context for this step only…' required></textarea><button>Add</button></form>"
        )
    if s.id in lane.skippable:
        out.append(skip_button(job, s, back=back))
    if s.id in lane.restorable:
        out.append(action_form("restore", "Undo skip", job=job.id, step=s.id, back=back))
    if job.state in ACTIVE and s.state in OPEN and lane.position != 1:
        out.append(action_form("move", f"Run {lane.project.name} next", job=job.id, value="first", back=back))
    if not out:
        return ""
    return "<section class='controls sheet-controls'>" + "".join(out) + "</section>"


# --- the job page's step lists --------------------------------------------------------


def steps_lists(lane: LaneData, signed_in: bool, now: datetime) -> str:
    current = plan.current(lane.steps)
    ahead = [s for s in lane.steps if s.state == "pending" and s is not current]
    ahead += [s for s in lane.steps if s.id in lane.restorable]
    done = [
        s for s in lane.steps
        if s.state in ("done", "failed") or (s.state == "skipped" and s.id not in lane.restorable)
        if current is None or s.position < current.position
    ]  # fmt: skip
    html = ""
    if ahead:
        rows = "".join(_row(g, lane, signed_in) for g in _groups(sorted(ahead, key=lambda s: s.position)))
        html += f"<section class=next><h3>Up next</h3><ol class=steps>{rows}</ol></section>"
    if done:
        rows = "".join(_row(g, lane, signed_in, now) for g in _groups(done))
        html += f"<details class=done data-key=done-{lane.job.id} open><summary>{len(done)} steps done</summary><ol class=steps>{rows}</ol></details>"
    return html


def _row(group: list[Step], lane: LaneData, signed_in: bool, now: datetime | None = None) -> str:
    first = group[0]
    if first.kind in SHIP:  # pushing, PR, CI, merge, deploy as one row
        chips = "".join(
            f"<a class='chip {s.state}' href='{escape(step_href(lane.job, s))}' data-sheet>{ICONS.get(s.state, '')} {escape(plan.label(s))}</a>"
            for s in group
        )
        links = "".join(_links(s.links) for s in group if s.state in ("done", "failed"))
        took = _took_row(group[0], group[-1]) if now else ""
        icon = ICONS.get("done" if all(s.state in ("done", "skipped") for s in group) else "pending", "")
        return (
            f"<li class='row ship {group[-1].state}'><div class=what><span class=icon>{icon}</span><b>ship</b>"
            f"<span class=chips>{chips}</span></div>{links}{took}</li>"
        )
    s = first
    tag = "<span class=opt>optional</span>" if s.optional and s.state == "pending" else ""
    button = ""
    if signed_in and s.id in lane.skippable:
        button = skip_button(lane.job, s)
    elif signed_in and s.id in lane.restorable:
        button = action_form("restore", "Undo skip", job=lane.job.id, step=s.id, back=f"/#p-{lane.job.slug}")
    summary = f"<p>{escape(_short(s.summary, 300))}</p>" if s.state != "pending" and s.summary else ""
    tally = lane.tallies.get(s.id)
    usage = f"<span class=took> · {_money(tally.cost)} · {_count(tally.tokens)} tokens</span>" if tally and tally.runs else ""
    return (
        f"<li class='row {s.state}{' code' if s.kind != 'agent' else ''}'><div class=what><span class=icon>{ICONS.get(s.state, '')}</span>"
        f"<a href='{escape(step_href(lane.job, s))}' data-sheet><b>{escape(plan.label(s))}</b></a>{tag}{button}</div>"
        f"{summary}{_links(s.links)}{_took_row(s, s) if now else ''}{usage}</li>"
    )


def _groups(steps: list[Step]) -> list[list[Step]]:
    groups: list[list[Step]] = []
    for s in steps:
        if s.kind in SHIP and groups and groups[-1][0].kind in SHIP and groups[-1][-1].position == s.position - 1:
            groups[-1].append(s)
        else:
            groups.append([s])
    return groups


# --- insights -------------------------------------------------------------------------


def insights(r: Report, found: list[str], kinds: list[str], defaults: dict[str, tuple[str, str]],
             fallback: tuple[str, str], days: int | None, signed_in: bool) -> str:  # fmt: skip
    """Where the time and usage go, the hot spots, and each kind of step's default model and effort."""
    windows = "".join(
        f"<a href='/insights{f'?days={n}' if n else '?days=all'}'{' class=on' if n == days else ''}>{label}</a>"
        for n, label in ((7, "7 days"), (30, "30 days"), (None, "All time"))
    )
    t = r.total
    tiles = (
        f"<div class=tiles><div class=tile><span>Worker time</span><b>{span(timedelta(seconds=t.seconds))}</b></div>"
        f"<div class=tile><span>Usage</span><b>{_money(t.cost)}</b></div>"
        f"<div class=tile><span>Tokens</span><b>{_count(t.tokens)}</b></div>"
        f"<div class=tile><span>Agent runs</span><b>{t.runs}</b></div></div>"
    )
    if not t.seconds and not t.runs:
        return f"<section class=insights><h1>Insights</h1><nav class=windows>{windows}</nav><p class=empty>Nothing has run in this window yet.</p></section>{defaults_table(kinds, defaults, fallback, r, signed_in)}"
    found_html = "<ul class=findings>" + "".join(f"<li>{escape(f)}</li>" for f in found) + "</ul>" if found else ""
    time_rows = [(k.key, k.seconds, span(timedelta(seconds=k.seconds)), _per(k)) for k in r.kinds if k.seconds]
    cost_rows = [(k.key, k.cost, _money(k.cost), _per_cost(k)) for k in sorted(r.kinds, key=lambda k: -k.cost) if k.cost]
    project_rows = [(k.key, k.seconds, span(timedelta(seconds=k.seconds)), _money(k.cost)) for k in r.projects]
    model_rows = [(k.key, k.cost, _money(k.cost), f"{_n(k.runs, 'run')} · {span(timedelta(seconds=k.seconds))}") for k in r.models]
    hot = ""
    if r.slowest:
        hot += "<h3>Slowest steps</h3><ol class=hot>" + "".join(
            f"<li><a href='/steps/{s.id}'>{escape(slug)} · {escape(plan.label(s))}</a> <span>{span(timedelta(seconds=sec))}</span></li>"
            for s, slug, sec in r.slowest
        ) + "</ol>"
    if r.costliest:
        hot += "<h3>Most usage</h3><ol class=hot>" + "".join(
            f"<li><a href='/steps/{s.id}'>{escape(slug)} · {escape(plan.label(s))}</a> <span>{_money(cost)}</span></li>"
            for s, slug, cost in r.costliest
        ) + "</ol>"
    waiting = ""
    if r.waiting.steps:
        waiting = (
            f"<p class=hint>Not counted above: {span(timedelta(seconds=r.waiting.seconds))} waiting for you at "
            f"{r.waiting.steps} check-in{'s' if r.waiting.steps != 1 else ''}.</p>"
        )
    return (
        f"<section class=insights><h1>Insights</h1><nav class=windows>{windows}</nav>{tiles}{found_html}"
        + bars("Where the time goes", "time", time_rows, t.seconds, "Wall-clock time by kind of step: agent runs, retries included, and office's own steps such as waiting for CI.")
        + bars("Where the usage goes", "usage", cost_rows, t.cost, "Claude usage at API prices: the fair weight for how much of your allowance it took. Cached reading counts for little, haiku for less than opus.")
        + waiting
        + (f"<div class=two><div>{hot}</div><div>" if hot else "<div class=two><div>")
        + bars("By project", "time", project_rows, t.seconds, "", small=True)
        + bars("By model and effort", "usage", model_rows, t.cost, "", small=True)
        + "</div></div></section>"
        + defaults_table(kinds, defaults, fallback, r, signed_in)
    )


def bars(title: str, measure: str, rows: list[tuple[str, float, str, str]], whole: float, note: str, small: bool = False) -> str:
    """One measure as horizontal bars, biggest first, each with its value and share in words."""
    if not rows:
        return ""
    top = max(v for _, v, _, _ in rows) or 1
    items = "".join(
        f"<li title='{escape(name)}: {escape(value)} ({_share(v, whole)})'><span class=name>{escape(name)}</span>"
        f"<span class=bar><i style='width:{max(100 * v / top, 1.5):.1f}%'></i></span>"
        f"<span class=val>{escape(value)} <small>{_share(v, whole)}</small></span>"
        + (f"<span class=extra>{escape(extra)}</span>" if extra else "")
        + "</li>"
        for name, v, value, extra in rows
    )
    tag = "h3" if small else "h2"
    return f"<section class='bars {measure}'><{tag}>{escape(title)}</{tag}>{f'<p class=hint>{escape(note)}</p>' if note else ''}<ol>{items}</ol></section>"


def defaults_table(kinds: list[str], defaults: dict[str, tuple[str, str]], fallback: tuple[str, str], r: Report,
                   signed_in: bool) -> str:  # fmt: skip
    """Each kind of agent step's default model and effort, with how it's been doing."""
    by_kind = {k.key: k for k in r.kinds}
    every = defaults.get("*", ("", ""))
    rows = []
    for kind in ["*", *kinds]:
        model, effort = defaults.get(kind, ("", ""))
        t = by_kind.get(kind)
        how = " · ".join(x for x in (_per(t), _per_cost(t)) if x) if t and t.steps else "not run yet"
        if kind == "*":
            name, how = "Every step", "unless set below"
            unset = (f"{fallback[0]} (OFFICE_MODEL)", f"{fallback[1]} (OFFICE_EFFORT)")
        else:
            name, unset = kind, (f"default: {every[0] or fallback[0]}", f"default: {every[1] or fallback[1]}")
        if signed_in:
            picks = "".join(
                select_form(field, value, values, unset[i], action="default", kind=kind, back="/insights#defaults")
                for i, (field, value, values) in enumerate((("model", model, MODELS), ("effort", effort, EFFORTS)))
            )
        else:
            picks = f"<span>{escape(model or unset[0])}</span><span>{escape(effort or unset[1])}</span>"
        rows.append(f"<li><div><b>{escape(name)}</b> <span class=muted>{escape(how)}</span></div><div class=picks>{picks}</div></li>")
    return (
        "<section class=defaults id=defaults><h2>Defaults for each kind of step</h2>"
        "<p class=hint>The model and effort each kind of agent step runs on. A project's own choice (from your dictation, "
        "!model, or its lane) wins over these, and a single step's choice wins over both.</p>"
        f"<ol class=kinds>{''.join(rows)}</ol>"
        + ("" if signed_in else "<p class=hint>Sign in to change them.</p>")
        + "</section>"
    )


# --- pieces ---------------------------------------------------------------------------


def step_href(job: Job, step: Step) -> str:
    """A step's sheet. Steps of a job that hasn't started aren't stored yet, so go by position."""
    return f"/steps/{step.id}" if step.id else f"/jobs/{job.id}/planned/{step.position}"


def when(at: datetime, now: datetime) -> str:
    """A time to come or gone, in words; the page's script shows it on the viewer's clock."""
    delta = at - now
    words = f"in {span(delta)}" if delta.total_seconds() > 30 else ("now" if delta.total_seconds() > -60 else f"{span(-delta)} ago")
    return f"<time datetime='{at.isoformat()}' data-at='{at.isoformat()}'>{words}</time>"


def action_form(action: str, label: str, *, job: int | None = None, step: int | None = None, value: str = "",
                slug: str = "", back: str = "", confirm: str = "", danger: bool = False) -> str:  # fmt: skip
    hidden = _hidden(action=action, job=job, step=step, value=value, slug=slug, back=back)
    check = f" onsubmit='return confirm({escape(json.dumps(confirm))})'" if confirm else ""
    return f"<form method=post action=/act class=inline{check}>{hidden}<button{' class=danger' if danger else ''}>{escape(label)}</button></form>"


def select_form(name: str, current: str, values: tuple[str, ...], unset: str, **fields) -> str:
    """A select that saves on change. The first option is "" (`unset` says what that means)."""
    options = f"<option value=''{' selected' if not current else ''}>{escape(unset)}</option>" + "".join(
        f"<option{' selected' if current == v else ''}>{v}</option>" for v in values
    )
    fields = {k: v for k, v in fields.items() if k != "value"}
    return (
        f"<form method=post action=/act class=inline>{_hidden(name=name, **fields)}"
        f"<select name=value aria-label={name} onchange='this.form.submit()'>{options}</select></form>"
    )


def reply_form(job: Job, placeholder: str) -> str:
    return (
        f"<form method=post action=/act class=reply>{_hidden(action='note', job=job.id, back=f'/#p-{job.slug}')}"
        f"<textarea name=value rows=2 placeholder='{escape(placeholder)}' required></textarea><button>Send</button></form>"
    )


def skip_button(job: Job, step: Step, label: str = "Skip", back: str = "") -> str:
    return action_form("skip", label, job=job.id, step=step.id, back=back or f"/#p-{job.slug}",
                       confirm="" if step.kind == "checkin" else f"Skip {plan.label(step)}?")  # fmt: skip


def read_notes(path: Path, keep: int = 4) -> list[str]:
    try:
        lines = path.read_text(errors="replace").splitlines()
    except FileNotFoundError:
        return []
    return [line.strip() for line in lines if line.strip()][-keep:]


def _hidden(**fields) -> str:
    return "".join(f"<input type=hidden name={k} value='{escape(str(v))}'>" for k, v in fields.items() if v not in (None, ""))


def _links(links: dict[str, str]) -> str:
    safe = [(k, v) for k, v in links.items() if v.startswith(("https://", "http://"))]
    if not safe:
        return ""
    return "<div class=links>" + "".join(f"<a href='{escape(v)}'>{escape(k)} ↗</a>" for k, v in safe) + "</div>"


def _out_links(lane: LaneData) -> str:
    return f"<a href='{escape(lane.site)}'>Site ↗</a><a href='{escape(lane.repo)}'>Code ↗</a>"


def _pause(p: Project) -> str:
    if p.paused:
        return action_form("resume", "Resume project", slug=p.slug, back=f"/#p-{p.slug}")
    return action_form("pause", "Pause project", slug=p.slug, back=f"/#p-{p.slug}")


def _tone(lane: LaneData) -> str:
    job, step = lane.job, plan.current(lane.steps)
    if job is None or job.state not in ACTIVE:
        return "finished"
    if lane.project.paused:
        return "paused"
    if job.state == "waiting":
        return "waiting"
    if step and step.error:
        return "trouble"
    return "running" if job.state == "running" else "queued"


def _state_tone(s: Step) -> str:
    return {"done": "finished", "failed": "trouble", "running": "running", "waiting": "waiting"}.get(s.state, "")


def _show_now(lane: LaneData) -> bool:
    """The card under the line: for a step under way, or one waiting on Riggy."""
    step = plan.current(lane.steps)
    return bool(step.started_at) or lane.job.state == "waiting" or bool(step.error)


def _doing(lane: LaneData, s: Step, now: datetime) -> str:
    if s.kind == "agent":
        so_far = now - _dt(s.started_at) if s.started_at else timedelta(0)
        usual = estimate(lane.known, s)
        about = f" (usually {span(timedelta(seconds=usual))})" if plan.label(s) in lane.known else ""
        return f"{s.name}, {span(so_far)} in{about}"
    return {"checks": "waiting for CI", "deploy": "deploying", "merge": "merging", "pr": "opening a PR",
            "push": "pushing", "push-main": "pushing to main", "setup": "setting up",
            "checkin": "waiting for you to try it"}.get(s.kind, plan.label(s))  # fmt: skip


def _waiting_words(lane: LaneData) -> str:
    step = plan.current(lane.steps)
    if step and step.kind == "checkin":
        return "is waiting for you to try it"
    return "has a question for you" if lane.job.resume_session else "needs a hint from you"


def _agents_before(steps: list[Step], deploy: Step) -> list[str]:
    """The agent steps this deploy ships: those since the previous deploy."""
    names: list[str] = []
    for s in steps:
        if s.position >= deploy.position:
            break
        if s.kind == "deploy":
            names = []
        elif s.kind == "agent" and s.state != "skipped":
            names.append(s.name)
    return names


def _engine_words(p: Project) -> str:
    if not p.model and not p.effort:
        return "default models"
    return f"{p.model or 'default'}/{p.effort or 'default'}"


def _spent(lane: LaneData) -> str:
    runs = [t for t in lane.tallies.values() if t.runs]
    if not runs:
        return ""
    cost = sum(t.cost for t in runs)
    tokens = sum(t.tokens for t in runs)
    took = sum(_took(lane, s) for s in lane.steps if s.state in ("done", "failed") and s.kind != "checkin")
    return f"So far: {span(timedelta(seconds=took))} of work, {_money(cost)} of usage, {_count(tokens)} tokens."


def _took(lane: LaneData, s: Step) -> float:
    """Seconds a step took: an agent step's runs, or office's step start to end."""
    tally = lane.tallies.get(s.id)
    if s.kind == "agent" and tally and tally.seconds:
        return tally.seconds
    if s.started_at and s.ended_at:
        return (_dt(s.ended_at) - _dt(s.started_at)).total_seconds()
    return 0.0


def _took_row(first: Step, last: Step) -> str:
    if not (first.started_at and last.ended_at):
        return ""
    return f"<time class=took>{last.ended_at[11:16]} · {span(_dt(last.ended_at) - _dt(first.started_at))}</time>"


def _seconds(run: dict) -> float:
    if not run.get("ended_at"):
        return 0.0
    return (_dt(run["ended_at"]) - _dt(run["started_at"])).total_seconds()


def _outcome(run: dict) -> str:
    return {"verdict": "finished", "max_turns": "out of turns", "limit": "hit the usage limit", None: "running"}.get(
        run["outcome"], str(run["outcome"])
    )


def _per(t: Tally | None) -> str:
    return f"{span(timedelta(seconds=t.per_step))} a step" if t and t.steps else ""


def _per_cost(t: Tally | None) -> str:
    return f"{_money(t.cost_per_step)} a step" if t and t.steps and t.cost else ""


def _share(part: float, whole: float) -> str:
    return f"{round(100 * part / whole)}%" if whole else ""


def _money(cost: float) -> str:
    return f"${cost:,.2f}" if cost < 100 else f"${cost:,.0f}"


def _count(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1000:
        return f"{n / 1000:.0f}k"
    return str(n)


def _n(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def _and(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def _mark(line: Line) -> str:
    return {"says": "💬", "call": "→", "result": "←", "error": "✗"}.get(line.kind, "")


def _dt(value: str | None) -> datetime:
    return datetime.fromisoformat(value) if value else datetime.now(UTC)


def _short(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


LEGEND = """<details class=legend data-key=legend><summary>How to read the lines</summary>
<p>Each job is a line through its steps, left to right. The <b>upper track</b> is work on the job's branch: agent
steps (big circles) and office pushing it, opening a PR and waiting for CI (small dots). The line drops to the
<b>lower track</b>, main, when office merges, and every square there is a deploy: that's when it goes live.
Solid line: done so far. Dashed: still to come, with when it should start.</p>
<p><span class=key-done>✓</span> done · <span class=key-now></span> now · <span class=key-wait>?</span> waiting for you ·
<span class=key-fail>✕</span> failed (a fix step follows) · <span class=key-skip>–</span> skipped · <span class=key-todo></span> to come.
Tap a step for what it did, what it's doing, or to change it before it starts.</p></details>"""

STYLE = """
:root { --bg:#f3f4f7; --card:#fff; --ink:#111827; --muted:#6b7280; --line:#e5e7eb; --blue:#2563eb; --grey:#9ca3af;
  --good:#0ca30c; --warning:#fab219; --critical:#d03b3b; --radius:14px; color-scheme: light dark;
  --c1:#2a78d6; --c2:#eb6834; --c3:#1baf7a; --c4:#eda100; --c5:#e87ba4; --c6:#008300; --c7:#6250d6; --c8:#e34948; }
@media (prefers-color-scheme: dark) { :root { --bg:#0b1020; --card:#131a2e; --ink:#e5e7eb; --muted:#94a3b8; --line:#243049;
  --c1:#3987e5; --c2:#d95926; --c3:#199e70; --c4:#c98500; --c5:#d55181; --c6:#008300; --c7:#9085e9; --c8:#e66767; } }
.c1 { --pc:var(--c1); } .c2 { --pc:var(--c2); } .c3 { --pc:var(--c3); } .c4 { --pc:var(--c4); }
.c5 { --pc:var(--c5); } .c6 { --pc:var(--c6); } .c7 { --pc:var(--c7); } .c8 { --pc:var(--c8); }
* { box-sizing:border-box; }
body { margin:0; font:15px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif; background:var(--bg); color:var(--ink); }
a { color:var(--blue); text-decoration:none; }
h1, h2, h3 { margin:0; }
.top { position:sticky; top:0; z-index:5; display:flex; align-items:center; justify-content:space-between; gap:.5rem;
  padding:.6rem max(1rem, env(safe-area-inset-left)); background:#0f172a; color:#fff; }
.brand { color:#fff; font-weight:700; font-size:1.15rem; letter-spacing:.02em; }
.top nav { display:flex; gap:.3rem; align-items:center; }
.top button, .top summary, .navlink { color:#fff; } .navlink { padding:.35rem .5rem; font-size:.9rem; }
.top form button { background:transparent; border-color:#334155; }
main { max-width:56rem; margin:0 auto; padding:1rem max(.75rem, env(safe-area-inset-left)) 4rem; }
.banner { max-width:56rem; margin:.75rem auto 0; padding:.6rem 1rem; border-radius:var(--radius); }
.banner.paused { background:#fef3c7; color:#92400e; }
.flash { position:fixed; z-index:20; left:50%; bottom:max(1rem, env(safe-area-inset-bottom)); transform:translateX(-50%);
  width:max-content; max-width:calc(100% - 2rem); padding:.6rem 1rem; border-radius:999px; box-shadow:0 6px 24px rgb(0 0 0 / .25);
  transition:opacity .4s; background:#dbeafe; color:#1e3a8a; }
.swatch { display:inline-block; width:.7rem; height:.7rem; border-radius:50%; background:var(--pc, var(--grey)); margin-right:.4rem; vertical-align:baseline; }
.needs h2, .agenda h2, .idle > h2, .bars h2, .defaults h2 { font-size:.8rem; text-transform:uppercase; letter-spacing:.08em; color:var(--muted); margin-bottom:.4rem; }
.needs, .agenda { background:var(--card); border-radius:var(--radius); padding:.8rem 1rem; margin-bottom:1rem; box-shadow:0 1px 2px rgb(0 0 0 / .06); }
.needs { border-left:5px solid var(--warning); }
.needs ol, .agenda ol { list-style:none; padding:0; margin:0; }
.needs li a, .agenda li a { color:var(--ink); }
.needs li { padding:.25rem 0; }
.agenda li { display:flex; gap:.6rem; align-items:baseline; padding:.3rem 0; border-top:1px solid var(--line); }
.agenda li:first-child { border-top:0; }
.agenda .when { flex:0 0 4.6rem; color:var(--muted); font-size:.85rem; font-variant-numeric:tabular-nums; }
.agenda details summary { cursor:pointer; color:var(--muted); font-size:.85rem; padding:.3rem 0; }
.hint { color:var(--muted); font-size:.8rem; margin:.4rem 0 0; }
.lane, .card { scroll-margin-top:4rem; }
.lane { background:var(--card); border-radius:var(--radius); padding:1rem; margin-bottom:1rem; border-top:4px solid var(--pc, var(--grey));
  box-shadow:0 1px 2px rgb(0 0 0 / .06); }
.lane-head { display:flex; justify-content:space-between; gap:.75rem; align-items:flex-start; }
.lane-head h2 { font-size:1.15rem; } .lane-head h2 a { color:inherit; }
.sub, .muted { color:var(--muted); font-size:.88em; margin:.15rem 0 0; }
.out { display:flex; gap:.6rem; flex-shrink:0; font-size:.9em; }
.request { color:var(--muted); font-style:italic; margin:.5rem 0; }
.progress { display:flex; gap:.5rem; align-items:center; flex-wrap:wrap; margin:.25rem 0 .25rem; font-size:.9em; color:var(--muted); }
.place { font-size:.85em; }
.map { overflow-x:auto; overscroll-behavior-x:contain; margin:0 -1rem; padding:0 .5rem; scrollbar-width:thin;
  -webkit-mask-image:linear-gradient(90deg, transparent 0, #000 .75rem, #000 calc(100% - .75rem), transparent 100%); }
.graph { display:block; }
.graph .seg { fill:none; stroke:var(--grey); stroke-width:2; stroke-dasharray:4 5; opacity:.7; }
.graph .seg.reached { stroke:var(--pc); stroke-width:3.5; stroke-dasharray:none; opacity:1; stroke-linecap:round; }
.graph .hit { fill:transparent; }
.graph .dot { fill:var(--card); stroke:var(--grey); stroke-width:2; }
.graph .ring { fill:none; stroke:var(--grey); stroke-width:1.5; }
.graph .done .dot, .graph .running .dot { fill:var(--pc); stroke:var(--pc); }
.graph .done .ring { stroke:var(--pc); }
.graph .failed .dot { fill:var(--critical); stroke:var(--critical); }
.graph .waiting .dot { fill:var(--warning); stroke:var(--warning); }
.graph .skipped .dot { stroke-dasharray:3 3; }
.graph .skipped .name { text-decoration:line-through; fill:var(--muted); }
.graph .fix .name { fill:var(--critical); }
.graph .pulse { fill:var(--pc); opacity:.35; transform-box:fill-box; transform-origin:center; animation:pulse 1.6s ease-out infinite; }
.graph .glyph { fill:#fff; font-size:11px; font-weight:700; text-anchor:middle; pointer-events:none; }
.graph .skipped .glyph { fill:var(--muted); }
.graph .name { fill:var(--ink); font-size:12px; font-weight:600; text-anchor:middle; }
.graph .small .name { font-size:10px; font-weight:500; fill:var(--muted); }
.graph .pending .name { font-weight:500; }
.graph .meta { fill:var(--muted); font-size:10.5px; text-anchor:middle; }
.graph .now .name { fill:var(--pc); font-weight:700; }
.graph a:focus-visible .dot { stroke:var(--blue); stroke-width:3; }
.graph a:hover .dot { stroke:var(--pc); }
@keyframes pulse { 0% { transform:scale(1); opacity:.5; } 100% { transform:scale(2.3); opacity:0; } }
@media (prefers-reduced-motion: reduce) { .graph .pulse { animation:none; } }
.story { margin:.2rem 0 .3rem; }
.story time, .spent { color:var(--muted); } .story time { font-weight:600; color:var(--ink); }
.spent { font-size:.85em; margin:0 0 .5rem; }
.badge { display:inline-block; padding:.1rem .55rem; border-radius:999px; font-size:.78rem; font-weight:600; background:var(--line); color:var(--ink); }
.badge.running { background:#dbeafe; color:#1e40af; } .badge.waiting { background:#fef3c7; color:#92400e; }
.badge.trouble { background:#fee2e2; color:#991b1b; } .badge.paused { background:var(--line); color:var(--muted); }
.badge.finished { background:#dcfce7; color:#166534; }
.now { border-radius:12px; padding:.75rem .85rem; margin:.5rem 0 .25rem; background:rgb(37 99 235 / .07); border:1px solid rgb(37 99 235 / .2); }
.now.waiting { background:rgb(217 119 6 / .09); border-color:rgb(217 119 6 / .3); }
.now.trouble { background:rgb(220 38 38 / .07); border-color:rgb(220 38 38 / .3); }
.now.queued, .now.paused { background:transparent; border-color:var(--line); }
.now-head { display:flex; justify-content:space-between; align-items:baseline; gap:.5rem; }
.now-head .label { font-weight:700; font-size:1.05rem; color:var(--ink); }
.now p { margin:.4rem 0; }
.said { font-size:1rem; }
.notes { margin:.4rem 0; padding-left:1.1rem; } .notes li::marker { content:"▸ "; color:var(--pc, var(--blue)); }
.tail summary { cursor:pointer; color:var(--muted); font-size:.85em; }
.log { font:12px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace; background:#0f172a; color:#cbd5e1; border-radius:10px;
  padding:.5rem .6rem; margin-top:.35rem; max-height:16rem; overflow:auto; }
.t { display:flex; gap:.5rem; } .t time { color:#64748b; flex-shrink:0; min-width:2.6rem; }
.t span { overflow-wrap:anywhere; } .t.says span { color:#f1f5f9; } .t.error span { color:#fca5a5; } .t.nested { padding-left:1rem; opacity:.85; }
.legend { margin:-.25rem 0 1rem; font-size:.85rem; color:var(--muted); }
.legend summary { cursor:pointer; } .legend p { margin:.4rem 0; }
.legend span { display:inline-grid; place-items:center; width:1.1rem; height:1.1rem; border-radius:50%; font-size:.7rem; color:#fff; vertical-align:middle; }
.key-done { background:var(--c1); } .key-now { background:var(--c1); box-shadow:0 0 0 3px rgb(42 120 214 / .3); }
.key-wait { background:var(--warning); } .key-fail { background:var(--critical); }
.key-skip { border:2px dashed var(--grey); color:var(--muted) !important; } .key-todo { border:2px solid var(--grey); }
.next h3, .done summary { font-size:.8rem; text-transform:uppercase; letter-spacing:.08em; color:var(--muted); margin:.75rem 0 .3rem; }
.done summary { cursor:pointer; }
.steps { list-style:none; padding:0; margin:0; }
.row { padding:.45rem 0; border-top:1px solid var(--line); }
.row .what { display:flex; gap:.4rem; align-items:center; flex-wrap:wrap; } .row .what > a { color:var(--ink); }
.row p { margin:.2rem 0 0 1.4rem; color:var(--muted); font-size:.9em; }
.row .links, .row .took { margin-left:1.4rem; }
.icon { width:1rem; text-align:center; color:var(--good); } .row.failed .icon { color:var(--critical); } .row.skipped .icon { color:var(--grey); }
.row.pending b { font-weight:500; } .row.skipped b { text-decoration:line-through; color:var(--muted); }
.opt { font-size:.72rem; color:var(--muted); border:1px solid var(--line); border-radius:999px; padding:0 .4rem; }
.chips { display:inline-flex; flex-wrap:wrap; gap:.25rem; }
.chip { font-size:.78rem; padding:.05rem .45rem; border-radius:999px; border:1px solid var(--line); color:var(--muted); }
.chip.done { background:#dcfce7; color:#166534; border-color:transparent; } .chip.failed { background:#fee2e2; color:#991b1b; border-color:transparent; }
.chip.running { background:#dbeafe; color:#1e40af; border-color:transparent; }
.links { display:flex; flex-wrap:wrap; gap:.25rem .7rem; font-size:.85em; margin-top:.2rem; }
.took { display:inline; font-size:.8em; color:var(--muted); } time.took { display:block; }
.controls { display:flex; flex-wrap:wrap; gap:.4rem; align-items:center; margin-top:.75rem; padding-top:.75rem; border-top:1px dashed var(--line); }
.engine-pick { display:inline-flex; gap:.3rem; align-items:center; font-size:.85rem; color:var(--muted); flex-wrap:wrap; }
form.inline { display:inline; margin:0; }
button, .button, select { font:inherit; font-size:.88rem; min-height:36px; padding:.35rem .8rem; border-radius:10px; border:1px solid var(--line);
  background:var(--card); color:var(--ink); cursor:pointer; }
select { padding-right:.4rem; max-width:13rem; }
.button { display:inline-flex; align-items:center; background:var(--blue); color:#fff; border-color:var(--blue); }
button.danger { color:var(--critical); }
button.quiet, summary.quiet { background:transparent; border:0; cursor:pointer; color:inherit; font-size:.9rem; padding:.35rem .5rem; }
.row button { min-height:30px; padding:.1rem .7rem; font-size:.8rem; }
.row .what form { margin-left:auto; }
button { white-space:nowrap; }
.reply { display:flex; gap:.4rem; margin:.4rem 0; width:100%; }
.reply textarea { flex:1; font:inherit; padding:.45rem .6rem; border-radius:10px; border:1px solid var(--line); background:var(--card); color:var(--ink); min-height:42px; }
.note-form { width:100%; }
.signin { position:relative; } .signin summary { list-style:none; }
.signin form { position:absolute; right:0; top:2.4rem; display:flex; gap:.4rem; padding:.6rem; background:var(--card); border-radius:12px;
  box-shadow:0 6px 24px rgb(0 0 0 / .2); }
.signin input { font:inherit; padding:.4rem .6rem; border-radius:10px; border:1px solid var(--line); min-width:9rem; }
.idle .grid { display:grid; gap:.75rem; grid-template-columns:repeat(auto-fill, minmax(15rem, 1fr)); margin-top:.5rem; }
.card { background:var(--card); border-radius:var(--radius); padding:.85rem; box-shadow:0 1px 2px rgb(0 0 0 / .06); border-top:4px solid var(--pc, var(--grey)); }
.card h3 { font-size:1rem; } .card .out { margin-top:.5rem; }
.empty { color:var(--muted); text-align:center; padding:2rem 0; }
table { border-collapse:collapse; width:100%; font-size:.88em; } th, td { text-align:left; padding:.35rem .4rem; border-bottom:1px solid var(--line); }
td select { min-height:32px; font-size:.82rem; }
.runs { overflow-x:auto; background:var(--card); border-radius:var(--radius); padding:.5rem; }
dialog#sheet { border:0; padding:0; width:min(40rem, 100%); max-height:85vh; border-radius:18px; background:var(--card); color:var(--ink);
  box-shadow:0 12px 48px rgb(0 0 0 / .35); }
dialog#sheet::backdrop { background:rgb(15 23 42 / .45); }
dialog#sheet .close { position:sticky; float:right; top:.4rem; right:.4rem; z-index:1; font-size:1.1rem; }
.sheet-body { padding:1rem 1.1rem 1.4rem; }
.sheet-head h2 { font-size:1.3rem; margin:.15rem 0; }
.sheet-body h3 { font-size:.8rem; text-transform:uppercase; letter-spacing:.08em; color:var(--muted); margin:1rem 0 .3rem; }
.sheet-body .tail { margin-top:.6rem; }
.facts { display:grid; grid-template-columns:max-content 1fr; gap:.25rem .9rem; margin:.75rem 0; font-size:.92em; }
.facts dt { color:var(--muted); } .facts dd { margin:0; }
.summary { background:var(--bg); border-radius:10px; padding:.6rem .75rem; }
.problem { white-space:pre-wrap; font-size:12px; background:var(--bg); padding:.5rem; border-radius:8px; max-height:14rem; overflow:auto; }
.row-form { display:flex; gap:.4rem; align-items:center; flex-wrap:wrap; width:100%; }
.row-form > span, .row-form label { color:var(--muted); font-size:.88rem; }
@media (max-width:640px) {
  dialog#sheet { width:100%; max-width:100%; margin:auto 0 0; border-radius:18px 18px 0 0; max-height:88vh; }
  .lane-head { flex-direction:column; } .lane { padding:.85rem; } .map { margin:0 -.85rem; }
}
.insights h1 { font-size:1.4rem; margin-bottom:.5rem; }
.windows { display:flex; gap:.3rem; margin-bottom:.75rem; }
.windows a { padding:.3rem .75rem; border-radius:999px; border:1px solid var(--line); background:var(--card); color:var(--ink); font-size:.88rem; }
.windows a.on { background:var(--ink); color:var(--card); border-color:var(--ink); }
.tiles { display:grid; grid-template-columns:repeat(auto-fit, minmax(8rem, 1fr)); gap:.6rem; margin-bottom:.75rem; }
.tile { background:var(--card); border-radius:var(--radius); padding:.7rem .85rem; box-shadow:0 1px 2px rgb(0 0 0 / .06); }
.tile span { display:block; color:var(--muted); font-size:.8rem; } .tile b { font-size:1.4rem; font-weight:650; }
.findings { background:var(--card); border-radius:var(--radius); padding:.75rem 1rem .75rem 2rem; margin:0 0 1rem; box-shadow:0 1px 2px rgb(0 0 0 / .06); }
.findings li { margin:.2rem 0; }
.bars { background:var(--card); border-radius:var(--radius); padding:.8rem 1rem; margin-bottom:1rem; box-shadow:0 1px 2px rgb(0 0 0 / .06); }
.bars h3 { font-size:.95rem; margin-bottom:.3rem; }
.bars ol { list-style:none; padding:0; margin:.5rem 0 0; }
.bars li { display:grid; grid-template-columns:minmax(5rem, 9rem) 1fr auto; gap:.15rem .6rem; align-items:center; padding:.2rem 0; }
.bars .name { font-size:.9rem; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.bars .bar { height:12px; display:block; }
.bars .bar i { display:block; height:100%; border-radius:0 4px 4px 0; background:var(--c1); }
.bars.usage .bar i { background:var(--c2); }
.bars .val { font-size:.85rem; font-variant-numeric:tabular-nums; text-align:right; white-space:nowrap; }
.bars .val small { color:var(--muted); margin-left:.25rem; }
.bars .extra { grid-column:2 / 4; color:var(--muted); font-size:.78rem; margin-top:-.15rem; }
.bars li:hover .bar i { filter:brightness(1.1); }
.two { display:grid; gap:1rem; grid-template-columns:repeat(auto-fit, minmax(16rem, 1fr)); }
.two > div > h3 { font-size:.95rem; margin:.25rem 0; }
.hot { padding-left:1.2rem; margin:.25rem 0 1rem; } .hot li { margin:.2rem 0; } .hot span { color:var(--muted); font-variant-numeric:tabular-nums; }
.defaults { margin-top:1rem; }
.kinds { list-style:none; padding:.25rem 1rem; margin:.5rem 0 0; background:var(--card); border-radius:var(--radius); box-shadow:0 1px 2px rgb(0 0 0 / .06); }
.kinds li { display:flex; justify-content:space-between; align-items:center; gap:.4rem 1rem; flex-wrap:wrap; padding:.55rem 0; border-top:1px solid var(--line); }
.kinds li:first-child { border-top:0; } .kinds .muted { font-size:.8rem; margin-left:.3rem; }
.picks { display:flex; gap:.4rem; flex-wrap:wrap; font-size:.8rem; color:var(--muted); }
.picks label, .row-form label { display:inline-flex; gap:.3rem; align-items:center; }
.picks select { min-height:32px; font-size:.82rem; }
.picks > span { color:var(--ink); font-size:.88rem; } .picks > span + span::before { content:"· "; color:var(--muted); }
@media (max-width:640px) { .bars li { grid-template-columns:minmax(3.5rem, 6rem) 1fr auto; } }
"""

# Refresh the lanes every 10 seconds without losing what's open, scrolled or being typed; show
# times on the viewer's clock; open a step's sheet in place.
SCRIPT = """
(() => {
  const main = document.getElementById('lanes');
  const sheet = document.getElementById('sheet');
  const sheetBody = sheet.querySelector('.sheet-body');
  const typing = (root) => root.contains(document.activeElement) && ['TEXTAREA','INPUT','SELECT'].includes(document.activeElement.tagName);
  const scrolled = {};
  function clocks(root) {
    root.querySelectorAll('[data-at]').forEach(el => {
      const at = new Date(el.dataset.at), now = new Date();
      if (isNaN(at)) return;
      const opts = {hour: 'numeric', minute: '2-digit'};
      if (at - now > 18 * 3600e3) opts.weekday = 'short';
      const clock = at.toLocaleTimeString([], opts);
      if (!el.title) el.title = el.textContent;
      el.textContent = (at > now ? '~' : '') + clock;
    });
  }
  function center(root) {
    root.querySelectorAll('.map').forEach(m => {
      if (m.dataset.key in scrolled) { m.scrollLeft = scrolled[m.dataset.key]; return; }
      const node = m.querySelector('.node.now') || m.querySelector('.node.pending');
      if (!node) { m.scrollLeft = m.scrollWidth; return; }
      const box = node.getBBox();
      m.scrollLeft = box.x + box.width / 2 - m.clientWidth / 2;
    });
    root.querySelectorAll('.map').forEach(m => m.addEventListener('scroll', () => { scrolled[m.dataset.key] = m.scrollLeft; }, {passive: true}));
  }
  function keepOpen(root, fn) {
    const open = new Set([...root.querySelectorAll('details[data-key]')].filter(d => d.open).map(d => d.dataset.key));
    const closed = new Set([...root.querySelectorAll('details[data-key]')].filter(d => !d.open).map(d => d.dataset.key));
    const logs = {}; root.querySelectorAll('.log').forEach(l => { logs[l.closest('details').dataset.key] = l.scrollHeight - l.scrollTop - l.clientHeight < 20; });
    fn();
    root.querySelectorAll('details[data-key]').forEach(d => {
      if (open.has(d.dataset.key)) d.open = true;
      if (closed.has(d.dataset.key)) d.open = false;
    });
    root.querySelectorAll('.log').forEach(l => { if (logs[l.closest('details').dataset.key] !== false) l.scrollTop = l.scrollHeight; });
  }
  async function refresh() {
    if (document.hidden) return;
    if (!typing(main)) {
      try {
        const r = await fetch(location.pathname + '?part=lanes', {cache: 'no-store'});
        if (r.ok) { const html = await r.text(); keepOpen(main, () => { main.innerHTML = html; }); clocks(main); center(main); }
      } catch (e) {}
    }
    if (sheet.open && sheet.dataset.href && sheetBody.querySelector('[data-live]') && !typing(sheet)) openSheet(sheet.dataset.href, true);
  }
  async function openSheet(href, again) {
    try {
      const r = await fetch(href + (href.includes('?') ? '&' : '?') + 'part=sheet', {cache: 'no-store'});
      if (!r.ok) return;
      const html = await r.text();
      keepOpen(sheetBody, () => { sheetBody.innerHTML = html; });
      clocks(sheetBody);
    } catch (e) { return; }
    sheet.dataset.href = href;
    if (!again) {
      if (!sheet.open) sheet.showModal();
      sheetBody.querySelectorAll('.log').forEach(l => l.scrollTop = l.scrollHeight);
      history.replaceState(null, '', location.pathname + '#' + href);
    }
  }
  document.addEventListener('click', e => {
    const a = e.target.closest('a[data-sheet]');
    if (!a || e.metaKey || e.ctrlKey) return;
    e.preventDefault();
    openSheet(a.getAttribute('href'));
  });
  sheet.querySelector('.close').addEventListener('click', () => sheet.close());
  sheet.addEventListener('click', e => { if (e.target === sheet) sheet.close(); });
  sheet.addEventListener('close', () => { delete sheet.dataset.href; history.replaceState(null, '', location.pathname); });
  clocks(document.body); center(main);
  main.querySelectorAll('.log').forEach(l => l.scrollTop = l.scrollHeight);
  setInterval(refresh, 10000);
  const flash = document.querySelector('.flash');
  if (flash) { setTimeout(() => flash.style.opacity = 0, 5000); setTimeout(() => flash.remove(), 5500); }
  if (location.search.includes('msg=')) history.replaceState(null, '', location.pathname + location.hash);
  if (location.hash.startsWith('#/')) {
    const href = location.hash.slice(1);
    const node = main.querySelector(`a[href="${CSS.escape(href)}"]`);
    if (node) node.closest('.lane')?.scrollIntoView();
    openSheet(href);
  }
})();
"""
