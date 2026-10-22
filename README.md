# office

A self-hosted system that turns ideas dictated from a phone into deployed web
projects at `<slug>.unlaunched.dev`, built by background Claude Code workers.

See `BRIEF.md` for the design and `AGENTS.md` for how to work on the repo.

## Local mode

Needs Docker (OrbStack) and [just](https://github.com/casey/just).

```sh
cp .env.example .env    # set BASE_DOMAIN=localhost, COMPOSE_PROFILES=standalone,
                        # DEPLOYER_GIT_SOURCE=local, DEPLOYER_REQUIRE_CHECKS=false
just up
```

## VPS

See `docs/runbook.md`.
