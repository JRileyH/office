"""Office settings, read once from the environment at startup."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .store import EFFORTS, MODELS, MODES


@dataclass(frozen=True)
class Settings:
    base_domain: str
    edge_port: str  # local mode's edge isn't on 80; only used in links
    harness: str  # "claude" or "fake"
    org: str
    matrix_url: str
    matrix_user: str
    matrix_password: str
    owner: str  # the only Matrix user office takes instructions from
    deployer_url: str
    state_dir: Path  # SQLite, run logs, lessons.md
    workspace_dir: Path  # one checkout per project
    process_dir: Path  # playbooks, skills and prompts, read-only
    # What a new project gets unless the dictation or a command says otherwise.
    default_mode: str = "standard"
    default_model: str = "opus"
    default_effort: str = "high"
    # Signs Riggy in on the status page to change things. Change it in .env.
    web_password: str = "supi"

    @classmethod
    def from_env(cls, env: dict[str, str] = os.environ) -> Settings:
        base_domain = env.get("BASE_DOMAIN", "unlaunched.dev")
        harness = env.get("HARNESS", "claude")
        if harness not in ("claude", "fake"):
            raise SystemExit("HARNESS must be claude or fake")
        defaults = {
            "default_mode": _choice(env, "OFFICE_DEFAULT_MODE", "standard", MODES),
            "default_model": _choice(env, "OFFICE_MODEL", "opus", MODELS),
            "default_effort": _choice(env, "OFFICE_EFFORT", "high", EFFORTS),
        }
        password = env.get("OFFICE_MATRIX_PASSWORD", "")
        if not password:
            raise SystemExit("OFFICE_MATRIX_PASSWORD must be set")
        return cls(
            base_domain=base_domain,
            edge_port=env.get("EDGE_PORT", ""),
            harness=harness,
            org=env.get("GITHUB_ORG", "unlaunched"),
            matrix_url=env.get("MATRIX_URL", "http://tuwunel:8008"),
            matrix_user=env.get("OFFICE_MATRIX_USER", f"@office:matrix.{base_domain}"),
            matrix_password=password,
            owner=env.get("OFFICE_OWNER", f"@riggy:matrix.{base_domain}"),
            deployer_url=env.get("DEPLOYER_URL", "http://deployer:8000"),
            state_dir=Path(env.get("OFFICE_STATE_DIR", "/var/lib/office")),
            workspace_dir=Path(env.get("OFFICE_WORKSPACE_DIR", "/workspace")),
            process_dir=Path(env.get("OFFICE_PROCESS_DIR", "/opt/office")),
            web_password=env.get("OFFICE_WEB_PASSWORD") or "supi",
            **defaults,
        )

    def url(self, host: str) -> str:
        """Public URL for `<host>.<base domain>`: https on the VPS, the local edge's port locally."""
        if self.base_domain == "localhost":
            port = f":{self.edge_port}" if self.edge_port and self.edge_port != "80" else ""
            return f"http://{host}.localhost{port}"
        return f"https://{host}.{self.base_domain}"

    def project_url(self, slug: str) -> str:
        return self.url(slug) + "/"

    def repo_url(self, slug: str) -> str:
        return f"https://github.com/{self.org}/{slug}"

    def job_logs(self, job_id: int) -> Path:
        """Where a job's runs are logged, one compressed event stream per run."""
        return self.state_dir / "jobs" / str(job_id)


def _choice(env: dict[str, str], name: str, default: str, allowed: tuple[str, ...]) -> str:
    value = env.get(name) or default
    if value not in allowed:
        raise SystemExit(f"{name} must be one of {', '.join(allowed)}")
    return value
