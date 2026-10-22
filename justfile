set dotenv-load

default:
    @just --list

# Run both test suites
test:
    cd deployer && uv run pytest -q
    cd office && uv run pytest -q

# First-time setup: fills in .env for this box (local or vps)
bootstrap mode:
    bin/bootstrap.sh {{mode}}

# Prove Claude Code runs headless in the office image on the subscription token
claude-check:
    docker compose run --rm --no-deps office claude -p "Reply with exactly: office ok" --output-format json --max-turns 1 \
        | jq '{result, is_error, model: (.modelUsage | keys), cost_usd: .total_cost_usd}'

# Ask the deployer to deploy a commit (the secret is expanded inside the office container)
deploy slug sha:
    docker compose exec -T office sh -c 'curl -sS -X POST http://deployer:8000/deploy \
        -H "X-Deployer-Secret: $DEPLOYER_SECRET" -H "Content-Type: application/json" \
        -d "{\"slug\": \"{{slug}}\", \"sha\": \"{{sha}}\"}"' | jq .

# Deployer status for a project
status slug:
    docker compose exec -T office sh -c 'curl -sS "http://deployer:8000/status?slug={{slug}}" \
        -H "X-Deployer-Secret: $DEPLOYER_SECRET"' | jq .

# Stop a project (keeps its volumes)
stop slug:
    docker compose exec -T office sh -c 'curl -sS -X POST http://deployer:8000/stop \
        -H "X-Deployer-Secret: $DEPLOYER_SECRET" -H "Content-Type: application/json" \
        -d "{\"slug\": \"{{slug}}\"}"' | jq .

# Local mode: push examples/hello to the bare repo, build its image, deploy it
hello-local:
    #!/usr/bin/env bash
    set -euo pipefail
    repo=var/git/hello.git
    [[ -d $repo ]] || git init -q --bare --initial-branch=main "$repo"
    work=$(mktemp -d)
    trap 'rm -rf "$work"' EXIT
    git clone -q "$repo" "$work" 2>/dev/null
    cp -R examples/hello/. "$work"/
    git -C "$work" add -A
    git -C "$work" -c user.name=office -c user.email=office@localhost commit -qm "Update hello" || true
    git -C "$work" push -q origin HEAD:main
    sha=$(git -C "$work" rev-parse HEAD)
    # What CI does on GitHub: an image tagged with the commit.
    docker build -q -t "ghcr.io/unlaunched/hello:$sha" examples/hello >/dev/null
    just deploy hello "$sha"

# Create a Matrix account and print its generated password once. The first account becomes server admin.
matrix-user name:
    docker compose stop tuwunel
    docker compose run --rm tuwunel --execute "users create_user {{name}}" --execute "server shutdown"
    docker compose start tuwunel

# Create the office bot account with OFFICE_MATRIX_PASSWORD from .env (never printed)
matrix-office-account:
    docker compose stop tuwunel
    docker compose run --rm tuwunel --execute "users create_user office $OFFICE_MATRIX_PASSWORD" \
        --execute "server shutdown" 2>&1 | grep -E '^@office|ERROR' || true
    docker compose start tuwunel

# DM a Matrix user from the office account (Phase 1 push-notification check)
matrix-ping to *message:
    docker compose exec -T office python -m office.matrix_ping {{to}} {{message}}

# Delete every project and start from zero (says what will go, asks first)
nuke:
    bin/nuke.sh

# Build and start the stack
up:
    docker compose up -d --build

# Stop the stack, keeping volumes
down:
    docker compose down

# Show service state
ps:
    docker compose ps

# Follow a service's logs
logs svc:
    docker compose logs -f {{svc}}
