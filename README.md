# llama-gui

A PySide6 desktop app for orchestrating [`llama-server`](https://github.com/ggml-org/llama.cpp) and [`llama-swap`](https://github.com/mostlygeek/llama-swap) on **Windows, Linux, and macOS**. It resolves, installs, launches, monitors, and stops the servers through a GUI and a machine-readable CLI.

## Features

- **Four binary sources**, resolved in priority order:
  - **pointed** — use binaries you already have (a folder or separate `llama-server` / `llama-swap` paths)
  - **managed-prebuilt** — download official GitHub release builds into a managed dir (no compiler needed)
  - **managed-build** — build from the `vendor/` submodules with the local toolchain
  - **system** — binaries found on `PATH`
- **Backends:** `vulkan` (default), `cuda13`, `cuda12` (data-driven — see `orchestrator.backend_names()`).
- **GUI:** Dashboard, Actions, Resolver, Models, Logs, Settings; system-tray with minimize-to-tray.
- **CLI:** typed envelope + exit codes for scripting and tests (`--json`).

## Requirements

- Python **3.12.x** (via [`uv`](https://docs.astral.sh/uv/))
- Windows: MSVC for the Nuitka build (not needed to run via `uv`)

## Quick start

```bash
# Install dependencies
uv sync --locked --dev

# Launch the GUI
uv run python -m llamagui gui

# Or run the CLI
uv run python -m llamagui describe --json
uv run python -m llamagui status  --json
uv run python -m llamagui install        # managed-prebuilt backends
uv run python -m llamagui launch         # start llama-swap
uv run python -m llamagui stop
```

Other CLI actions: `resolve`, `update`, `use`, `restart`, `build`, `list-assets`.

## Build

A standalone executable is produced with [Nuitka](https://nuitka.net/):

```bash
just build                 # runs checks, then builds into build/llamagui.dist/
just build-version 0.1.0.0 # set a product version
```

## Checks

```bash
just check                 # ruff, mypy, pyright, jscpd, pytest (fail-fast)
just fix                   # auto-fix formatting + lint
```

`scripts/check.py` runs the same checks independently of `just`.

## Documentation

- [`docs/BUILD.md`](docs/BUILD.md) — Python/PySide6/Nuitka triple, resolver design, toolchain decisions.
- [`Agent.md`](Agent.md) — design spec, invariants, and current implementation status.
- [`mapping.md`](mapping.md) — generated file/folder tree (`uv run python scripts/mapping.py`).

## Invariant

The reference directory `D:\Github\llama-prebuilt-swap\` (if present) is **read-only** — the app may point at its binaries but never writes to, edits, or executes anything under it.
