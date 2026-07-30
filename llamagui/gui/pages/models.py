from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ...config_yaml import ConfigYaml
from ..payload import as_payload
from ..widgets.model_table import ModelTable
from ..worker_pool import EngineWorker, WorkerPool


class ModelsPage(QWidget):
    def __init__(
        self, orch: Any, cfg_path: Path, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._orch = orch
        self._cfg = ConfigYaml(cfg_path)

        layout = QVBoxLayout(self)

        title = QLabel("Models")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        layout.addWidget(title)

        self._active_label = QLabel()
        layout.addWidget(self._active_label)

        self._server_label = QLabel()
        layout.addWidget(self._server_label)

        self._table = ModelTable()
        layout.addWidget(self._table)

        btn_row = QHBoxLayout()
        save_btn = QPushButton("Save")
        save_btn.clicked.connect(self._save)
        btn_row.addWidget(save_btn)

        reload_btn = QPushButton("Reload")
        reload_btn.clicked.connect(self._load)
        btn_row.addWidget(reload_btn)

        layout.addLayout(btn_row)

        self._status_label = QLabel()
        layout.addWidget(self._status_label)

        self._load()

    def _load(self) -> None:
        self._cfg.load()
        self._table.load_models(self._cfg.get_models())
        active = self._cfg.get_active()
        self._active_label.setText(f"Active backend: {active or 'none'}")
        self._show_server_path()

    def _show_server_path(self) -> None:
        worker = EngineWorker(self._orch, "resolve")
        worker.signals.finished.connect(self._on_resolve)
        worker.signals.error.connect(self._on_error)
        WorkerPool.instance().start(worker)

    def _on_resolve(self, data: Any) -> None:
        d = as_payload(data)
        resolved = d.get("llama_server", {})
        if isinstance(resolved, dict):
            self._server_label.setText(
                f"llama-server path: {resolved.get('path', 'unknown')}"
            )

    def _on_error(self, msg: str) -> None:
        from contextlib import suppress

        with suppress(RuntimeError):
            self._status_label.setText(f"Error: {msg}")

    def _save(self) -> None:
        models = self._table.get_models()
        self._cfg.load()
        # Replace the models list in place using the ConfigYaml API so other
        # keys (active, comments, ordering) round-trip intact.
        models_container = self._cfg.data.setdefault("models", [])
        if isinstance(models_container, list):
            del models_container[:]
        for m in models:
            self._cfg.add_model(m)
        self._cfg.save()
        self._status_label.setText("Saved. Restart llama-swap to apply.")
