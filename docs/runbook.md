# VPS runbook: bring office up on stonehenge

Everything here runs as `riggy` on stonehenge unless marked **(Mac)**. Nothing
touches `~/web/*`: the edge picks office up through Docker labels, and its
config files never change.

## 0. Before you start

**Tokens** (GitHub → Settings → Developer settings):

1. **Fine-grained token** for the deployer: resource owner `unlaunched`, all
   repositories, permissions **Contents: Read-only** and **Actions: Read-only**
   (Metadata is added automatically). Actions, not Checks: fine-grained
   tokens don't offer Checks, so the deployer reads workflow runs. Goes in `DEPLOYER_GITHUB_TOKEN`.
2. **Classic token** with only **`read:packages`**: ghcr.io doesn't accept
   fine-grained tokens. Goes in `DEPLOYER_REGISTRY_TOKEN`, with your GitHub
   username in `DEPLOYER_REGISTRY_USER`.

**DNS (Cloudflare, `unlaunched.dev` zone):** an `A` record `*` → stonehenge's
IP, proxied like the apex. Check from anywhere:
`python3 -c "import socket; print(socket.gethostbyname('x.unlaunched.dev'))"`.
The edge's `CF_DNS_API_TOKEN` must also be allowed to edit this zone, or the
wildcard certificate can't be issued.

**hello on GitHub:** `unlaunched/hello` has the contents of `examples/hello`
on `main`, its `build` workflow is green, and the package
`ghcr.io/unlaunched/hello` exists. Note the commit sha.

**Neighbor baseline (Mac):**

```sh
bin/check-neighbors.sh > var/neighbors-before.txt
```

**Box state:**

```sh
free -m                      # ~4.2 GB available expected; office caps total ~2.3 GB with OFFICE_MEM_LIMIT=1536m
df -h /                      # images need ~2 GB
docker network inspect web >/dev/null && echo "web network ok"
sudo apt install -y just     # optional; every recipe is plain docker compose in justfile
```

## 1. Get the code

```sh
git clone git@github.com:JRileyH/office.git ~/office
cd ~/office
```

(If stonehenge has no SSH key on GitHub, add a read-only deploy key to the
office repo: `ssh-keygen -t ed25519 -f ~/.ssh/office_deploy -N ''`, paste the
`.pub` into the repo's Deploy keys, and clone with
`GIT_SSH_COMMAND='ssh -i ~/.ssh/office_deploy' git clone …`.)

## 2. Configure

```sh
bin/bootstrap.sh vps        # creates .env, generates the secrets it can
nano .env                   # fill in CLAUDE_CODE_OAUTH_TOKEN, DEPLOYER_GITHUB_TOKEN,
                            # DEPLOYER_REGISTRY_USER, DEPLOYER_REGISTRY_TOKEN
bin/bootstrap.sh vps        # should end with "ok" and nothing left to fill in
```

## 3. Build and start

```sh
docker compose build                   # x86 images, built on the box
docker compose up -d
docker compose ps                      # all four healthy within a minute
docker logs --since 5m traefik 2>&1 | grep -iE 'error|unlaunched'
```

The edge requests the `*.unlaunched.dev` wildcard certificate (DNS challenge)
on the first request to an office host, and one for `matrix.unlaunched.dev`.
Allow a minute.

## 4. Verify the stack

```sh
curl -sS -o /dev/null -w '%{http_code}\n' https://office.unlaunched.dev/        # 200
curl -sS https://matrix.unlaunched.dev/_matrix/client/versions | head -c 80    # JSON
curl -sS https://matrix.unlaunched.dev/.well-known/matrix/client               # base_url
curl -sS https://nothing-here.unlaunched.dev/ -o /dev/null -w '%{http_code}\n' # 404 from inner Traefik
just claude-check                                                              # "office ok"
```

## 5. Deploy hello

```sh
just deploy hello <sha>       # the sha from step 0
curl -sS https://hello.unlaunched.dev/ | grep '<h1>'
```

Certificate check **(Mac)**:

```sh
curl -sv https://hello.unlaunched.dev/ -o /dev/null 2>&1 | grep -E 'subject|subjectAltName|issuer'
```

A deploy with failing or pending checks is refused with the workflow names; a
compose file that breaks a rule is refused with the rule.

## 6. Matrix and push notifications

```sh
just matrix-user riggy          # FIRST: riggy becomes server admin. Save the printed password.
just matrix-office-account      # @office, password from .env, not printed
```

On the phone: install **Element X**, "Change account provider" →
`matrix.unlaunched.dev`, sign in as `riggy`, allow notifications, then change
the password in Settings.

```sh
just matrix-ping @riggy:matrix.unlaunched.dev           # creates a DM, invites riggy
# accept the invite on the phone, lock it, then:
just matrix-ping @riggy:matrix.unlaunched.dev "push test"
```

The phone should show a notification from `office`.

## 7. Neighbors after (Mac)

```sh
bin/check-neighbors.sh | diff var/neighbors-before.txt - && echo unchanged
```

## 8. Turn on the bot (Phase 2)

Needs the workers' token (`OFFICE_GITHUB_TOKEN` in `.env`: fine-grained,
`unlaunched`, all repositories, repository creation plus Contents, Pull
requests, Issues and Workflows read and write).

```sh
cd ~/web/office
git pull
bin/bootstrap.sh vps                     # nothing left to fill in
docker compose up -d --build office
docker compose logs office | grep -E 'logged in|ERROR'   # "logged in as @office..."
```

In Element X, accept the invites to **office inbox** and **office status**,
then dictate a small idea into the inbox. Each project gets its own room; accept
that invite too. Progress also shows at https://office.unlaunched.dev.

To stop all work without stopping the bot, send `!pause` in the inbox.

## Rollback

The edge discovers office through container labels, so stopping office's
containers removes its routes; nothing in `~/web` needs undoing.

```sh
cd ~/office
docker compose -p office-hello down     # each deployed project (keeps its volumes)
docker compose down                     # the office stack (keeps volumes: Matrix DB, state)
docker network rm office-apps office-internal 2>/dev/null
```

Then re-run the neighbor check. To remove office entirely, also
`docker compose down --volumes` (**deletes the Matrix database and office
state**) and `rm -rf ~/office`.

To roll back just one change, `git checkout <previous commit>` in `~/office`
and `docker compose up -d --build`.

## Suggested edge changes (yours to make, in `~/web/proxy`)

- Pin the image: `traefik:latest` → `traefik:v3.7.16` (the running version).
- `certificatesResolvers.letsencrypt.acme.dnsChallenge.delayBeforeCheck` is the
  v2 spelling; v3 moved it to `dnsChallenge.propagation.delayBeforeChecks`.
  Check `docker logs traefik 2>&1 | grep -i deprecat`.
- Set `entryPoints.websecure.http.aliasHeadersStrategy: delete` (Traefik 3.7
  warns about it; it stops clients spoofing headers like `X_Forwarded_For`).
