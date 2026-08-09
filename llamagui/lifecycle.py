"""Process lifecycle and file-based state reads.

Launching, verifying and stopping the router works the same on all three
platforms, using the right primitive for each:

* Windows – ``DETACHED_PROCESS | CREATE_NO_WINDOW`` so no console flashes and
  the router outlives the GUI; ``TerminateProcess`` to stop it.
* Linux/macOS – ``start_new_session=True`` (own process group, survives the
  parent) and ``SIGTERM`` escalating to ``SIGKILL``.

Reads (active backend, versions, current link, port liveness) are pure file
and socket operations: never a subprocess, so the dashboard can poll them.
"""

from __future__ import annotations

import contextlib
import json
import os
import signal
import socket
import struct
import subprocess
import sys
import time
from pathlib import Path
from typing import IO, Any, cast

from .paths import is_windows
from .schemas import EngineError, ExitCode

PIDS_FILE = "state/pids.json"

#: Grace period before a still-running process is force-killed.
_TERM_GRACE_SECONDS = 5.0


class LifecycleError(EngineError):
    """A launch/stop step failed; carries the contract exit code and log tail."""

    def __init__(
        self,
        exit_code: int,
        message: str,
        log_tail: list[str] | None = None,
    ) -> None:
        super().__init__(ExitCode(exit_code), message, log_tail)


# ─── pid bookkeeping ──────────────────────────────────────────────────────


def _read_pids(root: Path) -> dict[str, Any]:
    pids_path = root / PIDS_FILE
    if not pids_path.exists():
        return {"llama_swap": None, "servers": {}}
    try:
        data: dict[str, Any] = json.loads(pids_path.read_text(encoding="utf-8"))
        return data
    except (json.JSONDecodeError, OSError):
        return {"llama_swap": None, "servers": {}}


def _write_pids(root: Path, data: dict[str, Any]) -> None:
    pids_path = root / PIDS_FILE
    pids_path.parent.mkdir(parents=True, exist_ok=True)
    pids_path.write_text(json.dumps(data, indent=2), encoding="utf-8")


if sys.platform == "win32":
    import ctypes

    _kernel32 = ctypes.windll.kernel32
    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    _SYNCHRONIZE = 0x00100000
    _PROCESS_TERMINATE = 0x0001
    _WAIT_TIMEOUT = 0x00000102

    def _pid_exists(pid: int) -> bool:
        """True when a process with this PID is still running.

        A terminated process whose handle is still open (for example a
        ``Popen`` child that has not been reaped) can still be opened by PID,
        so liveness is decided by waiting on the handle rather than by the
        mere success of ``OpenProcess``.
        """
        access = _PROCESS_QUERY_LIMITED_INFORMATION | _SYNCHRONIZE
        handle = _kernel32.OpenProcess(access, False, pid)
        if not handle:
            return False
        try:
            return bool(_kernel32.WaitForSingleObject(handle, 0) == _WAIT_TIMEOUT)
        finally:
            _kernel32.CloseHandle(handle)

    def _terminate_pid(pid: int, force: bool = False) -> bool:
        """Terminate a Windows process.

        ``os.kill(pid, SIGTERM)`` only delivers CTRL_BREAK to console
        processes and cannot stop a DETACHED_PROCESS/CREATE_NO_WINDOW child,
        so ``TerminateProcess`` is used for both the graceful and forced pass.
        """
        handle = _kernel32.OpenProcess(_PROCESS_TERMINATE, False, pid)
        if not handle:
            return False
        try:
            return bool(_kernel32.TerminateProcess(handle, 1))
        finally:
            _kernel32.CloseHandle(handle)

else:

    def _pid_exists(pid: int) -> bool:
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            # The process exists but belongs to another user.
            return True
        except OSError:
            return False

    def _terminate_pid(pid: int, force: bool = False) -> bool:
        try:
            os.kill(pid, signal.SIGKILL if force else signal.SIGTERM)
            return True
        except OSError:
            return False


def _spawn_kwargs() -> dict[str, Any]:
    """Platform flags that detach a child from this app's console/session."""
    if is_windows():
        detached = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
        no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        return {"creationflags": detached | no_window}
    # POSIX: setsid() so the router survives the GUI and never receives the
    # terminal's signals (Ctrl-C in the launching shell must not kill it).
    return {"start_new_session": True}


# ─── Launch ───────────────────────────────────────────────────────────────


def build_llama_swap_args(
    exe_path: str,
    config_path: str,
    host: str = "127.0.0.1",
    port: int = 8080,
    listen_flag: str = "--listen",
) -> list[str]:
    """Compose the llama-swap command line (listen flag is optional)."""
    cmd = [exe_path, "--config", config_path]
    if listen_flag:
        cmd += [listen_flag, f"{host}:{port}"]
    return cmd


def launch_llama_swap(
    exe_path: str,
    config_path: str,
    host: str = "127.0.0.1",
    port: int = 8080,
    listen_flag: str = "--listen",
    root: Path | None = None,
    verify: bool = False,
) -> int | None:
    """Launch llama-swap detached from this process.

    Returns the PID. With ``verify=True`` the port is polled first and ``None``
    is returned when the router never came up (invariant #6: a successful spawn
    is not proof of liveness).
    """
    cmd = build_llama_swap_args(exe_path, config_path, host, port, listen_flag)

    out_log: IO[Any] | int
    err_log: IO[Any] | int
    if root:
        log_dir = root / "state"
        log_dir.mkdir(parents=True, exist_ok=True)
        out_log = (log_dir / "llama-swap.out.log").open("w", encoding="utf-8")
        err_log = (log_dir / "llama-swap.err.log").open("w", encoding="utf-8")
    else:
        out_log = subprocess.DEVNULL
        err_log = subprocess.DEVNULL

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=out_log,
            stderr=err_log,
            stdin=subprocess.DEVNULL,
            close_fds=True,
            **_spawn_kwargs(),
        )
    except OSError as e:
        raise LifecycleError(1, f"Failed to launch llama-swap: {e}") from e
    finally:
        for handle in (out_log, err_log):
            if not isinstance(handle, int):
                with contextlib.suppress(OSError):
                    handle.close()

    if root:
        pids = _read_pids(root)
        pids["llama_swap"] = proc.pid
        _write_pids(root, pids)

    if verify and not verify_launch(proc.pid, host, port):
        return None

    return proc.pid


def wait_for_port(
    host: str,
    port: int,
    timeout: float = 8.0,
    interval: float = 0.2,
) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check_port(host, port, timeout=interval):
            return True
        time.sleep(interval)
    return False


def verify_launch(
    pid: int,
    host: str,
    port: int,
    timeout: float = 8.0,
) -> bool:
    if not _pid_exists(pid):
        return False
    return wait_for_port(host, port, timeout)


def read_log_tail(root: Path, name: str = "llama-swap", lines: int = 20) -> list[str]:
    log_path = root / "state" / f"{name}.err.log"
    if not log_path.exists():
        log_path = root / "state" / f"{name}.out.log"
    if not log_path.exists():
        return []
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return text.splitlines()[-lines:]


# ─── Stop ─────────────────────────────────────────────────────────────────


def _reap(pid: int) -> None:
    """Collect a zombie child on POSIX; harmless for non-children."""
    if is_windows() or not hasattr(os, "waitpid"):
        return
    with contextlib.suppress(OSError):
        os.waitpid(pid, os.WNOHANG)  # type: ignore[attr-defined]


def _stop_pid(pid: int) -> bool:
    """Ask a process to exit, escalating to a hard kill after the grace period."""
    if not _pid_exists(pid):
        return False
    _terminate_pid(pid, force=False)
    deadline = time.monotonic() + _TERM_GRACE_SECONDS
    while time.monotonic() < deadline:
        _reap(pid)
        if not _pid_exists(pid):
            return True
        time.sleep(0.1)
    _terminate_pid(pid, force=True)
    _reap(pid)
    return not _pid_exists(pid)


def stop_processes(
    root: Path,
    host: str = "127.0.0.1",
    port: int | None = None,
) -> dict[str, Any]:
    """Stop exactly the processes this app started (invariant #8).

    Processes are never matched by name or by scanning the process list, so a
    llama-server started by the user's own script is left untouched.
    """
    pids = _read_pids(root)
    stopped: list[int] = []

    targets: list[int] = []
    ls_pid = pids.get("llama_swap")
    if isinstance(ls_pid, int):
        targets.append(ls_pid)
    servers: dict[str, Any] = pids.get("servers", {}) or {}
    targets.extend(pid for pid in servers.values() if isinstance(pid, int))

    for pid in targets:
        if _stop_pid(pid):
            stopped.append(pid)

    _write_pids(root, {"llama_swap": None, "servers": {}})

    # A port still accepting connections after our processes are gone is held
    # by something we did not spawn: report it, never kill it.
    port_free = True
    still_listening = False
    unknown_holder = False
    if port is not None and check_port(host, port, timeout=0.2):
        still_listening = True
        port_free = False
        unknown_holder = not stopped

    return {
        "stopped_pids": stopped,
        "port_free": port_free,
        "still_listening": still_listening,
        "unknown_holder": unknown_holder,
    }


def running_pids(root: Path) -> list[int]:
    """PIDs recorded by this app that are still alive."""
    pids = _read_pids(root)
    swap_pid = cast("int | None", pids.get("llama_swap"))
    servers = cast("dict[str, int]", pids.get("servers", {}) or {})
    candidates: list[int | None] = [swap_pid, *servers.values()]
    return [p for p in candidates if isinstance(p, int) and _pid_exists(p)]


# ─── State reads ──────────────────────────────────────────────────────────


def read_active_backend(root: Path) -> str | None:
    active_path = root / "state" / "active.txt"
    if not active_path.exists():
        return None
    return active_path.read_text(encoding="utf-8").strip() or None


def read_component_version(root: Path, name: str) -> tuple[str, str | None] | None:
    version_file = root / "managed" / name / ".version"
    if not version_file.exists():
        return None
    text = version_file.read_text(encoding="utf-8").strip()
    parts = text.split("\n", 1)
    tag = parts[0].strip()
    source_str = parts[1].strip() if len(parts) > 1 else ""
    return (tag, source_str)


def read_link_target(path: Path) -> str | None:
    """Resolve a symlink or Windows junction, tolerating a broken link."""
    if not path.is_symlink() and not path.exists():
        return None
    try:
        return os.readlink(str(path))
    except (OSError, NotImplementedError, ValueError):
        pass
    return _read_reparse_point(path)


def read_junction_target(root: Path) -> str | None:
    """Target of the ``managed/current`` link, or None when not set."""
    return read_link_target(root / "managed" / "current")


def _read_reparse_point(path: Path) -> str | None:
    """Read a Windows junction target from the raw reparse-point bytes."""
    try:
        handle = os.open(str(path), os.O_RDONLY)
        try:
            data = os.read(handle, 1024)
            if len(data) < 20:
                return None
            tag = struct.unpack_from("I", data, 0)[0]
            if tag != 0xA000000C:
                return None
            name_len = struct.unpack_from("H", data, 12)[0]
            raw = data[20 : 20 + name_len]
            return raw.decode("utf-16-le").rstrip("\x00")
        finally:
            os.close(handle)
    except (OSError, struct.error, UnicodeDecodeError):
        return None


def check_port(host: str, port: int, timeout: float = 0.2) -> bool:
    """True when something accepts a TCP connection on ``host:port``."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


__all__ = [
    "PIDS_FILE",
    "LifecycleError",
    "_read_reparse_point",
    "build_llama_swap_args",
    "check_port",
    "launch_llama_swap",
    "read_active_backend",
    "read_component_version",
    "read_junction_target",
    "read_link_target",
    "read_log_tail",
    "running_pids",
    "stop_processes",
    "verify_launch",
    "wait_for_port",
]
