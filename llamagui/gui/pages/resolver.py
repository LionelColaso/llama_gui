from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget

from ..worker_pool import EngineWorker, WorkerPool


class ResolverPage(QWidget):
    def __init__(self, orch: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._orch = orch

        layout = QVBoxLayout(self)

        title = QLabel("Resolver / Sources")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        layout.addWidget(title)

        self._server_label = QLabel("llama-server: resolving...")
        layout.addWidget(self._server_label)

        self._swap_label = QLabel("llama-swap: resolving...")
        layout.addWidget(self._swap_label)

        refresh_btn = QPushButton("Refresh resolve")
        refresh_btn.clicked.connect(self._resolve)
        layout.addWidget(refresh_btn)

        layout.addStretch()

        self._resolve()

    def _resolve(self) -> None:
        worker = EngineWorker(self._orch, "resolve")
        worker.signals.finished.connect(self._on_resolved)
        worker.signals.error.connect(self._on_error)
        WorkerPool.instance().start(worker)

    def _on_resolved(self, data: Any) -> None:
        try:
            d = data.model_dump() if hasattr(data, "model_dump") else data
            ls = d.get("llama_server", {})
            lsw = d.get("llama_swap", {})

            self._server_label.setText(
                f"llama-server: {ls.get('path', 'not found')} "
                f"[v{ls.get('version', '?')}] "
                f"{'valid' if ls.get('valid') else 'invalid'}"
            )
            self._swap_label.setText(
                f"llama-swap: {lsw.get('path', 'not found')} "
                f"[v{lsw.get('version', '?')}] "
                f"{'valid' if lsw.get('valid') else 'invalid'}"
            )
        except RuntimeError:
            pass

    def _on_error(self, msg: str) -> None:
        from contextlib import suppress

        with suppress(RuntimeError):
            self._server_label.setText(f"Error: {msg}")
