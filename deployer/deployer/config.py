"""Deployer settings, read once from the environment at startup."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .validate import Policy


@dataclass(frozen=True)
class Settings:
    secret: str
    base_domain: str
    git_source: str  # "github" or "local"
    org: str
    github_token: str
    require_checks: bool
    pull_policy: str  # "always" on the VPS; "missing" locally, where images are built by hand
    data_dir: Path  # project files, deploy state and the audit log
    routes_dir: Path  # inner Traefik's dynamic config
    git_dir: Path  # bare repos, local mode only
    inner_traefik_url: str
    policy: Policy

    @classmethod
    def from_env(cls, env: dict[str, str] = os.environ) -> Settings:
        secret = env.get("DEPLOYER_SECRET", "")
        if len(secret) < 32:
            raise SystemExit("DEPLOYER_SECRET must be set (at least 32 characters)")
        git_source = env.get("DEPLOYER_GIT_SOURCE", "github")
        if git_source not in ("github", "local"):
            raise SystemExit("DEPLOYER_GIT_SOURCE must be github or local")
        github_token = env.get("DEPLOYER_GITHUB_TOKEN", "")
        if git_source == "github" and not github_token:
            raise SystemExit("DEPLOYER_GITHUB_TOKEN must be set when DEPLOYER_GIT_SOURCE=github")
        org = env.get("GITHUB_ORG", "unlaunched")
        return cls(
            secret=secret,
            base_domain=env.get("BASE_DOMAIN", "unlaunched.dev"),
            git_source=git_source,
            org=org,
            github_token=github_token,
            require_checks=env.get("DEPLOYER_REQUIRE_CHECKS", "true").lower() == "true",
            pull_policy="always" if git_source == "github" else "missing",
            data_dir=Path(env.get("DEPLOYER_DATA_DIR", "/data")),
            routes_dir=Path(env.get("DEPLOYER_ROUTES_DIR", "/routes")),
            git_dir=Path(env.get("DEPLOYER_GIT_DIR", "/git")),
            inner_traefik_url=env.get("DEPLOYER_INNER_TRAEFIK_URL", "http://inner-traefik"),
            policy=Policy(
                org=org,
                service_mem_cap_mb=int(env.get("DEPLOYER_SERVICE_MEM_CAP_MB", "512")),
                project_mem_cap_mb=int(env.get("DEPLOYER_PROJECT_MEM_CAP_MB", "1024")),
            ),
        )
