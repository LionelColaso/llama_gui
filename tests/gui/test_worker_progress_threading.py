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

from pytestqt.qtbot import QtBot

from llamagui.backends.prebuilt import emit_progress
from llamagui.gui.worker_pool import EngineWorker, WorkerPool
from llamagui.orchestrator import Orchestrator


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
