# llama-gui Build Documentation

## Overview

llama-gui is a PySide6 desktop application for orchestrating `llama-server` and `llama-swap` on **Windows, Linux, and macOS**. It supports four binary sources: **pointed**, **managed-prebuilt**, **managed-build**, and **system**.

## Working Triple (Python / PySide6 / Nuitka)

| Component | Version | Notes |
|-----------|---------|-------|
| Python | 3.12.x | Required: `>=3.12,<3.13` |
| PySide6 | >=6.7 | Pin to a line Nuitka's pyside6 plugin supports |
| Nuitka | >=2.0 | `--standalone` mode; `--onefile` is riskier with Qt |

> **Re-resolve at build time.** The versions above are examples — always verify against the actual release pages and Nuitka release notes.

## Binary Resolver — Four Sources

| Source | Meaning | When Used |
|--------|---------|-----------|
| **pointed** | User supplies explicit path(s) to already-working binaries | "I already have a working install" |
| **managed-prebuilt** | App downloads official GitHub release archives (.zip / .tar.gz) matched to the current OS & architecture | Default "just works, no compiler" path |
| **managed-build** | App builds from git submodules (`vendor/llama.cpp`, `vendor/llama-swap`) | "I want to build from source" |
| **system** | Binaries found on `PATH` (`shutil.which`) | "I installed them globally" |

**Runtime independence:** The submodules are a build-time/dev-time reproducibility source. At runtime the app never *requires* the submodules to be present or built — it uses whatever the resolver finds.

## Read / Write Split

```
┌──────────────────── PySide6 GUI + CLI (Python 3.12) ────────────────────┐
│  dashboard │ actions │ models │ logs │ settings                          │
└──────┬───────────────────────────────────────────────┬──────────────────┘
       │ READ  (pure Python, sub-ms, no subprocess)    │ WRITE (in-process engine)
       ▼                                               ▼
  app's own managed root files:                  engine.orchestrator.*
   - state/active.txt                            (downloads / builds / launches /
   - managed/<b>/.version                         stops / edits config)
   - managed/current  (junction target)
   + socket connect 127.0.0.1:<port>
```

## Contract Version

The `contract_version` is `"1"` and is **locked**. The CLI JSON envelope and the GUI's in-process calls share the same dataclasses in `contract.py`. The GUI checks `contract_version` against the engine it imported (trivially equal in-process, but the field exists so an externally-driven CLI can't drift silently).

## Submodule Pin vs Prebuilt Latest Cadence

- **Submodules** (`vendor/llama.cpp`, `vendor/llama-swap`): Pinned commits for reproducible builds. Update deliberately.
- **Prebuilt downloads**: Resolved from the latest GitHub release at download time. These are two independent update cadences.

## Toolchain Notes

### Check Suite
`just check` is the canonical command for the full check suite (ruff format, ruff check, mypy, pyright, jscpd, pytest). It is fail-fast and stops at the first failing step:
```bash
just check                                 # run all checks
just fix                                   # auto-fix formatting + lint issues
```
`scripts/check.py` is **independent** of `just` (it never shells out to `just`) and runs the **exact same checks with the same scope and config** (`--config pyproject.toml .`, pytest `tests/unit tests/gui -v --tb=short`, jscpd via `npx`). Use it on machines without `just` installed:
```bash
uv run python scripts/check.py             # run all checks (no `just` required)
uv run python scripts/check.py --fix       # auto-fix (mirrors `just fix`)
```

PSScriptAnalyzer is still invoked *if* PowerShell is available (legacy `.ps1` files), but it is never required.

### shfmt ≠ PS Formatter
- **shfmt** formats POSIX shell / bash only (`.sh` files). Configured via `.editorconfig` `[*.sh]`.
- **PSScriptAnalyzer** is for PowerShell (`.ps1` files). Run `Invoke-ScriptAnalyzer -Path scripts -EnableExit -Recurse`.

### jscpd = Duplication Detection
jscpd is a copy-paste detector, not a formatter. Run via `npx --yes jscpd@latest .`. Configuration in `.jscpd.json`.

## Building with Nuitka

### Prerequisites (per platform)

| OS | Required Toolchain |
|----|--------------------|
| Windows | MSVC (Visual Studio 2022 Build Tools with C++ workload). Run from a `vcvarsall`-initialized shell, or let Nuitka locate MSVC. |
| Linux | `gcc`/`clang`, `cmake`, `ninja` or `make`, plus Qt/X11 dev packages (e.g. `libxkbcommon-x11-0`, `libxcb-*`). |
| macOS | Xcode Command Line Tools (`xcode-select --install`), `cmake`, `ninja`. |

All platforms also require Python 3.12.x and `uv` (or an equivalent virtual environment with the project dependencies installed).

### Build (all platforms)

The Python build wrapper `scripts/build.py` produces a standalone executable for the current OS:

```bash
# Debug build (console visible on Windows; normal output on POSIX)
uv run python scripts/build.py --dev

# Release build with a specific version string
uv run python scripts/build.py --product-version "0.1.0.0"

# Help
uv run python scripts/build.py --help
```

### Via just
```bash
just build
just build-version 0.1.0.0
```

### Nuitka Gotchas
1. **Qt plugins at runtime**: `platforms/qwindows.dll` (Windows) / `platforms/libqminimal.so` etc. (Linux) must be present. The build script includes `--include-qt-plugins=platforms,imageformats,iconengines,tls`.
2. **Ship `--standalone`** (a folder). Attempt `--onefile` only after standalone works.
3. **C/C++ runtime**: On Windows the VC++ runtime must be present or bundled in the output. On Linux, glibc compatibility matters when distributing to older systems.
4. **Console disabled**: With console disabled, an uncaught exception is invisible. The app installs a top-level `sys.excepthook` + file logging.
5. **First compile is long**: Use ccache/`--clang-cache` if available.
6. **AV false positives**: Expect them on packed executables. Sign if distributing.
7. **Post-build verify**: The build script runs `describe --json` on the built executable (the CLI has no `--version` flag) and asserts exit 0.

## Running Without Building

```bash
# Launch GUI
uv run python -m llamagui

# CLI commands
uv run python -m llamagui describe --json
uv run python -m llamagui status --json
uv run python -m llamagui install vulkan --json
```

## Testing

```bash
# Unit + GUI tests (no network)
uv run pytest tests/unit tests/gui -q

# Integration tests (needs network)
uv run pytest tests/integration -q

# All checks
just check
# or
uv run python scripts/check.py
```
