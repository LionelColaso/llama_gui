"""Regression: the progress slot must run on the GUI thread.

The engine calls the progress callback from the worker thread. Historically
the worker passed the widget's bound method straight to the engine, so the
QWidget was touched off the GUI thread — undefined behaviour that crashed
the process mid-download (silent C-level abort, no Python traceback). The
worker now routes progress through its Qt signal, which auto-queues
delivery onto the GUI thread.
"""

from __future__ import annotations

import threading
from typing import cast

from PySide6.QtCore import SignalInstance
from pytestqt.qtbot import QtBot

from app.backends.prebuilt import emit_progress
from app.gui.worker_pool import EngineWorker, WorkerPool
from app.orchestrator import Orchestrator


class _ProbeOrch:
    """Stand-in orchestrator whose action emits one tick and waits for the slot."""

    def __init__(self, released: threading.Event) -> None:
        self._released = released

    def bootstrap(self) -> None:
        emit_progress("cuda12", 0, 250_798_945, "download")
        self._released.wait(timeout=10)


def test_progress_slot_runs_on_gui_thread(qtbot: QtBot) -> None:
    main_thread = threading.current_thread()
    released = threading.Event()
    off_thread: list[str] = []

    def slot(done: int, total: int, phase: str, overall: float | None) -> None:
        if threading.current_thread() is not main_thread:
            off_thread.append(phase)
        released.set()

    worker = EngineWorker(
        cast("Orchestrator", _ProbeOrch(released)),
        "bootstrap",
        progress_callback=slot,
    )
    WorkerPool.instance().start(worker)

    qtbot.waitUntil(released.is_set, timeout=10_000)
    assert released.is_set(), "progress tick was never delivered to the slot"
    assert off_thread == [], f"progress slot ran off the GUI thread: {off_thread}"


# ─── action-name validation ────────────────────────────────────────────────


class _StrictOrch:
    """An orchestrator that does *not* auto-create missing attributes.

    MagicMock fabricates any attribute, so it can never reproduce the
    AttributeError a typo would cause against the real Orchestrator.
    """

    def status(self) -> str:
        return "ok"


def test_unknown_action_reports_a_clear_error() -> None:
    """A typo must name the action and list the valid ones, not raise
    a bare AttributeError from inside the worker."""
    errors: list[str] = []
    worker = EngineWorker(
        cast("Orchestrator", _StrictOrch()),
        "staus",  # typo for status
    )
    worker.signals.error.connect(errors.append)
    worker.run_sync()

    assert len(errors) == 1
    assert "staus" in errors[0]
    assert "Unknown action" in errors[0]
    assert "status" in errors[0], "the error should list the valid actions"


def test_known_action_still_runs() -> None:
    finished: list[object] = []
    worker = EngineWorker(cast("Orchestrator", _StrictOrch()), "status")
    worker.signals.finished.connect(finished.append)
    worker.run_sync()

    assert finished == ["ok"]


def test_gui_action_names_are_not_mistaken_for_typos() -> None:
    """Regression: the GUI passes method names, not the CLI's hyphenated ones.

    orchestrator.ACTIONS holds "list-models"; the GUI calls list_models, and
    several methods (log_tail, set_active_model) are not CLI actions at all.
    Validating against ACTIONS would have broken seven real GUI actions.
    """
    from app.orchestrator import ACTIONS, Orchestrator

    gui_actions = [
        "list_models",
        "pending_downloads",
        "discard_download",
        "set_active_model",
        "remove_model",
        "download_model",
        "log_tail",
    ]
    for action in gui_actions:
        assert hasattr(Orchestrator, action), f"{action} is not an orchestrator method"
        assert callable(getattr(Orchestrator, action))

    # And they are deliberately *not* all in the CLI's ACTIONS tuple.
    assert "list-models" in ACTIONS
    assert "list_models" not in ACTIONS


def test_a_worker_without_progress_runs_without_wiring(qtbot: QtBot) -> None:
    """No progress slot: nothing is connected, the action still runs."""
    finished: list[object] = []
    worker = EngineWorker(cast("Orchestrator", _StrictOrch()), "status")
    worker.signals.finished.connect(finished.append)

    worker.run()

    assert finished == ["ok"]


def test_emit_survives_a_deleted_receiver(qtbot: QtBot) -> None:
    """A widget gone during shutdown must not crash the worker thread."""

    class _BoomSignal:
        @staticmethod
        def emit(value: object) -> None:
            raise RuntimeError("receiver deleted")

    worker = EngineWorker(cast("Orchestrator", _StrictOrch()), "status")

    worker._emit(cast(SignalInstance, _BoomSignal()), "value")


def test_the_pool_reports_no_active_workers(qtbot: QtBot) -> None:
    assert WorkerPool.instance().active_count == 0
