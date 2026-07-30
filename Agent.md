---

# Agent.md — `llama_gui` build specification

> **Who this is for:** an AI coding agent (or human) working in `D:\Github\llama_gui`.
> **Read order:** §1 (read‑only invariant), §2 (binary sources), §4 (architecture), §5 (invariants to port), §10 (actual repo layout), then §18 (current status). The phases in §11 are the historical build order, not a todo list.
> **Doc status:** this file documents both the design spec *and* the implemented codebase, kept in sync as of 2026‑08‑09. Version numbers marked `EXAMPLE` are placeholders — re‑resolve at build time.
> **Module renames vs the original spec:** `contract.py` → `schemas.py`; `managed/` → `backends/`; `state_reader.py` + `progress.py` → merged into `lifecycle.py` + `models.py`; `gui/workers.py` → `gui/worker_pool.py`; `gui/config_yaml` → `config_yaml.py`. See §10 for the real layout. `mapping.md` is the generated file/folder tree.

---

## 1. Invariant #0 — the reference path is READ‑ONLY (do not violate)

`D:\Github\llama-prebuilt-swap\` (and everything under it: `setup-llama.ps1`, `bin\`, `llama-swap\`, `config\`, `downloads\`, `state\`) is **reference only**.

- **You MAY read** `D:\Github\llama-prebuilt-swap\setup-llama.ps1` to study proven logic.
- **You MUST NOT write, edit, delete, or create anything** under `D:\Github\llama-prebuilt-swap\`. Not the script, not its runtime dirs, not a lockfile, not a log. Zero.
- The new project lives **entirely** in `D:\Github\llama_gui`. Any file you need from the reference (logic, ideas) is **re‑implemented or summarized** in the new tree — never symlinked, never imported at runtime, never executed.
- The reference stack remains independently operational; the new app must not depend on it existing. (It *can* optionally **point at** its binaries via the resolver in §6 — that's a read‑only use, which is allowed.)

> **Consequence for the engine:** an earlier plan proposed adding a `-Json` machine interface *to that PS script*. That is now **impossible and superseded** — the script is frozen. The machine interface therefore lives in the **Python CLI** (§4.3), and the orchestration logic is **re‑implemented in Python**, faithfully reproducing the reference script's *behavior and invariants* (§5). Do not shell out to a copied PS script as the engine.

---

## 2. Glossary — the four ways the app obtains `llama-server` / `llama-swap`

The app must work whether or not it built anything itself. There are **four sources**, resolved by a single **Binary Resolver** (§6):

| Source | Meaning | When used |
|---|---|---|
| **pointed** | User supplies explicit path(s) to already‑working binaries (a folder, or separate paths for the two exes). | "I already have a working install somewhere" — incl. pointing at the reference dir's `bin\vulkan` + `llama-swap`. |
| **managed‑prebuilt** | App downloads official GitHub release zips into its own managed dir (port of the reference's asset‑glob + cache + `.version` logic). | Default "just works, no compiler" path. |
| **managed‑build** | App builds from the **git submodules** (`vendor/llama.cpp`, `vendor/llama-swap`) using the local toolchain. | "I want to build from source / pin a commit / apply patches." |
| **system** | Binaries found on `PATH` (`shutil.which`). | "I installed them globally / via a package manager." |

**Submodules vs runtime independence (the user's exact intent):** the submodules are a *build‑time / dev‑time* reproducibility source. At **runtime** the app never *requires* the submodules to be present or built — it uses whatever the resolver finds (pointed / managed‑prebuilt / system). So: clone without `--recurse-submodules` must still let the app run via managed‑prebuilt or system/pointed. Document this clearly; gate every "build from submodule" code path behind a toolchain check that degrades to "download prebuilt instead."

---

## 3. Goals / Non‑goals

**Goals**
- Native Windows desktop app (PySide6, Python 3.12) that **is** the orchestration engine (resolve / obtain / launch / verify / stop llama‑cpp + llama‑swap) **and** its GUI.
- Works with **no build step of its own**: pointed folder, system `PATH`, or managed prebuilt download.
- *Optionally* builds from submodules when a toolchain exists.
- Strict polyglot toolchain: `uv` (deps/run), `ruff` (lint+format), `mypy` + `pyright` (types), `shfmt` (shell, *if any*), `PSScriptAnalyzer` (PowerShell, *if any*), `jscpd` (duplication). One command runs all checks.
- Reproducible Windows packaging with **Nuitka**.
- A **machine interface** (CLI `--json` + exit codes) so the GUI, tests, and external tools all drive one typed engine.

**Non‑goals**
- Editing or executing anything under the reference path (§1).
- Shelling out to PowerShell as the engine (the engine is Python; PowerShell is only invoked, if at all, for an optional submodule‑build helper on Windows — and even that should be pure‑Python `subprocess` to `cmake`/`go` where possible).
- Linux/macOS in v1 (Windows‑only launch primitives). Design seams so it's *possible* later; do not build it.
- A long‑running background daemon in v1 (the file‑read split removes the need; see §4.2).
- Nuitka `--onefile` as the default (ship `--standalone` first; §13).

---

## 4. Architecture

### 4.1 Engine‑in‑Python (the pivot)
All orchestration is Python. The reference PS script is **not** executed by the app. The Python engine reproduces, in Python, the reference's proven behavior: asset selection by glob, zip cache by size, per‑component `.version` markers, idempotent update, wipe‑then‑extract, junction handling, hidden launch, port verification, scoped deletion. §5 is the porting checklist.

### 4.2 Read / write split (both Python now)
```
┌──────────────────── PySide6 GUI + CLI (Python 3.12) ────────────────────┐
│  dashboard │ actions │ models │ logs │ settings                          │
└──────┬───────────────────────────────────────────────┬──────────────────┘
       │ READ  (pure Python, sub‑ms, no subprocess)    │ WRITE (in‑process engine)
       ▼                                               ▼
  app's own managed root files:                  engine.orchestrator.*
   - state/active.txt                            (downloads / builds / launches /
   - managed/<b>/.version                         stops / edits config) — spawns
   - managed/current  (junction target)           the *resolved servers* via
   - llama-swap/.version                          subprocess (CREATE_NO_WINDOW),
  + socket connect 127.0.0.1:<port>               tracks the PIDs it spawned
  + (optional) read a *pointed* root the same way
```
- **READS** (status, versions, active backend, is‑listening): pure Python — read files, `os.readlink` the junction (try/except → `"none"`), non‑blocking `socket.create_connection(("127.0.0.1", port), 0.2)` for liveness. No subprocess on the hot path. If the active source is **pointed** at an external layout, the reader reads *that* layout's files the same way (the reader takes a root argument).
- **WRITES**: in‑process engine calls. The only `subprocess` calls are (a) launching the **resolved** `llama-server`/`llama-swap` binaries, and (b) optional `cmake`/`go`/`git` for managed‑build. Never `powershell.exe` as the engine.

### 4.3 Machine interface (CLI; replaces the frozen PS `-Json` plan)
The engine is a typed Python API returning dataclasses (§9). A thin CLI serializes them:
```
python -m llamagui <command> [--json] [--root PATH] [...]
```
- **stdout with `--json`** = exactly **one** JSON envelope (§9.1), nothing else.
- **stderr** = human logs **plus** machine progress lines `PROGRESS\t<component>\t<bytes_done>\t<bytes_total>\t<phase>` (the GUI's log/progress tail parses these; everything else is plain log text).
- **exit code** = status (§9.2); the envelope also carries `exit_code`.
- Without `--json`, human‑readable text. The two modes never mix.
- The GUI drives the engine **in‑process** (imports it); the CLI exists for tests, scripting, and external tooling, and is the contract‑parity target.

### 4.4 Single‑writer rules
- **`config.yaml` (llama‑swap models):** the **app owns it** once it exists, edited with **`ruamel.yaml`** (round‑trip, preserves comments), validated by re‑parse. If the app is pointed at an *external* config it does not own, it edits only with explicit user confirmation and never auto‑overwrites.
- **Managed root mutation:** a **named mutex / lockfile** (`state/mutation.lock`, or `win32` `Mutex` "Global\llama-gui-mutation") guards mutations; concurrent callers get exit **4**. Reads never lock.

---

## 5. Invariants to PORT from the reference script (porting checklist)

These are observed facts / already‑fixed bug classes from the reference implementation. The Python engine **must** reproduce them; each maps to a unit test.

1. **Asset selection by glob**, not hardcoded build number: `llama-*-bin-win-vulkan-x64.zip`, `llama-*-bin-win-cuda-13.3-x64.zip`, `llama-*-bin-win-cuda-12.4-x64.zip`; cudart packs anchored on `cudart-*` so the 372 MB DLL pack never collides with the 137 MB binary. (Verify current asset names via the GitHub API at runtime; the globs are the stable contract.)
2. **Zip cache by exact size**; skip re‑download when cached size matches; `-Force` bypasses cache + extract.
3. **Per‑component `.version` marker**; idempotent update skips a component whose marker == current release tag.
4. **Wipe‑then‑extract** a backend dir on (re)install (prevents stale DLLs). Deletion is **only** ever: temp unzip scratch, **one** backend dir being replaced, or the `current` junction **link** (never its target's contents). Never the root, other backends, downloads, config, or llama‑swap.
5. **Copy via iteration, not wildcard‑on‑literal‑path.** In Python this is `shutil.copytree(src, dst, dirs_exist_ok=True)` or iterating `Path.iterdir()` — never a glob string passed where a literal path is expected. (The PS bug was `-LiteralPath '…*'` copying nothing.)
6. **Launch success ≠ liveness.** After spawning a server, **poll the port** (and/or the pid) before claiming success. A crash happens in milliseconds; `Popen` returning is not proof.
7. **Hidden launch that outlives the parent.** On Windows: open log files in Python, pass as `stdout`/`stderr`, spawn with `creationflags = DETACHED_PROCESS | CREATE_NO_WINDOW` (verify both flags against the Python docs / test that no console window appears and the server survives the app exiting). Store the pid.
8. **Stop kills only PIDs the app spawned.** The reference scoped kills by *path under its root*; that breaks for pointed/system binaries. **Generalization:** the engine keeps a **pidfile** (`state/pids.json`: `{llama_swap: <pid>, servers: {<slot>: <pid>}}`) and an in‑memory set; Stop terminates **exactly those**, then verifies the port is free. Never scan‑and‑kill by name (that would kill the user's `.bat` server / LM Studio). On startup, reconcile the pidfile against live pids (a pid that no longer exists is stale → drop it; a pid that exists but wasn't ours → do **not** kill it, report "port held by unknown process").
9. **`/v1` is a 404 by design.** Health probe uses `/health`; model list `/v1/models`. Don't treat a `/v1` 404 as "server down."
10. **Backend list is data** (a table: `vulkan`, `cuda13`, `cuda12` + notes + `needs_cudart`). Adding a 4th backend = one row + one glob. Default backend = `vulkan` (the user's measured winner: ~189 t/s prefill / ~19.26 decode on a tensor‑core‑less GTX 1650; CUDA default path loses prefill badly — cite as the user's measurement, not a universal claim).
11. **cuda12 self‑contained:** the cuda‑12.4 binary needs its bundled cudart‑12.4 DLLs (the user's toolkit is 13.3, which won't satisfy a 12.4 binary). cuda13 uses the installed 13.3 toolkit. The managed‑prebuilt path must fetch the cudart pack for cuda12 and drop the DLLs next to the binary.
12. **Never emit CUDA arch `compute_61`** if the engine ever invokes `nvcc` (managed‑build): CUDA ≥13.0 removed Pascal. Use `75` for this card; make arch configurable.
13. **Listen flag is configurable with fallback.** `--listen 127.0.0.1:<port>` is **observed working in this environment**; make it a setting and, if launch fails, retry with **no listen flag** (llama‑swap's default port) and surface the log. Read `llama-swap --help` text into diagnostics on failure rather than asserting flag names.

---

## 6. Binary Resolver — design

A single component `resolver.py` returns, for each of `{llama_server, llama_swap}`, a `ResolvedBinary(path, source, version, valid)`.

**Resolution order (configurable; default = pointed → managed → system):**
1. **pointed** — if the user set explicit path(s), use them. Accept (a) one folder containing both exes (auto‑detect), or (b) **separate** paths for `llama-server` and `llama-swap` (the reference layout has them in different dirs, so separate paths are the general case).
2. **managed** — the app's own `managed/<backend>/llama-server.exe` and `managed/llama-swap/llama-swap.exe` (from prebuilt download or submodule build). The active backend is the `managed/current` junction.
3. **system** — `shutil.which("llama-server")` / `shutil.which("llama-swap")`.

**Validation (mandatory before trusting any source):** run `<exe> --version` (or `--help` if `--version` unsupported) with a short timeout; `valid = (returncode == 0)`. Capture the version string. A pointed/system binary that fails validation is reported `valid=false` with the stderr, **not** silently used. The UI shows the **source** label (`pointed` / `managed‑prebuilt b10189` / `managed‑build <commit>` / `system`) so the user always knows what's running.

**Fast-path reads:** `resolve_*` / `resolve_both` accept `validate=False`. The `status()` action uses this so the 3s dashboard refresh never spawns `--version` subprocesses (upholds the §4.2 "reads never spawn on the hot path" invariant); binary *existence* is treated as valid there. The `resolve` action (resolver page, models page) and `launch`/`restart` use the default `validate=True` for authoritative checks. Validate-only findings are narrowed to concrete exception types; do not broaden them back to blind `except Exception`.

**First‑run convenience / acceptance:** the resolver, pointed at `D:\Github\llama-prebuilt-swap\bin\vulkan` (server) + `…\llama-swap` (swap), must validate and run the *already‑working* reference binaries with **zero** download or build. This is the day‑one path and a required integration test (§12).

---

## 7. Managed install — prebuilt download **and** submodule build

### 7.1 Prebuilt download (default managed path; no compiler needed)
Port the reference logic into Python (`managed/prebuilt.py`): query `https://api.github.com/repos/ggml-org/llama.cpp/releases/latest` (or pinned tag), pick assets by the §5 globs, download into `downloads/` (cache by size), extract the folder containing `llama-server.exe` flat into `managed/<backend>/`, write `.version`. For llama‑swap: `https://api.github.com/repos/mostlygeek/llama-swap/releases/latest`, asset regex `^llama-swap_.*_windows_amd64\.zip$`. Optional GitHub token (from §10 settings, **encrypted at rest**) for rate limits.

### 7.2 Submodule build (explicit "build from source" path)
`.gitmodules`:
```ini
[submodule "vendor/llama.cpp"]
    path = vendor/llama.cpp
    url = https://github.com/ggml-org/llama.cpp
[submodule "vendor/llama-swap"]
    path = vendor/llama-swap
    url = https://github.com/mostlygeek/llama-swap
```
- Pin commits; record them in `docs/BUILD.md`. The submodule pin and the "latest prebuilt" tag are **two independent update cadences** — document that.
- **llama.cpp** build = `cmake -B build -DGGML_<BACKEND>=ON …` + `cmake --build build --config Release --parallel` via Python `subprocess` (no PS required). Gate on toolchain: detect MSVC (`vswhere`/`cl`), and for CUDA `nvcc`, for Vulkan the SDK; if missing, **offer prebuilt download instead** with a clear message. Default backend flags = Vulkan ON (per invariant #10); expose CUDA as opt‑in with arch `75` (invariant #12).
- **llama‑swap** build = Go (`go build`); gate on `go` on PATH; if missing, fall back to the llama‑swap **prebuilt** release (the easy path) regardless of the llama.cpp choice. (The two binaries are resolved independently — you can run a submodule‑built llama‑server under a prebuilt llama‑swap, etc.)
- **Runtime independence:** a clone without submodules initialized must still run (managed‑prebuilt / system / pointed). The "build from submodule" UI action first checks the submodule dir is populated (`git submodule status` / presence of `vendor/llama.cpp/CMakeLists.txt`) and prompts to init if not.

---

## 8. Launch / verify / stop (the server lifecycle)

- **Launch llama‑swap** with the resolved binary, args `["--config", <config>, <listen_flag>, "127.0.0.1:<port>"]` (listen flag per invariant #13), `creationflags = DETACHED_PROCESS | CREATE_NO_WINDOW`, stdout/stderr → `state/llama-swap.{out,err}.log` (opened in Python). Record pid in `state/pids.json`.
- **Verify:** poll the port up to ~8 s; success only if listening **and** pid alive. On failure: read last N log lines, return exit **1** with `log_tail` (never claim success — invariant #6).
- **llama‑server** is spawned **by llama‑swap** (per `config.yaml` `cmd:`), *not* by the app — except in a "direct server" mode (no swap) the app may spawn it itself; if so, track its pid too.
- **Stop:** read `state/pids.json`, terminate exactly those pids (graceful then forceful), remove the pidfile, verify the port is free. For pointed/system sources this is the *only* safe scoping (invariant #8).
- **Switch backend (managed):** flip the `managed/current` junction (`os.symlink`/reparse via `ctypes` or a tiny `mklink /J` subprocess) atomically; then the user restarts the router to pick it up (or the app does, with the lock held).

---

## 9. Contract — internal types + CLI JSON + exit codes (LOCKED)

### 9.1 Envelope (CLI `--json`; also the shape the GUI's in‑process calls can serialize for tests)
```jsonc
{
  "contract_version": "1",
  "ok": true,                       // == (exit_code == 0)
  "exit_code": 0,
  "action": "status",
  "root": "D:\\Github\\llama_gui",
  "timestamp": "2026-07-31T12:34:56Z",
  "duration_ms": 412,
  "data": { /* per action, see 9.3 */ },
  "error": null,                    // string when exit_code != 0
  "log_tail": null,                 // string[] when exit_code != 0
  "warnings": []
}
```
The GUI and CLI **share** the dataclasses in `contract.py`; the GUI checks `contract_version` against the engine it imported (trivially equal in‑process, but the field exists so an externally‑driven CLI can't drift silently).

### 9.2 Exit codes
| code | meaning | reaction |
|---:|---|---|
| 0 | success | refresh |
| 2 | target not available (e.g. switch to an uninstalled managed backend; pointed path missing) | prompt to obtain/fix |
| 3 | network / GitHub API failure | show `error` + retry |
| 4 | lock or port conflict (mutation in progress / port held) | "busy" / show holder |
| 5 | bad argument / unknown backend / unknown action / unknown source | disable control / bug |
| 6 | contract/version mismatch (external CLI vs engine) | hard block |
| 7 | toolchain missing for a requested build (no MSVC / no Go) | offer prebuilt fallback |
| 1 | unexpected error | show `error` + `log_tail` |

### 9.3 Per‑action `data`
- **`describe`** — self‑description: backend table (notes, `needs_cudart`), supported sources (`pointed|managed-prebuilt|managed-build|system`), available actions, defaults (port, listen flag), valid exit codes. The GUI builds its backend list from this — **never hardcode it**.
- **`status`** — `{ backends: {vulkan:{installed,version,source}, …}, active, junction_target, router:{port,listening}, llama_swap:{installed,version,source}, config_present, resolved:{llama_server:{path,source,version,valid}, llama_swap:{…}} }`. (The fast GUI path derives the same from files; this is the authoritative/refresh form.)
- **`install` / `update`** — `{ release, results:[{name,status:ok|skipped|failed,version,bytes}], llama_swap:{status,version}, summary:{updated,skipped,failed} }`.
- **`resolve`** — the resolver's decision per binary (path/source/valid) — useful for the settings page and diagnostics.
- **`use`** — `{ backend, active_before, active_after, auto_installed }`.
- **`stop` / `stop_all`** — `{ stopped_pids, port_free, still_listening, unknown_holder }`. (`stop_all` here means "stop everything *this app* launched," *not* a machine‑wide name‑scan — invariant #8.)
- **`list_assets`** — `{ release, assets:[{name,size,flag}] }`.

---

## 10. Repository layout (current implementation)

```
D:\Github\llama_gui\
├── Agent.md                         # this file
├── .gitmodules                      # vendor/llama.cpp, vendor/llama-swap
├── vendor\llama.cpp\                # submodule (build-time source; optional at runtime)
├── vendor\llama-swap\               # submodule
├── pyproject.toml                   # uv project + ruff/mypy/pyright config
├── uv.lock                          # committed
├── .python-version                  # 3.12
├── .editorconfig                    # shfmt reads this (for *.sh only)
├── .jscpd.json
├── .pre-commit-config.yaml
├── mapping.md                     # generated project tree (run: uv run python scripts/mapping.py)
├── justfile                         # one-command aggregator
├── scripts\
│   ├── check.py                     # independent of `just`; runs the same checks (mirror of `just check`)
│   ├── build.py                     # Nuitka build wrapper
│   ├── clean.py                     # removes build artifacts
│   ├── stats.py                     # file / LOC / test counts
│   └── mapping.py                   # generates mapping.md (project tree, respects .gitignore)
├── docs\
│   └── BUILD.md                     # working Python/PySide6/Nuitka triple; resolver design; decisions log
├── llamagui\                        # NOTE: package lives at repo root (not src\llamagui)
│   ├── __init__.py
│   ├── __main__.py                  # CLI entry: python -m llamagui <cmd> [--json]
│   ├── cli.py                       # argparse → engine calls → envelope JSON + exit code
│   ├── config.py                    # app settings: root, port, listen flag, default backend,
│   │                                #   pointed paths, source priority, theme
│   ├── schemas.py                   # envelope dataclasses + exit-code enum + contract_version
│   │                                #   (was contract.py in the original spec)
│   ├── resolver.py                  # §6 four-source resolver + --version validation
│   ├── orchestrator.py              # install/update/force/use/stop/launch — the engine
│   ├── backends\                    # (was managed\ in the original spec)
│   │   ├── prebuilt.py              # GitHub release download + cache + .version (port of ref)
│   │   └── build.py                 # cmake/go build from submodules (toolchain-gated)
│   ├── lifecycle.py                 # hidden launch + port verify + pidfile stop (§8)
│   │                                #   also hosts the pure-Python state reads that were
│   │                                #   spec'd as state_reader.py (active.txt, .version,
│   │                                #   junction, socket)
│   ├── config_yaml.py               # ruamel.yaml read/write/validate llama-swap config.yaml
│   ├── models.py                    # domain types + PROGRESS-line parser (progress.py merged here)
│   ├── locking.py                   # named mutex / lockfile for mutations
│   ├── gui\
│   │   ├── app.py  main_window.py   # main_window wires launch-on-start / start-minimized / tray
│   │   ├── token.py                 # keyring-backed GitHub token (never plaintext on disk)
│   │   ├── worker_pool.py           # QThread/QRunnable around the engine (never block UI)
│   │   ├── pages\ {dashboard, actions, models, logs, resolver, settings}.py
│   │   └── widgets\ {backend_card, source_badge, progress_bar, log_view, model_table}.py
└── tests\
    ├── conftest.py
    ├── unit\   conftest.py test_contract.py test_progress.py test_resolver.py  # stub-exe validation + fast path
    │           test_orchestrator.py test_build.py test_lifecycle.py
    │           test_cli.py test_prebuilt.py test_state_reader.py
    ├── integration\  __init__.py test_managed_prebuilt.py      # gated: network
    └── gui\  conftest.py test_main_window.py test_phase7.py test_dashboard.py  # pytest-qt, engine mocked
```

---

## 11. Phased build order (each demoable + testable before the next)

- **Phase 1 — skeleton + types + state reader (no network, no subprocess).** `uv` project per §toolchain; `contract.py`, `models.py`, `state_reader.py`. Unit tests with a fake root (write `active.txt`, `.version`; create a real junction in the fixture via `subprocess` `mklink /J` or `ctypes` reparse — document the fixture; on non‑Windows skip). Port check via a real ephemeral socket. **Gate:** `uv run pytest -m "not integration"` green; `ruff`/`mypy`/`pyright` clean.
- **Phase 2 — resolver.** `resolver.py` with the four sources + `--version` validation (test with a stub exe that exits 0/1). Settings for pointed paths + source priority. Unit tests per source. **Gate:** `describe`/`resolve` return correct sources; a missing pointed path → `valid=false` not crash.
- **Phase 3 — managed prebuilt.** `managed/prebuilt.py` (glob asset pick, size cache, `.version`, wipe‑then‑extract, cudart for cuda12). Integration test gated on network. **Gate:** install vulkan into a temp managed root, marker written, idempotent re‑run skips.
- **Phase 4 — lifecycle (launch/verify/stop).** `lifecycle.py` + `locking.py`: hidden launch, port poll, pidfile, stop‑only‑ours. Test launch/stop against a *tiny fake server* (a Python `http.server` bound to the port) so the lifecycle logic is tested without a real model. **Gate:** launch reports listening only when it is; stop frees the port; double‑launch under lock → exit 4; an "unknown holder" is reported, not killed.
- **Phase 5 — orchestrator + CLI machine interface.** Wire install/update/force/use/stop through the lock; `cli.py` emits the envelope + exit codes; `contract_version` enforced. **Gate:** `python -m llamagui status --json` parses; a bad action → exit 5; `--json` stdout is a single object.
- **Phase 6 — GUI shell + dashboard + actions + resolver page.** Workers thread every mutation; progress bar from `progress.py`; source badges from the resolver; first‑run wizard that can **point at the reference dir** and run immediately. **Gate:** end‑to‑end against managed‑prebuilt *and* against pointed‑reference binaries; double‑click shows "busy"; failure dialog shows `log_tail`.
- **Phase 7 — models + config ownership + logs + settings.** `config_yaml.py` single‑writer; token via `keyring`/DPAPI (never plaintext); log tail via `QFileSystemWatcher`. **Gate:** round‑trip equality of config edits; token absent from disk in plaintext.
- **Phase 8 — managed build from submodules (optional, toolchain‑gated).** `managed/build.py`; graceful fallback to prebuilt when MSVC/Go absent (exit 7 in CLI). **Gate:** on a machine with the toolchain, build vulkan from the submodule into managed; on a machine without, the action offers prebuilt and does not crash.
- **Phase 9 — toolchain hardening + CI** (§toolchain). **Gate:** `just check` / `scripts/check.ps1` green from clean clone.
- **Phase 10 — Nuitka packaging** (§13). **Gate:** `--standalone` build launches on a clean folder, renders Qt, runs a mutation, `--version` self‑test exits 0.

---

## 12. Toolchain (uv + polyglot linters)

### 12.1 uv project (`pyproject.toml`)
```toml
[project]
name = "llamagui"
version = "0.1.0"
requires-python = ">=3.12,<3.13"
dependencies = [
  "pyside6>=6.7",        # EXAMPLE — re-resolve; pin to a line nuitka's pyside6 plugin supports
  "ruamel-yaml>=0.18",
  "httpx>=0.27",         # optional direct /health & /v1/models probes
  "keyring>=25",         # encrypted token storage (Windows uses DPAPI backend)
]
[dependency-groups]
dev = [
  "pytest>=8", "pytest-qt>=4", "pytest-mock>=3",
  "ruff>=0.6", "mypy>=1.11", "pyright>=1.1",
  "nuitka>=2.0", "ordered-set", "zstandard",   # EXAMPLE — verify nuitka↔pyside6↔python triple
]
[project.scripts]
llamagui = "llamagui.__main__:main"
[build-system]
requires = ["hatchling"]; build-backend = "hatchling.build"

[tool.ruff]
line-length = 100; target-version = "py312"
[tool.ruff.lint]
select = ["E","F","W","I","UP","B","SIM","RUF","PTH","T20","PT"]   # PTH=pathlib; T20=no stray print
[tool.ruff.lint.per-file-ignores]
"tests/**" = ["T20"]
[tool.ruff.format]
quote-style = "double"

[tool.mypy]
python_version = "3.12"; strict = true
[[tool.mypy.overrides]]
module = ["PySide6.*","ruamel.*"]
ignore_missing_imports = true     # prefer generating stubs where feasible

[tool.pyright]
include = ["src","tests"]; pythonVersion = "3.12"; typeCheckingMode = "strict"
venvPath = "."; venv = ".venv"; reportMissingTypeStubs = "warning"

[tool.pytest.ini_options]
testpaths = ["tests"]; qt_api = "pyside6"
markers = ["integration: needs network or external binaries (skipped by default)"]
addopts = "-q"
```
> **mypy + pyright both** is intentional (as requested). Rule: same python version, same strictness intent; when they disagree, **fix the code**, don't silence one.

### 12.2 Non‑uv tools — and the two corrections you must honor
- **shfmt** formats **POSIX shell / bash**, **NOT PowerShell.** Configure it (`.editorconfig` `[*.sh]` indent 2, `shell_variant=bash`; run `shfmt -w -d .`). **If the project has no `*.sh`, shfmt is configured‑but‑idle — that is fine; do not invent a shell script to satisfy it.**
- **PowerShell formatting/linting = `PSScriptAnalyzer`** (`Install-Module -Scope CurrentUser PSScriptAnalyzer`; `Invoke-ScriptAnalyzer -EnableExit`; `Invoke-Formatter`). **Only relevant if a `*.ps1` exists in the new repo** (e.g. `scripts/check.ps1`, `scripts/build_nuitka.ps1`). It applies to *those*, never to the frozen reference path.
- **jscpd** = **duplication detection, not a formatter.** Run via `npx --yes jscpd@latest .` (or pin; or the Docker image — pick one, document it). `.jscpd.json`:
  ```json
  { "threshold": 5, "minLines": 6, "minTokens": 60,
    "ignore": ["**/.venv/**","**/build/**","**/dist/**","**/*.lock","**/resources/**","**/vendor/**"],
    "reporters": ["console"], "format": ["python","powershell","yaml","json"] }
  ```
  (Ignore `vendor/` — submodule code is not yours to dedupe.)

### 12.3 One‑command check
`justfile` (optional; `scoop install just`) **or** `scripts/check.ps1` (no extra install) running, in order, **aggregating all exit codes (do not silently short‑circuit)**: `ruff format` → `ruff check` → `mypy` → `pyright` → (if `*.ps1`) PSScriptAnalyzer → (if `*.sh`) `shfmt -d` → `jscpd` → `pytest -m "not integration"`. Print a summary table; exit non‑zero if any step failed. Optional `.pre-commit-config.yaml` calling the same.

---

## 13. Nuitka packaging

MSVC required (present via VS 2022 Build Tools); run from a `vcvarsall`‑initialized shell or let Nuitka locate MSVC. **Pin the PySide6 line to one the Nuitka `pyside6` plugin supports** (check Nuitka release notes at build time — this is the #1 compatibility risk). Record the working **Python / PySide6 / Nuitka triple** in `docs/BUILD.md` and CI.

Debug build first (console on):
```powershell
uv run python -m nuitka --standalone --enable-plugin=pyside6 `
  --include-qt-plugins=platforms,imageformats,iconengines,tls `
  --assume-yes-for-downloads --windows-console-mode=force `
  --output-dir=build\nuitka --remove-output src\llamagui\__main__.py
```
Release (windowed): add `--windows-console-mode=disable --windows-icon-from-ico=…` + version metadata.

**Gotchas to test before shipping (do not skip):**
1. Qt plugins at runtime (`platforms/qwindows.dll` present in the build; missing‑platform‑plugin = the classic failure → `--include-qt-plugins=platforms` and/or set `QT_QPA_PLATFORM_PLUGIN_PATH` next to exe as fallback).
2. **Ship `--standalone`** (a folder; zip / wrap with Inno Setup or NSIS). Attempt `--onefile` only after standalone works and only if a single exe is required (Qt + onefile is the riskiest path).
3. VC++ runtime present in the output or on the target; test on a clean machine/VM.
4. With console disabled, an uncaught exception is invisible → install a top‑level `sys.excepthook` + file logging under the app data dir + a message box in release mode. Keep the console build for support.
5. First compile is long; use ccache/`--clang-cache` if available; cache `build/` in CI.
6. Resource files (icons/qrc) bundled; verify they render.
7. AV false positives on packed exes — expect it; sign if distributing.
8. `scripts/build_nuitka.ps1` runs `just check` first (never package a red tree), builds, then a **post‑build verify**: launch the built exe with `--version` (add a tiny `--version` to `__main__` that prints the envelope and exits 0) and assert exit 0 — so a broken Qt‑plugin bundle fails the *build*, not the user.

---

## 14. GUI screens (product direction, not over‑spec)

- **Dashboard:** per‑backend cards (installed? version? **source badge**), `current` junction target, router port + LISTENING badge, llama‑swap version+source, config‑present. Fast refresh = file read on a timer + focus; manual "deep refresh" = engine `status`.
- **Resolver / Sources page:** the four sources per binary; pointed‑path pickers (single folder *or* separate server/swap paths); "validate" button (runs `--version`); source‑priority ordering; a one‑click "point at the reference install" that fills the pointed paths from `D:\Github\llama-prebuilt-swap` (read‑only use).
- **Actions:** Install (multi‑select backends; managed‑prebuilt by default, "build from submodule" toggle), Update (+ Force), Switch backend (+ Auto‑install), Restart router, Stop. Each → worker thread → envelope → UI; exit 4 = "busy"; non‑zero = dialog with `log_tail`.
- **Progress:** per‑mutation bar from the `PROGRESS` parser; indeterminate fallback.
- **Models:** table from `config.yaml` (ruamel round‑trip); add/edit/remove (id, cmd template, group, flags); model‑file picker → absolute path; Save validates by re‑parse; "restart to apply." Show the active server path = resolved `llama-server` so the indirection is visible.
- **Logs:** live tail of `state/llama-swap.{err,out}.log` (`QFileSystemWatcher` + tail) + last mutation's stderr; "open log folder."
- **Settings:** root, port, listen flag, default backend, source priority, auto‑update toggle+interval (headless `update` + notify), token (**keyring**, never plaintext), theme, "launch router on start," "start minimized to tray."
- **First‑run wizard:** choose a source — *point at an existing install* (fastest, zero download) **or** *download prebuilt* **or** *build from submodule* → validate → set active → start router → probe `/health`.

---

## 15. Testing strategy

- **Unit (fast, no network, no real binaries):** `state_reader` (fake root + real junction fixture + real socket), `contract` (envelope parse, version mismatch, every exit code), `config_yaml` (round‑trip + validation + single‑writer invariant), `progress` (parser), `resolver` (fake roots per source; validation via a **stub exe** you write into the fake root that exits 0 or 1), `orchestrator` (fake downloader + fake `subprocess`). Bulk of the suite; runs every commit.
- **Integration (gated `RUN_INTEGRATION=1`):**
  - `test_real_resolver`: point the resolver at the **reference** dir's `bin\vulkan\llama-server.exe` + `…\llama-swap\llama-swap.exe` (**read‑only**) and assert `valid=true` + versions parsed. This proves the "point at existing install" feature against a real install *and* honors §1 (read‑only).
  - `test_managed_prebuilt`: real GitHub download into a temp root (network).
  - lifecycle tested against a **fake server** (Python `http.server`) so launch/verify/stop don't need a model.
- **GUI (`pytest-qt`, `qt_api=pyside6`):** drive the window with `qtbot`; **mock the engine** so GUI tests need no PS/network; assert a failed envelope opens the error dialog, a success refreshes the dashboard, a double‑click shows "busy."
- **Contract parity:** a test asserting the CLI `--json describe` output's keys/exit‑codes are consistent with `contract.py` (catches drift between CLI and types).

---

## 16. Acceptance criteria ("done")

1. `just check` / `scripts/check.ps1` passes on a clean clone: ruff, mypy (strict), pyright (strict), PSScriptAnalyzer on any `*.ps1`, shfmt on any `*.sh`, jscpd under threshold (ignoring `vendor/`), unit+gui tests green.
2. **Nothing under `D:\Github\llama-prebuilt-swap\` was modified** (a CI/local check can `git -C <reference> status --porcelain` if the reference is a git repo, or compare a manifest — but at minimum the agent's own discipline + code review enforce §1; the app itself never writes there).
3. The app runs with **no build of its own**: pointed‑folder (incl. the reference binaries), system `PATH`, and managed‑prebuilt all work; managed‑build works when a toolchain exists and degrades gracefully (exit 7 / offer prebuilt) when not.
4. End‑to‑end via the engine: status from files; install/update/force/use/stop with progress; config.yaml managed as single writer; logs tailed; token encrypted.
5. No colored‑text scraping anywhere; reads never spawn subprocess; the engine never trusts a launch without a port check; stop kills only self‑spawned pids.
6. Double‑clicking any mutation is safe (lock → exit 4).
7. A Nuitka `--standalone` build launches on a clean Windows folder, renders Qt, runs a mutation, and `--version` self‑test exits 0.
8. `docs/BUILD.md` records: the working triple; the resolver's four sources; the "shfmt ≠ PS formatter → PSScriptAnalyzer for `.ps1`" decision; the "jscpd = dup detection" decision; the read/write split; the locked `contract_version`; and the submodule‑pin vs prebuilt‑latest cadence distinction.

---

## 17. Hard "do not" list (put in CONTRIBUTING too)

- Do **not** write/edit/delete anything under `D:\Github\llama-prebuilt-swap\`.
- Do **not** execute the reference `setup-llama.ps1` as the engine (or at all, at runtime).
- Do **not** parse colored/`[OK]`/`[XX]` text from any process.
- Do **not** treat `Popen`/launch return as liveness — poll the port.
- Do **not** copy with a wildcard where a literal path is expected (invariant #5).
- Do **not** kill processes by name or by path‑scan — only pids the app spawned (invariant #8).
- Do **not** hardcode the backend list, the root path, or version pins (data / config / resolve‑at‑build‑time).
- Do **not** store the GitHub token in plaintext (keyring/DPAPI).
- Do **not** edit `config.yaml` from two writers; the app owns it (or confirms before editing an external one).
- Do **not** emit CUDA arch `compute_61` (invariant #12).
- Do **not** require submodules to be present at runtime (invariant: runtime independence).
- Do **not** point `shfmt` at `*.ps1`; do **not** treat `jscpd` as a formatter.
- Do **not** build a phase's code that depends on a later phase's contract before that phase's tests pass (follow §11 order).

---

## 18. Current implementation status (2026-08-09)

**Implemented & verified** — `just check` is green (ruff format/check, mypy, pyright, jscpd) and 111 pytest pass:

- **Engine:** four-source resolver (`resolver.py`), orchestrator (`orchestrator.py`), hidden launch + port/pidfile stop (`lifecycle.py`), managed prebuilt + submodule build (`backends/`), ruamel config ownership (`config_yaml.py`), mutation lock (`locking.py`).
- **CLI** (`cli.py`): `describe / status / resolve / install / update / use / stop / launch / restart / build / list-assets` plus `--json` envelope and exit codes (§9). `gui` subcommand launches the GUI.
- **GUI** (PySide6): Dashboard, Actions, Resolver, Models, Logs, Settings pages; system-tray icon with Show/Quit menu and minimize-to-tray on close; theme (system/light/dark) applied in `gui/app.py`; download/extract progress bar; auto-update timer.
- **Checks:** `just check` is canonical; `scripts/check.py` is fully independent and mirrors it (runs the same tools/scope, no `just` dependency). `just fix` / `scripts/check.py --fix` auto-fix.

**Run / verify:**
```bash
uv run python -m llamagui gui          # launch the GUI
uv run python -m llamagui status --json
just check                             # full check suite (fail-fast)
uv run python scripts/mapping.py      # regenerate mapping.md
```

**Known gaps (not yet built):**
- First-run wizard (spec §14): the point-at-reference / download-prebuilt / build-from-submodule flow.
- Resolver page is display-only (Settings has the pointed-path pickers + source-priority UI); some §14 niceties (e.g. auto-install prompt UX) are minimal.

**Read-next:**
- `mapping.md` — exact file/folder tree (regenerated by `scripts/mapping.py`, respects `.gitignore`).
- `docs/BUILD.md` — Python/PySide6/Nuitka triple, resolver design, shfmt/jscpd decisions.
- This file: §1–§17 invariants + rationale, §10 real layout, this §18 status.
