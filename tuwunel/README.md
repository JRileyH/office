# Tuwunel

The Matrix homeserver at `matrix.<BASE_DOMAIN>`. Configured entirely through
`TUWUNEL_*` environment variables in `compose.yml` (no config file):

| Setting | Value | Why |
| --- | --- | --- |
| `server_name` | `matrix.<BASE_DOMAIN>` | Bot is `@office:matrix.unlaunched.dev`. **Can't change without wiping the database.** |
| `address` | `0.0.0.0` | Default is loopback, unreachable from the proxies |
| `allow_federation` | `false` | Default is `true` |
| `allow_registration` | `false` | Accounts are created by hand (below) |
| `trusted_servers` | `[]` | No federation, so no key servers |
| `well_known.client` | `MATRIX_PUBLIC_URL` | Element X discovers the server from `/.well-known/matrix/client` |

Image `ghcr.io/matrix-construct/tuwunel:v1.9.4`, port 8008, data in the
`tuwunel-db` volume. It supports simplified sliding sync, which Element X
requires. Docs: https://matrix-construct.github.io/tuwunel/

## Accounts

Accounts are created with Tuwunel's admin `--execute` flag, which needs the
server stopped (the database allows one process). **The first account created
becomes server admin, so create Riggy's first.**

With `just` (from the repo root):

```sh
just matrix-user riggy        # prints riggy's generated password once: save it
just matrix-office-account    # @office, with OFFICE_MATRIX_PASSWORD from .env (not printed)
```

Without `just`:

```sh
docker compose stop tuwunel
docker compose run --rm tuwunel --execute "users create_user riggy" --execute "server shutdown"
set -a; . ./.env; set +a
docker compose run --rm tuwunel --execute "users create_user office $OFFICE_MATRIX_PASSWORD" \
  --execute "server shutdown" 2>&1 | grep -E '^@office|ERROR'
docker compose start tuwunel
```

Change Riggy's password after the first sign-in (Element X → Settings).

## Push notifications check

Element X registers its own pusher with the homeserver; Tuwunel only needs
outbound HTTPS to Element's push gateway, which it has.

```sh
just matrix-ping @riggy:matrix.unlaunched.dev     # first run: creates a DM and invites riggy
# accept the invite in Element X, lock the phone, then:
just matrix-ping @riggy:matrix.unlaunched.dev "push test"
```
