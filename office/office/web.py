"""The status page at office.<base domain>, and the health check.

Anyone can look: one swim lane per project with its job's steps, what the running step
is doing, and what comes next (pages.py). Signing in with OFFICE_WEB_PASSWORD gives a
cookie good for 24 hours that allows changes (control.py): skipping optional steps,
reordering the queue, pausing, stopping, settings and notes.

The cookie is an expiry signed with a key made from a random secret in the database and
the password, so changing the password signs everyone out. It's HttpOnly and
SameSite=Strict, and changes must come from this site's own pages (Origin check).
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from collections import deque
from datetime import UTC, datetime
from html import escape
from urllib.parse import parse_qs, quote

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from . import pages, plan
from .config import Settings
from .control import Control, ControlError
from .live import LiveRuns
from .pages import LaneData
from .scheduler import Scheduler
from .store import ACTIVE, Job, Store

COOKIE = "office_token"
TOKEN_SECONDS = 24 * 3600
MAX_FAILURES = 10  # wrong passwords in FAILURE_WINDOW before sign-in is refused for a while
FAILURE_WINDOW = 15 * 60


class Auth:
    def __init__(self, settings: Settings, store: Store, clock=time.time):
        self.settings = settings
        self.store = store
        self.clock = clock
        self.failures: deque[float] = deque()

    def key(self) -> bytes:
        secret = self.store.get("web_secret")
        if not secret:
            secret = secrets.token_hex(32)
            self.store.put("web_secret", secret)
        return hashlib.sha256(f"{secret}:{self.settings.web_password}".encode()).digest()

    def token(self) -> str:
        expires = str(int(self.clock()) + TOKEN_SECONDS)
        return f"{expires}.{hmac.new(self.key(), expires.encode(), hashlib.sha256).hexdigest()}"

    def valid(self, token: str | None) -> bool:
        expires, _, signature = (token or "").partition(".")
        if not expires.isdigit() or int(expires) < self.clock():
            return False
        expected = hmac.new(self.key(), expires.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(signature, expected)

    def check_password(self, password: str) -> bool | None:
        """True or False; None while too many wrong guesses are recent."""
        now = self.clock()
        while self.failures and self.failures[0] < now - FAILURE_WINDOW:
            self.failures.popleft()
        if len(self.failures) >= MAX_FAILURES:
            return None
        if hmac.compare_digest(password.encode(), self.settings.web_password.encode()):
            return True
        self.failures.append(now)
        return False


def create_app(settings: Settings, store: Store, scheduler: Scheduler) -> Starlette:
    auth = Auth(settings, store)
    control = Control(store, scheduler)
    live = LiveRuns()
    secure = settings.base_domain != "localhost"

    def signed_in(request: Request) -> bool:
        return auth.valid(request.cookies.get(COOKIE))

    def lane(job: Job | None, project, position: int | None, signed: bool) -> LaneData:
        steps = plan.planned(settings, store, job) if job else []
        current = plan.current(steps)
        lines, notes = [], []
        if job and current and current.kind == "agent" and job.state == "running":
            runs = store.runs(job.id)
            if runs and runs[-1]["outcome"] is None:
                lines = live.lines(runs[-1])
            notes = pages.read_notes(scheduler.progress_path(job))
        active = job is not None and job.state in ACTIVE
        return LaneData(
            project=project,
            job=job,
            steps=steps,
            state=_state(store, job, steps) if job else "idle",
            live=lines,
            notes=notes,
            skippable={s.id for s in steps if s.id and active and signed and control.skippable(job, s.id)},
            restorable={s.id for s in steps if s.id and active and signed and control.restorable(job, s.id)},
            position=position,
            site=settings.project_url(project.slug),
            repo=settings.repo_url(project.slug),
        )

    def queue_order() -> list[Job]:
        """Unfinished jobs in the order the worker will take them, the running one first."""
        jobs = store.jobs(ACTIVE)
        running = [j for j in jobs if j.state == "running"]
        ready = [j for j in jobs if j.state in ("queued", "parked") and not store.project(j.slug).paused]
        return running + ready

    def overview_html(signed: bool) -> str:
        now = datetime.now(UTC)
        order = {j.id: i for i, j in enumerate(queue_order(), start=1)}
        lanes, busy = [], set()
        active = sorted(store.jobs(ACTIVE), key=lambda j: (order.get(j.id, 999), j.id))
        for job in active:
            if job.slug in busy:
                continue
            busy.add(job.slug)
            lanes.append(lane(job, store.project(job.slug), order.get(job.id), signed))
        idle = []
        for project in store.projects():
            if project.slug not in busy:
                last = next((j for j in store.jobs(limit=200) if j.slug == project.slug), None)
                idle.append(lane(last, project, None, signed))
        live.forget({r["id"] for j in active for r in store.runs(j.id)[-1:]})
        return pages.overview(lanes, idle, signed, now)

    def job_html(job: Job, signed: bool) -> str:
        project = store.project(job.slug)
        order = {j.id: i for i, j in enumerate(queue_order(), start=1)}
        body = pages.lane_html(lane(job, project, order.get(job.id), signed), signed, datetime.now(UTC), full=True)
        runs = "".join(
            f"<tr><td>{escape(r['phase'])}</td><td>{escape(r['model'])}</td><td>{escape(str(r['outcome'] or 'running'))}</td>"
            f"<td>{r['num_turns'] or ''}</td><td>{_tokens(r)}</td><td>{r['started_at'][:16].replace('T', ' ')}</td></tr>"
            for r in store.runs(job.id)
        )
        return (
            body + "<h3 class=sub>Agent runs</h3><div class=runs><table><tr><th>Run</th><th>Model</th><th>Outcome</th>"
            f"<th>Turns</th><th>Tokens in/out</th><th>Started</th></tr>{runs}</table></div>"
            f"<p class=sub>Everything a run did: <code>bin/watch.sh {job.id}</code> on the box.</p>"
        )

    def respond(request: Request, title: str, body: str, signed: bool) -> Response:
        if request.query_params.get("part") == "lanes":
            return HTMLResponse(body, headers={"Cache-Control": "no-store"})
        flash = request.query_params.get("msg", "")[:300]
        html = pages.page(title, body, signed, scheduler.paused, flash, back=request.url.path)
        return HTMLResponse(html, headers={"Cache-Control": "no-store"})

    async def healthz(request: Request) -> JSONResponse:
        return JSONResponse({"ok": True, "harness": settings.harness})

    async def index(request: Request) -> Response:
        signed = signed_in(request)
        return respond(request, "office", overview_html(signed), signed)

    async def job_page(request: Request) -> Response:
        job = store.job(request.path_params["job_id"])
        if job is None:
            return HTMLResponse("No such job.", 404)
        signed = signed_in(request)
        return respond(request, f"office: {store.project(job.slug).name}", job_html(job, signed), signed)

    async def login(request: Request) -> Response:
        form = await _form(request)
        if not _same_site(request):
            return HTMLResponse("Sign in from the status page.", 403)
        ok = auth.check_password(form.get("password", ""))
        if ok is None:
            return _back(form, "Too many wrong passwords. Try again in a while.")
        if not ok:
            return _back(form, "Wrong password.")
        response = _back(form, "Signed in for 24 hours.")
        response.set_cookie(COOKIE, auth.token(), max_age=TOKEN_SECONDS, httponly=True, samesite="strict", secure=secure)
        return response

    async def logout(request: Request) -> Response:
        response = _back(await _form(request), "Signed out.")
        response.delete_cookie(COOKIE, httponly=True, samesite="strict", secure=secure)
        return response

    async def act(request: Request) -> Response:
        form = await _form(request)
        if not signed_in(request) or not _same_site(request):
            return _back(form, "Sign in first.")
        action, job, step = form.get("action", ""), _int(form.get("job")), _int(form.get("step"))
        slug, value = form.get("slug", ""), form.get("value", "")
        try:
            match action:
                case "skip":
                    message = await control.skip(job, step)
                case "restore":
                    message = await control.restore(job, step)
                case "move":
                    message = await control.move(job, value)
                case "pause" | "resume":
                    message = await control.pause(slug, action == "pause")
                case "pause-all" | "resume-all":
                    message = control.pause_all(action == "pause-all")
                case "kill":
                    message = await control.kill(job)
                case "setting":
                    message = await control.setting(slug, form.get("name", ""), value)
                case "note":
                    message = await control.note(job, value)
                case _:
                    message = "Unknown action."
        except ControlError as e:
            message = str(e)
        return _back(form, message)

    return Starlette(
        routes=[
            Route("/healthz", healthz),
            Route("/", index),
            Route("/jobs/{job_id:int}", job_page),
            Route("/login", login, methods=["POST"]),
            Route("/logout", logout, methods=["POST"]),
            Route("/act", act, methods=["POST"]),
        ]
    )


async def _form(request: Request) -> dict[str, str]:
    """A plain URL-encoded form, read without needing python-multipart."""
    body = (await request.body())[:20_000].decode(errors="replace")
    return {k: v[0] for k, v in parse_qs(body, keep_blank_values=True).items()}


def _same_site(request: Request) -> bool:
    """A change has to come from this site's own page, not a form on someone else's."""
    origin = request.headers.get("origin")
    if origin is None or origin == "null":
        return origin is None  # no Origin: a same-site navigation by an older browser
    host = request.headers.get("host", "")
    return origin.split("://", 1)[-1] == host


def _back(form: dict[str, str], message: str) -> RedirectResponse:
    back = form.get("back", "/")
    if not back.startswith("/") or back.startswith("//"):
        back = "/"
    path, _, anchor = back.partition("#")
    return RedirectResponse(f"{path}?msg={quote(message)}" + (f"#{anchor}" if anchor else ""), status_code=303)


def _int(value: str | None) -> int:
    return int(value) if value and value.isdigit() else 0


def _state(store: Store, job: Job, steps) -> str:
    step = plan.current(steps)
    project = store.project(job.slug)
    if job.state not in ACTIVE:
        return {"done": "done", "killed": "stopped", "failed": "failed"}.get(job.state, job.state)
    if project and project.paused:
        return "paused"
    match job.state:
        case "waiting" if step and step.kind == "checkin":
            return "waiting for you to try it"
        case "waiting":
            return "waiting for you"
        case "running":
            return f"working: {plan.label(step)}" if step else "working"
        case "parked" if not store.steps(job.id):
            return "starting shortly"
        case "parked" if step and step.state == "running" and not step.error:
            return "waiting for CI"
        case "parked":
            return "retrying soon"
    return "queued"


def _tokens(run: dict) -> str:
    if run["input_tokens"] is None and run["output_tokens"] is None:
        return ""
    return f"{run['input_tokens'] or 0:,} / {run['output_tokens'] or 0:,}"
