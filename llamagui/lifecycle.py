from __future__ import annotations

import json
import os
import signal
import socket
import struct
import subprocess
import sys
import time
from contextlib import suppress
from pathlib import Path
from typing import IO, Any


class LifecycleError(Exception):
    def __init__(
        self, exit_code: int, message: str, log_tail: list[str] | None = None
    ) -> None:
        self.exit_code = exit_code
        self.log_tail = log_tail
        super().__init__(message)


PIDS_FILE = "state/pids.json"


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
    _OpenProcess = _kernel32.OpenProcess
    _CloseHandle = _kernel32.CloseHandle
    _TerminateProcess = _kernel32.TerminateProcess
    _PROCESS_QUERY_INFORMATION = 0x0400
    _PROCESS_TERMINATE = 0x0001

    def _pid_exists(pid: int) -> bool:
        """Check if a process with the given PID exists on Windows.
        Uses OpenProcess directly to avoid a CPython bug (os.kill(pid, 0) hangs
        on Windows when called for an invalid PID after a successful check).
        """
        handle = _OpenProcess(_PROCESS_QUERY_INFORMATION, False, pid)
        if handle:
            _CloseHandle(handle)
            return True
        return False

    def _kill_pid(pid: int) -> bool:
        """Terminate a Windows process by PID.

        ``os.kill(pid, SIGTERM)`` on Windows only delivers CTRL_BREAK to
        console processes and does NOT kill a DETACHED_PROCESS spawned with
        CREATE_NO_WINDOW. ``TerminateProcess`` reliably kills the detached
        server we launched (invariant #8: only pids we spawned).
        """
        handle = _OpenProcess(_PROCESS_TERMINATE, False, pid)
        if not handle:
            return False
        try:
            _TerminateProcess(handle, 1)
            return True
        except OSError:
            return False
        finally:
            _CloseHandle(handle)
else:

    def _pid_exists(pid: int) -> bool:
        try:
            os.kill(pid, 0)
            return True
        except (OSError, PermissionError):
            return False

    def _kill_pid(pid: int) -> bool:
        try:
            os.kill(pid, signal.SIGTERM)
            return True
        except OSError:
            return False


def launch_llama_swap(
    exe_path: str,
    config_path: str,
    host: str = "127.0.0.1",
    port: int = 8080,
    listen_flag: str = "--listen",
    root: Path | None = None,
    verify: bool = False,
) -> int | None:
    """Launch llama-swap as a detached subprocess.

    Returns the PID by default. If ``verify=True``, polls for the configured
    port and returns the PID only if the server is confirmed listening;
    otherwise returns ``None`` so callers can report liveness per invariant #6.
    """
    cmd = [exe_path, "--config", config_path, listen_flag, f"{host}:{port}"]

    out_log: IO[Any] | int | None
    err_log: IO[Any] | int | None
    if root:
        log_dir = root / "state"
        log_dir.mkdir(parents=True, exist_ok=True)
        out_log = (log_dir / "llama-swap.out.log").open("w", encoding="utf-8")
        err_log = (log_dir / "llama-swap.err.log").open("w", encoding="utf-8")
    else:
        out_log = subprocess.DEVNULL
        err_log = subprocess.DEVNULL

    creationflags = 0
    if sys.platform == "win32":
        # DETACHED_PROCESS so the server survives the parent app exiting;
        # CREATE_NO_WINDOW so no console window flashes.
        creationflags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=out_log,
            stderr=err_log,
            creationflags=creationflags,
        )
    except OSError as e:
        raise LifecycleError(1, f"Failed to launch llama-swap: {e}") from e

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
        try:
            with socket.create_connection((host, port), timeout=interval):
                return True
        except OSError:
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
        text = log_path.read_text(encoding="utf-8")
        all_lines = text.splitlines()
        return all_lines[-lines:]
    except OSError:
        return []


def stop_processes(
    root: Path,
    host: str = "127.0.0.1",
    port: int | None = None,
) -> dict[str, Any]:
    pids = _read_pids(root)
    stopped: list[int] = []
    still_listening = False
    unknown_holder = False

    targets: list[int] = []
    ls_pid = pids.get("llama_swap")
    if ls_pid is not None:
        targets.append(ls_pid)
    servers: dict[str, Any] = pids.get("servers", {})
    for pid in servers.values():
        if pid is not None:
            targets.append(pid)

    for pid in targets:
        if _pid_exists(pid) and _kill_pid(pid):
            stopped.append(pid)

    # os.waitpid is POSIX-only; it's not available on Windows, so guard it.
    if hasattr(os, "waitpid"):
        for pid in targets:
            with suppress(OSError):
                os.waitpid(pid, 0)

    _write_pids(root, {"llama_swap": None, "servers": {}})

    # Verify the port actually freed up (invariant #6/#8). A port that is still
    # accepting connections after our processes were terminated is held by a
    # process we did NOT spawn — report it rather than silently claiming success.
    port_free = True
    if port is not None and check_port(host, port, timeout=0.2):
        still_listening = True
        port_free = False
        # We stopped everything we spawned, so somebody else holds it now.
        if not stopped:
            unknown_holder = True

    return {
        "stopped_pids": stopped,
        "port_free": port_free,
        "still_listening": still_listening,
        "unknown_holder": unknown_holder,
    }


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


def read_junction_target(root: Path) -> str | None:
    junction_path = root / "managed" / "current"
    if not junction_path.exists():
        return None
    try:
        target = os.readlink(str(junction_path))
        return target
    except (OSError, NotImplementedError):
        pass
    try:
        return _read_reparse_point(junction_path)
    except (OSError, struct.error, UnicodeDecodeError):
        return None


def _read_reparse_point(path: Path) -> str | None:
    """Read Windows junction/symlink target from raw reparse-point bytes."""
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
    """Return True if something is listening on host:port within timeout."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


__all__ = [
    "PIDS_FILE",
    "LifecycleError",
    "_read_reparse_point",
    "check_port",
    "launch_llama_swap",
    "read_active_backend",
    "read_component_version",
    "read_junction_target",
    "read_log_tail",
    "stop_processes",
    "verify_launch",
    "wait_for_port",
]
