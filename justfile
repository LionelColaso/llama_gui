# ─── Shell ───────────────────────────────────────────────────────────────
# Use cmd.exe on Windows (just defaults to sh which may not be in PATH).
set shell := ["cmd.exe", "/C"]

# ─── Variables ───────────────────────────────────────────────────────────
# Shared flag values to keep recipes DRY and consistent.
config_and_path := "--config pyproject.toml ."
pytest_unit_gui := "--no-qt-log -s -ra tests/unit tests/gui"
pytest_integration := "--no-qt-log -s -ra tests/integration"
cov_opts := "--cov=app --cov-report=xml --cov-report=html --cov-report=term-missing --junitxml=junit/test-results.xml"

# ─── Default Target ──────────────────────────────────────────────────────
default: check

# ═══════════════════════════════════════════════════════════════════════
# Run
# ═══════════════════════════════════════════════════════════════════════

# Run the app (GUI by default; pass args for the CLI, e.g. `just run status --json`)
[group('run')]
run *ARGS='':
    uv run --locked python -m app {{ARGS}}

# Alias for `run` with no args.
[group('run')]
gui: run

# ═══════════════════════════════════════════════════════════════════════
# Tests
# ═══════════════════════════════════════════════════════════════════════

# Run all tests (integration tests skip themselves when offline)
[group('test')]
test *ARGS='':
    uv run --locked pytest {{pytest_unit_gui}} {{pytest_integration}} -q {{ARGS}}

# Run all tests with verbose output and short tracebacks
[group('test')]
test-verbose *ARGS='':
    uv run --locked pytest {{pytest_unit_gui}} {{pytest_integration}} -v --tb=short {{ARGS}}

# Run only the unit tests
[group('test')]
test-unit *ARGS='':
    uv run --locked pytest tests/unit -q {{ARGS}}

# Run only the GUI tests
[group('test')]
test-gui *ARGS='':
    uv run --locked pytest tests/gui -q {{ARGS}}

# Run only the integration suite (skips itself when offline)
[group('test')]
test-integration *ARGS='':
    uv run --locked pytest {{pytest_integration}} -v --tb=short {{ARGS}}

# Run tests and emit an HTML coverage report + XML for CI uploads
[group('test')]
coverage *ARGS='':
    uv run --locked pytest {{pytest_unit_gui}} {{cov_opts}} {{ARGS}}

# ═══════════════════════════════════════════════════════════════════════
# Code Quality
# ═══════════════════════════════════════════════════════════════════════

# Full check suite: format check, lint, typecheck, jscpd, actionlint, tests
[group('quality')]
check: ruff-format-check ruffcheck typecheck jscpd actionlint test-verbose

# Verify formatting without modifying files
[group('quality')]
ruff-format-check:
    uv run --locked ruff format --check {{config_and_path}}

# Lint with ruff
[group('quality')]
ruffcheck:
    uv run --locked ruff check {{config_and_path}}

# Type-check with mypy and pyright
[group('quality')]
typecheck: mypy pyright

# Auto-fix formatting AND lint issues
[group('quality')]
fix: format ruff-fix

# Lint fix with ruff
[group('quality')]
ruff-fix:
    uv run --locked ruff check --fix {{config_and_path}}

# Format code with ruff
[group('quality')]
format:
    uv run --locked ruff format {{config_and_path}}

# Type-check with mypy
[group('quality')]
mypy:
    uv run --locked mypy {{config_and_path}}

# Type-check with pyright
[group('quality')]
pyright:
    uv run --locked pyright -p pyproject.toml .

# Copy/paste detection (skips gracefully if npx is unavailable)
[group('quality')]
jscpd:
    uv run --locked python -c "import shutil,sys; raise SystemExit(0 if shutil.which('npx') is None else 1)" && (echo jscpd skipped: npx not found) || npx --yes jscpd@latest . --config .jscpd.json

# Lint GitHub Actions workflows (skips gracefully if not installed)
[group('quality')]
actionlint:
    uv run --locked python -c "import shutil,sys; raise SystemExit(0 if shutil.which('actionlint') is None else 1)" && (echo actionlint skipped: not installed) || actionlint

# ═══════════════════════════════════════════════════════════════════════
# Build / Distribution
# ═══════════════════════════════════════════════════════════════════════

# Build the standalone executable with Nuitka
[group('build')]
build *ARGS='':
    uv run --locked python scripts/build.py {{ARGS}}

# Build with a specific product version string
[group('build')]
build-version VERSION:
    uv run --locked python scripts/build.py --product-version "{{VERSION}}"

# Show build script help
[group('build')]
build-help:
    uv run --locked python scripts/build.py --help

# ═══════════════════════════════════════════════════════════════════════
# Dependencies
# ═══════════════════════════════════════════════════════════════════════

# Install all dependencies (including dev)
[group('deps')]
dev-setup:
    uv sync --locked --dev

# Upgrade the lockfile and sync the environment
[group('deps')]
update:
    uv lock --upgrade
    uv sync --dev

# ═══════════════════════════════════════════════════════════════════════
# Utilities
# ═══════════════════════════════════════════════════════════════════════

# Remove build artifacts, caches, and generated files
[group('util')]
clean:
    uv run --locked python scripts/clean.py

# Print file / LOC / test counts
[group('util')]
stats:
    uv run --locked python scripts/stats.py

# Regenerate mapping.md from the current tree
[group('util')]
mapping:
    uv run --locked python scripts/mapping.py

# Diff the serverargs catalogue against llama-server --help
[group('util')]
check-server-args *ARGS='':
    uv run --locked python scripts/check_server_args.py {{ARGS}}

# Show app status
[group('cli')]
status *ARGS='':
    uv run --locked python -m app status {{ARGS}}

# List models
[group('cli')]
list-models *ARGS='':
    uv run --locked python -m app list-models {{ARGS}}

# ─── Aliases (shorter / conventional names) ──────────────────────────────
alias lint := ruffcheck
alias format-check := ruff-format-check
alias lint-fix := ruff-fix
alias test-all := test
