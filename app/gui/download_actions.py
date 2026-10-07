"""Shared download-action slots for the Models and Downloads sections.

Both drive long-running model downloads through the same worker-pool +
progress-widget machinery, so the launch wiring (``EngineWorker`` construction,
signal connections) and the progress / error slots live here to keep the host
code focused on layout instead of duplicating the same handful of lines. It sits
at the ``gui/`` root because both its consumers are sections of the Models tab.

The host class is expected to provide:

* ``self._orch``        – the orchestrator
* ``self._progress``     – a :class:`ProgressWidget`
* ``self._status_label`` – a ``QLabel`` for status text
* ``self._on_downloaded`` – a slot invoked when a download finishes
  (host-specific)
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from ..download import DownloadControl
from .widgets.progress_bar import ProgressWidget
from .worker_pool import EngineWorker, WorkerPool


def add_list_footer(
    layout: QVBoxLayout,
    on_refresh: Callable[[], None],
    tooltip: str,
) -> QLabel:
    """Append the Refresh row and status label every list section ends with.

    Returns the status label so the host can assign it to ``self._status_label``.
    """
    btn_row = QHBoxLayout()
    refresh_btn = QPushButton("Refresh")
    refresh_btn.setToolTip(tooltip)
    refresh_btn.clicked.connect(on_refresh)
    btn_row.addWidget(refresh_btn)
    btn_row.addStretch()
    layout.addLayout(btn_row)

    status_label = QLabel()
    status_label.setWordWrap(True)
    layout.addWidget(status_label)
    return status_label


class DownloadActionsMixin:
    """Progress/error slots and a download-model launcher shared by sections."""

    # Host-provided attributes (declared here so the mixin is self-documenting).
    _orch: Any
    _status_label: QLabel
    _progress: ProgressWidget

    def _start_download_worker(self, url: str, *, status_text: str) -> None:
        """Launch ``download_model`` for *url* with progress + error wiring."""
        control = DownloadControl()
        self._progress.start_operation(status_text, control=control)
        worker = EngineWorker(
            self._orch,
            "download_model",
            progress_callback=self._on_progress,
            control=control,
            url=url,
        )
        worker.signals.finished.connect(self._on_downloaded)
        worker.signals.error.connect(self._on_download_error)
        WorkerPool.instance().start(worker)

    def _on_downloaded(self, data: Any) -> None:
        """Host class overrides: surfaces the finished download and reloads."""

    def _on_progress(
        self, done: int, total: int, phase: str, overall: float | None = None
    ) -> None:
        self._progress.update_progress(done, total, phase, overall)

    def _on_download_error(self, msg: str) -> None:
        self._progress.fail_operation("Failed")
        self._status_label.setText(f"Download failed: {msg}")
