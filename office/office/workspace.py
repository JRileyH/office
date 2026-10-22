"""Project checkouts and the GitHub side of a job: the repo and its issue.

Done by office rather than the worker, so they happen the same way every time.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Protocol

log = logging.getLogger(__name__)


class WorkspaceError(Exception):
    pass


class Workspace(Protocol):
    def path(self, slug: str) -> Path: ...
    async def ensure_repo(self, slug: str, description: str) -> Path: ...
    async def sync_main(self, slug: str) -> None: ...
    async def open_issue(self, slug: str, title: str, body: str) -> int | None: ...
    async def repo_exists(self, slug: str) -> bool: ...


async def run(*args: str, cwd: Path | None = None) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *args, cwd=cwd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
    )
    out, _ = await proc.communicate()
    return proc.returncode, out.decode(errors="replace").strip()


class GitHubWorkspace:
    def __init__(self, root: Path, org: str):
        self.root = root
        self.org = org

    def path(self, slug: str) -> Path:
        return self.root / slug

    async def repo_exists(self, slug: str) -> bool:
        code, _ = await run("gh", "repo", "view", f"{self.org}/{slug}", "--json", "name")
        return code == 0

    async def ensure_repo(self, slug: str, description: str) -> Path:
        repo = f"{self.org}/{slug}"
        if not await self.repo_exists(slug):
            code, out = await run("gh", "repo", "create", repo, "--private", "--description", description[:350])
            if code != 0:
                raise WorkspaceError(f"creating {repo}: {out[-300:]}")
        path = self.path(slug)
        if not (path / ".git").is_dir():
            code, out = await run("gh", "repo", "clone", repo, str(path))
            if code != 0:
                raise WorkspaceError(f"cloning {repo}: {out[-300:]}")
        return path

    async def sync_main(self, slug: str) -> None:
        """Start a job from the latest main. Best effort: an empty repo or local changes leave it alone."""
        path = self.path(slug)
        await run("git", "fetch", "--quiet", "origin", cwd=path)
        code, dirty = await run("git", "status", "--porcelain", cwd=path)
        if code != 0 or dirty:
            return
        for args in (("git", "checkout", "--quiet", "main"), ("git", "merge", "--quiet", "--ff-only", "origin/main")):
            code, out = await run(*args, cwd=path)
            if code != 0:
                log.info("sync %s: %s", slug, out[-200:])
                return

    async def open_issue(self, slug: str, title: str, body: str) -> int | None:
        code, out = await run(
            "gh", "issue", "create", "--repo", f"{self.org}/{slug}", "--title", title[:200], "--body", body
        )  # fmt: skip
        if code != 0:
            log.warning("issue for %s: %s", slug, out[-300:])
            return None
        try:
            return int(out.rstrip("/").rsplit("/", 1)[-1])
        except ValueError:
            return None


class LocalWorkspace:
    """No GitHub: plain directories. For tests and the fake harness."""

    def __init__(self, root: Path):
        self.root = root
        self.repos: set[str] = set()
        self.issues: list[tuple[str, str]] = []

    def path(self, slug: str) -> Path:
        return self.root / slug

    async def repo_exists(self, slug: str) -> bool:
        return slug in self.repos

    async def ensure_repo(self, slug: str, description: str) -> Path:
        self.repos.add(slug)
        path = self.path(slug)
        path.mkdir(parents=True, exist_ok=True)
        return path

    async def sync_main(self, slug: str) -> None:
        pass

    async def open_issue(self, slug: str, title: str, body: str) -> int | None:
        self.issues.append((slug, title))
        return len(self.issues)


async def configure_git(email: str) -> None:
    """Commit identity, and git authenticating through gh's GH_TOKEN."""
    for args in (
        ("git", "config", "--global", "user.name", "office"),
        ("git", "config", "--global", "user.email", email),
        ("git", "config", "--global", "init.defaultBranch", "main"),
        ("gh", "auth", "setup-git"),
    ):
        code, out = await run(*args)
        if code != 0:
            log.warning("%s: %s", " ".join(args[:3]), out[-200:])
