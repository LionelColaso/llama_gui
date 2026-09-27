# TODO — bugs, correctness gaps and improvements

Findings from a full read of `llamagui/` + `tests/` + `scripts/` + CI config,
with `just check` verified green (ruff, mypy, pyright, jscpd, 187 passed /
7 skipped).

Tasks are ordered **P0 (real bug / data risk) → P1 (correctness & robustness) →
P2 (tests) → P3 (docs, CI, ergonomics)**. Each task names the file(s) to touch.
Anything tagged *(unverified)* is a suspicion raised from reading, not from a
reproduction — confirm before acting.

---

## P0 — Bugs

- [x] ~~**`pids.json` parsing assumes a JSON object**~~ — **done**
      (`llamagui/lifecycle.py` `_read_pids`): the payload is now typed `object`
      and rejected unless it is a dict, and `servers` is normalised to a dict so
      the later `.values()` cannot fail either. A malformed file now degrades to
      "no pids" instead of raising `AttributeError` out of `status` / `stop`.
      Covered by 8 regression tests in `tests/unit/test_lifecycle.py`.
- [ ] **`launch` / `restart` do not take the mutation lock** —
      `llamagui/orchestrator.py`. Every other mutation (`install`, `update`,
      `use`, `stop`, `download_model`, `remove_model`, `discard_download`) wraps
      itself in `mutation_lock(self.root)`, but `launch()` calls
      `stop_processes()` + `launch_llama_server()` unlocked. A concurrent
      `install` runs `wipe_and_extract`, deleting the backend directory out from
      under a spawning server. Wrap the whole body in the lock.
- [ ] **`latest_release` is `lru_cache`d forever** —
      `llamagui/backends/prebuilt.py`. With `auto_update` enabled the GUI timer
      re-runs `update` in the same process, but the cached release dict pins
      "latest" to whatever was fetched at first call for the whole session. Add
      a TTL, or `cache_clear()` on the `update` and `use --auto-install` paths.
- [ ] **Progress + download-control callbacks are process-global** —
      `llamagui/gui/worker_pool.py` `EngineWorker.run` installs
      `set_progress_callback(...)` / `set_download_control(...)` as module
      globals and clears them in `finally`. Two overlapping workers (e.g. the
      auto-update timer plus a model download) clobber each other: the first
      worker's `finally` clears the second's control, so Pause/Cancel silently
      stop working. Either serialise mutations, or thread the callback / control
      through the orchestrator call instead of a global.
- [ ] **`--json` is read from `sys.argv`, not the parsed argv, on argparse
      errors** — `llamagui/cli.py` `_Parser.error` uses
      `"--json" in sys.argv` while `main()` uses its `argv` argument. Invoking
      `main(["bogus", "--json"])` programmatically (tests, embedding) exits 5 but
      prints the *human* error instead of the documented JSON envelope. Pass
      `argv` (or a resolved `use_json` flag) into the parser.
- [ ] **`clear_quarantine` spawns one `xattr` process per file** —
      `llamagui/paths.py`. The llama.cpp release tree holds thousands of files,
      so a macOS install spawns thousands of subprocesses (minutes of wall clock,
      and easy to interrupt halfway). Use one
      `xattr -dr com.apple.quarantine <target>` and only fall back to the
      per-file loop for entries the recursive call misses.
- [ ] **Unit tests touch the real user config and root** —
      `tests/unit/test_cli.py::test_describe_json` / `test_status_json` /
      `test_resolve_json` call `main([...])` with no `--root` and no
      `LLAMAGUI_CONFIG_DIR`, so they read (and can write) the developer's real
      `%APPDATA%/llamagui/config.json` and `~/.llamagui`. Add an autouse fixture
      in `tests/conftest.py` that points `LLAMAGUI_CONFIG_DIR` at `tmp_path` and
      forces `--root`.
- [ ] **A real network download runs in the unit suite** —
      `tests/unit/test_cli.py::test_use_auto_install_succeeds` hits the GitHub
      releases API and pulls a ~150 MB vulkan archive. It is not marked
      `integration`, so `just test` and `scripts/check.py` both depend on the
      network and on llama.cpp's publishing state. Move it to
      `tests/integration/test_managed_prebuilt.py`, or monkeypatch
      `latest_release` / `cached_download`.

## P1 — Correctness & robustness

- [ ] **Clamp numeric settings on load** — `llamagui/config.py` `_coerce_int`
      accepts anything. A hand-edited `port: 0` / `port: 99999` or a negative
      `ctx_size` is persisted verbatim and reaches `check_port` /
      `build_llama_server_args`. Clamp `port` to 1–65535, clamp
      `ctx_size` / `n_gpu_layers` to a sane range (or `auto`), and add a
      `load_warnings` entry so the GUI can show it.
- [ ] **"Ready" only means "a file exists"** — `llamagui/orchestrator.py`
      `status()` sets `ready=bool(server.path)`. `MainWindow._maybe_first_run`
      keys off `ready`, so a present-but-broken binary (wrong arch, missing CUDA
      runtime, quarantine xattr) suppresses the first-run dialog and leaves the
      user with a dead UI. Re-check validity, or surface the resolver error,
      before marking ready.
- [ ] **Recursive model scan on every dashboard poll** — `status()` calls
      `list_models()`, which `rglob`s the entire models directory, and the
      Dashboard refresh timer fires every few seconds. With a large `.gguf`
      library this hammers the disk. Cache the listing keyed on
      `(dir, mtime)`, or move it to the Models page's own refresh.
- [ ] **Blocking `stop()` on the GUI thread during shutdown** —
      `llamagui/gui/main_window.py` `closeEvent` calls `self._orch.stop()`
      synchronously; the POSIX path can sleep up to the 5 s
      `_TERM_GRACE_SECONDS` before escalating, freezing the window as it closes.
      Run it on a worker, or use a short grace period on the close path only.
- [ ] **`EngineWorker` action name is not validated** —
      `llamagui/gui/worker_pool.py` does `getattr(self.orch, self._action)`, so a
      typo surfaces as a runtime `AttributeError` inside a worker instead of a
      clear error. Check the name against `llamagui.orchestrator.ACTIONS` first.
- [ ] **First-run resume prompt blocks in a page constructor** —
      `llamagui/gui/pages/models.py` `ModelsPage.__init__` → `_offer_resume()`
      shows a modal `QMessageBox` per pending `.part` while the widget tree is
      still being built, and it uses the older `resumable_tasks` helper instead
      of the shared `pending_downloads`. Move the prompt to `showEvent` or an
      explicit "Resume pending downloads?" button, and consolidate on one helper.
- [ ] **Download meta sidecar is written on the hot path** — `llamagui/download.py`
      rewrites `<name>.part.meta` inside the chunk loop *(unverified: confirm the
      write cadence)*. For multi-GB downloads that is a lot of small synchronous
      writes on the hot path. Throttle to ~1 Hz and fsync only on phase
      transitions.
- [ ] **`cached_download` treats a size match as a cache hit** —
      `llamagui/backends/prebuilt.py`. If the release API reports an unexpected
      `size`, a truncated or partial cached archive is accepted as valid. Verify
      the archive actually opens before short-circuiting the download.


## P2 — Test gaps

- [ ] **No tests for `llamagui/serverargs.py`** (2 100+ lines, 248 catalogue
      rows) — `options_to_cli`, `validate_value`, `validate_options` and
      `find_arg` are only exercised indirectly. Add
      `tests/unit/test_serverargs.py` covering catalogue invariants (unique
      flags, dedicated flags ⊆ `DEDICATED_FLAGS`, sections ⊆ `SECTIONS`),
      value normalisation per `ArgKind`, and token serialisation order.
- [ ] **No orchestrator tests for the server-arg actions** —
      `describe_server_args` / `set_server_arg` / `clear_server_args` have zero
      coverage, including the dedicated-flag path (`--port`, `--ctx-size`,
      `--n-gpu-layers` → `AppConfig` fields) and the `volatile` rejection.
- [ ] **No tests for `llamagui/model_store.py`** — `list_models` (recursive scan,
      hidden-dir skip, sorting), `model_name_from_url` (query strings, HF
      `resolve/main/...`, no-asset fallback) and `remove_model` path-traversal
      rejection are all untested at the unit level.
- [ ] **No tests for the keyring token path** — `llamagui/gui/token.py`
      (get / set / delete) and `SettingsPage._clear_token` / `_validate` are
      untested. Add a test with a monkeypatched `keyring`.
- [ ] **No test for `main_window._maybe_first_run`** — the ready/skip decision
      and `first_run_complete` persistence are untested. Add a test that the
      dialog is skipped when `ready` and shown when it is not.
- [ ] **`scripts/` is untested and `stats.py` walks `.venv`** —
      `scripts/mapping.py`, `stats.py` and `clean.py` have no coverage;
      `scripts/stats.py::_iter_source_files` has a dead
      `if path.is_dir(): continue` branch that makes `IGNORE_DIRS`
      ineffective, so it traverses the whole virtualenv. Fix the pruning and add
      tests.
- [ ] **`tests/gui/test_phase7.py` is a stale grab-bag** — the name no longer
      describes its contents (model table, size formatting, models page,
      settings page) and it keeps a class name (`TestConfigYaml`) left over from
      a deleted module. Split it into `test_model_table.py`,
      `test_models_page.py` and `test_settings_page.py`.

## P3 — Docs, CI, ergonomics

- [ ] **`just check` and `scripts/check.py` have drifted** — the justfile runs
      `actionlint` and `pytest -m "not integration" -v`; `scripts/check.py` runs
      neither. Both files claim to mirror each other exactly. Re-sync them, or
      have one delegate to the other.
- [ ] **Overlapping GitHub workflows** — `.github/workflows/` has `lint.yml`,
      `pyright.yml`, `pytest.yml`, `ci.yml`, `build.yml`, `auto_build.yml` and
      `release.yml`, several of which appear to re-run the same static-analysis
      and test steps. Consolidate the PR gate into `ci.yml` calling
      `scripts/check.py`, and leave the build/release workflows to artifact
      production only.
- [x] ~~**Doc drift in `AGENTS.md` / `serverargs.py` / `justfile`**~~ — **done**:
      `AGENTS.md` rewritten as a clean design spec (stale `§18` status text, the
      pre-rewrite `§12` test layout, the fictional Actions/Resolver pages and the
      `token.py` misplacement are all fixed; `§18` now points at this file);
      `justfile` and `llamagui/models.py` now say `AGENTS.md` with the correct
      section/invariant numbers; and the missing `scripts/check_server_args` was
      **built** (not deleted), so the `serverargs` docstring is now true. See
      the P3 catalogue-drift task below for what it immediately found.
- [ ] **README page list is stale** — it advertises "Dashboard, Actions,
      Resolver, Models, Logs, Settings", but the shipped sidebar is
      "Dashboard, Server options, Logs, Settings, Downloads" (Actions and
      Resolver were folded into the Dashboard). Update §Features.
- [ ] **The server-arg catalogue is behind a current `llama-server`** — found by
      the new `scripts/check_server_args.py` against a real binary (winget
      `ggml.llamacpp`); the committed snapshot itself is still in sync. These
      options exist upstream but have no `ServerArg` row, so the GUI cannot set
      them: `--kv-unified-per-slot`, `--lazy-mode` / `-lzm`, `--log-jsonl` /
      `--no-log-jsonl`, `--mmproj-device` / `-mmdev`, `--n-cpu-ffn` / `-ncffn`,
      `--spec-synth-len`, `--spec-synth-rates`, `--video-fps`,
      `--video-timestamp-interval`, `--video-ffmpeg-dir`. Add the rows (with
      section, kind, help and default), refresh
      `docs/reference/llama-server-help.txt`, then re-run
      `just check-server-args`. Run it with `--strict` in CI so a nightly that
      adds a flag fails the build instead of drifting silently.
- [ ] **Snapshot is older than the toolchain** — `serverargs.py` pins the
      snapshot to b10488 / commit 9d77fa172, but a current binary already differs
      (see above). Record the version the snapshot came from in the file header
      when refreshing, so the next diff has a baseline to compare against.
- [ ] **`mapping.md` header mentions a `vendor/` submodule that no longer
      exists** — regenerate, and adjust the header text emitted by
      `scripts/mapping.py`.
- [ ] **`EngineError` exit codes are a coarse bucket** — `llamagui/cli.py`
      `main()` maps `OSError` / `ValueError` / `RuntimeError` / `KeyError` /
      `TypeError` to a single `UNEXPECTED_ERROR`. Consider mapping
      `FileNotFoundError` / `PermissionError` to `NOT_AVAILABLE` with a clearer
      message, since both are common user-facing states (missing model file,
      read-only models directory).
- [ ] **No coverage measurement despite `clean.py` supporting it** —
      `pyproject.toml` has no `pytest-cov` / coverage gate even though
      `scripts/clean.py` removes `htmlcov` and `coverage.xml`. Add coverage
      reporting (at minimum in CI) so the P2 gaps stay visible over time.
- [ ] **Consider splitting `llamagui/serverargs.py`** (2 114 lines) — the
      catalogue is data and the helpers are logic; separating them makes the
      data easy to regenerate and diff against
      `docs/reference/llama-server-help.txt`.

---

## Verified-good (no action)

Checked and confirmed **not** problems, so a future pass does not re-litigate
them:

- `just check` is fully green (ruff format + check, mypy strict, pyright
  strict, jscpd 0 clones, pytest).
- Zip-slip / tar-slip rejection in `wipe_and_extract` is implemented and tested.
- Windows `TerminateProcess` and POSIX `SIGTERM`→`SIGKILL` process-group
  handling is correct; the POSIX paths are covered by
  `tests/unit/test_lifecycle_posix.py`.
- The mutation lock handles the abandoned-owner case on both backends
  (`_WAIT_ABANDONED` on Win32, stale-pid steal on the POSIX lock file).
- Config writes are atomic (temp file + `os.replace` + directory fsync), unknown
  keys round-trip, and a corrupt file is preserved as
  `config.corrupt-<ts>.json`.
- The GitHub token is never persisted to the config file (keyring only).
- Worker → GUI progress is correctly routed through a Qt signal (no cross-thread
  widget access), with a dedicated regression test.

