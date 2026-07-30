"""Resolver / Sources page.

Shows exactly which binary would run, where it came from, and whether it
actually works — and lets the user change that on the spot: point at an
existing install, or download the latest official release.
"""

from __future__ import annotations

from typing import Any, cast

from PySide6.QtWidgets import (
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..payload import as_payload
from ..widgets.path_picker import PathPicker
from ..widgets.progress_bar import ProgressWidget
from ..widgets.source_badge import SourceBadge
from ..worker_pool import EngineWorker, WorkerPool


class BinaryRow(QGroupBox):
    """One binary: resolved state plus a pointed-path override."""

    def __init__(self, title: str, picker_caption: str) -> None:
        super().__init__(title)
        layout = QVBoxLayout(self)

        header = QHBoxLayout()
        self.badge = SourceBadge(None)
        header.addWidget(self.badge)
        self.state_label = QLabel("resolving…")
        self.state_label.setWordWrap(True)
        header.addWidget(self.state_label, stretch=1)
        layout.addLayout(header)

        self.path_label = QLabel("")
        self.path_label.setStyleSheet("color: #757575;")
        self.path_label.setWordWrap(True)
        layout.addWidget(self.path_label)

        self.picker = PathPicker(caption=picker_caption, placeholder="Use my own path")
        form = QFormLayout()
        form.addRow("Point at", self.picker)
        layout.addLayout(form)

    def update_state(self, info: dict[str, Any]) -> None:
        source = info.get("source")
        self.badge.update_source(source or "unknown")
        path = info.get("path")
        if not path:
            self.state_label.setText("not found — point at a binary or download one")
            self.state_label.setStyleSheet("color: #F44336;")
            self.path_label.setText("")
            return
        if info.get("valid"):
            self.state_label.setText(f"OK — {info.get('version') or 'version unknown'}")
            self.state_label.setStyleSheet("color: #4CAF50;")
        else:
            self.state_label.setText(
                f"cannot run: {info.get('error') or 'unknown error'}"
            )
            self.state_label.setStyleSheet("color: #F44336;")
        self.path_label.setText(str(path))


class ResolverPage(QWidget):
    def __init__(self, orch: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._orch = orch

        layout = QVBoxLayout(self)

        title = QLabel("Resolver / Sources")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        layout.addWidget(title)

        hint = QLabel(
            "Priority: "
            + " → ".join(getattr(orch.cfg, "source_priority", []))
            + ". A pointed path always wins, whether or not anything is installed."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #757575;")
        layout.addWidget(hint)

        self._progress = ProgressWidget()
        layout.addWidget(self._progress)

        self._server_row = BinaryRow("llama-server (llama.cpp)", "Select llama-server")
        self._swap_row = BinaryRow("llama-swap", "Select llama-swap")
        layout.addWidget(self._server_row)
        layout.addWidget(self._swap_row)

        # Kept for API compatibility with older tests/tools.
        self._server_label = self._server_row.state_label
        self._swap_label = self._swap_row.state_label

        btn_row = QHBoxLayout()
        refresh_btn = QPushButton("Re-check")
        refresh_btn.clicked.connect(self._resolve)
        btn_row.addWidget(refresh_btn)

        save_btn = QPushButton("Save pointed paths")
        save_btn.clicked.connect(self._save_pointed)
        btn_row.addWidget(save_btn)

        download_btn = QPushButton("Download latest release")
        download_btn.clicked.connect(self._download)
        btn_row.addWidget(download_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._status_label = QLabel()
        self._status_label.setWordWrap(True)
        layout.addWidget(self._status_label)
        layout.addStretch()

        self._load_pointed()
        self._resolve()

    # ─── Actions ─────────────────────────────────────────────────────────

    def _load_pointed(self) -> None:
        pointed = getattr(self._orch.cfg, "pointed", None)
        if pointed is None:
            return
        self._server_row.picker.setText(pointed.llama_server)
        self._swap_row.picker.setText(pointed.llama_swap)

    def _resolve(self) -> None:
        worker = EngineWorker(self._orch, "resolve")
        worker.signals.finished.connect(self._on_resolved)
        worker.signals.error.connect(self._on_error)
        WorkerPool.instance().start(worker)

    def _save_pointed(self) -> None:
        self._orch.save_config(
            {
                "pointed": {
                    "llama_server": self._server_row.picker.value(),
                    "llama_swap": self._swap_row.picker.value(),
                }
            }
        )
        self._status_label.setText("Pointed paths saved. Re-checking…")
        self._resolve()

    def _download(self) -> None:
        """Fetch whatever is missing from the latest upstream releases."""
        self._progress.start_operation("Downloading latest release…")
        worker = EngineWorker(
            self._orch, "bootstrap", progress_callback=self._on_progress
        )
        worker.signals.finished.connect(self._on_downloaded)
        worker.signals.error.connect(self._on_error)
        WorkerPool.instance().start(worker)

    # ─── Signals ─────────────────────────────────────────────────────────

    def _on_progress(self, done: int, total: int, phase: str) -> None:
        self._progress.update_progress(done, total, phase)

    def _on_resolved(self, data: Any) -> None:
        payload = as_payload(data)
        server: dict[str, Any] = cast(
            "dict[str, Any]", payload.get("llama_server", {}) or {}
        )
        swap: dict[str, Any] = cast(
            "dict[str, Any]", payload.get("llama_swap", {}) or {}
        )
        self._server_row.update_state(server)
        self._swap_row.update_state(swap)

    def _on_downloaded(self, data: Any) -> None:
        self._progress.finish_operation()
        payload = as_payload(data)
        message = str(payload.get("message", ""))
        self._status_label.setText(message or "Download finished.")
        self._resolve()

    def _on_error(self, msg: str) -> None:
        self._progress.fail_operation("Failed")
        self._status_label.setText(f"Error: {msg}")
