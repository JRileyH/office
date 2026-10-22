"""The public status page at office.<base domain>, and the health check."""

from __future__ import annotations

from html import escape

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route

from .config import Settings
from .scheduler import Scheduler
from .store import Store

STYLE = """
body { font: 15px/1.5 system-ui, sans-serif; max-width: 60rem; margin: 2rem auto; padding: 0 1rem; color: #222; }
table { border-collapse: collapse; width: 100%; margin-bottom: 2rem; }
th, td { text-align: left; padding: .3rem .6rem; border-bottom: 1px solid #ddd; vertical-align: top; }
th { font-weight: 600; }
.muted { color: #777; }
"""


def create_app(settings: Settings, store: Store, scheduler: Scheduler) -> Starlette:
    async def healthz(request: Request) -> JSONResponse:
        return JSONResponse({"ok": True, "harness": settings.harness})

    async def index(request: Request) -> HTMLResponse:
        projects = store.projects()
        jobs = store.jobs(limit=30)
        runs = store.runs(limit=30)
        names = {p.slug: p.name for p in projects}
        paused = "<p><strong>Work is paused.</strong></p>" if scheduler.paused else ""

        project_rows = "".join(
            f"<tr><td>{escape(p.name)}{' (paused)' if p.paused else ''}</td>"
            f"<td><a href='{escape(settings.project_url(p.slug))}'>{escape(p.slug)}</a></td>"
            f"<td>{escape(p.summary)}</td></tr>"
            for p in projects
        )
        job_rows = "".join(
            f"<tr><td>{j.id}</td><td>{escape(names.get(j.slug, j.slug))}</td><td>{j.kind}</td>"
            f"<td>{escape(_phase(scheduler, j))}</td><td>{j.state}</td><td class='muted'>{j.updated_at}</td></tr>"
            for j in jobs
        )
        run_rows = "".join(
            f"<tr><td>{r['job_id']}</td><td>{escape(r['phase'])}</td><td>{escape(r['model'])}</td>"
            f"<td>{escape(str(r['outcome'] or 'running'))}</td><td>{r['num_turns'] or ''}</td>"
            f"<td>{_tokens(r)}</td><td class='muted'>{r['started_at']}</td></tr>"
            for r in runs
        )
        html = f"""<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>office</title><style>{STYLE}</style>
<h1>office</h1>{paused}
<h2>Projects</h2>
<table><tr><th>Name</th><th>Site</th><th>Summary</th></tr>{project_rows or "<tr><td colspan=3 class=muted>None yet.</td></tr>"}</table>
<h2>Jobs</h2>
<table><tr><th>#</th><th>Project</th><th>Kind</th><th>Phase</th><th>State</th><th>Updated</th></tr>{job_rows or "<tr><td colspan=6 class=muted>None yet.</td></tr>"}</table>
<h2>Recent runs</h2>
<table><tr><th>Job</th><th>Phase</th><th>Model</th><th>Outcome</th><th>Turns</th><th>Tokens in/out</th><th>Started</th></tr>{run_rows or "<tr><td colspan=7 class=muted>None yet.</td></tr>"}</table>
"""
        return HTMLResponse(html)

    return Starlette(routes=[Route("/healthz", healthz), Route("/", index)])


def _phase(scheduler: Scheduler, job) -> str:
    try:
        phases = scheduler.playbook(job.playbook).phases
    except (OSError, ValueError):
        return str(job.phase)
    return phases[job.phase].name if job.phase < len(phases) else "finished"


def _tokens(run: dict) -> str:
    if run["input_tokens"] is None and run["output_tokens"] is None:
        return ""
    return f"{run['input_tokens'] or 0:,} / {run['output_tokens'] or 0:,}"
