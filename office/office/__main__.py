"""office: the Matrix bot, the scheduler and the status page, in one process."""

from __future__ import annotations

import asyncio
import logging
import os

import uvicorn

from .bot import Bot
from .config import Settings
from .deployer_client import DeployerClient
from .harness import ClaudeHarness, FakeHarness
from .matrix import Matrix
from .prompts import Prompts
from .scheduler import Scheduler
from .store import Store
from .web import create_app
from .workspace import GitHubWorkspace, LocalWorkspace, configure_git

log = logging.getLogger("office")


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # a line per Matrix sync otherwise
    settings = Settings.from_env()
    settings.state_dir.mkdir(parents=True, exist_ok=True)
    store = Store(settings.state_dir / "office.db")

    if settings.harness == "fake":
        harness, workspace = FakeHarness(), LocalWorkspace(settings.workspace_dir)
    else:
        harness, workspace = ClaudeHarness(), GitHubWorkspace(settings.workspace_dir, settings.org)
        await configure_git(f"office@{settings.base_domain}")

    chat = Matrix(settings.matrix_url, settings.matrix_user, settings.matrix_password, settings.owner, store)
    prompts = Prompts(settings)
    scheduler = Scheduler(settings, store, chat, harness, workspace, prompts)
    deployer = DeployerClient(settings.deployer_url, os.environ.get("DEPLOYER_SECRET", ""))
    bot = Bot(settings, store, chat, scheduler, harness, workspace, prompts, deployer)

    server = uvicorn.Server(uvicorn.Config(create_app(settings, store, scheduler), host="0.0.0.0", port=8000, access_log=False))
    web = asyncio.create_task(server.serve())

    # Tuwunel may still be starting; the status page is up meanwhile.
    while True:
        try:
            await chat.login()
            await bot.ensure_rooms()
            break
        except Exception as e:
            log.warning("Matrix not ready (%s); retrying in 10s", e)
            await asyncio.sleep(10)
    log.info("logged in as %s; taking instructions from %s", settings.matrix_user, settings.owner)

    await asyncio.gather(web, chat.sync_forever(bot.handle), scheduler.run_forever())


if __name__ == "__main__":
    asyncio.run(main())
