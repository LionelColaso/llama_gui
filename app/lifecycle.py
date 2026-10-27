"""Server process lifecycle: launch, verify and stop llama-server.

Launching, verifying and stopping works the same on all three platforms,
using the right primitive for each:

* Windows – ``DETACHED_PROCESS | CREATE_NO_WINDOW`` so no console flashes and
  the server outlives the GUI; ``TerminateProcess`` to stop it.
* Linux/macOS – ``start_new_session=True`` (own process group, survives the
  parent) and ``SIGTERM`` escalating to ``SIGKILL``.

This module owns process control and the pid file. The pure managed-root *reads*
(active backend, versions, the ``current`` link, port liveness) live in
:mod:`app.state`, and the ``current`` link itself is written by
:mod:`app.links`.
"""

from __future__ import annotations

import contextlib
import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any, cast

from loguru import logger

from .paths import is_windows
from .schemas import EngineError, ExitCode
from .serverargs import options_to_cli
from .state import check_port

if TYPE_CHECKING:
    from .config import AppConfig

PIDS_FILE = "state/pids.json"

#: Grace period before a still-running process is force-killed.
_TERM_GRACE_SECONDS = 5.0

#: How many launches' stdout/stderr pairs are kept in ``state/``.
LOG_LAUNCHES_TO_KEEP = 5

#: Read window sizes for :func:`read_log_tail`; the log is never read whole.
_TAIL_BLOCK_BYTES = 64 * 1024
_TAIL_MAX_BYTES = 4 * 1024 * 1024


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


#: The pid file shape the engine reads. Anything else is treated as absent.
_EMPTY_PIDS: dict[str, Any] = {"llama_server": None, "servers": {}}


def _read_pids(root: Path) -> dict[str, Any]:
    """Read ``state/pids.json``, tolerating a missing or damaged file.

    The file is only ever written by this app, but it is user-editable state:
    a hand-edited, truncated or otherwise malformed payload must never crash
    ``status`` / ``stop``. Anything that is not a JSON object — and any object
    missing the two expected keys — falls back to :data:`_EMPTY_PIDS`.
    """
    pids_path = root / PIDS_FILE
    if not pids_path.exists():
        return dict(_EMPTY_PIDS)
    try:
        data: object = json.loads(pids_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError, OSError:
        return dict(_EMPTY_PIDS)
    if not isinstance(data, dict):
        return dict(_EMPTY_PIDS)
    record = cast("dict[str, Any]", data)
    raw_servers = record.get("servers")
    servers = (
        cast("dict[str, Any]", raw_servers) if isinstance(raw_servers, dict) else {}
    )
    return {"llama_server": record.get("llama_server"), "servers": servers}


def _write_pids(root: Path, data: dict[str, Any]) -> None:
    pids_path = root / PIDS_FILE
    pids_path.parent.mkdir(parents=True, exist_ok=True)
    pids_path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _reap(pid: int) -> None:
    """Collect a zombie child on POSIX; harmless for non-children."""
    if is_windows() or not hasattr(os, "waitpid"):
        return
    with contextlib.suppress(OSError):
        getattr(os, "waitpid")(pid, getattr(os, "WNOHANG"))  # noqa: B009


def _pid_alive(pid: int) -> bool:
    """Liveness after collecting an exited child.

    On POSIX an exited child stays a zombie -- and still answers
    ``kill(pid, 0)`` -- until it is waited for, so ``_pid_exists`` alone reports
    a dead server as running (status, stop and the next launch all believe it).
    """
    _reap(pid)
    return _pid_exists(pid)


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

#: Dedicated flags a per-model scope may override. ``--host``/``--port`` are
#: excluded on purpose: the app probes exactly one host:port for health, status
#: and stop, so letting one model move it would strand the rest.
MODEL_SCOPABLE_DEDICATED = ("--ctx-size", "--n-gpu-layers")

#: The two dedicated flags that are always global, for the same reason.
GLOBAL_ONLY_DEDICATED = ("--host", "--port")

#: Text forms the dedicated flags accept besides a plain number.
_AUTO_WORDS = ("auto", "default")
_ALL_WORDS = ("auto", "all", "default")


@dataclass(frozen=True)
class LaunchSettings:
    """Everything :func:`build_llama_server_args` needs for one launch."""

    host: str
    port: int
    ctx_size: int
    n_gpu_layers: int
    extra_args: str
    options: dict[str, str]


def dedicated_value_text(flag: str, value: int) -> str:
    """Render a dedicated flag's int as the text its editor shows.

    The inverse of :func:`dedicated_value_int`; ``-1`` ("auto") is blank, so an
    empty field means "let llama.cpp decide", exactly like the other rows.
    """
    if flag == "--ctx-size":
        return str(value) if value > 0 else ""
    if flag == "--n-gpu-layers":
        if value < 0:
            return ""
        if value == 999:
            return "all"
        return str(value)
    return str(value)


def dedicated_value_int(flag: str, text: str, default: int) -> int:
    """Parse a dedicated flag's editor text back to the int the config stores.

    Blank / ``auto`` / ``all`` map to ``-1`` (llama.cpp's "decide for me"), a
    number is taken as-is, and anything else falls back to ``default`` so a
    hand-edited file cannot put a non-numeric ``-c`` on the command line.
    """
    value = text.strip().lower()
    if not value:
        return -1
    if flag == "--n-gpu-layers" and value in _ALL_WORDS:
        return -1
    if flag == "--ctx-size" and value in _AUTO_WORDS:
        return -1
    try:
        return int(value)
    except ValueError:
        return default


def model_server_options(cfg: AppConfig, model: str | None = None) -> dict[str, str]:
    """The catalogue options that actually apply to ``model``.

    No entry for the model means it follows the global defaults; an entry means
    it runs on its own values alone (see
    :attr:`AppConfig.model_server_options`). Either way this is what the GUI
    shows row by row for the model.
    """
    entry = cfg.model_server_options.get(model or "")
    if entry is None:
        return dict(cfg.server_options)
    return dict(entry)


def uses_global_server_config(cfg: AppConfig, model: str | None = None) -> bool:
    """True when ``model`` has no own entry, i.e. it follows the global defaults."""
    return model is not None and model not in cfg.model_server_options


def launch_settings(cfg: AppConfig, model: str | None = None) -> LaunchSettings:
    """Resolve the settings for launching ``model``.

    The global defaults apply until a model has an entry of its own, at which
    point its values alone decide and a blank flag means "let llama.cpp decide"
    rather than "inherit". ``host``/``port`` and the raw extra args are always
    global: the app probes exactly one host:port for health, status and stop.
    """
    entry = cfg.model_server_options.get(model or "")
    if entry is None:
        options = dict(cfg.server_options)
        ctx_size = cfg.ctx_size
        n_gpu_layers = cfg.n_gpu_layers
    else:
        options = dict(entry)
        ctx_size = dedicated_value_int("--ctx-size", options.get("--ctx-size", ""), -1)
        n_gpu_layers = dedicated_value_int(
            "--n-gpu-layers", options.get("--n-gpu-layers", ""), -1
        )
    return LaunchSettings(
        host=cfg.host,
        port=cfg.port,
        ctx_size=ctx_size,
        n_gpu_layers=n_gpu_layers,
        extra_args=cfg.extra_server_args,
        options=options,
    )


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
    :func:`app.serverargs.options_to_cli`), then any raw ``extra_args``
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
        # ``posix=False`` keeps Windows-style quoted paths whole
        # (e.g. ``--chat-template "D:\My Files\t.jinja"`` survives as one
        # argument); shlex strips the surrounding quotes.
        cmd.extend(shlex.split(extra_args, posix=False))
    return cmd


def _new_log_paths(state_dir: Path) -> tuple[Path, Path]:
    """Return a fresh ``(out, err)`` pair that cannot collide with an older launch.

    A timestamped name means a relaunch never truncates a log that a previous
    (possibly still running) server holds open -- on Windows that is a
    ``PermissionError``, and it also makes every launch's output available for
    post-mortem. The numeric suffix keeps rapid launches in the same second
    apart.
    """
    base = f"llama-server-{time.strftime('%Y%m%d-%H%M%S')}"
    attempt = 1
    while True:
        # Fixed-width: a suffixed name must still sort after the plain one.
        stem = f"{base}-{attempt:03d}"
        out_path = state_dir / f"{stem}.out.log"
        err_path = state_dir / f"{stem}.err.log"
        if not out_path.exists() and not err_path.exists():
            return out_path, err_path
        attempt += 1


def prune_server_logs(state_dir: Path, keep: int = LOG_LAUNCHES_TO_KEEP) -> list[Path]:
    """Delete all but the newest ``keep`` launches' log pairs.

    Old files can still be held by a server that never exited; a failed delete
    is ignored rather than allowed to disturb the launch that just succeeded.
    """
    keep = max(keep, 0)
    pruned: list[Path] = []
    for stream in ("out", "err"):
        logs = sorted(state_dir.glob(f"llama-server-*.{stream}.log"))
        stale = logs if keep == 0 else logs[:-keep]
        for old in stale:
            try:
                old.unlink()
            except OSError:
                continue
            pruned.append(old)
    return pruned


def _latest_log(state_dir: Path, name: str, stream: str) -> Path | None:
    """The newest rotated log for ``stream``, or the legacy fixed name."""
    rotated = sorted(state_dir.glob(f"{name}-*.{stream}.log"))
    if rotated:
        return rotated[-1]
    legacy = state_dir / f"{name}.{stream}.log"
    return legacy if legacy.exists() else None


def _read_tail_lines(path: Path, lines: int) -> list[str]:
    """Read only the last ``lines`` lines, seeking backwards from the end."""
    if lines <= 0:
        return []
    try:
        with path.open("rb") as handle:
            offset = handle.seek(0, os.SEEK_END)
            chunks: list[bytes] = []
            newlines = 0
            read = 0
            while offset > 0 and newlines < lines and read < _TAIL_MAX_BYTES:
                step = min(_TAIL_BLOCK_BYTES, offset, _TAIL_MAX_BYTES - read)
                offset -= step
                handle.seek(offset)
                chunk = handle.read(step)
                chunks.append(chunk)
                newlines += chunk.count(b"\n")
                read += step
        data = b"".join(reversed(chunks))
    except OSError:
        return []
    return data.decode("utf-8", errors="replace").splitlines()[-lines:]


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

    out_log: IO[Any] | int = -1
    err_log: IO[Any] | int = -1
    if root:
        log_dir = root / "state"
        log_dir.mkdir(parents=True, exist_ok=True)
        out_path, err_path = _new_log_paths(log_dir)
    else:
        # CLI mode without a managed root: still capture logs so --verify and
        # --json can surface diagnostics. Use a temporary directory and store
        # its path so read_log_tail can return the captured output.
        import tempfile

        log_dir = Path(tempfile.mkdtemp(prefix="llama-server-logs-"))
        out_path, err_path = _new_log_paths(log_dir)
        _SERVER_LOG_DIR.dir = log_dir

    try:
        out_log = out_path.open("w", encoding="utf-8")
        err_log = err_path.open("w", encoding="utf-8")
        proc = subprocess.Popen(
            cmd,
            stdout=out_log,
            stderr=err_log,
            stdin=subprocess.DEVNULL,
            close_fds=True,
            **_spawn_kwargs(),
        )
        _CLI_LOG_DIRS[log_dir] = proc.pid
    except OSError as e:
        raise LifecycleError(1, f"Failed to launch llama-server: {e}") from e
    finally:
        for handle in (out_log, err_log):
            if not isinstance(handle, int):
                with contextlib.suppress(OSError):
                    handle.close()

    if root:
        prune_server_logs(log_dir)
        pids = _read_pids(root)
        pids["llama_server"] = proc.pid
        _write_pids(root, pids)

    if verify and not verify_launch(proc.pid, host, port):
        # A spawn is not a liveness claim: stop the child before failing.
        # Otherwise a retry can start a second server and overwrite the pid
        # record, untracking the first one so Stop can never reach it.
        logger.warning("llama-server pid {} never listened; stopping it", proc.pid)
        _stop_pid(proc.pid)
        if root:
            pids = _read_pids(root)
            if pids.get("llama_server") == proc.pid:
                pids["llama_server"] = None
                _write_pids(root, pids)
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
    if not _pid_alive(pid):
        return False
    return wait_for_port(host, port, timeout)


def read_log_tail(
    root: Path | None,
    state_dir: Path | None = None,
    name: str = "llama-server",
    lines: int = 20,
) -> list[str]:
    """Read the tail of llama-server logs.

    When ``root`` is None (CLI without a managed root), use ``state_dir``
    instead of ``root / "state"`` so captured logs are still readable. Only the
    newest launch's log is read, and only a bounded window from its end.
    """
    if state_dir is None:
        if root is not None:
            state_dir = root / "state"
        elif getattr(_SERVER_LOG_DIR, "dir", None) is not None:
            state_dir = _SERVER_LOG_DIR.dir
    if state_dir is None:
        return []
    # Prefer stderr (diagnostics), but fall back to stdout so a server that only
    # writes to stdout still yields its log tail. The newest per-launch file
    # wins; the fixed legacy names are still read for pre-rotation installs.
    log_path = _latest_log(state_dir, name, "err") or _latest_log(
        state_dir, name, "out"
    )
    if log_path is None:
        return []
    return _read_tail_lines(log_path, lines)


# ─── Stop ─────────────────────────────────────────────────────────────────


def _stop_pid(pid: int, grace: float = _TERM_GRACE_SECONDS) -> bool:
    """Ask a process to exit, escalating to a hard kill after ``grace`` seconds.

    The caller can shorten (or remove) the grace period. GUI shutdown uses a
    short one, because it must not hold the event loop while waiting.
    """
    if not _pid_alive(pid):
        return False
    _terminate_pid(pid, force=False)
    if grace > 0:
        deadline = time.monotonic() + grace
        while time.monotonic() < deadline:
            if not _pid_alive(pid):
                return True
            time.sleep(0.1)
    _terminate_pid(pid, force=True)
    return not _pid_alive(pid)


def stop_processes(
    root: Path,
    host: str = "127.0.0.1",
    port: int | None = None,
    grace: float = _TERM_GRACE_SECONDS,
) -> dict[str, Any]:
    """Stop exactly the processes this app started (invariant #8).

    Processes are never matched by name or by scanning the process list, so a
    llama-server started by the user's own script is left untouched.

    ``grace`` is how long a process is given to exit on its own before being
    force-killed. GUI shutdown passes a short value so closing the window
    cannot block the event loop for the full period.
    """

    pids = _read_pids(root)
    stopped: list[int] = []

    targets: list[int] = []
    pid = pids.get("llama_server")
    if isinstance(pid, int):
        targets.append(pid)
    servers: dict[str, Any] = pids.get("servers", {}) or {}
    targets.extend(pid for pid in servers.values() if isinstance(pid, int))

    for pid in targets:
        if _stop_pid(pid, grace):
            stopped.append(pid)

    # Rewrite the pidfile to clear any pid we just stopped or that was already
    # dead, so the record never holds a stale pid. We skip the write only when we
    # had nothing to stop (a true no-op -- avoids creating an empty file), or when
    # a target is still alive because we failed to stop it (keep the record so a
    # later stop can retry).
    still_alive = [pid for pid in targets if _pid_alive(pid)]
    if targets and not still_alive:
        _write_pids(root, {"llama_server": None, "servers": {}})

    # Reap CLI-mode temporary log directories whose server is no longer running,
    # so CLI launches never leak a temp dir (see A-4).
    for log_dir, pid in list(_CLI_LOG_DIRS.items()):
        if not _pid_alive(pid):
            shutil.rmtree(log_dir, ignore_errors=True)
            _CLI_LOG_DIRS.pop(log_dir, None)

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
    server_pid = cast("int | None", pids.get("llama_server"))
    servers = cast("dict[str, int]", pids.get("servers", {}) or {})
    candidates: list[int | None] = [server_pid, *servers.values()]
    return [p for p in candidates if isinstance(p, int) and _pid_alive(p)]


__all__ = [
    "PIDS_FILE",
    "LifecycleError",
    "build_llama_server_args",
    "launch_llama_server",
    "read_log_tail",
    "running_pids",
    "stop_processes",
    "verify_launch",
    "wait_for_port",
]


# ─── Module-level state for CLI log recovery ──────────────────────────────

#: Per-thread temporary log directory used when ``root=None`` so that
#: ``read_log_tail`` can return captured output in CLI mode.
_SERVER_LOG_DIR = threading.local()

#: CLI-mode temporary log directories keyed by the server pid that owns them,
#: so :func:`stop_processes` can remove them once the server is gone.
_CLI_LOG_DIRS: dict[Path, int] = {}
