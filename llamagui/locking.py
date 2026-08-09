"""Single-writer guard for mutations of the managed root.

Reads never lock. Every mutation (install / update / build / switch / stop)
takes this lock so a double-click can never run two installs at once; the
second caller fails fast with exit code 4.

Windows uses a named mutex (per-user namespace, no elevation needed); POSIX
uses an exclusive lock file that records the owning PID so a crashed process
cannot leave the app permanently "busy".
"""

from __future__ import annotations

import os
import sys
from collections.abc import Generator
from contextlib import contextmanager, suppress
from pathlib import Path

from .schemas import EngineError, ExitCode


class LockAcquisitionError(EngineError):
    """Another mutation holds the lock (contract exit code 4)."""

    def __init__(self, exit_code: ExitCode, message: str) -> None:
        super().__init__(exit_code, message)


@contextmanager
def mutation_lock(root: Path, timeout: float = 0.0) -> Generator[None, None, None]:
    if sys.platform == "win32":
        with _win32_mutex(timeout):
            yield
    else:
        with _file_lock(root, timeout):
            yield


@contextmanager
def _win32_mutex(timeout: float) -> Generator[None, None, None]:
    import ctypes

    _kernel32 = getattr(ctypes, "WinDLL")("kernel32", use_last_error=True)  # noqa: B009
    # Per-user (Local\...) namespace so the mutex works unelevated.
    mutex_name = "Local\\llama-gui-mutation"

    mutex = _kernel32.CreateMutexW(None, False, mutex_name)
    if not mutex:
        raise OSError("Failed to create mutex")

    wait_ms = int(max(timeout, 0.0) * 1000)
    try:
        if _kernel32.WaitForSingleObject(mutex, wait_ms) == 0x00000102:
            raise LockAcquisitionError(
                ExitCode.LOCK_CONFLICT, "Another mutation is in progress"
            ) from None
        yield
    finally:
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
