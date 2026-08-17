"""Process lifecycle and file-based state reads.

Launching, verifying and stopping the llama-server works the same on all three
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
from collections.abc import Mapping
from pathlib import Path
from typing import IO, Any, cast

from loguru import logger

from .paths import is_windows
from .schemas import EngineError, ExitCode
from .serverargs import options_to_cli

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
        return {"llama_server": None, "servers": {}}
    try:
        data: dict[str, Any] = json.loads(pids_path.read_text(encoding="utf-8"))
        return data
    except (json.JSONDecodeError, OSError):
        return {"llama_server": None, "servers": {}}


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


def build_llama_server_args(
    exe_path: str,
    model_path: str,
    host: str = "127.0.0.1",
    port: int = 8080,
    ctx_size: int = 4096,
    n_gpu_layers: int = 999,
    extra_args: str = "",
    server_options: Mapping[str, str] | None = None,
) -> list[str]:
    """Compose the llama-server command line for one model.

    Order: the app-managed flags (``-m``, ``--host``, ``--port``, ``-c``,
    ``-ngl``), then every catalogue option the user set (stable order, see
    :func:`llamagui.serverargs.options_to_cli`), then any raw ``extra_args``
    last so an explicit flag can still override a generated one.
    """
    cmd = [
        exe_path,
        "-m",
        model_path,
        "--host",
        host,
        "--port",
        str(port),
    ]
    # llama.cpp: ``-c 0`` means "use the model's default context". The Settings
    # "auto" value (-1) maps to omitting the flag so the model decides.
    if ctx_size > 0:
        cmd += ["-c", str(ctx_size)]
    cmd += ["-ngl", str(n_gpu_layers)]
    if server_options:
        cmd.extend(options_to_cli(server_options))
    if extra_args:
        cmd.extend(extra_args.split())
    return cmd


def launch_llama_server(
    cmd: list[str],
    host: str,
    port: int,
    root: Path | None = None,
    verify: bool = False,
) -> int | None:
    """Launch an already-built ``llama-server`` command line detached from this process.

    ``cmd`` is produced by :func:`build_llama_server_args` (build and launch are
    separate concerns, so this only owns the spawn). ``host``/``port`` are used
    solely for post-launch port verification.

    Returns the PID. With ``verify=True`` the port is polled first and ``None``
    is returned when the server never came up (invariant #6: a successful spawn
    is not proof of liveness).
    """
    logger.info("launching llama-server: {}", " ".join(cmd))

    out_log: IO[Any] | int
    err_log: IO[Any] | int
    if root:
        log_dir = root / "state"
        log_dir.mkdir(parents=True, exist_ok=True)
        out_log = (log_dir / "llama-server.out.log").open("w", encoding="utf-8")
        err_log = (log_dir / "llama-server.err.log").open("w", encoding="utf-8")
    else:
        # CLI mode without a managed root: still capture logs so --verify and
        # --json can surface diagnostics. Use a temporary directory and store
        # its path so read_log_tail can return the captured output.
        import tempfile

        log_dir = Path(tempfile.mkdtemp(prefix="llama-server-logs-"))
        out_log = (log_dir / "llama-server.out.log").open("w", encoding="utf-8")
        err_log = (log_dir / "llama-server.err.log").open("w", encoding="utf-8")
        global _SERVER_LOG_DIR
        _SERVER_LOG_DIR = log_dir  # pyright: ignore[reportConstantRedefinition]

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
        raise LifecycleError(1, f"Failed to launch llama-server: {e}") from e
    finally:
        for handle in (out_log, err_log):
            if not isinstance(handle, int):
                with contextlib.suppress(OSError):
                    handle.close()

    if root:
        pids = _read_pids(root)
        pids["llama_server"] = proc.pid
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


def read_log_tail(
    root: Path | None,
    state_dir: Path | None = None,
    name: str = "llama-server",
    lines: int = 20,
) -> list[str]:
    """Read the tail of llama-server logs. When ``root`` is None (CLI without managed root),"""
    """use ``state_dir`` instead of ``root / "state"`` so captured logs are still readable."""
    if state_dir is None:
        if root is not None:
            state_dir = root / "state"
        elif _SERVER_LOG_DIR is not None:
            state_dir = _SERVER_LOG_DIR
    if state_dir is None:
        return []
    # Prefer stderr (diagnostics), but fall back to stdout so a server that only
    # writes to stdout still yields its log tail (a Path is truthy even when the
    # file is missing, so an explicit existence check is required for the fallback).
    err_path = state_dir / f"{name}.err.log"
    out_path = state_dir / f"{name}.out.log"
    log_path = (
        err_path if err_path.exists() else (out_path if out_path.exists() else None)
    )
    if log_path is None:
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
        getattr(os, "waitpid")(pid, getattr(os, "WNOHANG"))  # noqa: B009


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

    # The server pid; a legacy "llama_swap" key (pre-llama-server-direct builds)
    # is honoured too so an upgrade never leaves an orphaned process behind.
    targets: list[int] = []
    for key in ("llama_server", "llama_swap"):
        pid = pids.get(key)
        if isinstance(pid, int):
            targets.append(pid)
    servers: dict[str, Any] = pids.get("servers", {}) or {}
    targets.extend(pid for pid in servers.values() if isinstance(pid, int))

    for pid in targets:
        if _stop_pid(pid):
            stopped.append(pid)

    # Rewrite the pidfile to clear any pid we just stopped or that was already
    # dead, so the record never holds a stale pid. We skip the write only when we
    # had nothing to stop (a true no-op -- avoids creating an empty file), or when
    # a target is still alive because we failed to stop it (keep the record so a
    # later stop can retry).
    still_alive = [pid for pid in targets if _pid_exists(pid)]
    if targets and not still_alive:
        _write_pids(root, {"llama_server": None, "servers": {}})

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
    server_pid = cast("int | None", pids.get("llama_server") or pids.get("llama_swap"))
    servers = cast("dict[str, int]", pids.get("servers", {}) or {})
    candidates: list[int | None] = [server_pid, *servers.values()]
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
    "build_llama_server_args",
    "check_port",
    "launch_llama_server",
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


# ─── Module-level state for CLI log recovery ──────────────────────────────

#: Track the last temporary log directory used when ``root=None`` so that
#: ``read_log_tail`` can return captured output in CLI mode.
_SERVER_LOG_DIR: Path | None = None
