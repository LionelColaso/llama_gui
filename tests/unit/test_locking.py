"""Locking: the single-writer guard must be per-root and cross-thread.

A backend mutation must block a concurrent mutation of the *same* root (the
single-writer guarantee), but two independent roots must never block each
other — that scoping is what stopped ``just check`` from colliding with a
running app (which uses a different root).
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

from llamagui.locking import LockAcquisitionError, _mutex_name_for, mutation_lock


def test_same_root_blocks_across_threads(tmp_path: Path) -> None:
    errored = threading.Event()
    started = threading.Event()

    def worker() -> None:
        started.set()
        try:
            with mutation_lock(tmp_path, timeout=0.0):
                pass
        except LockAcquisitionError:
            errored.set()

    with mutation_lock(tmp_path):
        t = threading.Thread(target=worker, daemon=True)
        t.start()
        assert started.wait(2.0)
        # Hold the lock a moment so the worker actually contends for it.
        time.sleep(0.3)
        assert errored.is_set(), "concurrent mutation of the same root must fail"
    t.join(timeout=2.0)


def test_different_roots_do_not_block(tmp_path: Path) -> None:
    a = tmp_path / "a"
    b = tmp_path / "b"
    # Independent roots get independent locks; entering both must not deadlock.
    with (
        mutation_lock(a),
        mutation_lock(b),
    ):
        pass


def test_mutex_name_is_stable_per_root(tmp_path: Path) -> None:
    r = tmp_path / "root"
    assert _mutex_name_for(r) == _mutex_name_for(r)
    assert _mutex_name_for(r) != _mutex_name_for(tmp_path / "other")
