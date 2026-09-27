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

- [x] ~~**"Ready" only means "a file exists"**~~ — **done**:
      added `Orchestrator.first_run_needed()`, which resolves with
      `validate=True` so the first-run decision is based on a binary that
      actually runs, not just one that exists. `MainWindow._maybe_first_run`
      now uses it instead of `status().ready`. The probe runs once at startup
      (not on the dashboard poll, which keeps `status()` subprocess-free).
      5 new tests, plus the GUI `fake_orch` fixture now stubs the probe so the
      suite does not spawn a real binary.

- [x] ~~**Recursive model scan on every dashboard poll**~~ — **done**:
      added `model_store.list_models_cached()`, which memoises the listing
      against a cheap fingerprint of the tree (name, size and mtime per
      `.gguf`), plus `clear_model_cache()` which `download_model` /
      `remove_model` call after they change the tree. `Orchestrator.list_models()`
      uses the cached variant, so a poll over a large library no longer rebuilds
      every `ModelInfo` row each time while still reacting immediately to a model
      being added, removed, or completed. 6 new tests in
      `tests/unit/test_model_store.py`.

- [x] ~~**Blocking `stop()` on the GUI thread during shutdown**~~ — **done**:
      `stop_processes` / `Orchestrator.stop` now take a `grace` argument, and
      `MainWindow.closeEvent` passes `SHUTDOWN_GRACE_SECONDS` (0.5 s) instead of
      the engine default of 5 s, so a stubborn server is force-killed promptly
      rather than freezing the window mid-close. The default is unchanged for
      CLI use. 3 new tests, including a real SIGTERM-ignoring child that proves
      the short grace is actually honoured.

- [x] ~~**`EngineWorker` action name is not validated**~~ — **done**:
      `EngineWorker` now resolves its action through `_resolve_method()`, which
      raises a clear `EngineError` naming the bad action and listing the valid
      ones, instead of a bare `AttributeError` from inside the worker.
      Validated against the orchestrator's **methods**, not `orchestrator.ACTIONS`
      — that distinction matters (see below). 3 new tests, including a guard that
      the GUI's method names are never mistaken for typos.

- [x] ~~**First-run resume prompt blocks in a page constructor**~~ — **done**:
      `ModelsPage` now defers the modal resume prompt from `__init__` to
      `showEvent`, guarded by a `_resume_prompted` flag so it happens at most
      once. Showing a `QMessageBox` while the widget tree — and `MainWindow`
      itself — is still being constructed is fragile, and it made startup depend
      on a yes/no answer. 2 new tests: the constructor must not prompt, and the
      prompt must fire exactly once on first show.

- [x] ~~**Download meta sidecar is written on the hot path**~~ — **done**:
      confirmed the suspicion — `_write_meta` ran once per chunk (501 writes for
      a 500-chunk download). Sidecar writes are now throttled to
      `_META_WRITE_INTERVAL` (1 s) inside the chunk loop, with an unconditional
      final write so the recorded byte count is still exact. 1 new test asserts
      the write count is far below the chunk count while the URL and final size
      are still recorded.

- [x] ~~**`cached_download` treats a size match as a cache hit**~~ — **done**:
      a cache hit now requires the expected size **and** an archive that
      actually opens (`_is_readable_archive`, using `zipfile.is_zipfile` /
      `tarfile.getmembers`). A cached file that does not open is deleted and
      re-downloaded instead of being returned and failing later during
      extraction. 2 new tests, both using a corrupt file of the *same length*
      as the good archive, so they fail against the old size-only check.



## P2 — Test gaps

- [x] ~~**No tests for `llamagui/serverargs.py`**~~ — **done**: new
      `tests/unit/test_serverargs.py` with 47 tests covering catalogue invariants
      (unique flags, `--`-only spellings, sections, dedicated flags present,
      alias uniqueness/non-collision, choice values, help text, `negated` only on
      booleans), `find_arg` (canonical + alias + unknown), `validate_value` per
      `ArgKind` (bool normalisation, int, float, choice, string, path, blank),
      `options_to_cli` (bare flag, flag+value, dedicated flags skipped, blanks
      dropped, negated form, catalogue ordering, unknown dropped) and
      `validate_options` / `count`. The catalogue itself turned out to be clean,
      so no data changes were needed.

- [x] ~~**No orchestrator tests for the server-arg actions**~~ — **done**:
      24 tests covering `describe_server_args` (full catalogue, row metadata,
      filtering by flag and by alias, values from `server_options` and from the
      dedicated config fields), `set_server_arg` (normalisation, alias → canonical
      field, blank resets, unknown/volatile/invalid rejection, returns the updated
      row) and `clear_server_args` (empties the map, leaves host/port alone), plus
      an end-to-end check that a set option reaches the built command line. The
      work also **fixed a real bug**: `set_server_arg` let a raw `ValueError`
      escape from `validate_value`, so bad input surfaced as exit 1
      (`UNEXPECTED_ERROR`) instead of 5 (`BAD_ARGUMENT`).

- [x] ~~**No tests for `llamagui/model_store.py`**~~ — **done**: extended
      `tests/unit/test_model_store.py` with 21 tests for `list_models` (missing
      dir, top-level, nested dirs with `/`-separated names, non-`.gguf` and
      `.part` ignored, hidden-dir skip, case-insensitive sort, size/mtime),
      `model_name_from_url` (HF `resolve/main`, query/fragment stripping, dotted
      asset names, hex-digest fallback that is stable and cannot contain `/` or
      `..`) and `remove_model` (delete, nested name, missing file, `../`
      traversal, absolute path outside, non-`.gguf`). The traversal tests confirm
      the `is_relative_to` guard holds and that nothing outside the models dir is
      ever deleted.

- [x] ~~**No tests for the keyring token path**~~ — **done**: new
      `tests/gui/test_token.py` (15 tests) using an in-memory fake keyring — never
      the developer's real credential store. Covers set/get round-trip, unset and
      empty values, delete (including of a missing entry), and all three
      operations against a *broken* keyring backend. On the Settings page: a
      stored token is masked, saving routes it to the keyring, the save payload
      provably contains no token, re-saving under the mask does not clobber the
      real value, clear-token works, and validate reports both success and
      failure. Writing the failure case **found a real bug**:
      `_format_resolution` reported a bare "not found" whenever there was no
      `path`, silently dropping the resolver's `error` — so a binary that was
      found but could not be validated was reported as missing.

- [x] ~~**No test for `main_window._maybe_first_run`**~~ — **done**: new
      `tests/gui/test_first_run.py` (11 tests) covering the decision against the
      real `Orchestrator` (prompt when nothing resolves, when a binary exists but
      cannot run, not when it works, and not once `first_run_complete` is set — the
      last asserting the binary is *not* probed at all) and the dialog's exits
      (skip persists, use-OS enables the toggle, a button disables so it cannot
      double-save, a successful download closes, a failed one stays open with
      its message, a worker error is shown and Retry is re-enabled, and choosing
      the OS install verifies it actually resolved).

- [x] ~~**`scripts/` is untested and `stats.py` walks `.venv`**~~ — **done**:
      new `tests/unit/test_scripts.py` (11 tests) loading each script by path.
      **Fixed the real bug**: `_iter_source_files` used `rglob("*")` with an
      `is_dir()` branch that could not prevent descent, so every run walked the
      whole virtualenv; it now uses `os.walk` with in-place pruning (verified:
      fails on the old code, and `stats.py` now runs in ~0.2 s). Tests also
      cover `mapping.py` (generates a tree, respects `.gitignore` and the
      hardcoded extras) and `clean.py` (`--dry-run` deletes nothing, a real run
      removes caches and coverage files).

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

