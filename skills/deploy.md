# Deploying a project

A project goes live at `https://<slug>.unlaunched.dev` through the **deployer**, a
separate service that fetches `deploy/compose.yml` from GitHub at a commit, checks
it against strict rules, and runs it. You can't run Docker yourself.

## The sequence

1. **Push** the commit to GitHub (normally by merging a PR into `main`).
2. **Wait for CI on that exact commit.** The build workflow pushes
   `ghcr.io/unlaunched/<slug>:<sha>`. The deployer refuses commits whose
   workflow runs haven't all passed.

   ```sh
   sha=$(git rev-parse origin/main)
   gh run list --commit "$sha" --json databaseId,status,conclusion,name
   gh run watch <databaseId> --exit-status
   ```

   A run can take a few seconds to appear after the push; list again if it's empty.
3. **Deploy:**

   ```sh
   office-deploy deploy "$sha"
   ```

   It prints JSON. `"ok": true` comes with the `url`. `"reasons"` means the compose
   file broke a rule: fix it, push, wait for CI, deploy the new commit.
   `"error"` with `"logs"` means it started but didn't answer: read the logs.
4. **Check the live site** with `curl -sS "$OFFICE_URL"` and whatever paths matter.

Other commands: `office-deploy status`, `office-deploy logs [service] [lines]`.

## deploy/compose.yml rules

The deployer allows only what's listed here; anything else is rejected with a reason.

- **Images:** your own, from `ghcr.io/unlaunched/<slug>` (or
  `ghcr.io/unlaunched/<slug>/<name>` for extra images), written **without a tag**:
  the deployer pins them to the commit it deploys. Or an official image with a
  tag: `postgres`, `redis`, `nginx`, `caddy`, `httpd`, `mariadb`, `mysql`,
  `mongo`, `memcached` (e.g. `postgres:17-alpine`). No `build:`.
- **Exactly one public service** with labels `office.public: "true"` and
  `office.port: "<port it listens on>"`. Traffic for the URL goes there. No
  `ports:`; services reach each other by service name on the project network.
- **Memory:** every service sets `mem_limit` (e.g. `64m`), at most 512 MB each and
  1 GB for the whole project. Static sites need ~32m; small Node/Python apps 128–256m.
- **Volumes:** named only, declared at the top level with no options
  (`volumes: {data: {}}`), mounted as `data:/app/data`. No bind mounts.
- **Allowed service keys:** `image`, `command`, `entrypoint`, `environment`,
  `expose`, `healthcheck`, `depends_on`, `labels`, `mem_limit`, `deploy`
  (resources only), `networks`, `volumes`, `configs` (inline `content:` only),
  `read_only`, `restart`, `init`, `user`, `working_dir`, `stop_signal`,
  `stop_grace_period`.
- **Not allowed:** `ports`, `build`, `container_name`, `env_file`, `privileged`,
  `cap_add`, `devices`, `network_mode`, `extends`, `include`, `secrets`, any
  `traefik.*` label, YAML anchors/aliases, duplicate keys.
- **No `$`** variable interpolation: write a literal dollar sign as `$$`.

Minimal example (a static site):

```yaml
services:
  web:
    image: ghcr.io/unlaunched/<slug>
    mem_limit: 32m
    labels:
      office.public: "true"
      office.port: "80"
```

With a database:

```yaml
services:
  app:
    image: ghcr.io/unlaunched/<slug>
    mem_limit: 256m
    environment:
      DATABASE_URL: postgres://app:app@db:5432/app
    depends_on: [db]
    labels:
      office.public: "true"
      office.port: "3000"
  db:
    image: postgres:17-alpine
    mem_limit: 256m
    environment:
      POSTGRES_USER: app
      POSTGRES_PASSWORD: app
      POSTGRES_DB: app
    volumes:
      - db:/var/lib/postgresql/data
volumes:
  db: {}
```

Data is disposable and the database is only reachable inside the project, so plain
passwords like this are fine. Prefer SQLite in a named volume when it's enough.

## When a deploy fails

- **Checks not passed / no workflow runs:** CI hasn't finished or failed on that
  commit. `gh run view <id> --log-failed`, fix, push, deploy the new sha.
- **Image not found:** CI didn't push it. Only pushes to `main` push images; PR
  builds don't. Deploy a commit that's on `main`.
- **Didn't respond:** the app crashed or listens on another port or interface. It
  must listen on `0.0.0.0` at `office.port`. Read `office-deploy logs`.
