# llama_gui Analysis Plan

Findings below are evidence-backed and ordered by user impact. Each cites the exact
code path; no source file was modified while producing this plan.

## Verified Safe

- Stop is pid-scoped: only `state/pids.json` entries are terminated, then the port is
  re-checked and an unknown holder is reported, never killed (`lifecycle.py:492-530`).
- Mutations are serialized per root via a Win32 mutex or a POSIX `O_EXCL` lock file with
  stale-pid recovery (`locking.py:50-55,103-114`), and downloads use thread-local
  control state rather than a shared global (`download.py:27`).
- Worker progress crosses threads only through a Qt signal (auto-queued); the engine
  callback never touches a widget (`worker_pool.py:119-143`), and progress callbacks are
  thread-local (`prebuilt.py:82-93`).
- The log pane is bounded (`setMaximumBlockCount(10_000)`, `log_view.py:20`) and tails
  incrementally by file position instead of re-reading the file (`log_view.py:52-58`).
