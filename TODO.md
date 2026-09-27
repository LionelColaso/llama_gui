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
- [x] ~~**`launch` / `restart` do not take the mutation lock**~~ — **done**
      (`llamagui/orchestrator.py`): `launch()` now wraps itself in
      `mutation_lock`, and `restart()` takes the lock *once* around both its stop
      and its launch rather than delegating to the individually-locking `stop`
      and `launch`. The body was split into `_launch_locked` / `_stop_locked`
      because the POSIX lock file is created `O_EXCL` and is therefore **not
      reentrant** — a nested acquire would have raised. This also makes the stop
      and the launch atomic with respect to other mutations. Proven by 4 new
      tests, which were checked to fail against the old unlocked code.
- [x] ~~**`latest_release` is `lru_cache`d forever**~~ — **done**
      (`llamagui/backends/prebuilt.py`): replaced the process-lifetime
      `lru_cache` with a 5-minute TTL cache (`RELEASE_CACHE_TTL`), so
      `auto_update` on a timer now sees newly published releases. Added
      `clear_release_cache()`, which `Orchestrator.update()` calls first so an
      explicit update always observes the newest release rather than whatever
      was fetched at startup. 5 new tests cover TTL reuse, expiry,
      `clear_release_cache`, per-repo/token keying, and that `update()` re-fetches.
- [x] ~~**Progress + download-control callbacks are process-global**~~ — **done**
      (`llamagui/backends/prebuilt.py`, `llamagui/download.py`): both are now
      **thread-local** rather than process-global. The GUI runs each mutation on
      its own `QThreadPool` thread, so scoping to the thread keeps each worker's
      callback and its Pause/Cancel handle to itself. Overlapping workers (the
      auto-update timer plus a model download) can no longer clobber each other.
      Added `get_progress_callback()` and fixed the two extraction sites that
      still read the old global. 4 new tests cover isolation and non-leakage
      across threads.
- [x] ~~**`--json` is read from `sys.argv`, not the parsed argv, on argparse
      errors**~~ — **done** (`llamagui/cli.py`): `_Parser` now takes a `use_json`
      flag that `main()` passes in from its real `argv`, so an argument error
      emits the documented JSON envelope for programmatic callers (tests,
      embedding) as well as the process CLI. The two CLI tests were strengthened
      to assert the actual output (envelope fields / stderr text), not just the
      exit code — the exit code alone hid this bug entirely.
- [x] ~~**`clear_quarantine` spawns one `xattr` process per file**~~ — **done**
      (`llamagui/paths.py`): now tries a single recursive
      `xattr -dr com.apple.quarantine <target>` first (one process for the whole
      tree), falling back to the per-file loop only if that fails. The old
      per-file `xattr -d` is kept as a fallback for symlinks and filesystems
      where `-r` does not descend. 3 new tests assert a single recursive call,
      the fallback, and that it stays a no-op off macOS.
- [x] ~~**Unit tests touch the real user config and root**~~ — **done**
      (`tests/conftest.py`): an autouse `_isolated_config` fixture now points
      `LLAMAGUI_CONFIG_DIR`, the platform data dir (`LOCALAPPDATA` /
      `XDG_DATA_HOME` / `XDG_CONFIG_HOME`) and `LEGACY_ROOT` at `tmp_path` for
      every test, so nothing reaches the developer's real
      `%APPDATA%/llamagui` or `~/.llamagui`. 3 new tests assert the sandbox
      holds (config path, default root, and that saving the default config
      writes into `tmp_path`).
- [x] ~~**A real network download runs in the unit suite**~~ — **done**: the
      unit test now stubs `latest_release` and `_obtain_backend` and asserts the
      backend is obtained and activated, fully offline and deterministic. The
      live variant moved to `tests/integration/test_managed_prebuilt.py`
      (`test_cli_use_auto_install_live`, marked `integration` + `GITHUB_TOKEN`),
      so the real download is still covered where it belongs.


## P1 — Correctness & robustness

- [x] ~~**Clamp numeric settings on load**~~ — **done** (`llamagui/config.py`):
      `port` (1–65535), `ctx_size` (0 = auto … 4 Mi), `n_gpu_layers` (-1 = auto …
      65536) and `auto_update_interval_hours` (1 … 1 year) are now clamped on
      load, and each correction is appended to `load_warnings` so the GUI/CLI can
      show it. 14 new tests cover the ranges, the warnings, and that in-range
      values (including `ctx_size: 0`) stay untouched.

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

