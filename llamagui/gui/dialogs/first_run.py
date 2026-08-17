"""First-run setup dialog.

Shown when ``llama-server`` cannot be resolved, so a fresh install offers the
two ways to get going instead of a broken UI: download the latest release into
the backend location, or use the llama.cpp already installed on this system.
"""

from __future__ import annotations

from typing import Any, cast

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..payload import as_payload
from ..widgets.progress_bar import ProgressWidget
from ..worker_pool import EngineWorker, WorkerPool


class FirstRunDialog(QDialog):
    def __init__(self, orch: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._orch = orch
        self.setWindowTitle("Set up llama-gui")
        self.setMinimumWidth(560)

        layout = QVBoxLayout(self)

        intro = QLabel(
            "llama-gui could not find <b>llama-server</b>.\n"
            "Download the latest official release, or use the llama.cpp "
            "already installed on this system."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self._progress = ProgressWidget()
        layout.addWidget(self._progress)

        self._download_btn = QPushButton("Download latest releases (recommended)")
        self._download_btn.clicked.connect(self._download)
        layout.addWidget(self._download_btn)

        self._use_os_btn = QPushButton("Use OS installed llama.cpp (PATH)")
        self._use_os_btn.setToolTip(
            "Prefer the llama-server found on PATH (OS install / package "
            "manager) over a downloaded backend."
        )
        self._use_os_btn.clicked.connect(self._use_os)
        layout.addWidget(self._use_os_btn)

        self._status = QLabel()
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self._skip)
        layout.addWidget(buttons)

    # ─── Actions ─────────────────────────────────────────────────────────

    def _download(self) -> None:
        self._download_btn.setEnabled(False)
        self._progress.start_operation("Downloading…")
        worker = EngineWorker(
            self._orch, "bootstrap", progress_callback=self._on_progress
        )
        worker.signals.finished.connect(self._on_finished)
        worker.signals.error.connect(self._on_error)
        WorkerPool.instance().start(worker)

    def _use_os(self) -> None:
        self._use_os_btn.setEnabled(False)
        self._orch.save_config(
            {
                "use_os_llama_server": True,
                "first_run_complete": True,
            }
        )
        self._status.setText("Saved. Checking the binaries…")
        worker = EngineWorker(self._orch, "resolve")
        worker.signals.finished.connect(self._on_resolved)
        worker.signals.error.connect(self._on_error)
        WorkerPool.instance().start(worker)

    def _skip(self) -> None:
        """Close without setup, but do not nag on every launch."""
        self._orch.save_config({"first_run_complete": True})
        self.reject()

    # ─── Signals ─────────────────────────────────────────────────────────

    def _on_progress(self, done: int, total: int, phase: str) -> None:
        self._progress.update_progress(done, total, phase)

    def _on_finished(self, data: Any) -> None:
        self._progress.finish_operation()
        self._download_btn.setEnabled(True)
        payload = as_payload(data)
        ready = bool(payload.get("ready"))
        message = str(payload.get("message", ""))
        self._status.setText(message or ("Ready." if ready else "Setup incomplete."))
        if ready:
            self.accept()

    def _on_resolved(self, data: Any) -> None:
        payload = as_payload(data)
        server = cast("dict[str, Any]", payload.get("llama_server") or {})
        if not server.get("valid"):
            self._status.setText(f"llama-server: {server.get('error') or 'not found'}")
            return
        self.accept()

    def _on_error(self, msg: str) -> None:
        self._progress.fail_operation("Failed")
        self._download_btn.setEnabled(True)
        self._status.setText(f"Error: {msg}")
