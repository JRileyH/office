import uvicorn

from .app import create_app
from .audit import Audit
from .compose import Docker
from .config import Settings
from .deploy import Deployer
from .source import GitHubSource, LocalSource


def main() -> None:
    settings = Settings.from_env()
    docker = Docker(settings.data_dir / "projects", settings.pull_policy)
    if settings.git_source == "github":
        source = GitHubSource(settings.org, settings.github_token)
        docker.login("ghcr.io", settings.github_token)
    else:
        source = LocalSource(settings.git_dir)
    settings.routes_dir.mkdir(parents=True, exist_ok=True)
    audit = Audit(settings.data_dir / "audit.jsonl")
    audit.write("start", "ok", git_source=settings.git_source, require_checks=settings.require_checks)
    app = create_app(Deployer(settings, source, docker), audit, settings.secret)
    uvicorn.run(app, host="0.0.0.0", port=8000, access_log=False)


if __name__ == "__main__":
    main()
