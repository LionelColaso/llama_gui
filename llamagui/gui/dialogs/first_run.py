"""First-run setup dialog.

Shown when neither ``llama-server`` nor ``llama-swap`` can be resolved, so a
fresh install offers the three ways to get going instead of a broken UI:
download the latest releases, point at binaries the user already has, or build
from the vendored sources.
"""

from __future__ import annotations

from typing import Any, cast

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..payload import as_payload
from ..widgets.path_picker import PathPicker
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
            "llama-gui could not find <b>llama-server</b> or <b>llama-swap</b>.\n"
            "Download the latest official releases, or point at binaries you "
            "already have."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self._progress = ProgressWidget()
        layout.addWidget(self._progress)

        self._download_btn = QPushButton("Download latest releases (recommended)")
        self._download_btn.clicked.connect(self._download)
        layout.addWidget(self._download_btn)

        form = QFormLayout()
        self._server_picker = PathPicker(caption="Select llama-server")
        self._swap_picker = PathPicker(caption="Select llama-swap")
        form.addRow("I already have llama-server", self._server_picker)
        form.addRow("I already have llama-swap", self._swap_picker)
        layout.addLayout(form)

        self._use_paths_btn = QPushButton("Use these paths")
        self._use_paths_btn.clicked.connect(self._use_paths)
        layout.addWidget(self._use_paths_btn)

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

    def _use_paths(self) -> None:
        self._orch.save_config(
            {
                "pointed": {
                    "llama_server": self._server_picker.value(),
                    "llama_swap": self._swap_picker.value(),
                },
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
        pairs: list[tuple[str, dict[str, Any]]] = [
            ("llama-server", cast("dict[str, Any]", payload.get("llama_server") or {})),
            ("llama-swap", cast("dict[str, Any]", payload.get("llama_swap") or {})),
        ]
        problems = [
            f"{label}: {info.get('error') or 'not found'}"
            for label, info in pairs
            if not info.get("valid")
        ]
        if problems:
            self._status.setText("\n".join(problems))
            return
        self.accept()

    def _on_error(self, msg: str) -> None:
        self._progress.fail_operation("Failed")
        self._download_btn.setEnabled(True)
        self._status.setText(f"Error: {msg}")
