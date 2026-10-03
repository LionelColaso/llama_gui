# llama-gui

A PySide6 desktop app that drives [`llama-server`](https://github.com/ggml-org/llama.cpp) directly on **Windows, Linux, and macOS**. The app's only external component is the `llama-server` binary itself — a *backend location* (a folder you point at, a managed prebuilt download, or `PATH`) plus llama.cpp is the entire external surface of the app. It resolves and installs the server binary, manages a library of `.gguf` models (list, download, set active, delete), and launches, monitors, and stops the server through a GUI and a machine-readable CLI.

[![codecov](https://codecov.io/gh/LionelColaso/llama_gui/graph/badge.svg?token=f7733256-a8cf-4159-830f-46f75400159f)](https://codecov.io/gh/LionelColaso/llama_gui)

> **Positioning:** this is a GUI for llama.cpp and nothing else — it wraps no other server or router. (llama-swap, the model-swapping router used by early builds of this app, was removed.)

## Features

- **Two ways to get `llama-server`** — one location, one toggle:
  - **backend location** (default) — the app downloads the official llama.cpp prebuilt release into its own managed tree
  - **OS installed** (the "Use OS installed llama.cpp" toggle) — use the `llama-server` found on your `PATH`, with the downloaded backend as fallback
- **Backends:** `vulkan` (default), `cuda13`, `cuda12`, `cpu`, `metal` (data-driven — see `backends/catalogue.py`). One catalogue drives Windows, Linux **and** macOS.
- **Model library:** the Models tab lists the `.gguf` files in your models directory, downloads a model from a URL, sets the active model, and deletes models — with the interrupted-downloads rows (resume/discard any partial transfer, model or backend) right below it. The server is launched directly with the active model.
- **Per-model server options:** the Server options page holds the global configuration. *Server options…* on a selected model opens a popup where the model either follows those globals (checked by default, and for every model without options of its own — it keeps following their later edits) or keeps a configuration of its own, where an empty option means the binary's default. *Reset server config* clears that model's own values; it does not silently put it back on the globals. `--host` / `--port` stay global, since the app probes that one port for health, status and stop.
- **Auto-install / first run:** a first-run dialog appears when nothing resolves yet, offering a download or the OS-install toggle. `bootstrap` downloads what is missing and activates it.
- **Durable settings:** paths, chosen backend and CUDA-runtime mode live in a platform-native config dir (`%APPDATA%/llamagui`, `~/.config/llamagui`, `~/Library/Preferences/llamagui`) and survive restarts; a corrupt file is backed up, not overwritten. The GitHub token is kept only in the OS keyring.
- **GUI:** Dashboard (backends, with launch/stop), Models (library + interrupted
  downloads), Server options (every `llama-server` flag, generated from the same
  catalogue the CLI uses), Logs and Settings; system-tray with minimize-to-tray.
- **CLI:** typed envelope + exit codes for scripting and tests (`--json`).

## Requirements

- Python **3.12.x** (via [`uv`](https://docs.astral.sh/uv/))
- Windows: MSVC for the Nuitka build (not needed to run via `uv`)

## Quick start

```bash
# Install dependencies
uv sync --locked --dev

# Launch the GUI
uv run python -m app gui

# Or run the CLI
uv run python -m app describe --json
uv run python -m app status  --json
uv run python -m app install        # managed-prebuilt backends
uv run python -m app launch         # start llama-server with the active model
uv run python -m app stop
```

Other CLI actions: `resolve`, `update`, `use`, `restart`, `list-assets`, `bootstrap`, `config`, and model management: `list-models`, `download-model`, `set-model`, `remove-model`.

### Install sources

`install` / `update` / `bootstrap` download the official prebuilt `llama-server`
releases (the GitHub "releases" are nightly/dev builds — llama.cpp publishes no
stable releases there, so "latest" moves frequently). If a backend has no
official prebuilt for your platform, point the app at an existing binary
(Settings → Paths) or put it on `PATH`. CUDA backends bundle
their runtime per the *CUDA runtime* setting (`auto` / `always` / `never`).

## Build

A standalone executable is produced with [Nuitka](https://nuitka.net/):

```bash
just build                 # runs checks, then builds into build/app.dist/
just build-version 0.1.0.0 # set a product version
```

`scripts/build.py` is the single build entrypoint (Nuitka `--standalone`). The
`llama-server` binary itself is **not** built here — it is downloaded as a
prebuilt release by the app at runtime (see *Install sources*).

## Checks

```bash
just check                 # ruff, mypy, pyright, jscpd, pytest (fail-fast)
just fix                   # auto-fix formatting + lint
```

`scripts/check.py` runs the same checks independently of `just`.

## Documentation

- [`docs/BUILD.md`](docs/BUILD.md) — Python/PySide6/Nuitka triple, resolver design, toolchain decisions.
- [`AGENTS.md`](AGENTS.md) — design spec and invariants.
- [`TODO.md`](TODO.md) — known bugs, gaps and planned work.
- [`mapping.md`](mapping.md) — generated file/folder tree (`uv run python scripts/mapping.py`).

## Logs

Application errors are logged via [loguru](https://loguru.readthedocs.io) to `<data-root>/logs/llamagui.log` (10 MB rotation, 7-day retention). Every CLI envelope, GUI worker failure (with traceback), llama-server spawn, and uncaught crash is recorded — the GUI has no console, so this file is the post-mortem record. `llama-server`'s own output goes to `state/llama-server.{out,err}.log` under the managed root.

## Invariant

The app's only write surface is its own managed root (default `~/.llamagui`, configurable). Any install you point it at — a folder or a direct binary path — is **read-only** to the app: it resolves the `llama-server` there and runs it, but never writes to, edits, or creates anything under it (no state, no logs, no lockfiles).
