# AGENTS.md — `llama_gui` build spec & architecture

> **Who this is for:** an AI coding agent (or human) working in this repo.
> **What this app is:** a PySide6 desktop app that drives
> [`llama-server`](https://github.com/ggml-org/llama.cpp) (llama.cpp) **directly**
> on Windows, Linux and macOS, and manages a library of `.gguf` models. Its
> entire external surface is exactly one binary — `llama-server` — obtained from
> exactly one place: the **backend location** (`<root>/managed`, the tree the app
> downloads prebuilt backends into), unless the user checks the **"Use OS
> installed llama.cpp"** toggle, in which case `PATH` wins. It resolves &
> installs the server binary, lists / downloads / activates / deletes models, and
> launches / monitors / stops the server — through a GUI and a machine-readable
> CLI.
> **Read order:** §1 (read-only invariant) → §2 (binary sources) → §3 (model
> library) → §5 (architecture) → §6 (invariants) → §12 (repo layout) → §17
> (hard "do not" list).
> **This file is the *design* spec.** Implementation status, known gaps and all
> actionable work live in [`TODO.md`](TODO.md) — keep them there, not here.
> Version numbers marked `EXAMPLE` are placeholders; re-resolve at build time.

---

## 1. Invariant #0 — the app only writes inside its own managed root

The app's **write** surface is exactly its managed root (`AppConfig.root`):
`state/`, `managed/`, `downloads/`, and the models directory. Everything else is
**read-only** to the app.

- A **system (OS-installed)** `llama-server` — used when the "Use OS installed
  llama.cpp" toggle is on — is used **read-only**: the app resolves and executes
  the binary but never writes, edits, deletes, or creates anything in the system
  install location. No lockfile, no log, no state.
- A reference install directory the user points at has the same guarantee: point
  at it, never mutate it.
- The app never symlinks into an external dir; any logic it needs is
  re-implemented in this tree.

> **Consequence:** the engine is pure Python. It does **not** shell out to any
> external script and does not parse colored/`[OK]` process output. The machine
> interface is the Python CLI (§11).

---

## 2. Binary sources — how the app obtains `llama-server`

The app must work whether or not it downloaded anything itself. There is exactly
**one location input** — the **backend location** (`<root>/managed`) — plus one
**toggle** (`AppConfig.use_os_llama_server`, "Use OS installed llama.cpp") that
switches resolution to the OS install on `PATH`. One **Binary Resolver** (§7)
performs the resolution:

| Source | Meaning | When used |
|---|---|---|
| **managed‑prebuilt** | App downloads the official `ggml-org/llama.cpp` GitHub release assets into the backend location (asset regex + size cache + `.version` marker). | Default "just works, no compiler" path. |
| **system** | `llama-server` found on `PATH` (`shutil.which`). | "I installed it globally / via a package manager" — checked as a **toggle**, never as a path. Falls back to the backend location when `PATH` has nothing. |

**No from-source builds.** The app never compiles llama.cpp. The GitHub "releases"
the downloader fetches are nightly/dev builds (llama.cpp publishes no stable
releases there — see its `docs/release.md`), so "latest" moves frequently.
Legacy: `.version` markers from older versions' from-source builds are still
*read* and reported as the `managed-build` source label; such artifacts are never
produced again.

---

## 3. Model library — `.gguf` store

Models are plain `.gguf` files in a user-configurable directory (default
`<root>/models`, set via `AppConfig.models_dir`). There is **no index and no
lock**:

- **List** — a directory scan for `*.gguf` (name, size, mtime), sorted by name.
- **Download** — stream a URL (e.g. a Hugging Face `resolve/main/…gguf` link)
  into a `<name>.part` temp file, then rename into place only when complete, so a
  crash or cancel never leaves a truncated model. Downloads are **resumable**
  (HTTP `Range`). Progress rides the same `emit_progress("model", …)` channel as
  backend downloads.
- **Set active** — `AppConfig.active_model` records the file name the server will
  launch.
- **Delete** — remove one file by name; path traversal (`../`) is rejected by
  design.

`launch` runs `llama-server -m <models_dir>/<active_model>`; if no active model is
set the GUI/CLI says so rather than guessing.

---

## 4. Goals / Non‑goals

**Goals**
- Native desktop app (PySide6, Python 3.12) that **is** the orchestration engine
  (resolve / obtain / launch / verify / stop `llama-server`, plus model
  management) **and** its GUI.
- Works with **no build step of its own**: the downloaded backend location, or
  the OS install on `PATH` (toggle).
- Strict polyglot toolchain: `uv` (deps/run), `ruff` (lint+format), `mypy` +
  `pyright` (types), `jscpd` (duplication). One command runs all checks.
- Reproducible packaging with **Nuitka** (`--standalone`).
- A **machine interface** (CLI `--json` + exit codes) so the GUI, tests, and
  external tools all drive one typed engine.

**Non‑goals**
- Writing/editing/deleting anything outside the managed root (§1).
- Shelling out to PowerShell as the engine (the engine is Python; the only
  `subprocess` use is spawning the resolved `llama-server` and the platform
  link/junction helpers).
- A long‑running background daemon (the read/write split removes the need; §5.2).
- Nuitka `--onefile` as the default (ship `--standalone` first).
- Managing any binary other than `llama-server` (no router, no swap binary).

---

## 5. Architecture

### 5.1 Engine‑in‑Python
All orchestration is Python. `llama-server` is spawned **directly by the app**
(no intermediary). The engine's responsibilities: asset selection, download
cache, per-backend `.version` markers, idempotent install/update,
wipe‑then‑extract, the `managed/current` link, hidden launch, port verification,
scoped stop, and the model store.

### 5.2 Read / write split (both Python)
```
┌──────────── PySide6 GUI + CLI (Python 3.12), one process ─────────────┐
│  dashboard │ server options │ logs │ settings │ downloads              │
└──────┬──────────────────────────────────────────────┬─────────────────┘
       │ READ  (pure Python, sub-ms, no subprocess)    │ WRITE (in-process engine)
       ▼                                              ▼
  managed root files:                        engine.orchestrator.*
   - state/active.txt                         (downloads / installs / launches /
   - managed/<backend>/.version                stops / model ops / config) —
   - managed/current  (link target)             spawns the resolved llama-server
   - state/pids.json                            detached, tracks its pid
   - models/ (*.gguf)
  + socket connect 127.0.0.1:<port>
```
- **READS** (`status`, versions, active backend, models list, is-listening): pure
  Python — read files, resolve the `managed/current` link (`os.readlink`, with a
  reparse-point fallback on Windows), non-blocking
  `socket.create_connection((host, port), 0.2)` for liveness, and a directory
  scan for models. No subprocess on the hot path, so the dashboard can poll.
- **WRITES**: in-process engine calls, serialised by the mutation lock (§6.15).
  The only `subprocess` call is launching the resolved `llama-server` (plus the
  `mklink /J` fallback on Windows). Never `powershell.exe` as the engine.

### 5.3 Machine interface (CLI)
`python -m app <action> [--json]` emits a single JSON **envelope** (or a
human-readable summary) with a typed payload and a stable exit code (§11). The
GUI calls the same `Orchestrator` methods in-process; the CLI is the externally
drivable surface. `contract_version` is `"4"`.

---

## 6. Invariants (the rules that must survive refactors)

1. **Never write outside the managed root** (§1). The OS-installed binary is
   read-only.
2. **Asset selection is data** (regex per backend). Adding a backend = one row
   in `backends/catalogue.py`'s `BACKENDS`, not new control flow.
3. **Wipe‑then‑extract** a backend dir on (re)install (prevents stale DLLs).
   Deletion is only ever: the temp download scratch, the one backend dir being
   replaced, the `managed/current` **link** (never the target's contents), or
   the source tree of an accepted relocation (§11), which only runs after every
   file arrived at the destination.
4. **Idempotent update:** re-running `install`/`update` when nothing changed
   reports `skipped`, not a redundant download.
5. **Copy with literal paths**, never a wildcard where a specific file is
   expected.
6. **A launch is not a liveness claim.** `launch --verify` polls the port;
   success requires the port to be listening **and** the pid alive. On failure,
   return exit **1** with `log_tail`.
7. **Hidden launch:** Windows `DETACHED_PROCESS | CREATE_NO_WINDOW`; POSIX
   `start_new_session=True`. The server outlives the GUI and gets no console
   window. Logs → `state/llama-server.{out,err}.log`.
8. **Stop kills only PIDs the app spawned.** The engine keeps
   `state/pids.json` (`{llama_server: <pid>, servers: {…}}`); Stop terminates
   **exactly those**, then verifies the port is free. Never scan-and-kill by
   name or path. Stale pids are
   dropped; a live-but-not-ours holder is reported as "port held by unknown
   process", never killed.
9. **Health probe:** `/health`; model list `/v1/models`. A `/v1` 404 is not
   "server down."
10. **Backend list is data.** Default backend = `vulkan`.
11. **cuda12 is self-contained:** its binary needs the bundled cudart‑12.x DLLs;
    the managed-prebuilt path fetches the cudart pack and drops the DLLs next to
    the binary (per the *CUDA runtime* setting `auto`/`always`/`never`).
12. **Server args are explicit and configurable:** `--host`, `--port`,
    `-c <ctx>` (omitted when ctx is `auto`/0 so llama.cpp uses the model
    default), `-ngl <layers>`, plus a free-form *extra server args* string
    appended last. The full option catalogue is **data** in
    `app/serverargs.py`, consumed by the GUI grid, the CLI
    (`server-args` / `set-arg` / `clear-args`) and the command-line builder.
13. **App errors are always logged.** loguru writes every entry to
    `<data-root>/logs/llamagui.log` (10 MB rotation, 7-day retention, enqueued so
    worker threads are safe). Choke points: `cli.emit` (every envelope),
    `EngineWorker._execute_action` (every GUI worker failure, with traceback),
    `lifecycle.launch_llama_server` (spawn), and the entry-point excepthook
    (uncaught crashes). The enqueued queue is drained (`logger.complete()`)
    before CLI/GUI exit and inside the excepthook, so a last-second error
    survives process death. The GUI has no console — the log file is the only
    post-mortem record.
14. **Qt widgets are touched only on the GUI thread.** Worker actions run on
    QThreadPool threads; progress and results travel via Qt signals
    (auto-queued). `EngineWorker` connects the progress slot to the `progress`
    signal and the engine's callback only ever emits that signal — a direct
    widget call from a worker thread is undefined behaviour and crashed the
    process mid-download (silent C-level abort, no traceback).
15. **Every mutation takes the mutation lock.** `locking.mutation_lock(root)` is
    a per-root single-writer guard (Win32 named mutex; POSIX lock file with a
    stale-pid steal). A second concurrent mutation fails fast with exit **4**
    rather than corrupting the managed root. Reads never lock.

---

## 7. Binary Resolver — design

`resolver.py` returns a `ResolvedBinary(path, source, version, valid, error)`
for `llama-server` (the only binary, `BINARY_NAMES = ("llama-server",)`).

**Resolution order** (one toggle, `AppConfig.use_os_llama_server`, default off):
1. **system** (only when the "Use OS installed llama.cpp" toggle is on) —
   `shutil.which("llama-server")` (OS install / package manager).
2. **managed** — the backend location `<root>/managed`: the `managed/current`
   link's target first, else `<root>/managed/<default_backend>`. The source
   label distinguishes `managed-prebuilt` from the legacy `managed-build`
   (older from-source artifacts) via the `.version` marker. When the toggle is on
   and `PATH` has nothing, resolution falls back to the backend location.

Discovery searches the folder and one level of well-known nested layouts
(`bin`, `build/bin`, `build/bin/Release`, `Release`). An executable match always
wins; a match lacking the execute bit is returned as a last resort so validation
can explain the problem instead of reporting a misleading "not found".

**Validation (mandatory before trusting a source):** run `<exe> --version`
(short timeout), falling back to `--help` for builds that only answer one;
`valid = (returncode == 0)`; capture the version string. A binary that fails is
reported `valid=false` with stderr, **not** silently used. The UI shows the
**source** label so the user always knows what's running.

**Fast-path reads:** `resolve_llama_server(cfg, validate=False)` only checks
existence (no subprocess) so `status()` never spawns `--version` on the
dashboard's refresh. The `resolve` action and `launch`/`restart` use
`validate=True`. `anything_resolved(cfg)` (validate=False) drives the first-run
decision.

**First-run acceptance:** with the OS toggle on and a working `llama-server` on
`PATH`, the app must validate and run the already-installed binary with **zero**
download. That is the day-one path and a required test.

---

## 8. Managed install — prebuilt download

`backends/prebuilt.py`: query
`https://api.github.com/repos/ggml-org/llama.cpp/releases/latest` (or a pinned
tag), pick the asset for the backend + platform by regex, download into
`downloads/` (cache by size), extract the folder containing `llama-server(.exe)`
flat into `managed/<backend>/`, write `.version`. For cuda12, also fetch the
cudart pack (invariant #11). Optional GitHub token (keyring) for rate limits.

Downloads share the resumable / pausable / cancellable engine in
`app/download.py` (`.part` file + `.part.meta` sidecar, HTTP `Range` resume,
exponential backoff on transient failures, pause/resume/cancel via
`DownloadControl`).

> The fetched assets are nightly/dev builds (llama.cpp publishes no stable GitHub
> releases — see its `docs/release.md`), so "latest" moves frequently. The app
> does not vendor or compile llama.cpp sources.

---

## 9. Launch / verify / stop — the server lifecycle

All in `lifecycle.py`, cross-platform (Windows `DETACHED_PROCESS |
CREATE_NO_WINDOW` + `TerminateProcess`; POSIX `start_new_session` +
`SIGTERM`→`SIGKILL`).

- **Command line** (`build_llama_server_args`):
  `[llama-server, -m, <model_path>, --host, <host>, --port, <port>, [-c, <ctx>], -ngl, <ngl>, <extra…>]`
  where `<model_path> = <models_dir>/<active_model>` and `host/port/ctx/ngl`
  come from `AppConfig`; then the `server_options` catalogue
  (`serverargs.options_to_cli`, stable catalogue order); then the raw
  extra-args string last, so an explicit flag can still override a generated
  one. `-c` is omitted when the configured ctx is `auto` (≤ 0) so llama.cpp
  falls back to the model's own default context.
- **Launch** (`launch_llama_server`): spawn detached, stdout/stderr →
  `state/llama-server.{out,err}.log` (opened in Python), record the pid under
  `llama_server` in `state/pids.json`. With `verify=True`, poll the port first
  and treat "never came up" as failure (invariant #6).
- **Verify:** poll the port up to ~8 s (`verify_launch`); success only if
  listening **and** pid alive. On failure, read the last N log lines and return
  exit **1** with `log_tail`.
- **Stop** (`stop_processes`): read `state/pids.json`, terminate exactly the
  recorded pids (graceful, then force after `_TERM_GRACE_SECONDS`), then verify
  the port is free. For the system source this is the *only* safe scoping
  (invariant #8).
- **Switch backend** (`use`): repoint the `managed/current` link to
  `managed/<backend>`, write `state/active.txt`. Requires the backend installed
  (or `--auto-install`).

---

## 10. Model store

`model_store.py` (no lock, no index):
- `list_models(dir)` → sorted `ModelInfo(name, size_bytes, modified)[]`
  (recursive scan; hidden directories skipped).
- `model_name_from_url(url)` → the asset name (strips `?…`/`#…`), or a stable
  `model-<hash>.gguf` when the URL has no file name.
- `download_model(url, dir)` → stream into `<name>.part` (resume via `Range`),
  then `rename` into place. Emits `emit_progress("model", done, total,
  "download")`. Raises `ModelDownloadError` on HTTP/network failure (the
  `.part` is kept so a retry resumes).
- `remove_model(dir, name)` → delete one file; rejects path traversal; raises
  `FileNotFoundError` when missing.

---

## 11. CLI contract

`python -m app <action> [--json]`. Actions (orchestrator `ACTIONS`):
`describe`, `status`, `resolve`, `bootstrap`, `install [backends…] [--force]`,
`update [backends…] [--force]`, `use <backend> [--auto-install]`,
`list-models`, `download-model <url>`, `set-model <name>`,
`remove-model <name>`, `stop`, `launch [--verify]`, `restart [--verify]`,
`list-assets`, `pending-downloads`, `discard-download <dest>`, `config`,
`server-args [--flag]`, `set-arg <flag> [value]`, `clear-args`, and `gui`.

- `--json` prints a single JSON **envelope**:
  `{ contract_version, ok, exit_code, action, root, timestamp, duration_ms, data, error?, log_tail?, warnings? }`.
  Stdout is one object; diagnostics and the `PROGRESS` line protocol go to
  stderr.
- **Exit codes** (`schemas.ExitCode`): 0 success, 1 unexpected error, 2 not
  available, 3 network, 4 lock conflict, 5 bad argument, 6 contract mismatch.
- **Progress:** in CLI mode the engine emits a stable 4-field line on stderr —
  `PROGRESS\t<component>\t<done>\t<total>\t<phase>` — parsed by
  `progress.parse_progress_line`. The GUI instead receives
  `(done, total, phase, overall)` over a Qt signal.
- **`status` payload** (`StatusData`): `{ backends: {<name>:{installed, version,
  source, prebuilt_available, unavailable_reason}}, active, junction_target,
  server: {host, port, listening, pids, model}, models: {dir,
  models:[{name, size_bytes, modified}], active}, resolved: {llama_server:
  {path, source, version, valid, error}}, platform, root, config_file, ready,
  first_run_complete }`.
- **`install`/`update` payload** (`InstallData`): `{ release, results:[{name,
  status: ok|skipped|failed, version, bytes}], summary:{updated, skipped, failed} }`.
- **Config durability:** the settings file lives in the OS config dir (not under
  the managed root), so changing the root can never orphan it. Writes are atomic
  (temp file + `os.replace` + directory fsync); unknown keys round-trip; a
  corrupt file is preserved as `config.corrupt-<ts>.json` rather than
  overwritten. The GitHub token is **never** written to disk (keyring only).
- **Defaults are absent keys, never frozen paths:** `root` and `models_dir`
  follow their platform defaults (`default_root()`, `<root>/models`) unless the
  user overrides them, and a default is stored as a **missing** key
  (`AppConfig.{root,models_dir}_is_default`) rather than as today's resolved
  path — otherwise the Settings page's *Use Default* button would pin the value
  on the next unrelated save. A read (`config`, `status`) still reports the
  paths in effect. `Orchestrator.reset_root` / `reset_models_dir` drop the
  override; neither is a CLI action (they are GUI-only by design).
- **A path change offers the transfer, it does not perform it:** changing `root`
  and/or `models_dir` leaves the existing data where it is. `Orchestrator.
  plan_relocation` reports what *would* move (`RelocationData`: `backends` from
  `<root>/managed`, `models` = `*.gguf`, planned **independently**, so either or
  both can move in one step) and the Settings page asks via `RelocateDialog`
  (*Move & save* / *Copy & save* / *Save paths only* / *Cancel*), one checkbox per
  tree. `relocate_data(transfer="move"|"copy", …)` then runs the ticked trees on a
  worker thread (never the GUI thread), **before** the config is written, so a
  failure leaves the settings pointing at the data that is still on disk. Safety:
  never overwrite a non-empty destination, never follow `managed/current` (it is
  re-pointed at the new location afterwards, and a copy leaves the old one
  untouched), keep the user's models directory itself, and hold the mutation lock
  of **both** roots in a canonical order (`app/relocate.py`, `_relocation_locks`).
  Like the resets, this is GUI-only and not part of the `ACTIONS` contract.

---

## 12. Real repo layout

Authoritative generated tree: [`mapping.md`](mapping.md)
(regenerate with `uv run python scripts/mapping.py`).

The **import package is `app`**; the **distribution and console script are
still `llamagui`** (`[project.scripts] llamagui = "app.__main__:main"`, plus
`[tool.hatch.build.targets.wheel] packages = ["app"]`, since hatchling cannot
infer `app/` from the project name). User-facing identity is likewise unchanged:
`APP_NAME`, `%APPDATA%/llamagui`, `~/.llamagui`, `LLAMAGUI_*` env vars and
`logs/llamagui.log` all keep the `llamagui` name so existing installs are not
orphaned.

```
llama_gui/
├── pyproject.toml                 # uv project; deps (no ruamel); check tooling
├── justfile                       # canonical tasks (check/fix/build/…)
├── README.md  AGENTS.md  TODO.md  mapping.md
├── docs/BUILD.md                  # triple, resolver, cadence, Nuitka notes
├── scripts/                       # build.py check.py clean.py mapping.py stats.py
│                                  #   + check_server_args.py (catalogue vs --help)
├── app/                           # the engine
│   ├── __init__.py  __main__.py   # `python -m app` (loguru + excepthook)
│   ├── applog.py                  # loguru config: rotating file sink + excepthook
│   ├── cli.py                     # argparse + envelope + exit codes (§11)
│   ├── config.py                  # AppConfig (JSON, atomic), derived paths
│   ├── schemas.py                 # contract v4 models (StatusData, InstallData, …)
│   ├── orchestrator.py            # actions, locking, wiring
│   ├── resolver.py                # llama-server resolver (backend location + OS toggle)
│   ├── lifecycle.py               # launch/verify/stop + the pid file
│   ├── state.py                   # pure reads: active backend, .version, current link, port
│   ├── links.py                   # the `managed/current` link (symlink / mklink /J)
│   ├── progress.py                # the PROGRESS stderr line protocol (§11)
│   ├── model_store.py             # .gguf list/download/set-active/remove
│   ├── download.py                # resumable/pausable/cancellable download engine
│   ├── paths.py                   # platform paths (root, config_file, exe_suffix)
│   ├── locking.py                 # named mutex / lockfile for mutations
│   ├── relocate.py                # move or copy a tree when a path setting changes
│   ├── backends/catalogue.py      # BACKENDS data table + Source + availability
│   ├── backends/prebuilt.py       # GitHub release download + cache + .version
│   └── serverargs/                # llama-server option catalogue (data) + helpers
└── app/gui/                       # the PySide6 front-end (§13)
    ├── bootstrap.py               # `run()`: logging, QApplication, theme, window
    ├── main_window.py             # sidebar, tray, launch-on-start, start-minimized
    ├── theme.py  token.py         # QSS design system; keyring token
    ├── payload.py                 # worker-result → dict normalisation
    ├── worker_pool.py             # QRunnable around the engine (never block UI)
    ├── download_actions.py        # download slots shared by a page and a section
    ├── dialogs/first_run.py       # shown when nothing resolves
    ├── dialogs/relocate.py        # offered when a path change would strand data
    ├── pages/                     # the 5 sidebar pages: dashboard, models,
    │                              #   server_args, logs, settings
    ├── sections/                  # panels: backends (dashboard), models +
    │                              #   downloads (the models tab)
    └── widgets/                   # backend_card, log_view, model_table, path_picker,
                                   #   progress_bar, source_badge

tests/                            # mirrors app/ 1:1 (unit + gui + integration)
```

---

## 13. GUI (PySide6)

- **Sidebar** (5 pages, in order): **Dashboard** → **Models** → **Server
  options** → **Logs** → **Settings**.
  - *Dashboard* is a single scrolling page about the backend: a **Backends**
    section (per-backend cards with install/update/use, source badges, active
    backend, server-listening badge, and the resolved `llama-server` row showing
    source / valid / path). Models have their own tab.
  - *Models* is one scrolling tab holding both halves of getting a model: the
    **library** (`.gguf` table + download / set-active / remove / open folder)
    above the **Interrupted downloads** rows — every resumable `.part`,
    models *and* half-downloaded backend archives, each with Resume / Discard.
    The library and its downloads are one subject, so they share one tab.
  - *Server options* is a searchable, sectioned editor generated from the
    `serverargs` catalogue, with a live command-line preview.
  - *Settings* is a scrolling page of grouped cards — **Locations** (managed
    root, backend location, models directory, settings file), **Server** (host,
    port, default backend, OS `llama-server`, CUDA runtime, *Validate
    binaries*), **Application** (theme, launch on start, start minimized) and
    **Updates** (auto-check, interval, GitHub token). Row labels share one
    fixed column so every input lines up; derived paths are elided with the
    full value in the tooltip; Save / Reload / progress / status live in a
    footer **outside** the scroll area, so the actions stay reachable.
- **Workers:** every mutation runs on a `WorkerPool` `QRunnable`; the UI thread
  never blocks. Progress arrives over the `progress` Qt signal (§6.14).
- **First run:** when nothing resolves and `first_run_complete` is unset,
  `FirstRunDialog` offers *download the latest release* or *use the OS-installed
  llama.cpp on PATH*.
- **Tray:** a persistent system-tray icon with Show/Quit; closing the window
  tears the app down (it does **not** merely hide to tray — see the regression
  test in `tests/gui/test_main_window.py`).
- **Theme:** system/light/dark, applied from `gui/bootstrap.py` via the
  `gui/theme.py` QSS design system (centralized tokens, palette + stylesheet;
  `system` follows the OS colour scheme).

---

## 14. Testing

- **Unit** (`tests/unit`): resolver (stub exes, fast path), orchestrator (temp
  root), lifecycle (launch/stop against a tiny fake server bound to the port;
  POSIX variant), prebuilt (stubbed `httpx`, archive-traversal rejection),
  config (atomic save, corrupt recovery, unknown-key preservation), contract
  (envelope round-trip, `describe --json` validates against the schema),
  locking (mutex / lockfile, abandoned-owner recovery), download (resume,
  backoff, pending/discard), paths, backend catalogue, progress-line parser.
- **GUI** (`tests/gui`, `pytest-qt`, engine mocked): main window, navigation,
  pages, widgets, progress widget, worker/thread handoff. Run headless with
  `QT_QPA_PLATFORM=offscreen`.
- **Integration** (`tests/integration`): real network download into a temp
  managed root — gated behind the `integration` marker.
- Regression guards prove closing the window stops the app (not just hides it)
  and that the progress slot runs on the GUI thread.
- **Known coverage gaps are tracked in [`TODO.md`](TODO.md) §P2** — notably
  `serverargs.py`, `model_store.py` and the keyring token path.

## 15. Build & checks

- `just check` is the canonical, fail-fast suite: `ruff format --check`,
  `ruff check`, `mypy`, `pyright`, `jscpd`, `actionlint`, pytest.
- `just fix` auto-fixes formatting + lint. `just test` runs unit + GUI tests
  (`-m "not integration"`).
- `scripts/check.py` is a `just`-independent runner for the same checks (it has
  drifted slightly — see TODO.md).
- `just build` → Nuitka `--standalone` into `build/app.dist/`;
  `just build-version X.Y.Z.W` sets a product version. `scripts/build.py` is the
  single build entrypoint and verifies the artifact with `describe --json`.

## 16. CI & releases

Standalone lint / pyright / pytest gate workflows, a reusable `build.yml`, a PR
gate (`ci.yml`), a push-to-main auto-build (`auto_build.yml`), and a manual
`release.yml`. (See `docs/BUILD.md`.)

## 17. Hard "do not" list

- Do **not** write/edit/delete anything outside the managed root (§1).
- Do **not** reintroduce a router, a swap binary, `config.yaml`, or
  `ruamel.yaml`.
- Do **not** parse colored/`[OK]` text from any process.
- Do **not** treat a `Popen`/launch return as liveness — poll the port
  (invariant #6).
- Do **not** copy with a wildcard where a literal path is expected
  (invariant #5).
- Do **not** kill processes by name or path-scan — only pids the app spawned
  (invariant #8).
- Do **not** hardcode the backend list, the root path, or version pins
  (data / config / resolve-at-build-time).
- Do **not** store the GitHub token in plaintext (keyring).
- Do **not** reintroduce a from-source llama.cpp build path, `vendor/`
  submodule, or toolchain detection.
- Do **not** bypass the mutation lock for a new mutation (§6.15).

---

## 18. Implementation status & known work

Status, known gaps and all actionable tasks live in
[`TODO.md`](TODO.md) — kept out of this file deliberately, so the design spec
stays a stable reference and the work-in-progress list has one home.

**Run / verify:**
```bash
uv run python -m app gui          # launch the GUI
uv run python -m app status --json
uv run python -m app list-models
just check                             # full check suite (fail-fast)
uv run python scripts/mapping.py       # regenerate mapping.md
```

**Read-next:** `TODO.md` (work items), `mapping.md` (file/folder tree),
`docs/BUILD.md` (triple / resolver / cadence / Nuitka), this file §1–§17.

