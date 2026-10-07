"""Locking: the single-writer guard must be per-root and cross-thread.

A backend mutation must block a concurrent mutation of the *same* root (the
single-writer guarantee), but two independent roots must never block each
other — that scoping is what stopped ``just check`` from colliding with a
running app (which uses a different root).
"""

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

import pytest

from app.locking import (
    LockAcquisitionError,
    _file_lock,
    _mutex_name_for,
    _pid_alive,
    _steal_if_stale,
    _win32_mutex,
    mutation_lock,
)
from app.schemas import ExitCode


def _kill_noop(_pid: int, _sig: int) -> None:
    """Stand in for ``os.kill``: the target pid is alive."""
    return


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


# ─── POSIX file lock (pure Python; exercised directly on any OS) ───


def test_mutation_lock_uses_the_file_lock_off_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    with mutation_lock(tmp_path):
        assert (tmp_path / "state" / "mutation.lock").exists()
    assert not (tmp_path / "state" / "mutation.lock").exists()


def test_file_lock_records_and_removes_the_owner(tmp_path: Path) -> None:
    lock = tmp_path / "state" / "mutation.lock"
    with _file_lock(tmp_path):
        assert lock.exists()
        assert lock.read_text(encoding="utf-8") == str(os.getpid())
    assert not lock.exists()


def test_file_lock_conflict_fails_fast(tmp_path: Path) -> None:
    with (
        _file_lock(tmp_path),
        pytest.raises(LockAcquisitionError) as excinfo,
        _file_lock(tmp_path),
    ):
        pass
    assert excinfo.value.exit_code == ExitCode.LOCK_CONFLICT


def test_file_lock_steals_a_stale_lock(tmp_path: Path) -> None:
    lock = tmp_path / "state" / "mutation.lock"
    lock.parent.mkdir(parents=True)
    lock.write_text("999999999", encoding="utf-8")  # no such pid
    with _file_lock(tmp_path):
        assert lock.read_text(encoding="utf-8") == str(os.getpid())


def test_file_lock_leaves_a_live_lock_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock = tmp_path / "state" / "mutation.lock"
    lock.parent.mkdir(parents=True)
    lock.write_text("1", encoding="utf-8")
    monkeypatch.setattr(os, "kill", _kill_noop)  # pid 1: alive
    with pytest.raises(LockAcquisitionError), _file_lock(tmp_path):
        pass


# ─── stale-lock detection ──────────────────────────────────────────


def test_steal_if_stale_missing_file(tmp_path: Path) -> None:
    assert not _steal_if_stale(tmp_path / "mutation.lock")


def test_steal_if_stale_dead_owner(tmp_path: Path) -> None:
    lock = tmp_path / "mutation.lock"
    lock.write_text("999999999", encoding="utf-8")
    assert _steal_if_stale(lock)
    assert not lock.exists()


def test_steal_if_stale_garbage_owner(tmp_path: Path) -> None:
    lock = tmp_path / "mutation.lock"
    lock.write_text("not-a-pid", encoding="utf-8")
    assert _steal_if_stale(lock)
    assert not lock.exists()


def test_steal_if_stale_live_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock = tmp_path / "mutation.lock"
    lock.write_text(str(os.getpid()), encoding="utf-8")
    monkeypatch.setattr(os, "kill", _kill_noop)
    assert not _steal_if_stale(lock)
    assert lock.exists()


def test_pid_alive_true(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "kill", _kill_noop)
    assert _pid_alive(4242)


def test_pid_alive_gone(monkeypatch: pytest.MonkeyPatch) -> None:
    def _gone(pid: int, sig: int) -> None:
        raise ProcessLookupError(pid)

    monkeypatch.setattr(os, "kill", _gone)
    assert not _pid_alive(4242)


def test_pid_alive_permission_denied_is_alive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pid we may not signal still belongs to a running process."""

    def _denied(pid: int, sig: int) -> None:
        raise PermissionError(pid)

    monkeypatch.setattr(os, "kill", _denied)
    assert _pid_alive(4242)


def test_pid_alive_other_oserror_is_dead(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _broken(pid: int, sig: int) -> None:
        raise OSError(pid, "signal failed")

    monkeypatch.setattr(os, "kill", _broken)
    assert not _pid_alive(4242)


# ─── platform-specific failure paths ────────────────────


@pytest.mark.skipif(sys.platform != "win32", reason="the Win32 named-mutex path")
def test_win32_mutex_creation_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A mutex that cannot be created is an OSError, not a hang."""
    import ctypes

    class _FakeKernel32:
        def CreateMutexW(self, *args: object) -> int:
            return 0

    def _fake_win_dll(name: str, use_last_error: bool = False) -> _FakeKernel32:
        return _FakeKernel32()

    monkeypatch.setattr(ctypes, "WinDLL", _fake_win_dll)
    with (
        pytest.raises(OSError, match="Failed to create mutex"),
        _win32_mutex("local\\llamagui-test-mutex", 0.0),
    ):
        pass


def test_steal_if_stale_keeps_the_lock_when_unlink_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A read-only volume keeps the stale lock rather than crash."""
    lock = tmp_path / "mutation.lock"
    lock.write_text("999999999", encoding="utf-8")  # no such pid

    def _boom(self: Path, missing_ok: bool = False) -> None:
        raise OSError("read-only volume")

    monkeypatch.setattr(Path, "unlink", _boom)
    assert _steal_if_stale(lock) is False
