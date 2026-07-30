from __future__ import annotations

import os
import sys
from collections.abc import Generator
from contextlib import contextmanager, suppress
from pathlib import Path

from .schemas import ExitCode


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

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    # Use a per-user (Local\...) namespace instead of Global\... so that the
    # mutex works without elevated (Administrator) privileges.
    MUTEX_NAME = "Local\\llama-gui-mutation"

    mutex = _kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if not mutex:
        raise OSError("Failed to create mutex")

    try:
        if _kernel32.WaitForSingleObject(mutex, 0) == 0x00000102:
            raise LockAcquisitionError(
                ExitCode.LOCK_CONFLICT, "Another mutation is in progress"
            ) from None
        yield
    finally:
        _kernel32.ReleaseMutex(mutex)
        _kernel32.CloseHandle(mutex)


@contextmanager
def _file_lock(root: Path, timeout: float = 0.0) -> Generator[None, None, None]:
    lock_dir = root / "state"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / "mutation.lock"

    acquired = False
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
        acquired = True
        yield
    except FileExistsError:
        raise LockAcquisitionError(
            ExitCode.LOCK_CONFLICT,
            "Lock file exists; another mutation may be in progress",
        ) from None
    finally:
        if acquired:
            with suppress(FileNotFoundError):
                lock_path.unlink()


class LockAcquisitionError(Exception):
    def __init__(self, exit_code: ExitCode, message: str) -> None:
        self.exit_code = exit_code
        super().__init__(message)
