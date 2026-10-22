"""Where compose files come from: pushed commits only, never an agent's workspace."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Protocol

import httpx

from .validate import Rejected

COMPOSE_PATH = "deploy/compose.yml"
PASSING = frozenset({"success", "neutral", "skipped"})


class Source(Protocol):
    def compose_file(self, slug: str, sha: str) -> str: ...
    def failing_checks(self, slug: str, sha: str) -> list[str]: ...


class GitHubSource:
    """Reads unlaunched/<slug> through the GitHub API with a read-only token."""

    def __init__(self, org: str, token: str, client: httpx.Client | None = None):
        self.org = org
        self.client = client or httpx.Client(
            base_url="https://api.github.com",
            headers={
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=15,
        )

    def compose_file(self, slug: str, sha: str) -> str:
        r = self.client.get(
            f"/repos/{self.org}/{slug}/contents/{COMPOSE_PATH}",
            params={"ref": sha},
            headers={"Accept": "application/vnd.github.raw+json"},
        )
        if r.status_code == 404:
            raise Rejected([f"{COMPOSE_PATH} not found in {self.org}/{slug} at {sha}; is the commit pushed?"])
        r.raise_for_status()
        return r.text

    def failing_checks(self, slug: str, sha: str) -> list[str]:
        r = self.client.get(
            f"/repos/{self.org}/{slug}/commits/{sha}/check-runs",
            params={"per_page": 100},
            headers={"Accept": "application/vnd.github+json"},
        )
        r.raise_for_status()
        runs = r.json()["check_runs"]
        if not runs:
            return ["no check runs on this commit yet; wait for CI to start"]
        return [
            f"{run['name']}: {run['conclusion'] or run['status']}"
            for run in runs
            if run["status"] != "completed" or run["conclusion"] not in PASSING
        ]


class LocalSource:
    """Reads bare repos at <git_dir>/<slug>.git. Local mode only; there is no CI."""

    def __init__(self, git_dir: Path):
        self.git_dir = git_dir

    def compose_file(self, slug: str, sha: str) -> str:
        repo = self.git_dir / f"{slug}.git"
        if not repo.is_dir():
            raise Rejected([f"no repository for `{slug}`"])
        result = subprocess.run(
            ["git", "-c", "safe.directory=*", "--git-dir", str(repo), "show", f"{sha}:{COMPOSE_PATH}"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode != 0:
            raise Rejected([f"{COMPOSE_PATH} not found in `{slug}` at {sha}; is the commit pushed?"])
        return result.stdout

    def failing_checks(self, slug: str, sha: str) -> list[str]:
        return []
