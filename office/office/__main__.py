"""Phase 1 placeholder: a health endpoint and a status page stub. Behavior comes in Phase 2."""

import os

import uvicorn
from starlette.applications import Starlette
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route

HARNESS = os.environ.get("HARNESS", "claude")


async def healthz(request):
    return JSONResponse({"ok": True, "harness": HARNESS})


async def index(request):
    return HTMLResponse("<!doctype html><title>office</title><h1>office</h1><p>Nothing queued yet.</p>")


app = Starlette(routes=[Route("/healthz", healthz), Route("/", index)])

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000, access_log=False)
