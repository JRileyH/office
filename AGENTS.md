# Working on office

office turns ideas dictated into Matrix into deployed web projects, built by
headless Claude Code workers. `BRIEF.md` is the full design; read it before
changing anything structural.

## Layout

| Path | What |
| --- | --- |
| `compose.yml` | The whole stack: office, deployer, inner-traefik, tuwunel (+ `edge` in the `standalone` profile) |
| `office/` | Python service, Dockerfile and tests: Matrix bot, SQLite queue, scheduler, status page. Also the worker image. |
| `deployer/` | Python service, Dockerfile and tests. The only container with the Docker socket. |
| `traefik/` | Inner Traefik static config, and the local edge config for `standalone` |
| `tuwunel/` | Matrix homeserver notes |
| `playbooks/`, `skills/`, `prompts/` | Process files baked read-only into the office image |
| `bin/bootstrap.sh` | First-time setup on a fresh box |
| `docs/` | Runbooks |

## Rules

- **The deployer's validation is the security boundary.** Allow-list, never
  deny-list. Every rule has a fixture in `deployer/tests/fixtures/` and a test
  asserting its exact rejection reason. A new rule without a test isn't done.
- Never print, log or commit secrets. Secrets live in `.env` (git-ignored);
  document new variables in `.env.example`.
- Nothing here touches the neighbor stacks on the VPS (`~/web/proxy`, `misg`,
  `trellis`, `shlink`). The edge Traefik's config files never change.
- Workers run inside the office container with no Docker access; the deployer
  is their only way to production. Process files (`playbooks/`, `skills/`,
  `prompts/`) are what workers follow: change them as carefully as code.
- Small steps, one commit each.

## Commands

Run `just` to list recipes. The common ones:

```sh
just test        # deployer and office test suites
just up          # build and start the stack
just logs <svc>  # follow a service's logs
just down        # stop the stack (keeps volumes)
```
