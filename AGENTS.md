---

# AGENTS.md — `llama_gui` build spec & architecture

> **Who this is for:** an AI coding agent (or human) working in `D:\Github\llama_gui`.
> **What this app is:** a PySide6 desktop app that drives [`llama-server`](https://github.com/ggml-org/llama.cpp) (llama.cpp) **directly** on Windows, Linux and macOS, and manages a library of `.gguf` models. Its entire external surface is exactly one binary — `llama-server` — from exactly one place: the **backend location** (the app's download tree), unless the user checks the **"Use OS installed llama.cpp"** toggle, in which case `PATH` wins. It resolves & installs the server binary, lists / downloads / activates / deletes models, and launches / monitors / stops the server — all through a GUI and a machine-readable CLI.
> **What changed:** the app no longer uses **llama-swap**. There is no router, no `config.yaml`, and no swap binary. The app spawns `llama-server` itself with the active model. Any "swap" you see is historical. The app also **no longer builds llama.cpp from source**: the `vendor/llama.cpp` submodule, `backends/build.py`, the `build` action and the `install_source` setting are removed — binaries are downloaded from prebuilt GitHub releases into the backend location, or (user toggle) taken from `PATH` (legacy `managed-build` artifacts from older versions still resolve).
> **Read order:** §1 (read-only invariant), §2 (binary sources), §3 (model library), §5 (architecture), §6 (invariants), §12 (real repo layout), §18 (current status).
> **Doc status:** documents the design *and* the implemented codebase, kept in sync as of 2026-08-17. Version numbers marked `EXAMPLE` are placeholders — re-resolve at build time.
> **Module map vs the original spec:** `contract.py` → `schemas.py` (contract v4); `managed/` → `backends/`; `state_reader.py` + `progress.py` → merged into `lifecycle.py` + `models.py`; `gui/workers.py` → `gui/worker_pool.py`; **`config_yaml.py` deleted** (settings are now a single JSON file owned by `config.py`; `ruamel.yaml` is gone). See §12 for the real layout. `mapping.md` is the generated file/folder tree.

---

## 1. Invariant #0 — the app only writes inside its own managed root

The app's **write** surface is exactly its managed root (`AppConfig.root`): `state/`, `managed/`, `downloads/`, and the models directory. Everything else is **read-only** to the app.

- A **system (OS-installed)** `llama-server` — used when the "Use OS installed llama.cpp" toggle is on — is used **read-only**: the app resolves and executes the binary but never writes, edits, deletes, or creates anything in the system install location. No lockfile, no log, no state.
- A reference install directory the user points at has the same guarantee: point at it, never mutate it.
- The app never symlinks into an external dir; any logic it needs is re-implemented in this tree.

> **Consequence:** the engine is pure Python. It does **not** shell out to any external script (there is no `setup-llama.ps1` anymore) and does not parse colored/`[OK]` process output. The machine interface is the Python CLI (§11).

---

## 2. Glossary — how the app obtains `llama-server`

The app must work whether or not it downloaded anything itself. There is exactly **one location input** — the **backend location** (`<root>/managed`, the tree the app downloads prebuilt backends into) — plus one **toggle** (`AppConfig.use_os_llama_server`, "Use OS installed llama.cpp") that switches resolution to the OS install on `PATH`. One **Binary Resolver** (§7) performs the resolution:

| Source | Meaning | When used |
|---|---|---|
| **managed‑prebuilt** | App downloads the official `ggml-org/llama.cpp` GitHub release assets into the backend location (asset-glob + size cache + `.version` marker). | Default "just works, no compiler" path. |
| **system** | `llama-server` found on `PATH` (`shutil.which`). | "I installed it globally / via a package manager" — checked as a **toggle**, never as a path. Falls back to the backend location when `PATH` has nothing. |

**No from-source builds.** The `vendor/llama.cpp` submodule and the managed-build path were removed: the app never compiles llama.cpp. Note the GitHub "releases" the downloader fetches are nightly/dev builds (llama.cpp publishes no stable releases there — see its `docs/release.md`), so "latest" moves frequently. Legacy: `.version` markers from older versions' from-source builds are still *read* and reported as the `managed-build` source label (resolver + dashboard badge); such artifacts are never produced again.

---

## 3. Model library — `.gguf` store

Models are plain `.gguf` files in a user-configurable directory (default `<root>/models`, set via `AppConfig.models_dir`). There is **no index and no lock**:

- **List** — a directory scan for `*.gguf` (name, size, mtime), sorted by name.
- **Download** — stream a URL (e.g. a Hugging Face `resolve/main/…gguf` link) into a `<name>.part` temp file, then rename into place only when complete, so a crash or cancel never leaves a truncated model. Downloads are **resumable** (HTTP `Range`). Progress rides the same `emit_progress("model", …)` channel as backend downloads.
- **Set active** — `AppConfig.active_model` records the file name the server will launch.
- **Delete** — remove one file by name; path traversal (`../`) is rejected by design.

`launch` runs `llama-server -m <models_dir>/<active_model>`; if no active model is set the GUI/CLI says so rather than guessing.

---

## 4. Goals / Non‑goals

**Goals**
- Native desktop app (PySide6, Python 3.12) that **is** the orchestration engine (resolve / obtain / launch / verify / stop `llama-server`, plus model management) **and** its GUI.
- Works with **no build step of its own**: the downloaded backend location, or the OS install on `PATH` (toggle).
- Strict polyglot toolchain: `uv` (deps/run), `ruff` (lint+format), `mypy` + `pyright` (types), `jscpd` (duplication). One command runs all checks.
- Reproducible packaging with **Nuitka** (`--standalone`).
- A **machine interface** (CLI `--json` + exit codes) so the GUI, tests, and external tools all drive one typed engine.

**Non‑goals**
- Writing/editing/deleting anything outside the managed root (§1).
- Shelling out to PowerShell as the engine (the engine is Python; the only `subprocess` use is spawning the resolved `llama-server` and the platform link/junction helpers).
- A long‑running background daemon (the read/write split removes the need; §5.2).
- Nuitka `--onefile` as the default (ship `--standalone` first).
- Managing any binary other than `llama-server` (no llama-swap, no router).

---

## 5. Architecture

### 5.1 Engine‑in‑Python
All orchestration is Python. `llama-server` is spawned **directly by the app** (no intermediary). The engine's responsibilities: asset selection, download cache, per-backend `.version` markers, idempotent install/update, wipe‑then-extract, the `managed/current` link, hidden launch, port verification, scoped stop, and the model store.

### 5.2 Read / write split (both Python)
```
┌────────────────────── PySide6 GUI + CLI (Python 3.12) ─────────────────────┐
│   dashboard │ actions │ resolver │ models │ logs │ settings                │
└──────┬───────────────────────────────────────────────────┬─────────────────┘
       │ READ  (pure Python, sub-ms, no subprocess)        │ WRITE (in-process engine)
       ▼                                                   ▼
  app's own managed root files:                    engine.orchestrator.*
   - state/active.txt                               (downloads / builds / launches /
   - managed/<backend>/.version                      stops / model ops / config) —
   - managed/current  (link target)                   spawns the resolved llama-server
   - state/pids.json                                   detached, tracks its pid
   - models/ (*.gguf)
  + socket connect 127.0.0.1:<port>
```
- **READS** (`status`, versions, active backend, models list, is-listening): pure Python — read files, resolve the `managed/current` link (`os.readlink`, with a reparse-point fallback on Windows), non-blocking `socket.create_connection((host, port), 0.2)` for liveness, and a directory scan for models. No subprocess on the hot path, so the dashboard can poll.
- **WRITES**: in-process engine calls. The only `subprocess` call is launching the resolved `llama-server` (plus the `mklink /J` fallback on Windows). Never `powershell.exe` as the engine.

### 5.3 Machine interface (CLI)
`python -m llamagui <action> [--json]` emits a single JSON **envelope** (or a human-readable summary) with a typed payload and a stable exit code (see §11). The GUI calls the same `Orchestrator` methods in-process; the CLI is the externally drivable surface. `contract_version` is `"4"` and is checked so an externally-driven CLI can't drift silently.

---

## 6. Invariants (the rules that must survive refactors)

1. **Never write outside the managed root** (§1). The OS-installed binary is read-only.
2. **Asset selection is data** (glob per backend). Adding a backend = one row in `models.BACKENDS`, not new control flow.
3. **Wipe‑then‑extract** a backend dir on (re)install (prevents stale DLLs). Deletion is only ever: the temp download scratch, the one backend dir being replaced, or the `managed/current` **link** (never the target's contents).
4. **Idempotent update:** re-running `install`/`update` when nothing changed reports `skipped`, not a redundant download.
5. **Copy with literal paths**, never a wildcard where a specific file is expected.
6. **A launch is not a liveness claim.** `launch --verify` polls the port; success requires the port to be listening **and** the pid alive. On failure, return exit **1** with `log_tail`.
7. **Hidden launch:** Windows `DETACHED_PROCESS | CREATE_NO_WINDOW`; POSIX `start_new_session=True`. The server outlives the GUI and gets no console window. Logs → `state/llama-server.{out,err}.log`.
8. **Stop kills only PIDs the app spawned.** The engine keeps `state/pids.json` (`{llama_server: <pid>, servers: {…}}`) and an in‑memory set; Stop terminates **exactly those**, then verifies the port is free. Never scan-and-kill by name or path. (A legacy `llama_swap` pid key is still honored so an upgrade from a pre-direct-server build never orphans a process.) On startup, reconcile the pidfile against live pids: stale → drop; live-but-not-ours → do **not** kill, report "port held by unknown process."
9. **Health probe:** `/health`; model list `/v1/models`. A `/v1` 404 is not "server down."
10. **Backend list is data.** Default backend = `vulkan`.
11. **cuda12 is self-contained:** its binary needs the bundled cudart‑12.x DLLs; the managed-prebuilt path fetches the cudart pack and drops the DLLs next to the binary (per the *CUDA runtime* setting `auto`/`always`/`never`).
12. **Server args are explicit and configurable:** `--host`, `--port`, `-c <ctx>` (omitted when ctx is `auto`/0 so llama.cpp uses the model default), `-ngl <layers>`, plus a free-form *extra server args* string appended last. All are Settings; the command line is built by `lifecycle.build_llama_server_args`.
13. **App errors are always logged.** loguru writes every entry to `<data-root>/logs/llamagui.log` (10 MB rotation, 7-day retention, enqueued so worker threads are safe). Choke points: `cli.emit` (every envelope), `EngineWorker._execute_action` (every GUI worker failure, with traceback), `lifecycle.launch_llama_server` (spawn), and the entry-point excepthook (uncaught crashes). The enqueued queue is drained (`logger.complete()`) before CLI/GUI exit and inside the excepthook, so a last-second error survives process death. The GUI has no console — the log file is the only post-mortem record.
14. **Qt widgets are touched only on the GUI thread.** Worker actions run on QThreadPool threads; progress and results travel via Qt signals (auto-queued). `EngineWorker` connects the progress slot to the `progress` signal and the engine's callback only ever emits that signal — a direct widget call from a worker thread is undefined behaviour and crashed the process mid-download (silent C-level abort, no traceback).

---

## 7. Binary Resolver — design

`resolver.py` returns a `ResolvedBinary(path, source, version, valid, error)` for `llama-server` (the only binary, `BINARY_NAMES = ("llama-server",)`).

**Resolution order** (one toggle, `AppConfig.use_os_llama_server`, default off):
1. **system** (only when the "Use OS installed llama.cpp" toggle is on) — `shutil.which("llama-server")` (OS install / package manager).
2. **managed** — the backend location `<root>/managed`: the `managed/current` link's target first, else `<root>/managed/<default_backend>`. The source label distinguishes `managed-prebuilt` vs the legacy `managed-build` (older from-source artifacts) from the `.version` marker. When the toggle is on and `PATH` has nothing, resolution falls back to the backend location.

**Validation (mandatory before trusting a source):** run `<exe> --version` (short timeout); `valid = (returncode == 0)`; capture the version string. A binary that fails is reported `valid=false` with stderr, **not** silently used. The UI shows the **source** label so the user always knows what's running.

**Fast-path reads:** `resolve_llama_server(cfg, validate=False)` only checks existence (no subprocess) so `status()` never spawns `--version` on the dashboard's 3 s refresh. The `resolve` action and `launch`/`restart` use `validate=True`. `anything_resolved(cfg)` (validate=False) drives the first-run decision.

**First-run acceptance:** with the OS toggle on and a working `llama-server` on `PATH`, the app must validate and run the already-installed binary with **zero** download. That is the day-one path and a required test.

---

## 8. Managed install — prebuilt download

`backends/prebuilt.py`: query `https://api.github.com/repos/ggml-org/llama.cpp/releases/latest` (or a pinned tag), pick the asset for the backend + platform by glob, download into `downloads/` (cache by size), extract the folder containing `llama-server(.exe)` flat into `managed/<backend>/`, write `.version`. For cuda12, also fetch the cudart pack (invariant #11). Optional GitHub token (keyring) for rate limits.

> The fetched assets are nightly/dev builds (llama.cpp publishes no stable GitHub releases — see its `docs/release.md`), so "latest" moves frequently. The app does not vendor or compile llama.cpp sources.

---

## 9. Launch / verify / stop — the server lifecycle

All in `lifecycle.py`, cross-platform (Windows `DETACHED_PROCESS | CREATE_NO_WINDOW` + `TerminateProcess`; POSIX `start_new_session` + `SIGTERM`→`SIGKILL`).

- **Command line** (`build_llama_server_args`):
  `[llama-server, -m, <model_path>, --host, <host>, --port, <port>, [-c, <ctx>], -ngl, <ngl>, <extra…>]`
  where `<model_path> = <models_dir>/<active_model>` and `host/port/ctx/ngl/extra` come from `AppConfig`. `-c` is omitted when the configured ctx is `auto` (≤ 0) so llama.cpp falls back to the model's own default context.
- **Launch** (`launch_llama_server`): spawn detached, stdout/stderr → `state/llama-server.{out,err}.log` (opened in Python), record the pid under `llama_server` in `state/pids.json`. With `verify=True`, poll the port first and treat "never came up" as failure (invariant #6).
- **Verify:** poll the port up to ~8 s; success only if listening **and** pid alive. On failure, read the last N log lines and return exit **1** with `log_tail`.
- **Stop** (`stop_processes`): read `state/pids.json`, terminate exactly the recorded pids (graceful, then force after a grace period), then verify the port is free. For the system source this is the *only* safe scoping (invariant #8).
- **Switch backend** (`use`): repoint the `managed/current` link to `managed/<backend>`, write `state/active.txt`. Requires the backend installed (or `--auto-install`).

---

## 10. Model store

`model_store.py` (no lock, no index):
- `list_models(dir)` → sorted `ModelInfo(name, size_bytes, modified)[]`.
- `model_name_from_url(url)` → the asset name (strips `?…`/`#…`), or a stable `model-<hash>.gguf` when the URL has no file name.
- `download_model(url, dir)` → stream into `<name>.part` (resume via `Range`), verify total size, `rename` into place. Emits `emit_progress("model", done, total, "download")`. Raises `ModelDownloadError` on HTTP/network failure (the `.part` is kept so a retry resumes).
- `remove_model(dir, name)` → delete one file; rejects path traversal; raises `FileNotFoundError` when missing.

---

## 11. CLI contract

`python -m llamagui <action> [--json]`. Actions (orchestrator `ACTIONS`):
`describe`, `status`, `resolve`, `bootstrap`, `install [backends…] [--force]`, `update [backends…] [--force]`, `use <backend> [--auto-install]`, `stop`, `launch [--verify]`, `restart [--verify]`, `list-models`, `download-model <url>`, `set-model <name>`, `remove-model <name>`, `list-assets`, `config`, and `gui`.

- `--json` prints a single JSON **envelope**: `{ contract_version, ok, exit_code, action, root, timestamp, duration_ms, data, error?, log_tail?, warnings? }`. Stdout is one object; diagnostics go to stderr.
- **Exit codes** (`schemas.ExitCode`): 0 success, 1 unexpected error, 2 not available, 3 network, 4 lock conflict, 5 bad argument, 6 contract mismatch.
- **`status` payload** (`StatusData`): `{ backends: {<name>:{installed,version,source,…}}, active, junction_target, server: {host, port, listening, pids, model}, models: {dir, models:[{name,size_bytes,modified}], active}, resolved: {llama_server: {path, source, version, valid}}, platform, root, config_file, ready, first_run_complete }`.
- **`install`/`update` payload** (`InstallData`): `{ release, results:[{name, status: ok|skipped|failed, version, bytes}], summary:{updated, skipped, failed} }`.

---

## 12. Real repo layout

```
llama_gui/
├── pyproject.toml                 # uv project; deps (no ruamel); check tooling
├── justfile                       # canonical tasks (check/fix/build/…)
├── README.md  AGENTS.md  mapping.md
├── docs/BUILD.md                  # triple, resolver, cadence, Nuitka notes
├── scripts/
│   ├── build.py                   # Nuitka GUI build
│   ├── check.py                   # independent check runner (no `just` dependency)
│   └── mapping.py                 # regenerates mapping.md
└── llamagui/
    ├── __init__.py  __main__.py   # `python -m llamagui` (installs loguru + excepthook)
    ├── applog.py                  # loguru config: rotating file sink + excepthook
    ├── cli.py                     # argparse + envelope + exit codes (§11)
    ├── config.py                  # AppConfig (JSON), keyring token
    ├── schemas.py                 # contract v4 dataclasses (StatusData, InstallData, …)
    ├── resolver.py                # llama-server resolver (backend location + OS toggle)
    ├── lifecycle.py               # launch/verify/stop + pure-Python state reads
    ├── model_store.py             # .gguf list/download/set-active/remove
    ├── orchestrator.py            # actions, locking, wiring
    ├── models.py                  # backend table + Source + PROGRESS-line parser
    ├── paths.py                   # platform paths (root, config_file, exe_suffix)
    ├── locking.py                 # named mutex / lockfile for mutations
    ├── token.py                   # keyring-backed GitHub token (never plaintext)
    ├── backends/
    │   └── prebuilt.py            # GitHub release download + cache + .version
    └── gui/
        ├── app.py  main_window.py # tray / launch-on-start / start-minimized
        ├── worker_pool.py         # QThread/QRunnable around the engine (never block UI)
        ├── dialogs/first_run.py   # shown when nothing resolves
        ├── pages/ {dashboard, actions, resolver, models, logs, settings}.py
        └── widgets/ {backend_card, source_badge, progress_bar, log_view, model_table}.py
tests/
├── conftest.py
├── unit/   test_contract.py test_progress.py test_resolver.py test_orchestrator.py
│           test_lifecycle.py test_lifecycle_posix.py test_cli.py
│           test_prebuilt.py test_config_durable.py test_applog.py
├── integration/  test_managed_prebuilt.py   # gated: network
└── gui/    conftest.py test_main_window.py test_phase7.py test_dashboard.py
```

---

## 13. GUI (PySide6)

- **Pages:** a single scrolling **Dashboard** whose first section is **Backends** — the former overview and Resolver merged into one (per-backend cards + source badges + active backend + server listening badge + the resolved `llama-server` binary with source/valid/path and a Download/Re-check row) — followed by **Actions** (install/update/stop/launch/restart with progress) and **Models** (read-only `.gguf` table + download/set-active/delete) sections; plus **Logs** (tail of `state/llama-server.{err,out}.log`) and **Settings** (all `AppConfig` fields incl. server options: host, port, ctx, GPU layers, extra args, models dir). The sidebar is thus Dashboard / Logs / Settings.
- **Workers:** every mutation runs on a `WorkerPool` `QThread`; the UI thread never blocks. Progress comes from the `emit_progress` channel.
- **First run:** when `anything_resolved` is false and `first_run_complete` is unset, `FirstRunDialog` offers *download the latest release* or *point at a binary I already have*.
- **Tray:** minimize-to-tray on close; a persistent tray icon with Show/Quit.
- **Theme:** system/light/dark, applied from `gui/app.py` via the `gui/theme.py` QSS design system (centralized tokens, palette + stylesheet; `system` follows the OS color scheme).

---

## 14. Testing

- **Unit** (`tests/unit`): resolver (stub exes, fast path), orchestrator (temp root), lifecycle (launch/stop against a tiny fake server bound to the port; POSIX variant), prebuilt (stubbed `httpx`), config (atomic save, corrupt recovery, unknown-key preservation), contract (envelope round-trip, `describe --json` validates against the schema), progress (PROGRESS-line parser).
- **GUI** (`tests/gui`, `pytest-qt`, engine mocked): main window, navigation, pages, widgets. Run headless with `QT_QPA_PLATFORM=offscreen`.
- **Integration** (`tests/integration`): real network download into a temp managed root — gated.
- Regression guards prove closing the window stops the app (not just hides it).

## 15. Build & checks

- `just check` is the canonical, fail-fast suite: ruff format/check, mypy, pyright, jscpd, pytest. `just fix` auto-fixes formatting + lint.
- `scripts/check.py` runs the **same checks** independently of `just`.
- `just build` → Nuitka `--standalone` into `build/llamagui.dist/`; `just build-version X.Y.Z.W` sets a product version.

## 16. CI & releases

Standalone gate workflows, a reusable `build.yml`, a PR gate (`ci.yml`), a push-to-main auto-build (`auto_build.yml`), and a manual `release.yml`. `scripts/build.py` is the single build entrypoint. (See `docs/BUILD.md`.)

## 17. Hard "do not" list

- Do **not** write/edit/delete anything outside the managed root (§1).
- Do **not** reintroduce llama-swap, a router, `config.yaml`, or `ruamel.yaml`.
- Do **not** parse colored/`[OK]` text from any process.
- Do **not** treat a `Popen`/launch return as liveness — poll the port (invariant #6).
- Do **not** copy with a wildcard where a literal path is expected (invariant #5).
- Do **not** kill processes by name or path-scan — only pids the app spawned (invariant #8).
- Do **not** hardcode the backend list, the root path, or version pins (data / config / resolve-at-build-time).
- Do **not** store the GitHub token in plaintext (keyring/DPAPI).
- Do **not** reintroduce a from-source llama.cpp build path, `vendor/` submodule, or toolchain detection.

---

## 18. Current implementation status (2026-08-17)

**Implemented & verified** — `just check` is green (ruff format/check, mypy, pyright, jscpd) and the pytest suite passes (159 passed, 5 skipped, 2 deselected with `QT_QPA_PLATFORM=offscreen` on Windows):

- **Engine:** backend-location + OS-toggle `llama-server` resolver (`resolver.py`); orchestrator with the full action set incl. model management (`orchestrator.py`); direct hidden launch + port/pidfile stop (`lifecycle.py`); managed prebuilt download (`backends/`); `.gguf` model store (`model_store.py`); JSON config ownership (`config.py`); mutation lock (`locking.py`).
- **CLI** (`cli.py`): `describe / status / resolve / config / bootstrap / install / update / use / stop / launch / restart / list-models / download-model / set-model / remove-model / list-assets / gui` plus the `--json` envelope and exit codes (§11).
- **GUI** (PySide6): Dashboard, Actions, Resolver, Models (read-only table + download/set-active/delete), Logs, Settings (incl. server options); first-run dialog; system-tray with Show/Quit and minimize-to-tray; theme; download/extract progress bar; auto-update timer.
- **Removed:** llama-swap (binary, `config.yaml`, `ruamel.yaml`, `config_yaml.py`, the `router` status field, and the `vendor/llama-swap` submodule). A legacy `llama_swap` pid key in `state/pids.json` and the `listen_flag` config key are still *read* (never written) for backward compatibility. The from-source build path (`vendor/llama.cpp` submodule, `.gitmodules`, `backends/build.py`, the `build` action, `--source build`, the `install_source` setting, exit code 7, contract v2) was removed in favor of prebuilt-only downloads; legacy `managed-build` `.version` markers are still read. The user-supplied **`pointed` source** (arbitrary `llama-server` path/folder), the `source_priority` setting and `PointedPaths` were removed in favor of a single **backend location** + the `use_os_llama_server` toggle (contract v4); legacy `pointed` / `source_priority` config keys are ignored but preserved verbatim in the config file.

**Run / verify:**
```bash
uv run python -m llamagui gui          # launch the GUI
uv run python -m llamagui status --json
uv run python -m llamagui list-models
just check                             # full check suite (fail-fast)
uv run python scripts/mapping.py       # regenerate mapping.md
```

**Read-next:** `mapping.md` (file/folder tree), `docs/BUILD.md` (triple/resolver/cadence/Nuitka), this file §1–§17.