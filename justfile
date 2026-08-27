# https://just.systems

# List available commands by default
default:
    @just --list

# Install and sync dependencies
install:
    uv sync

# Run the CLI entry point
run *args:
    uv run ocp-air {{args}}

# Lint and check code formatting
lint:
    uv run ruff check .
    uv run ruff format --check .

# Run tests
test:
    uv run pytest
