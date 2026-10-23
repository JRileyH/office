"""The public status page at office.<base domain>, and the health check.

`/` shows what each unfinished job has done, is doing and will do next, step by step,
with links to what the steps made (the repo, issue, PRs, commits, CI runs, the site).
`/jobs/<id>` shows one job, finished or not. Run logs stay on the box (bin/watch.sh):
the page is public and they hold everything the workers saw.
"""

from __future__ import annotations

from datetime import UTC, datetime
from html import escape

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route

from . import plan
from .config import Settings
from .scheduler import Scheduler
from .store import ACTIVE, Job, Step, Store
from .watch import span

STYLE = """
body { font: 15px/1.5 system-ui, sans-serif; max-width: 60rem; margin: 2rem auto; padding: 0 1rem; color: #222; }
table { border-collapse: collapse; width: 100%; margin-bottom: 2rem; }
th, td { text-align: left; padding: .3rem .6rem; border-bottom: 1px solid #ddd; vertical-align: top; }
th { font-weight: 600; }
a { color: #0b57d0; }
.muted, .pending td { color: #888; }
.job { margin-bottom: 2.5rem; }
.job h3 { margin-bottom: .2rem; }
.job p { margin: 0 0 .6rem; }
.steps td:first-child { width: 1.5rem; text-align: center; }
.steps td:nth-child(2) { white-space: nowrap; }
.running td, .waiting td { background: #fff7d6; }
.failed td:first-child { color: #c00; }
.links a { margin-right: .6rem; white-space: nowrap; }
"""
ICONS = {"done": "✓", "skipped": "–", "running": "▶", "waiting": "?", "failed": "✗", "pending": "○"}


def create_app(settings: Settings, store: Store, scheduler: Scheduler) -> Starlette:
    def page(title: str, body: str) -> HTMLResponse:
        return HTMLResponse(
            f'<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
            f"<title>{escape(title)}</title><style>{STYLE}</style>{body}"
        )

    def job_block(job: Job, now: datetime, heading: str = "h3") -> str:
        project = store.project(job.slug)
        steps = plan.planned(settings, store, job)
        name = escape(project.name) if project else escape(job.slug)
        what = "new project" if job.kind == "new" else "change"
        state = _state(store, job, steps)
        return (
            f"<div class=job><{heading}><a href='/jobs/{job.id}'>#{job.id}</a> {name}: {what}</{heading}>"
            f"<p>{escape(state)} · {escape(plan.where(steps))} · {job.playbook}"
            + (f", {escape(project.model)}/{escape(project.effort)}" if project else "")
            + f"</p><p class=muted>“{escape(_short(job.request, 300))}”</p>"
            f"{steps_table(steps, now)}</div>"
        )

    def steps_table(steps: list[Step], now: datetime) -> str:
        rows = "".join(
            f"<tr class={s.state}><td>{ICONS.get(s.state, '')}</td><td>{escape(plan.label(s))}</td>"
            f"<td>{escape(s.summary or '')}{_error(s)}<div class=links>{_links(s.links)}</div></td>"
            f"<td class=muted>{_when(s, now)}</td></tr>"
            for s in steps
        )
        return f"<table class=steps>{rows}</table>"

    async def healthz(request: Request) -> JSONResponse:
        return JSONResponse({"ok": True, "harness": settings.harness})

    async def index(request: Request) -> HTMLResponse:
        now = datetime.now(UTC)
        projects = store.projects()
        names = {p.slug: p.name for p in projects}
        paused = "<p><strong>Work is paused.</strong></p>" if scheduler.paused else ""
        active = "".join(job_block(j, now) for j in store.jobs(ACTIVE))
        finished = [j for j in store.jobs(limit=30) if j.state not in ACTIVE]

        project_rows = "".join(
            f"<tr><td>{escape(p.name)}{' (paused)' if p.paused else ''}</td>"
            f"<td><a href='{escape(settings.project_url(p.slug))}'>{escape(p.slug)}</a></td>"
            f"<td><a href='{escape(settings.repo_url(p.slug))}'>repo</a></td>"
            f"<td>{escape(p.summary)}</td></tr>"
            for p in projects
        )
        job_rows = "".join(
            f"<tr><td><a href='/jobs/{j.id}'>#{j.id}</a></td><td>{escape(names.get(j.slug, j.slug))}</td><td>{j.kind}</td>"
            f"<td>{j.state}</td><td>{escape(_short(j.request, 80))}</td><td class=muted>{j.updated_at[:16].replace('T', ' ')}</td></tr>"
            for j in finished
        )
        return page(
            "office",
            f"<h1>office</h1>{paused}"
            f"<h2>Now</h2>{active or '<p class=muted>Nothing under way.</p>'}"
            "<h2>Projects</h2><table><tr><th>Name</th><th>Site</th><th>Code</th><th>Summary</th></tr>"
            f"{project_rows or '<tr><td colspan=4 class=muted>None yet.</td></tr>'}</table>"
            "<h2>Finished jobs</h2><table><tr><th>#</th><th>Project</th><th>Kind</th><th>State</th><th>Request</th><th>Updated</th></tr>"
            f"{job_rows or '<tr><td colspan=6 class=muted>None yet.</td></tr>'}</table>",
        )

    async def job_page(request: Request) -> HTMLResponse:
        job = store.job(request.path_params["job_id"])
        if job is None:
            return HTMLResponse("No such job.", 404)
        run_rows = "".join(
            f"<tr><td>{escape(r['phase'])}</td><td>{escape(r['model'])}</td>"
            f"<td>{escape(str(r['outcome'] or 'running'))}</td><td>{r['num_turns'] or ''}</td>"
            f"<td>{_tokens(r)}</td><td class=muted>{r['started_at'][:16].replace('T', ' ')}</td></tr>"
            for r in store.runs(job.id)
        )
        return page(
            f"office: job {job.id}",
            f"<p><a href='/'>office</a></p>{job_block(job, datetime.now(UTC), 'h1')}"
            f"<h2>Agent runs</h2><p class=muted>Every step a run took: <code>bin/watch.sh {job.id}</code> on the box.</p>"
            "<table><tr><th>Run</th><th>Model</th><th>Outcome</th><th>Turns</th><th>Tokens in/out</th><th>Started</th></tr>"
            f"{run_rows or '<tr><td colspan=6 class=muted>None yet.</td></tr>'}</table>",
        )

    return Starlette(
        routes=[Route("/healthz", healthz), Route("/", index), Route("/jobs/{job_id:int}", job_page)]
    )


def _state(store: Store, job: Job, steps: list[Step]) -> str:
    step = plan.current(steps)
    match job.state:
        case "waiting" if step and step.kind == "checkin":
            return "waiting for you to try it"
        case "waiting":
            return "waiting on you"
        case "parked" if not store.steps(job.id):
            return "starting shortly"
        case "parked" if step and step.state == "running" and not step.error:
            return "waiting for CI"
        case "parked":
            return "retrying soon"
    return job.state


def _links(links: dict[str, str]) -> str:
    return "".join(
        f"<a href='{escape(url)}'>{escape(label)}</a>"
        for label, url in links.items()
        if url.startswith(("https://", "http://"))
    )


def _error(step: Step) -> str:
    if not step.error or step.state in ("done", "failed"):  # a failure's CI log stays off the public page
        return ""
    return f"<div class=muted>{escape(_short(step.error, 300))}</div>"


def _when(step: Step, now: datetime) -> str:
    if step.state in ("running", "waiting") and step.started_at:
        return f"for {span(now - datetime.fromisoformat(step.started_at))}"
    if step.ended_at and step.started_at:
        took = span(datetime.fromisoformat(step.ended_at) - datetime.fromisoformat(step.started_at))
        return f"{step.ended_at[11:16]} ({took})"
    return ""


def _short(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _tokens(run: dict) -> str:
    if run["input_tokens"] is None and run["output_tokens"] is None:
        return ""
    return f"{run['input_tokens'] or 0:,} / {run['output_tokens'] or 0:,}"
