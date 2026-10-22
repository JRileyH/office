set dotenv-load

default:
    @just --list

# Run the deployer test suite
test *args:
    cd deployer && uv run pytest {{args}}

# First-time setup: fills in .env for this box (local or vps)
bootstrap mode:
    bin/bootstrap.sh {{mode}}

# Prove Claude Code runs headless in the office image on the subscription token
claude-check:
    docker compose run --rm --no-deps office claude -p "Reply with exactly: office ok" --output-format json --max-turns 1 \
        | jq '{result, is_error, model: (.modelUsage | keys), cost_usd: .total_cost_usd}'

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
