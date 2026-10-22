# office — build brief for Claude Code

You are Claude Code, working with Riggy (full-stack engineer) to build **office**: a self-hosted system that turns ideas dictated from his phone into deployed web projects, built by background agents. This brief is the complete design, settled over several planning sessions. Read all of it before writing any code.

**How we work on this repo.** office is built hands-on, the way Riggy normally works: he reviews changes and handles deploys.

- Start by restating the Phase 1 plan in your own words and listing anything you'd verify first. Wait for his go.
- Work in small, reviewable steps, each ending in a commit with a clear message.
- Never touch the VPS (stonehenge) without an explicit OK for that specific action. When something must run there, give Riggy the exact commands, or run them over SSH only after he says yes.
- Never print, log or commit secrets. Use `.env` files that are git-ignored, with a `.env.example` beside them.
- The deployer's validation is the security boundary of the whole system. Treat it like production code: small, readable, thoroughly tested.
- If something in this brief turns out to be wrong once you see real files, say so and propose a fix rather than working around it silently.

## What we're building

Riggy acts as product manager. He dictates an idea into a Matrix chat on his phone ("a flashcard game for identifying northeastern US trees"). office queues it, and Claude Code workers spec it, build it, review it, push it to GitHub and deploy it to `<slug>.unlaunched.dev`, working for hours without him. They check in only at moments that matter (an MVP worth looking at, a genuine ambiguity, something irreversible) and otherwise decide, log the decision and move on. Later he can say "trees needs a quiz mode" and the feature ships to production without further input.

This runs alongside his hands-on workflow, not instead of it. Projects built here are personal tools: public, disposable data, no customers.

## The environment

**VPS (`stonehenge`, user `riggy`).** Ubuntu 24.04, 5 vCPU, 5.8 GB RAM (about 4.2 GB available), 96 GB disk (about 74 GB free). Docker Engine 29.9.2 from Docker's official apt repo, `overlay2` storage driver, containerd image store disabled in `/etc/docker/daemon.json`. Recently cleaned up and migrated off Snap Docker; RAM is the scarce resource.

**Existing stacks, all in `~/web/`, all `restart: unless-stopped`.** These are neighbors: office must never touch them.

| Stack | Domains | Notes |
| --- | --- | --- |
| `proxy` | — | The edge Traefik (details below) |
| `misg` | `mattsinternetsurvivor.com` | Live game people still play. Django, django-q worker, Postgres 18, Redis |
| `trellis` | `growtrellis.co`, `www.growtrellis.co` | Riggy's hands-on project; will move off this box eventually |
| `shlink` | `qr.` and `shlink.northerncatskillsmutualaid.com` | URL shortener for a mutual aid group with active QR codes on posters that can't break |

**Edge Traefik (`~/web/proxy/docker-compose.yml`).**

- Image `traefik:latest`, container `traefik`, owns host ports 80 and 443.
- Docker provider via `/var/run/docker.sock` (read-only); static config in `~/web/proxy/traefik.yml` (read: exposedByDefault is false, no dashboard or API enabled).
- TLS via a cert resolver named `letsencrypt` using the **Cloudflare DNS challenge** (`CF_DNS_API_TOKEN`). DNS challenge means the edge can issue a **wildcard** certificate for `*.unlaunched.dev`.
- Entrypoints: web on :80 redirects everything to HTTPS; the HTTPS entrypoint is named `websecure`. Certificates live in `~/web/proxy/acme.json`; access logs in `~/web/proxy/logs`.
- Shared external Docker network: `web`. Apps opt in with `traefik.enable=true` and `traefik.docker.network=web`.

**Label conventions on the edge,** taken from Trellis (one router per hostname, middlewares per app):

```yaml
labels:
  - "traefik.enable=true"
  - "traefik.docker.network=web"
  - "traefik.http.services.trellis.loadbalancer.server.port=3000"
  - "traefik.http.routers.trellis.rule=Host(`growtrellis.co`)"
  - "traefik.http.routers.trellis.entrypoints=websecure"
  - "traefik.http.routers.trellis.tls.certresolver=letsencrypt"
  - "traefik.http.routers.trellis.middlewares=trellis-headers"
  - "traefik.http.routers.trellis-www.rule=Host(`www.growtrellis.co`)"
  - "traefik.http.routers.trellis-www.entrypoints=websecure"
  - "traefik.http.routers.trellis-www.tls.certresolver=letsencrypt"
  - "traefik.http.routers.trellis-www.middlewares=trellis-www-redirect"
  - "traefik.http.middlewares.trellis-www-redirect.redirectregex.regex=^https://www.growtrellis.co/(.*)"
  - "traefik.http.middlewares.trellis-www-redirect.redirectregex.replacement=https://growtrellis.co/$${1}"
  - "traefik.http.middlewares.trellis-www-redirect.redirectregex.permanent=true"
  - "traefik.http.middlewares.trellis-headers.headers.customResponseHeaders.X-Frame-Options=SAMEORIGIN"
  - "traefik.http.middlewares.trellis-headers.headers.customResponseHeaders.X-Content-Type-Options=nosniff"
  - "traefik.http.middlewares.trellis-headers.headers.customResponseHeaders.Referrer-Policy=strict-origin-when-cross-origin"
```

**DNS.** Cloudflare hosts all domains. A wildcard record for `*.unlaunched.dev` already points at the VPS, so new subdomains need no DNS changes.

**GitHub.** A GitHub org named `unlaunched` exists (free plan) for agent-built project repos. The office repo itself lives in Riggy's **personal** account, so tokens scoped to the org can't reach it. Free orgs get no branch protection on private repos and 2,000 Actions minutes a month.

**Riggy's laptop.** MacBook Pro, M4 Pro, 48 GB RAM (ARM; the VPS is x86). Used for local mode.

**Claude.** Riggy has a Claude subscription (moving to Max). Workers use the official Claude Code CLI headless, signed in with that subscription.

## Fixed decisions

These were argued through already. Don't reopen them without a concrete reason found in real files.

| Area | Decision |
| --- | --- |
| Harness | Official Claude Code CLI, headless, on Riggy's subscription. Third-party harnesses (OpenCode etc.) may not use Pro/Max subscriptions under Anthropic's terms. |
| Model-agnostic | Harnesses sit behind a small adapter interface so another CLI (e.g. Codex) can be added later. Not now. |
| Language | office and the deployer are Python. |
| Chat | Self-hosted Matrix: Tuwunel at `matrix.unlaunched.dev`, federation off, Element X on the phone. |
| Packaging | One repo, one compose file: office, deployer, inner Traefik, Tuwunel. Portable to another box. |
| Sandbox | The office container is the sandbox. Workers run inside it. Only the deployer touches Docker. |
| Routing | Two tiers: the existing edge Traefik in front, office's inner Traefik behind it for agent projects only. |
| Projects | Each project is its own private repo in the `unlaunched` org, its own compose stack, its own data. Agent picks the stack. Public at `<slug>.unlaunched.dev`. Project data is disposable. |
| Process files | Playbooks, skills and prompts live in the office repo and are baked read-only into the office image. Agents can't edit them. |
| Learning | Agents append to a writable lessons file that feeds every prompt, and draft playbook edits for Riggy to apply by hand. |
| Scheduling | Priority queue, one worker by default. No work windows; a per-project pause instead. |
| Status | Matrix rooms, GitHub issues/PRs on an org Projects board, a status page at `office.unlaunched.dev` (public), and markdown docs in each repo. |
| Budget | Never buy extra usage. On a usage-limit error, park the job and retry after the window resets. |

## Office stack architecture

```
Internet → Cloudflare → edge Traefik (~/web/proxy, owns 80/443 and TLS)
   ├─ growtrellis.co, mattsinternetsurvivor.com, shlink hosts → neighbors (untouched)
   ├─ matrix.unlaunched.dev → tuwunel        (specific router, wins)
   ├─ office.unlaunched.dev → office         (specific router, wins)
   └─ *.unlaunched.dev (priority 1) → inner Traefik :80 → project stacks
```

| Service | Role | Networks |
| --- | --- | --- |
| `office` | Matrix bot, queue, scheduler, status page. Runs Claude Code workers as subprocesses, with git, gh and per-project toolchains. One memory cap for all agent work. | `web` (status page), `office-internal` |
| `deployer` | The only container with the Docker socket. Small HTTP API on the internal network. | `office-internal`, `office-apps` (to attach projects) |
| `inner-traefik` | Routes agent projects. **File provider only; no Docker socket.** | `web`, `office-apps` |
| `tuwunel` | Matrix homeserver | `web`, `office-internal` |

**Why the inner Traefik reads files, not Docker labels.** Both proxies would otherwise read labels from the same Docker socket, so a label an agent wrote for the inner proxy would also be picked up by the edge, including a router for `growtrellis.co`. Instead:

- Agent compose files contain **no `traefik.*` labels at all**; the deployer rejects any.
- The deployer adds `traefik.enable=false` to every project container, so the edge ignores them even if its `exposedByDefault` is true.
- For each deployed project, the deployer writes one dynamic config file (`<slug>.yml`) into a volume the inner Traefik watches, routing `<slug>.unlaunched.dev` to the project's public service.

Routing is then derived from the slug alone, and agents have no way to claim a hostname.

**Catch-all on the edge**, as labels on the `inner-traefik` container (DNS challenge allows the wildcard certificate):

```yaml
labels:
  - "traefik.enable=true"
  - "traefik.docker.network=web"
  - "traefik.http.routers.office-apps.rule=HostRegexp(`^[a-z0-9-]+\\.unlaunched\\.dev$`)"
  - "traefik.http.routers.office-apps.priority=1"
  - "traefik.http.routers.office-apps.entrypoints=websecure"
  - "traefik.http.routers.office-apps.tls.certresolver=letsencrypt"
  - "traefik.http.routers.office-apps.tls.domains[0].main=unlaunched.dev"
  - "traefik.http.routers.office-apps.tls.domains[0].sans=*.unlaunched.dev"
  - "traefik.http.services.office-apps.loadbalancer.server.port=80"
```

Check the regex escaping against Traefik v3 syntax when you write it. `tuwunel` and `office` get ordinary specific routers in the Trellis style. The edge's config files never change.

**Repo layout:**

```text
office/
  compose.yml
  .env.example         base domain, harness, Matrix and GitHub tokens
  AGENTS.md            how to work on this repo (CLAUDE.md points here)
  office/              Python service + Dockerfile (also the worker image)
  deployer/            Python service + Dockerfile + tests
  traefik/             inner static config; dynamic/ is a volume
  tuwunel/             homeserver config
  playbooks/           reckless.md, standard.md, rigorous.md, feature.md
  skills/              deploy.md, new-repo.md, spec-writing.md, review.md
  prompts/             intake triage, reply triage, phase wrappers
  bin/bootstrap.sh     first-time setup on a fresh box
```

**Volumes:** office state (SQLite, logs, `lessons.md`, `proposals/`), Claude Code credentials, agent workspace (repo checkouts), toolchain cache, Tuwunel database, inner Traefik dynamic config. Moving the office to another box means moving these plus `.env`.

## Deployer contract

The deployer is the only door from agents to Docker. Assume anything calling it may be a confused or overeager agent: workers run in the same container as office and could reach its API directly. So the deployer trusts nothing in a request beyond the slug and commit, and enforces every rule itself.

**API** (internal network only, shared-secret header):

| Call | Does |
| --- | --- |
| `POST /deploy {slug, sha}` | Fetch `deploy/compose.yml` from `unlaunched/<slug>` at that commit, check it, validate it, deploy it, write the route, verify the URL responds |
| `POST /stop {slug}` | `compose down` keeping volumes, remove the route |
| `POST /delete {slug}` | `compose down` with volumes, remove the route and deploy files |
| `GET /projects` | Every project with deploy files, running or stopped |
| `GET /status?slug=` | Running state, deployed commit, route |
| `GET /logs?slug=&service=&tail=` | Container logs, for agents debugging failed deploys |

Data is deleted only when Riggy asks (`!delete`, `bin/nuke.sh`): `/delete` needs a second secret that office holds and workers never get.

**Deploys come from pushed commits only.** The deployer fetches the compose file from GitHub itself with a read-only org token, never from the agent workspace, so nothing uncommitted can reach production. It also requires every check run on that commit to have passed. Reckless-mode projects simply have fewer checks (at minimum, the image build), so no mode flag is needed or trusted.

**Validation: allow-list, not deny-list.** Reject anything not explicitly allowed, with a reason the agent can act on.

- Slug matches `^[a-z0-9][a-z0-9-]{1,30}$` and isn't reserved (`office`, `matrix`, `www`, `status`, `traefik`, `deployer`, `api`, `mail`).
- Images come from `ghcr.io/unlaunched/` or a short allow-list of official images (postgres, redis, nginx and the like). No `build:`.
- Volumes are named only, with no `driver_opts` (a local driver with bind options is a disguised bind mount). No bind mounts, no external volumes or networks.
- Forbidden keys: `privileged`, `cap_add`, `devices`, `network_mode`, `pid`, `ipc`, `userns_mode`, `security_opt`, `sysctls`, `ports`, `container_name`, `env_file`, `extends`, `include`, file-based `secrets`/`configs`, and any `traefik.*` label.
- Every service sets a memory limit under a per-service cap; the project total stays under a per-project cap (start at 512 MB and 1 GB, configurable).
- Exactly one service carries `office.public=true` and `office.port=<port>`.

**How it deploys.** Compose project name `office-<slug>`. The deployer generates an override file that attaches the public service to `office-apps` with network alias `<slug>`, adds `traefik.enable=false` and `office.slug` labels, and sets log rotation. It runs `pull` then `up -d --remove-orphans`, writes `dynamic/<slug>.yml` (route `<slug>.unlaunched.dev` → `http://<slug>:<port>`), then requests the site through the inner Traefik and returns success, or failure with the relevant logs.

**Audit.** Every request, decision and rejection reason goes to an append-only JSON-lines log.

## Office runtime behavior

Reference for Phase 2 onward; not needed to finish Phase 1, but Phase 1 shouldn't make any of it harder.

**Matrix.** The bot is `@office:matrix.unlaunched.dev`.

| Room | Purpose |
| --- | --- |
| #inbox | Riggy dictates new ideas and cross-project commands |
| #status | Daily digest, budget warnings, queue changes, proposed playbook edits |
| One room per project | Check-ins, questions, Riggy's replies, feature requests |

Free text in #inbox becomes a job: a cheap triage call turns dictation into a brief (name, slug, mode, priority, summary). Free text in a project room is feedback or a feature request; replies like "looks good", "make it a quiz" or "shelve it" map to continue, revise or pause. Commands: `!queue`, `!status <project>`, `!pause` / `!resume` (scheduler), `!pause <project>` / `!resume <project>` (lock a project while Riggy tinkers), `!mode <project> <mode>`, `!priority`, `!kill <job>`, `!archive` / `!revive <project>`. Every message the bot sends links to something (preview URL, PR, status page).

**Jobs run in phases.** A job (new project, feature, bug fix) is a sequence of phases from its playbook. Each phase is one headless Claude Code run with a turn cap, checkpointed to SQLite and git, so a crash or usage limit costs at most one phase. Each phase ends with a JSON verdict the worker writes: `continue`, `checkin` (message + link), `blocked` (reason) or `done`. States: queued → running → awaiting feedback → running → done, with paused and failed as side exits. A job awaiting feedback frees its worker slot.

**Worker adapter.** `run(project_dir, prompt, model, max_turns)`, `resume(session_id, prompt)`, `parse_result()` (verdict + usage), `detect_limit()`. The Claude Code adapter runs `claude -p` with streamed JSON output and pre-approved permissions (nobody is there to click allow). A `fake` adapter returns scripted verdicts for testing.

**Modes** (default standard; stored per project, inherited by later features):

| Phase | Reckless | Standard | Rigorous |
| --- | --- | --- | --- |
| Intake brief | Posted, non-blocking | Posted, non-blocking | Posted, non-blocking |
| Spec | Paragraph in README | `docs/specs/` | Spec + fresh-context spec review |
| MVP check-in | None | Blocking | Blocking |
| Tests | None beyond the image build | Core paths | Thorough |
| Code review | None | One fresh-context review | Review + second pass on fixes |
| Deploy | Straight from main | PR, checks, merge | PR, checks, preview smoke test, merge |
| Retro | Skipped | Lessons | Lessons + proposed playbook edits |

New project flow: intake → spec → scaffold and first deploy (proves hosting early) → build to MVP → check-in → build out → review → deploy → docs and retro. Feature flow: read the project's `AGENTS.md` and docs → mini-spec → build → review per mode → deploy → update docs; no check-ins unless genuinely ambiguous. A fresh-context reviewer gets only the spec, the diff and `AGENTS.md`.

**Check-ins.** Blocking only for: MVP reached (standard/rigorous), ambiguity that changes what gets built, something irreversible, budget 2–3x over the mode's expected range, or stuck after retries. Never for colors, naming, library choice and similar; those go in `docs/decisions.md`. Unanswered check-ins cost nothing; a reminder goes to #status after a day.

**Every project repo:** `AGENTS.md` (stack, run, test, deploy, gotchas; `CLAUDE.md` points to it), `README.md` with the live URL, `docs/brief.md` (original dictation + brief), `docs/specs/`, `docs/decisions.md`, `docs/lessons.md`, `deploy/compose.yml`, and a GitHub Actions workflow that builds and pushes the image (cache dependencies to stay inside 2,000 minutes a month). One issue per job and a PR per change, on the org Projects board.

**Budget.** Background work, Riggy's hands-on Claude Code use and his chats share one pool. Keep a reserve so he's never locked out. One worker by default, two only when the queue is deep and usage is low. Small model for triage and mechanical steps, mid model for building, strongest only for specs and reviews in rigorous mode. Record token usage per run; after a few projects, each mode gets an expected range.

**Observability.** Every run's event stream saved and compressed per job, readable while it runs (`bin/watch.sh`); a hook logs every tool call (time, project, job, command or file) to an append-only log; job events in SQLite; git history. The status page links from each job to its logs. Recurring mistakes go to `lessons.md`.

## Local mode

The same compose file runs on the MacBook, driven by a local env file.

| Setting | VPS | Local |
| --- | --- | --- |
| Base domain | `unlaunched.dev` | `localhost` (`anything.localhost` resolves with no DNS setup) |
| Edge proxy | existing edge Traefik | `standalone` compose profile runs a local edge Traefik over plain HTTP |
| Harness | `claude` | `claude` or `fake` |
| Git source for the deployer | GitHub `unlaunched` org | a local bare git repo |
| Checks before deploy | required | off |

The phone can't reach `localhost`, so local Matrix testing uses Element on the laptop against `matrix.localhost`; push notifications are tested on the VPS. Images built locally are ARM and only run locally; GitHub Actions builds the x86 images the VPS uses.

## Your task now: Phase 1, foundations

Goal: the office stack runs, plugs into the edge, and can put a project live through the deployer. The office service itself is only a skeleton in this phase; its behavior is Phase 2.

**Deliverables**

1. **Repo skeleton** per the layout above: `AGENTS.md`, `README.md`, `.env.example`, `.gitignore`, and a `justfile` or `Makefile` for common commands.
2. **`compose.yml`** with all four services, the `office-internal` and `office-apps` networks, the external `web` network, memory limits on everything, and a `standalone` profile for local mode.
3. **Inner Traefik:** static config (one HTTP entrypoint, file provider watching the dynamic volume), plus the edge catch-all labels.
4. **Tuwunel:** config with federation off and open registration off; edge router for `matrix.unlaunched.dev`; documented steps to create Riggy's account and the `office` account.
5. **Office image skeleton:** Python, Node, git, gh and the Claude Code CLI installed; a placeholder entrypoint with a health endpoint. Prove Claude Code runs headless inside it on Riggy's subscription.
6. **Deployer v0:** the API, validation, override generation, route files and audit log from the contract above. A test suite with one fixture per rule (a valid compose file, plus one bad file per forbidden thing) asserting the exact rejection reason.
7. **A `hello` project:** the smallest possible static site with a GitHub Actions workflow that builds and pushes its image. Locally it deploys from a bare git repo; on the VPS from `unlaunched/hello`.
8. **`bin/bootstrap.sh` and a VPS runbook:** the exact commands Riggy runs to bring the stack up on stonehenge, and how to roll back.

**Done when**

- Locally, `hello.localhost` is served after a deploy request to the deployer, and every validator fixture is rejected with the right reason.
- On the VPS, `hello.unlaunched.dev` is served with a valid wildcard certificate, deployed through the deployer from a commit with passing checks.
- Riggy signs in to `matrix.unlaunched.dev` from Element X on his phone and gets a push notification for a message from the `office` account.
- MISG, Trellis and Shlink answer exactly as before (check them before and after the VPS bring-up).

When Phase 1 is done, stop and report: what was built, what deviated from this brief and why, and anything you'd change before Phase 2.

## Later phases

| Phase | Scope | Gate |
| --- | --- | --- |
| 2. office MVP | Matrix bot, SQLite queue and registry, Claude Code and fake adapters, phase verdicts, standard playbook, intake brief and MVP check-in | A dictated toy idea reaches a live URL without Riggy opening a terminal |
| 3. Shakedown | 3–5 small real projects in standard mode; tune playbook, deploy skill and check-ins; audit hook, session logs, status page | Two projects shipped with no hands-on fixes |
| 4. Modes, features, budget | Reckless and rigorous playbooks, feature flow for existing projects, budget envelopes, pause on usage limit | A feature request to an existing project ships unattended |
| 5. Ongoing | Lessons file and drafted playbook edits; a supervised mode that ends at an opened PR (for projects like Trellis); a second harness if wanted | — |

## Verify first, and what only Riggy can do

**Check these before building on them; tell Riggy if any differ from this brief.**

- [x] `~/web/proxy/traefik.yml` read: entrypoints `web` (:80, redirects to `websecure`) and `websecure` (:443); Docker provider with `exposedByDefault: false`; `letsencrypt` uses the Cloudflare DNS challenge, so wildcard certificates work. The deployer's `traefik.enable=false` is now a second safeguard, not the only one.
- [ ] The running Traefik version on the VPS (`docker exec traefik traefik version`). The edge runs `traefik:latest`, a floating tag; suggest pinning it to that version, but don't change it yourself. Watch Traefik's startup log for deprecation warnings in the existing config once you know the version.
- [ ] Tuwunel's current image, default port and config keys (federation, registration, admin bootstrap), from its own docs.
- [ ] The current, sanctioned way to authenticate Claude Code headless inside a container with a subscription, from Anthropic's Claude Code docs.
- [ ] Where you're running: Riggy's Mac (in Zed) or the VPS, and which Docker runtime the Mac uses. The proxy repo is checked out on the Mac too; confirm it matches what's deployed on the VPS before relying on it.

**Host housekeeping Riggy is finishing on the VPS.** Don't redo these or edit `/etc/docker/daemon.json` without asking: capping journald, adding Docker log rotation, pruning unused volumes, OS updates and a reboot, purging the old Snap Docker copy after a day or two, and rotating Traefik's access log in \~/web/proxy/logs, which Traefik never rotates on its own.

**Only Riggy can:**

- Create the `office` repo in his personal account (or authorize `gh` to).
- Create GitHub fine-grained tokens: a read-only one for the deployer (contents, checks and packages read on the `unlaunched` org) now, and the workers' token (create repos, write contents, issues and PRs) in Phase 2.
- Sign in to Claude Code inside the office container.
- Install Element X and confirm push notifications.
- Approve every action on the VPS.
