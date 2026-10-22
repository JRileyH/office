# office

A self-hosted system that turns ideas dictated from a phone into deployed web
projects at `<slug>.unlaunched.dev`, built by background Claude Code workers.

See `BRIEF.md` for the design and `AGENTS.md` for how to work on the repo.

## Local mode

Needs Docker (OrbStack) and [just](https://github.com/casey/just).

```sh
just bootstrap local     # writes local settings into .env; add CLAUDE_CODE_OAUTH_TOKEN by hand
just up
just hello-local         # deploys examples/hello -> http://hello.localhost:8800
just claude-check        # Claude Code headless in the office image
```

Status page: http://office.localhost:8800. Matrix: http://matrix.localhost:8800
(accounts: `tuwunel/README.md`).

## VPS

See `docs/runbook.md`.
