"""Shared download-action slots for the Models and Downloads pages.

Both pages drive long-running model downloads through the same worker-pool +
progress-widget machinery, so the launch wiring (``EngineWorker`` construction,
signal connections) and the progress / error slots live here to keep the
per-page code focused on layout instead of duplicating the same handful of lines.

The host class is expected to provide:

* ``self._orch``        – the orchestrator
* ``self._progress``     – a :class:`ProgressWidget`
* ``self._status_label`` – a ``QLabel`` for status text
* ``self._on_downloaded`` – a slot invoked when a download finishes (page-specific)
"""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QLabel

from ...download import DownloadControl
from ..worker_pool import EngineWorker, WorkerPool
from .progress_bar import ProgressWidget


class DownloadActionMixin:
    """Progress/error slots and a download-model launcher shared by the pages."""

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
