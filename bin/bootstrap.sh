#!/usr/bin/env bash
# First-time setup for office: `bin/bootstrap.sh vps` on the server,
# `bin/bootstrap.sh local` on the Mac. Safe to re-run. Never prints secret values.
set -euo pipefail

mode=${1:-}
if [[ $mode != local && $mode != vps ]]; then
  echo "usage: bin/bootstrap.sh local|vps" >&2
  exit 2
fi
cd "$(dirname "$0")/.."

env_file=.env
if [[ ! -f $env_file ]]; then
  cp .env.example "$env_file"
  echo "created .env from .env.example"
fi
chmod 600 "$env_file"

# The value of KEY in .env (empty if missing).
get() {
  awk -v k="$1" 'index($0, k "=") == 1 { v = substr($0, length(k) + 2) } END { print v }' "$env_file"
}

# Set KEY=VALUE in .env, replacing any existing line.
put() {
  awk -v k="$1" -v v="$2" '
    index($0, k "=") == 1 { if (!done) print k "=" v; done = 1; next }
    { print }
    END { if (!done) print k "=" v }' "$env_file" > "$env_file.tmp"
  mv "$env_file.tmp" "$env_file"
  chmod 600 "$env_file"
}

# Set KEY only if it is missing or empty.
default() {
  if [[ -z $(get "$1") ]]; then
    put "$1" "$2"
    echo "set $1"
  fi
}

# Mode settings: always applied, so the file matches the box it's on.
mode_setting() {
  if [[ $(get "$1") != "$2" ]]; then
    put "$1" "$2"
    echo "set $1=$2"
  fi
}

if [[ -z $(get DEPLOYER_SECRET) ]]; then
  put DEPLOYER_SECRET "$(openssl rand -hex 32)"
  echo "generated DEPLOYER_SECRET"
fi
default HARNESS claude
default GITHUB_ORG unlaunched

case $mode in
  local)
    mode_setting BASE_DOMAIN localhost
    mode_setting COMPOSE_PROFILES standalone
    mode_setting DEPLOYER_GIT_SOURCE local
    mode_setting DEPLOYER_REQUIRE_CHECKS false
    # Not 80: local dev stacks often hold it.
    default EDGE_PORT 8800
    mode_setting MATRIX_PUBLIC_URL "http://matrix.localhost:$(get EDGE_PORT)"
    mkdir -p var/git
    if ! docker network inspect web >/dev/null 2>&1; then
      docker network create web >/dev/null
      echo "created docker network web"
    fi
    ;;
  vps)
    mode_setting BASE_DOMAIN unlaunched.dev
    mode_setting COMPOSE_PROFILES ""
    mode_setting EDGE_PORT ""
    mode_setting DEPLOYER_GIT_SOURCE github
    mode_setting DEPLOYER_REQUIRE_CHECKS true
    mode_setting MATRIX_PUBLIC_URL https://matrix.unlaunched.dev
    mkdir -p var/git
    if ! docker network inspect web >/dev/null 2>&1; then
      echo "docker network web is missing; is the edge proxy (~/web/proxy) running?" >&2
      exit 1
    fi
    ;;
esac

missing=()
[[ -n $(get CLAUDE_CODE_OAUTH_TOKEN) ]] || missing+=(CLAUDE_CODE_OAUTH_TOKEN)
if [[ $mode == vps && -z $(get DEPLOYER_GITHUB_TOKEN) ]]; then
  missing+=(DEPLOYER_GITHUB_TOKEN)
fi
if (( ${#missing[@]} )); then
  echo "still to fill in by hand in .env: ${missing[*]}" >&2
fi

docker compose config --quiet
echo "ok: .env is complete enough for 'docker compose up -d --build'"
