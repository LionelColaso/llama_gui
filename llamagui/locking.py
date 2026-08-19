"""Single-writer guard for mutations of the managed root.

Reads never lock. Every mutation (install / update / build / switch / stop)
takes this lock so a double-click can never run two installs at once; the
second caller fails fast with exit code 4.

Windows uses a named mutex (per-user namespace, no elevation needed); POSIX
uses an exclusive lock file that records the owning PID so a crashed process
cannot leave the app permanently "busy".
"""

from __future__ import annotations

import hashlib
import os
import sys
from collections.abc import Generator
from contextlib import contextmanager, suppress
from pathlib import Path

from .schemas import EngineError, ExitCode

# WaitForSingleObject return codes.
_WAIT_OBJECT_0 = 0x00000000
_WAIT_ABANDONED = 0x00000080
_WAIT_TIMEOUT = 0x00000102


class LockAcquisitionError(EngineError):
    """Another mutation holds the lock (contract exit code 4)."""

    def __init__(self, exit_code: ExitCode, message: str) -> None:
        super().__init__(exit_code, message)


def _mutex_name_for(root: Path) -> str:
    """A per-root, per-user mutex name.

    Scoping by the managed root means two independent roots (e.g. the app's
    real root and a test's temp root) never block each other, and running
    ``just check`` while the app is open no longer collides with the test
    suite. The path is hashed so arbitrary characters can't break the
    (restricted) Win32 mutex namespace.
    """
    digest = hashlib.sha1(str(root.resolve()).encode("utf-8")).hexdigest()[:16]
    return f"Local\\llama-gui-mutation-{digest}"


@contextmanager
def mutation_lock(root: Path, timeout: float = 0.0) -> Generator[None, None, None]:
    if sys.platform == "win32":
        with _win32_mutex(_mutex_name_for(root), timeout):
            yield
    else:
        with _file_lock(root, timeout):
            yield


@contextmanager
def _win32_mutex(name: str, timeout: float) -> Generator[None, None, None]:
    import ctypes

    _kernel32 = getattr(ctypes, "WinDLL")("kernel32", use_last_error=True)  # noqa: B009
    # Per-user (Local\...) namespace so the mutex works unelevated.
    mutex = _kernel32.CreateMutexW(None, False, name)
    if not mutex:
        raise OSError("Failed to create mutex")

    wait_ms = int(max(timeout, 0.0) * 1000)
    result = _WAIT_TIMEOUT
    try:
        result = _kernel32.WaitForSingleObject(mutex, wait_ms)
        if result == _WAIT_TIMEOUT:
            raise LockAcquisitionError(
                ExitCode.LOCK_CONFLICT, "Another mutation is in progress"
            ) from None
        # _WAIT_OBJECT_0: we acquired it. _WAIT_ABANDONED: the previous owner
        # died without releasing (e.g. a crashed process) — we own it now and
        # may proceed, so a dead owner can never wedge the app permanently.
        yield
    finally:
        # Release only when we actually own it; releasing a mutex we never
        # acquired (the timeout path) is undefined behaviour on Windows.
        if result in (_WAIT_OBJECT_0, _WAIT_ABANDONED):
            _kernel32.ReleaseMutex(mutex)
        _kernel32.CloseHandle(mutex)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _steal_if_stale(lock_path: Path) -> bool:
    """Remove a lock file whose owning process is gone. True when removed."""
    try:
        owner = lock_path.read_text(encoding="utf-8").strip()
    except OSError:
        return False
    if owner.isdigit() and _pid_alive(int(owner)):
        return False
    with suppress(OSError):
        lock_path.unlink()
        return True
    return False


@contextmanager
def _file_lock(root: Path, timeout: float = 0.0) -> Generator[None, None, None]:
    lock_dir = root / "state"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / "mutation.lock"

    acquired = False
    try:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            if not _steal_if_stale(lock_path):
                raise LockAcquisitionError(
                    ExitCode.LOCK_CONFLICT,
                    "Another mutation is in progress",
                ) from None
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(str(os.getpid()))
        acquired = True
        yield
    finally:
        if acquired:
            with suppress(FileNotFoundError):
                lock_path.unlink()
