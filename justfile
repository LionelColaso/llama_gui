# ─── Shell ───────────────────────────────────────────────────────────────
# Use cmd.exe on Windows (just defaults to sh which may not be in PATH).
set shell := ["cmd.exe", "/C"]

# ─── Global Variables ────────────────────────────────────────────────────
# Shared flag values to keep recipes DRY and consistent.
config_and_path := "--config pyproject.toml ."

# ─── Default Target ──────────────────────────────────────────────────────
default: check

# ═══════════════════════════════════════════════════════════════════════════
# Core Development
# ═══════════════════════════════════════════════════════════════════════════

# Run the llama-gui application (GUI by default, CLI with args)
run:
    uv run python -m llamagui

# Run all tests (unit + gui, skip integration)
test:
    uv run pytest tests/unit tests/gui -q

# Run tests with verbose output and short tracebacks
test-verbose:
    uv run pytest tests/unit tests/gui -v --tb=short

# ═══════════════════════════════════════════════════════════════════════════
# Code Quality
# ═══════════════════════════════════════════════════════════════════════════

# Run the full check suite: ruff format --check, ruff check, mypy, pyright,
# jscpd, pytest (order per Agent.md §12.3). Each recipe is individually runnable.
check: ruff-format-check ruffcheck typecheck jscpd test-verbose

# Verify formatting without modifying files (ruff format --check)
ruff-format-check:
    uv run ruff format --check {{config_and_path}}

# check with ruff
ruffcheck:
    uv run ruff check {{config_and_path}}

# Type-check with mypy and pyright
typecheck: mypy pyright

# Auto-fix formatting AND lint issues (ruff only)
fix: format ruff-fix

# fix with ruff
ruff-fix:
    uv run ruff check --fix {{config_and_path}}

# Format code with ruff
format:
    uv run ruff format {{config_and_path}}

# Run copy/paste detection (jscpd); skip gracefully if npx is unavailable
jscpd:
    where npx >nul 2>nul && (npx --yes jscpd@latest . --config .jscpd.json) || (echo jscpd skipped: npx not found)

# check with mypy
mypy:
    uv run mypy {{config_and_path}}

# check with pyright
pyright:
    uv run pyright -p pyproject.toml .

# ═══════════════════════════════════════════════════════════════════════════
# Build / Distribution
# ═══════════════════════════════════════════════════════════════════════════

# Build llama-gui executable with Nuitka (runs checks first)
build *ARGS='': check
    uv run python scripts/build.py {{ARGS}}

# Build llama-gui executable with a specific version string
build-version VERSION: check
    uv run python scripts/build.py --product-version "{{VERSION}}"

# ═══════════════════════════════════════════════════════════════════════════
# Dependency Management
# ═══════════════════════════════════════════════════════════════════════════

# Install all dependencies (including dev)
dev-setup:
    uv venv
    uv sync --locked --dev

# Update all dependencies to their latest compatible versions
update:
    uv lock --upgrade

# Remove all build artifacts, caches, and generated files
clean:
    uv run python scripts/clean.py

# ═══════════════════════════════════════════════════════════════════════════
# Utilities
# ═══════════════════════════════════════════════════════════════════════════

# Show help for build script
build-help:
    uv run python scripts/build.py --help

# Print file / LOC / test counts
stats:
    uv run python scripts/stats.py