set dotenv-load

default:
    @just --list

# Run the deployer test suite
test *args:
    cd deployer && uv run pytest {{args}}

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
