"""Application logging (loguru): every app error lands in a rotating file log.

The GUI has no console, so an uncaught crash or a failed install leaves no
trace beyond the moment it happens. Both entry points (``__main__`` and the
GUI ``run``) install a rotating, thread-safe file sink under the data root
(``logs/llamagui.log``) plus an excepthook, so *uncaught* exceptions always
leave a traceback in the log for post-mortem inspection.

This module only *configures* loguru. Callers log explicitly at the choke
points: CLI envelope emission (``cli.emit``), the worker-thread catch
(``gui/worker_pool.WorkerBase``), and the llama-server spawn
(``lifecycle.launch_llama_server``).
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import TracebackType

from loguru import logger

LOG_FILENAME = "llamagui.log"

_FORMAT = (
    "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | "
    "{thread.name} | {name}:{line} | {message}"
)


def configure_logging(log_dir: Path, *, to_stderr: bool = False) -> Path | None:
    """Install a rotating file sink at ``log_dir/llamagui.log``.

    Idempotent: any existing sinks (including loguru's default stderr one)
    are removed first. The sink is enqueued so worker threads can log
    safely, rotates at 10 MB, and keeps 7 days of history.

    Args:
        log_dir: Directory to create and write the log into.
        to_stderr: Also mirror WARNING and above to stderr (useful when the
            GUI is launched from a terminal; skipped when stderr is absent,
            e.g. a frozen console-less build).

    Returns:
        The log file path, or ``None`` when the directory cannot be created
        (logging is then silently disabled rather than crashing the app).
    """
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None

    logger.remove()
    logger.add(
        log_dir / LOG_FILENAME,
        format=_FORMAT,
        level="DEBUG",
        rotation="10 MB",
        retention="7 days",
        compression="zip",
        enqueue=True,
        backtrace=True,
        diagnose=False,
    )
    if to_stderr and sys.stderr is not None:
        logger.add(
            sys.stderr,
            format=_FORMAT,
            level="WARNING",
            enqueue=True,
            diagnose=False,
        )
    return log_dir / LOG_FILENAME


def install_excepthook() -> None:
    """Log unhandled exceptions to the app log before the interpreter exits.

    The previously installed hook still runs afterwards, so a console user
    still sees the traceback; the log additionally preserves it for GUI
    crashes (no console) and for post-mortem inspection.
    """
    original = sys.excepthook

    def _hook(
        exc_type: type[BaseException],
        exc: BaseException,
        tb: TracebackType | None,
    ) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            original(exc_type, exc, tb)
            return
        logger.opt(exception=(exc_type, exc, tb)).error("unhandled exception")
        # Drain the enqueued queue *before* the interpreter dies with the
        # traceback, or the crash record would be lost.
        logger.complete()
        original(exc_type, exc, tb)

    sys.excepthook = _hook
