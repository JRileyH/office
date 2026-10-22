"""HTTP API. Internal network only; every call but /healthz needs the shared secret."""

from __future__ import annotations

import asyncio
import hmac
from collections.abc import Callable

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from .audit import Audit
from .deploy import Deployer, DeployFailed
from .validate import Rejected

SECRET_HEADER = "X-Deployer-Secret"


def create_app(deployer: Deployer, audit: Audit, secret: str) -> Starlette:
    # One change at a time: deploys and stops are serialized.
    lock = asyncio.Lock()

    async def handle(request: Request, action: str, op: Callable[[], dict], fields: dict, mutates: bool) -> JSONResponse:
        if not hmac.compare_digest(request.headers.get(SECRET_HEADER, "").encode(), secret.encode()):
            audit.write(action, "unauthorized", **fields)
            return JSONResponse({"ok": False, "reasons": [f"missing or wrong {SECRET_HEADER}"]}, 401)
        try:
            if mutates:
                async with lock:
                    result = await run_in_threadpool(op)
            else:
                result = await run_in_threadpool(op)
        except Rejected as e:
            audit.write(action, "rejected", **fields, reasons=e.reasons)
            return JSONResponse({"ok": False, "reasons": e.reasons}, 422)
        except DeployFailed as e:
            audit.write(action, "failed", **fields, error=e.message)
            return JSONResponse({"ok": False, "error": e.message, "logs": e.logs}, 502)
        except Exception as e:
            audit.write(action, "error", **fields, error=type(e).__name__)
            return JSONResponse({"ok": False, "error": f"internal error ({type(e).__name__})"}, 500)
        audit.write(action, "ok", **fields)
        return JSONResponse({"ok": True, **result})

    async def body(request: Request, keys: set[str]) -> dict:
        try:
            data = await request.json()
        except ValueError:
            data = None
        if not isinstance(data, dict) or set(data) != keys:
            raise Rejected([f"body must be a JSON object with exactly: {', '.join(sorted(keys))}"])
        return data

    async def deploy(request: Request) -> JSONResponse:
        try:
            data = await body(request, {"slug", "sha"})
        except Rejected as e:
            return await handle(request, "deploy", _raise(e), {}, mutates=True)
        fields = {"slug": _short(data["slug"]), "sha": _short(data["sha"])}
        return await handle(request, "deploy", lambda: deployer.deploy(data["slug"], data["sha"]), fields, mutates=True)

    async def stop(request: Request) -> JSONResponse:
        try:
            data = await body(request, {"slug"})
        except Rejected as e:
            return await handle(request, "stop", _raise(e), {}, mutates=True)
        return await handle(request, "stop", lambda: deployer.stop(data["slug"]), {"slug": _short(data["slug"])}, mutates=True)

    async def status(request: Request) -> JSONResponse:
        slug = request.query_params.get("slug")
        return await handle(request, "status", lambda: deployer.status(slug), {"slug": _short(slug)}, mutates=False)

    async def logs(request: Request) -> JSONResponse:
        q = request.query_params
        slug, service, tail = q.get("slug"), q.get("service"), q.get("tail")
        fields = {"slug": _short(slug), "service": _short(service)}
        return await handle(request, "logs", lambda: deployer.logs(slug, service, tail), fields, mutates=False)

    async def healthz(request: Request) -> JSONResponse:
        return JSONResponse({"ok": True})

    return Starlette(
        routes=[
            Route("/deploy", deploy, methods=["POST"]),
            Route("/stop", stop, methods=["POST"]),
            Route("/status", status, methods=["GET"]),
            Route("/logs", logs, methods=["GET"]),
            Route("/healthz", healthz, methods=["GET"]),
        ]
    )


def _raise(error: Exception) -> Callable[[], dict]:
    def op() -> dict:
        raise error

    return op


def _short(value: object) -> object:
    """Request values go into the audit log as given, but bounded."""
    if isinstance(value, str) and len(value) > 80:
        return value[:80] + "…"
    return value
