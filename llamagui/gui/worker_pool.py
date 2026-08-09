from __future__ import annotations

import threading
from typing import Any
from unittest.mock import MagicMock

from PySide6.QtCore import QObject, QRunnable, Signal, SignalInstance

from ..orchestrator import Orchestrator

_call_lock = threading.Lock()


class WorkerSignals(QObject):
    """Signals emitted by an EngineWorker during its lifecycle."""

    finished = Signal(object)  # (result_data)
    error = Signal(str)  # (error_message)
    progress = Signal(int, int, str)  # (done, total, phase)


class EngineWorker(QRunnable):
    """Runs a single orchestrator action on a background thread.

    The ``progress_callback``, if provided, is called by ``emit_progress``
    inside the managed modules so that the GUI progress bar can be updated
    during long-running install/update/build operations.
    """

    def __init__(
        self,
        orch: Orchestrator,
        action: str,
        progress_callback: Any = None,
        **kwargs: Any,
    ) -> None:
        super().__init__()
        self.orch = orch
        self._action = action
        self._kwargs = kwargs
        self._progress_callback = progress_callback
        self.signals = WorkerSignals()

    def _execute_action(self) -> None:
        """Invoke the orchestrator action and emit finished/error signals."""
        try:
            method = getattr(self.orch, self._action)
            result = method(**self._kwargs)
            self._emit(self.signals.finished, result)
        except Exception as e:  # noqa: BLE001 - forward any worker error to the UI
            self._emit(self.signals.error, f"{type(e).__name__}: {e}")

    @staticmethod
    def _emit(signal: SignalInstance, value: object) -> None:
        # The receiver (a GUI widget) may already be gone during shutdown; emitting
        # to a deleted QObject raises RuntimeError, which must not propagate out of
        # the worker thread and crash teardown.
        try:
            signal.emit(value)
        except RuntimeError:  # receiver (widget) deleted during shutdown
            pass

    def run(self) -> None:
        # MagicMock is not thread-safe; run synchronously for tests.
        if isinstance(self.orch, MagicMock):
            self.run_sync()
            return

        # Wire the global progress callback *before* running the action so
        # that httpx-download / extract progress is forwarded to the GUI.
        from ..backends.prebuilt import set_progress_callback

        set_progress_callback(self._progress_callback)
        try:
            with _call_lock:
                self._execute_action()
        finally:
            set_progress_callback(None)

    def run_sync(self) -> None:
        """Synchronous fallback used when ``orch`` is a MagicMock (tests)."""
        self._execute_action()


class WorkerPool(QObject):
    """Manages active EngineWorker instances to prevent premature GC.

    PySide6's ``QThreadPool.start(QRunnable)`` auto-deletes the runnable
    after ``run()`` completes, but the Python wrapper object can be garbage
    collected *before* the C++ thread finishes, causing a crash.  This pool
    holds a reference to every active worker until it finishes or errors out.
    """

    _instance: WorkerPool | None = None

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._active: dict[int, EngineWorker] = {}

    @classmethod
    def instance(cls) -> WorkerPool:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def start(self, worker: EngineWorker) -> None:
        # MagicMock is not thread-safe; run synchronously in the main thread
        # to avoid access-violation crashes during pytest-qt GC/teardown.
        if isinstance(worker.orch, MagicMock):
            worker.run_sync()
            return

        from PySide6.QtCore import QThreadPool

        wid = id(worker)
        self._active[wid] = worker

        def _on_done(*_: Any) -> None:
            self._active.pop(wid, None)

        worker.signals.finished.connect(_on_done)
        worker.signals.error.connect(_on_done)
        QThreadPool.globalInstance().start(worker)

    @classmethod
    def reset(cls) -> None:
        """Drop the singleton instance (for testing)."""
        cls._instance = None

    @property
    def active_count(self) -> int:
        return len(self._active)
