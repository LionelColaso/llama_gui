# llama-gui

A PySide6 desktop app for orchestrating [`llama-server`](https://github.com/ggml-org/llama.cpp) and [`llama-swap`](https://github.com/mostlygeek/llama-swap) on **Windows, Linux, and macOS**. It resolves, installs, launches, monitors, and stops the servers through a GUI and a machine-readable CLI.

## Features

- **Four binary sources**, resolved in priority order:
  - **pointed** — use binaries you already have (a folder or separate `llama-server` / `llama-swap` paths)
  - **managed-prebuilt** — download official GitHub release builds into a managed dir (no compiler needed)
  - **managed-build** — build from the `vendor/` submodules with the local toolchain
  - **system** — binaries found on `PATH`
- **Backends:** `vulkan` (default), `cuda13`, `cuda12`, `cpu`, `metal` (data-driven — see `models.BACKENDS`). One catalogue drives Windows, Linux **and** macOS.
- **Auto-install / first run:** a first-run dialog appears when nothing resolves yet, offering download, point-at, or build. `bootstrap` downloads what is missing and activates it.
- **Durable settings:** paths, chosen backend, install-source and CUDA-runtime mode live in a platform-native config dir (`%APPDATA%/llamagui`, `~/.config/llamagui`, `~/Library/Preferences/llamagui`) and survive restarts; a corrupt file is backed up, not overwritten. The GitHub token is kept only in the OS keyring.
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

Other CLI actions: `resolve`, `update`, `use`, `restart`, `build`, `list-assets`, `bootstrap`, `config`.

### Install sources

`install` / `update` / `bootstrap` prefer official prebuilt releases by default. If you
pass `--source build` (or set *Install source* to `build` in Settings), the app builds
from the vendored `llama.cpp` source instead — and if a build is impossible (no toolchain),
it reports `Toolchain not found` rather than silently downloading. CUDA backends bundle
their runtime per the *CUDA runtime* setting (`auto` / `always` / `never`).

## Build

A standalone executable is produced with [Nuitka](https://nuitka.net/):

```bash
just build                 # runs checks, then builds into build/llamagui.dist/
just build-version 0.1.0.0 # set a product version
```

Building from source uses the git submodules (`vendor/llama.cpp`, `vendor/llama-swap`):

```bash
just build-source                       # git submodule update, build backends + llama-swap + GUI
just build-llama-cpp cuda12             # only the CUDA 12 backend (skips the GUI build)
just build-llama-swap                   # only llama-swap (skips the GUI build)
uv run python scripts/build.py --build-llama-cpp --config Release --cuda-arch 75
```

`build.py` checks out the submodules for you (`git submodule update --init --recursive`)
unless you pass `--skip-submodules`. Compiled binaries land in the app's managed
directory (`<root>/managed/<backend>`), so the GUI can use them immediately.

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
