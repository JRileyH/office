"""office's own calls to the deployer (workers use `office-deploy`)."""

from __future__ import annotations

import logging
from typing import Protocol

import httpx

log = logging.getLogger(__name__)


class Deployer(Protocol):
    async def stop(self, slug: str) -> None: ...


class DeployerClient:
    def __init__(self, url: str, secret: str, client: httpx.AsyncClient | None = None):
        self.client = client or httpx.AsyncClient(base_url=url, headers={"X-Deployer-Secret": secret}, timeout=120)

    async def stop(self, slug: str) -> None:
        """Stop a project, keeping its data. Not having been deployed is fine."""
        try:
            r = await self.client.post("/stop", json={"slug": slug})
        except httpx.HTTPError as e:
            log.warning("stopping %s: %s", slug, e)
            return
        if r.status_code != 200 and "has not been deployed" not in r.text:
            log.warning("stopping %s: %s %s", slug, r.status_code, r.text[:300])
