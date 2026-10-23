"""The status page's HTML: one swim lane per project, phone first.

A lane shows the project's job as a strip of steps (done, running, waiting, failed,
skipped, to come), what the running step is doing right now, what comes next, and
what's done with links to what it made. Signed in, each lane also has controls.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from html import escape
from pathlib import Path

from . import plan
from .control import SKIPPED
from .live import Line, view
from .store import ACTIVE, EFFORTS, MODELS, Job, Project, Step
from .watch import span

SHIP = {"push", "pr", "checks", "merge", "push-main", "deploy"}  # shown together as one "ship" row
ICONS = {"done": "✓", "skipped": "–", "running": "▶", "waiting": "?", "failed": "✗", "pending": ""}
NEXT_SHOWN = 5


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
            "resume-all" if paused else "pause-all", "Resume all work" if paused else "Pause all work", back=back
        )
    banner = "<div class='banner paused'>Work is paused. Nothing new starts until it's resumed.</div>" if paused else ""
    flash_html = f"<div class=flash role=status>{escape(flash)}</div>" if flash else ""
    return f"""<!doctype html><html lang=en><meta charset=utf-8>
<meta name=viewport content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name=theme-color content="#0f172a">
<title>{escape(title)}</title><style>{STYLE}</style>
<header class=top><a href=/ class=brand>office</a><nav>{pause}{auth}</nav></header>
{banner}{flash_html}<main id=lanes>{body}</main><script>{SCRIPT}</script></html>"""


def overview(lanes: list[LaneData], idle: list[LaneData], signed_in: bool, now: datetime) -> str:
    queue = sorted((lane for lane in lanes if lane.position), key=lambda lane: lane.position)
    queue_html = ""
    if queue:
        chips = "".join(
            f"<li><a href='#p-{escape(lane.project.slug)}'><b>{lane.position}</b> {escape(lane.project.name)}"
            f"<span>{escape(lane.state)}</span></a></li>"
            for lane in queue
        )
        queue_html = f"<section class=queue><h2>Queue</h2><ol>{chips}</ol></section>"
    active = "".join(lane_html(lane, signed_in, now) for lane in lanes) or "<p class=empty>Nothing under way. Dictate an idea in the office inbox.</p>"
    idle_html = ""
    if idle:
        idle_html = "<section class=idle><h2>Other projects</h2><div class=grid>" + "".join(
            idle_card(lane, signed_in, now) for lane in idle
        ) + "</div></section>"
    return queue_html + active + idle_html


# --- a lane ---------------------------------------------------------------------------


def lane_html(lane: LaneData, signed_in: bool, now: datetime, full: bool = False) -> str:
    p, job, steps = lane.project, lane.job, lane.steps
    current = plan.current(steps)
    tone = _tone(lane)
    what = "New project" if job.kind == "new" else "Change"
    paused = "<span class='badge paused'>Paused</span>" if p.paused else ""
    count = sum(1 for s in steps if s.state in ("done", "skipped"))
    progress = f"{count} of {len(steps)} steps" if steps else "Not started"
    return f"""<article class="lane {tone}" id="p-{escape(p.slug)}">
<header class=lane-head><div><h2><a href='/jobs/{job.id}'>{escape(p.name)}</a> {paused}</h2>
<p class=sub>{what} · {escape(p.mode)} · {escape(p.model)}/{escape(p.effort)} · job #{job.id}</p></div>
<div class=out>{_out_links(p, lane)}</div></header>
<p class=request>“{escape(_short(job.request, 220))}”</p>
{strip(steps)}
<p class=progress><span class="badge {tone}">{escape(lane.state)}</span> {progress}</p>
{now_card(lane, signed_in, now) if current else ""}
{next_list(lane, signed_in, full)}
{done_list(lane, signed_in, now, full)}
{controls(lane) if signed_in else ""}
</article>"""


def strip(steps: list[Step]) -> str:
    dots = "".join(
        f"<li class='{s.state}{' code' if s.kind != 'agent' else ''}' title='{escape(plan.label(s))}: {s.state}'></li>"
        for s in steps
    )
    return f"<ol class=strip aria-label='Steps'>{dots}</ol>"


def now_card(lane: LaneData, signed_in: bool, now: datetime) -> str:
    step = plan.current(lane.steps)
    job = lane.job
    since = f" for {span(now - _dt(step.started_at))}" if step.started_at else ""
    head = f"<div class=now-head><span class=label>{escape(plan.label(step))}</span><span class=muted>{since}</span></div>"
    body = ""
    if step.kind == "checkin" and step.state == "waiting":
        said = plan.last_said(lane.steps)
        body = (
            f"<p>{escape(said)}</p><p><a class=button href='{escape(_site(lane))}'>Try it ↗</a></p>"
            + (
                "<p class=muted>Reply in its Matrix room, or here:</p>"
                + reply_form(job, "Looks good, or what to change…")
                + skip_button(job, step, "Looks good, carry on")
                if signed_in
                else "<p class=muted>Reply in its Matrix room.</p>"
            )
        )
    elif step.started_at is None and job.state in ("queued", "parked"):
        where = f" (number {lane.position} in the queue)" if lane.position else ""
        body = f"<p class=muted>Starts when the worker is free{where}.</p>"
        if step.error:
            body += f"<p class=muted>Last try: {escape(_short(step.error, 200))}</p>"
    elif job.state == "waiting":
        body = f"<p>{escape(step.summary or 'It needs your help: see its Matrix room.')}</p>"
        if step.error:
            body += f"<p class=muted>{escape(_short(step.error, 300))}</p>"
        if signed_in:
            body += reply_form(job, "Your answer or a hint…")
    elif step.kind == "agent":
        said = next((line.text for line in reversed(lane.live) if line.kind == "says"), "")
        body = f"<p class=said>{escape(said)}</p>" if said else ""
        if step.error:
            body += f"<p class=muted>Trying again: {escape(_short(step.error, 200))}</p>"
        if lane.notes:
            body += "<ul class=notes>" + "".join(f"<li>{escape(n)}</li>" for n in lane.notes[-4:]) + "</ul>"
        shown = view(lane.live, signed_in)
        if shown:
            rows = "".join(
                f"<div class='t {line.kind}{' nested' if line.nested else ''}'><time>{escape(line.at)}</time>"
                f"<span>{_mark(line)} {escape(line.text)}</span></div>"
                for line in shown
            )
            body += f"<details class=tail data-key=tail-{job.id} open><summary>Live</summary><div class=log>{rows}</div></details>"
    else:
        body = f"<p>{escape(step.summary or _doing(step))}</p>" + _links(step.links)
        if step.error and step.state == "pending":
            body += f"<p class=muted>Trying again: {escape(_short(step.error, 200))}</p>"
    return f"<section class='now {_tone(lane)}'>{head}{body}</section>"


def next_list(lane: LaneData, signed_in: bool, full: bool) -> str:
    current = plan.current(lane.steps)
    ahead = [s for s in lane.steps if s.state in ("pending",) and s is not current and (current is None or s.position > current.position)]
    skipped_ahead = [s for s in lane.steps if s.id in lane.restorable]
    if not ahead and not skipped_ahead:
        return ""
    rows = [_row(group, lane, signed_in) for group in _groups(sorted(ahead + skipped_ahead, key=lambda s: s.position))]
    shown, rest = (rows, []) if full else (rows[:NEXT_SHOWN], rows[NEXT_SHOWN:])
    more = f"<details data-key=more-{lane.job.id}><summary>{len(rest)} more</summary><ol class=steps>{''.join(rest)}</ol></details>" if rest else ""
    return f"<section class=next><h3>Up next</h3><ol class=steps>{''.join(shown)}</ol>{more}</section>"


def done_list(lane: LaneData, signed_in: bool, now: datetime, full: bool) -> str:
    current = plan.current(lane.steps)
    done = [
        s for s in lane.steps
        if s.state in ("done", "failed") or (s.state == "skipped" and s.id not in lane.restorable)
        if current is None or s.position < current.position
    ]  # fmt: skip
    if not done:
        return ""
    rows = "".join(_row(group, lane, signed_in, now) for group in _groups(done))
    return (
        f"<details class=done data-key=done-{lane.job.id}{' open' if full else ''}><summary>{len(done)} steps done</summary>"
        f"<ol class=steps>{rows}</ol></details>"
    )


def controls(lane: LaneData) -> str:
    p, job = lane.project, lane.job
    if job.state not in ACTIVE:
        return f"<section class=controls>{_pause(p)}</section>"
    selects = "".join(
        f"<form method=post action=/act class=inline><input type=hidden name=action value=setting>"
        f"<input type=hidden name=slug value='{escape(p.slug)}'><input type=hidden name=name value={name}>"
        f"<input type=hidden name=back value='/#p-{escape(p.slug)}'>"
        f"<select name=value aria-label={name} onchange='this.form.submit()'>"
        + "".join(f"<option{' selected' if getattr(p, name) == v else ''}>{v}</option>" for v in values)
        + "</select></form>"
        for name, values in (("model", MODELS), ("effort", EFFORTS))
    )
    return (
        "<section class=controls>"
        + action_form("move", "Run next", job=job.id, value="first")
        + action_form("move", "Run last", job=job.id, value="last")
        + _pause(p)
        + action_form("kill", "Stop job", job=job.id, confirm=f"Stop job {job.id} on {p.name} for good?", danger=True)
        + selects
        + "<details class=note-form><summary class=quiet>Note for the next step</summary>"
        + reply_form(job, "Feedback the next agent step gets…")
        + "</details></section>"
    )


def idle_card(lane: LaneData, signed_in: bool, now: datetime) -> str:
    p, job = lane.project, lane.job
    last = "No jobs yet."
    if job:
        when = f"{span(now - _dt(job.updated_at))} ago" if job.updated_at else ""
        word = {"done": "Done", "killed": "Stopped", "failed": "Failed"}.get(job.state, job.state)
        last = f"<a href='/jobs/{job.id}'>{word} {when}</a>: {escape(_short(job.request, 90))}"
    paused = "<span class='badge paused'>Paused</span>" if p.paused else ""
    return (
        f"<article class=card id='p-{escape(p.slug)}'><h3>{escape(p.name)} {paused}</h3>"
        f"<p class=sub>{escape(_short(p.summary, 120))}</p><p class=sub>{last}</p>"
        f"<div class=out><a href='{escape(_site(lane))}'>Site ↗</a><a href='{escape(_repo(lane))}'>Code ↗</a></div>"
        + (f"<div class=controls>{_pause(p)}</div>" if signed_in else "")
        + "</article>"
    )


# --- pieces ---------------------------------------------------------------------------


def action_form(action: str, label: str, *, job: int | None = None, step: int | None = None, value: str = "",
                slug: str = "", back: str = "", confirm: str = "", danger: bool = False) -> str:  # fmt: skip
    fields = {"action": action, "job": job, "step": step, "value": value, "slug": slug, "back": back}
    hidden = "".join(f"<input type=hidden name={k} value='{escape(str(v))}'>" for k, v in fields.items() if v not in (None, ""))
    check = f" onsubmit='return confirm({escape(_js(confirm))})'" if confirm else ""
    return f"<form method=post action=/act class=inline{check}>{hidden}<button{' class=danger' if danger else ''}>{escape(label)}</button></form>"


def reply_form(job: Job, placeholder: str) -> str:
    return (
        f"<form method=post action=/act class=reply><input type=hidden name=action value=note>"
        f"<input type=hidden name=job value={job.id}><input type=hidden name=back value='/#p-{escape(job.slug)}'>"
        f"<textarea name=value rows=2 placeholder='{escape(placeholder)}' required></textarea><button>Send</button></form>"
    )


def skip_button(job: Job, step: Step, label: str = "Skip") -> str:
    return action_form("skip", label, job=job.id, step=step.id, back=f"/#p-{job.slug}",
                       confirm="" if step.kind == "checkin" else f"Skip {plan.label(step)}?")  # fmt: skip


def _row(group: list[Step], lane: LaneData, signed_in: bool, now: datetime | None = None) -> str:
    first = group[0]
    if first.kind in SHIP:  # pushing, PR, CI, merge, deploy as one row
        chips = "".join(
            f"<span class='chip {s.state}'>{ICONS.get(s.state, '')} {escape(plan.label(s))}</span>" for s in group
        )
        links = "".join(_links(s.links) for s in group if s.state in ("done", "failed"))
        took = _took(group[0], group[-1]) if now else ""
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
    summary = ""
    if s.state != "pending" and s.summary:
        summary = f"<p>{escape(_short(s.summary, 300))}</p>"
    return (
        f"<li class='row {s.state}{' code' if s.kind != 'agent' else ''}'><div class=what><span class=icon>{ICONS.get(s.state, '')}</span>"
        f"<b>{escape(plan.label(s))}</b>{tag}{button}</div>{summary}{_links(s.links)}{_took(s, s) if now else ''}</li>"
    )


def _groups(steps: list[Step]) -> list[list[Step]]:
    groups: list[list[Step]] = []
    for s in steps:
        if s.kind in SHIP and groups and groups[-1][0].kind in SHIP and groups[-1][-1].position == s.position - 1:
            groups[-1].append(s)
        else:
            groups.append([s])
    return groups


def _links(links: dict[str, str]) -> str:
    safe = [(k, v) for k, v in links.items() if v.startswith(("https://", "http://"))]
    if not safe:
        return ""
    return "<div class=links>" + "".join(f"<a href='{escape(v)}'>{escape(k)} ↗</a>" for k, v in safe) + "</div>"


def _out_links(p: Project, lane: LaneData) -> str:
    return f"<a href='{escape(_site(lane))}'>Site ↗</a><a href='{escape(_repo(lane))}'>Code ↗</a>"


def _site(lane: LaneData) -> str:
    return lane.site


def _repo(lane: LaneData) -> str:
    return lane.repo


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


def _doing(step: Step) -> str:
    return {"checks": "Waiting for CI.", "deploy": "Deploying.", "merge": "Merging.", "pr": "Opening a PR."}.get(step.kind, "")


def _took(first: Step, last: Step) -> str:
    if not (first.started_at and last.ended_at):
        return ""
    return f"<time class=took>{last.ended_at[11:16]} · {span(_dt(last.ended_at) - _dt(first.started_at))}</time>"


def _mark(line: Line) -> str:
    return {"says": "💬", "call": "→", "result": "←", "error": "✗"}.get(line.kind, "")


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value) if value else datetime.now(UTC)


def _short(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _js(text: str) -> str:
    import json

    return json.dumps(text)


def read_notes(path: Path, keep: int = 4) -> list[str]:
    try:
        lines = path.read_text(errors="replace").splitlines()
    except FileNotFoundError:
        return []
    return [line.strip() for line in lines if line.strip()][-keep:]


STYLE = """
:root { --bg:#f3f4f7; --card:#fff; --ink:#111827; --muted:#6b7280; --line:#e5e7eb; --blue:#2563eb; --green:#16a34a;
  --amber:#d97706; --red:#dc2626; --grey:#9ca3af; --radius:14px; color-scheme: light dark; }
@media (prefers-color-scheme: dark) { :root { --bg:#0b1020; --card:#131a2e; --ink:#e5e7eb; --muted:#94a3b8; --line:#243049; } }
* { box-sizing:border-box; }
body { margin:0; font:15px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif; background:var(--bg); color:var(--ink); }
a { color:var(--blue); text-decoration:none; }
h2, h3 { margin:0; }
.top { position:sticky; top:0; z-index:5; display:flex; align-items:center; justify-content:space-between; gap:.5rem;
  padding:.6rem max(1rem, env(safe-area-inset-left)); background:#0f172a; color:#fff; }
.brand { color:#fff; font-weight:700; font-size:1.15rem; letter-spacing:.02em; }
.top nav { display:flex; gap:.4rem; align-items:center; }
.top button, .top summary { color:#fff; }
main { max-width:52rem; margin:0 auto; padding:1rem max(.75rem, env(safe-area-inset-left)) 4rem; }
.banner { max-width:52rem; margin:.75rem auto 0; padding:.6rem 1rem; border-radius:var(--radius); }
.flash { position:fixed; z-index:10; left:50%; bottom:max(1rem, env(safe-area-inset-bottom)); transform:translateX(-50%);
  width:max-content; max-width:calc(100% - 2rem); padding:.6rem 1rem; border-radius:999px; box-shadow:0 6px 24px rgb(0 0 0 / .25);
  transition:opacity .4s; }
.banner.paused { background:#fef3c7; color:#92400e; }
.flash { background:#dbeafe; color:#1e3a8a; }
.queue ol { list-style:none; padding:0; margin:.5rem 0 1rem; display:flex; gap:.5rem; overflow-x:auto; }
.queue li a { display:flex; gap:.4rem; align-items:baseline; white-space:nowrap; padding:.35rem .7rem; border-radius:999px;
  background:var(--card); border:1px solid var(--line); color:var(--ink); }
.queue li span { color:var(--muted); font-size:.85em; }
.queue h2, .idle > h2 { font-size:.8rem; text-transform:uppercase; letter-spacing:.08em; color:var(--muted); }
.lane, .card { scroll-margin-top:4rem; }
.lane { background:var(--card); border-radius:var(--radius); padding:1rem; margin-bottom:1rem; border-left:5px solid var(--grey);
  box-shadow:0 1px 2px rgb(0 0 0 / .06); }
.lane.running { border-left-color:var(--blue); } .lane.waiting { border-left-color:var(--amber); }
.lane.trouble { border-left-color:var(--red); } .lane.queued { border-left-color:#818cf8; } .lane.paused { border-left-color:var(--grey); }
.lane-head { display:flex; justify-content:space-between; gap:.75rem; align-items:flex-start; }
.lane-head h2 { font-size:1.15rem; } .lane-head h2 a { color:inherit; }
.sub, .muted { color:var(--muted); font-size:.88em; margin:.15rem 0 0; }
.out { display:flex; gap:.6rem; flex-shrink:0; font-size:.9em; }
.request { color:var(--muted); font-style:italic; margin:.5rem 0; }
.strip { list-style:none; display:flex; flex-wrap:wrap; gap:4px; padding:0; margin:.6rem 0; }
.strip li { width:14px; height:14px; border-radius:50%; border:2px solid var(--grey); }
.strip li.code { border-radius:4px; }
.strip li.done { background:var(--green); border-color:var(--green); }
.strip li.skipped { background:repeating-linear-gradient(45deg, var(--grey) 0 2px, transparent 2px 4px); border-color:var(--grey); }
.strip li.failed { background:var(--red); border-color:var(--red); }
.strip li.running { background:var(--blue); border-color:var(--blue); animation:pulse 1.4s ease-in-out infinite; }
.strip li.waiting { background:var(--amber); border-color:var(--amber); animation:pulse 2s ease-in-out infinite; }
@keyframes pulse { 50% { transform:scale(1.35); opacity:.6; } }
.progress { display:flex; gap:.5rem; align-items:center; margin:.25rem 0 .75rem; font-size:.9em; color:var(--muted); }
.badge { display:inline-block; padding:.1rem .55rem; border-radius:999px; font-size:.78rem; font-weight:600; background:var(--line); color:var(--ink); }
.badge.running { background:#dbeafe; color:#1e40af; } .badge.waiting { background:#fef3c7; color:#92400e; }
.badge.trouble { background:#fee2e2; color:#991b1b; } .badge.paused { background:var(--line); color:var(--muted); }
.badge.finished { background:#dcfce7; color:#166534; }
.now { border-radius:12px; padding:.75rem .85rem; margin-bottom:.75rem; background:rgb(37 99 235 / .07); border:1px solid rgb(37 99 235 / .2); }
.now.waiting { background:rgb(217 119 6 / .09); border-color:rgb(217 119 6 / .3); }
.now.trouble { background:rgb(220 38 38 / .07); border-color:rgb(220 38 38 / .3); }
.now.queued, .now.paused { background:transparent; border-color:var(--line); }
.now-head { display:flex; justify-content:space-between; align-items:baseline; gap:.5rem; }
.now-head .label { font-weight:700; font-size:1.05rem; }
.now p { margin:.4rem 0; }
.said { font-size:1rem; }
.notes { margin:.4rem 0; padding-left:1.1rem; } .notes li::marker { content:"▸ "; color:var(--blue); }
.tail summary { cursor:pointer; color:var(--muted); font-size:.85em; }
.log { font:12px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace; background:#0f172a; color:#cbd5e1; border-radius:10px;
  padding:.5rem .6rem; margin-top:.35rem; max-height:16rem; overflow:auto; }
.t { display:flex; gap:.5rem; } .t time { color:#64748b; flex-shrink:0; min-width:2.6rem; }
.t span { overflow-wrap:anywhere; } .t.says span { color:#f1f5f9; } .t.error span { color:#fca5a5; } .t.nested { padding-left:1rem; opacity:.85; }
.next h3, .done summary { font-size:.8rem; text-transform:uppercase; letter-spacing:.08em; color:var(--muted); margin:.5rem 0 .3rem; }
.done summary, .next details summary { cursor:pointer; }
.next details summary { font-size:.85em; color:var(--muted); padding:.4rem 0; border-top:1px solid var(--line); }
.steps { list-style:none; padding:0; margin:0; }
.row { padding:.45rem 0; border-top:1px solid var(--line); }
.row .what { display:flex; gap:.4rem; align-items:center; flex-wrap:wrap; }
.row p { margin:.2rem 0 0 1.4rem; color:var(--muted); font-size:.9em; }
.row .links, .row .took { margin-left:1.4rem; }
.icon { width:1rem; text-align:center; color:var(--green); } .row.failed .icon { color:var(--red); } .row.skipped .icon { color:var(--grey); }
.row.pending b { font-weight:500; } .row.skipped b { text-decoration:line-through; color:var(--muted); }
.opt { font-size:.72rem; color:var(--muted); border:1px solid var(--line); border-radius:999px; padding:0 .4rem; }
.chips { display:inline-flex; flex-wrap:wrap; gap:.25rem; }
.chip { font-size:.78rem; padding:.05rem .45rem; border-radius:999px; border:1px solid var(--line); color:var(--muted); }
.chip.done { background:#dcfce7; color:#166534; border-color:transparent; } .chip.failed { background:#fee2e2; color:#991b1b; border-color:transparent; }
.chip.running { background:#dbeafe; color:#1e40af; border-color:transparent; }
.links { display:flex; flex-wrap:wrap; gap:.25rem .7rem; font-size:.85em; margin-top:.2rem; }
.took { display:block; font-size:.8em; color:var(--muted); }
.controls { display:flex; flex-wrap:wrap; gap:.4rem; margin-top:.75rem; padding-top:.75rem; border-top:1px dashed var(--line); }
form.inline { display:inline; margin:0; }
button, .button, select { font:inherit; font-size:.88rem; min-height:36px; padding:.35rem .8rem; border-radius:10px; border:1px solid var(--line);
  background:var(--card); color:var(--ink); cursor:pointer; }
.button { display:inline-flex; align-items:center; background:var(--blue); color:#fff; border-color:var(--blue); }
button.danger { color:var(--red); }
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
.card { background:var(--card); border-radius:var(--radius); padding:.85rem; box-shadow:0 1px 2px rgb(0 0 0 / .06); }
.card h3 { font-size:1rem; } .card .out { margin-top:.5rem; }
.empty { color:var(--muted); text-align:center; padding:2rem 0; }
table { border-collapse:collapse; width:100%; font-size:.88em; } th, td { text-align:left; padding:.35rem .4rem; border-bottom:1px solid var(--line); }
.runs { overflow-x:auto; background:var(--card); border-radius:var(--radius); padding:.5rem; }
@media (max-width:480px) { .lane-head { flex-direction:column; } .lane { padding:.85rem; } }
"""

# Refresh the lanes every 10 seconds without losing what's open or being typed.
SCRIPT = """
(() => {
  const main = document.getElementById('lanes');
  const busy = () => document.hidden || main.contains(document.activeElement) &&
    ['TEXTAREA','INPUT','SELECT'].includes(document.activeElement.tagName);
  async function refresh() {
    if (busy()) return;
    const open = new Set([...main.querySelectorAll('details[data-key]')].filter(d => d.open).map(d => d.dataset.key));
    const closed = new Set([...main.querySelectorAll('details[data-key]')].filter(d => !d.open).map(d => d.dataset.key));
    const logs = {}; main.querySelectorAll('.log').forEach(l => { logs[l.closest('details').dataset.key] = l.scrollHeight - l.scrollTop - l.clientHeight < 20; });
    try {
      const r = await fetch(location.pathname + '?part=lanes', {cache: 'no-store'});
      if (!r.ok) return;
      main.innerHTML = await r.text();
    } catch (e) { return; }
    main.querySelectorAll('details[data-key]').forEach(d => {
      if (open.has(d.dataset.key)) d.open = true;
      if (closed.has(d.dataset.key)) d.open = false;
    });
    main.querySelectorAll('.log').forEach(l => { if (logs[l.closest('details').dataset.key] !== false) l.scrollTop = l.scrollHeight; });
  }
  main.querySelectorAll('.log').forEach(l => l.scrollTop = l.scrollHeight);
  setInterval(refresh, 10000);
  const flash = document.querySelector('.flash');
  if (flash) { setTimeout(() => flash.style.opacity = 0, 5000); setTimeout(() => flash.remove(), 5500); }
  if (location.search.includes('msg=')) history.replaceState(null, '', location.pathname + location.hash);
})();
"""
