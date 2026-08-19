"""Models page: the .gguf library — list, download, select and remove.

Models are files in the configured models directory (Settings → Paths). The
page drives the engine's model actions through the worker pool, so the GUI and
the CLI stay on the same code path.
"""

from __future__ import annotations

from contextlib import suppress
from typing import Any

from PySide6.QtWidgets import (
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..payload import as_payload
from ..theme import COLORS
from ..widgets.model_table import ModelTable
from ..widgets.progress_bar import ProgressWidget
from ..worker_pool import EngineWorker, WorkerPool


def _ignore_error(msg: str) -> None:
    """Swallow the message; resolve errors are surfaced on the Backends section."""


class ModelsPage(QWidget):
    def __init__(self, orch: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._orch = orch
        self._pending_active: str | None = None

        layout = QVBoxLayout(self)

        title = QLabel("Models")
        title.setObjectName("PageTitle")
        layout.addWidget(title)

        self._dir_label = QLabel()
        self._dir_label.setStyleSheet(f"color: {COLORS['muted']};")
        self._dir_label.setWordWrap(True)
        layout.addWidget(self._dir_label)

        self._server_label = QLabel()
        self._server_label.setWordWrap(True)
        layout.addWidget(self._server_label)

        self._table = ModelTable()
        layout.addWidget(self._table)

        self._progress = ProgressWidget()
        layout.addWidget(self._progress)

        btn_row = QHBoxLayout()
        for label, handler in (
            ("Download…", self._do_download),
            ("Set active", self._do_set_active),
            ("Remove", self._do_remove),
            ("Open folder", self._open_folder),
            ("Refresh", self._load),
        ):
            button = QPushButton(label)
            button.clicked.connect(handler)
            btn_row.addWidget(button)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._status_label = QLabel()
        self._status_label.setWordWrap(True)
        layout.addWidget(self._status_label)

        self._load()
        self._show_server_path()

    # ─── Loading ─────────────────────────────────────────────────────────

    def _load(self) -> None:
        worker = EngineWorker(self._orch, "list_models")
        worker.signals.finished.connect(self._on_list)
        worker.signals.error.connect(self._on_error)
        WorkerPool.instance().start(worker)

    def _on_list(self, data: Any) -> None:
        payload = as_payload(data)
        models: list[dict[str, Any]] = payload.get("models") or []
        active = payload.get("active")
        with suppress(RuntimeError):
            self._table.load_models(
                [dict(m) for m in models], active=str(active) if active else None
            )
            self._dir_label.setText(
                f"Models directory: {payload.get('dir', '?')}  "
                f"({len(models)} model{'s' if len(models) != 1 else ''})"
            )

    def _show_server_path(self) -> None:
        worker = EngineWorker(self._orch, "resolve")
        worker.signals.finished.connect(self._on_resolve)
        worker.signals.error.connect(_ignore_error)  # shown on the Backends section
        WorkerPool.instance().start(worker)

    def _on_resolve(self, data: Any) -> None:
        with suppress(RuntimeError):
            resolved: dict[str, Any] = as_payload(data).get("llama_server") or {}
            path = resolved.get("path")
            self._server_label.setText(
                f"llama-server: {path or 'not found'}"
                + (f"  [{resolved['source']}]" if resolved.get("source") else "")
            )

    # ─── Actions ─────────────────────────────────────────────────────────

    def _do_download(self) -> None:
        url, ok = QInputDialog.getText(
            self,
            "Download model",
            "Model URL (a direct .gguf link, e.g. a Hugging Face "
            '"resolve/main/…gguf" URL):',
            text="",
        )
        if not ok:
            return
        url = url.strip()
        if not url:
            return
        self._progress.start_operation(f"Downloading {url.rsplit('/', 1)[-1]}…")
        worker = EngineWorker(
            self._orch,
            "download_model",
            progress_callback=self._on_progress,
            url=url,
        )
        worker.signals.finished.connect(self._on_downloaded)
        worker.signals.error.connect(self._on_download_error)
        WorkerPool.instance().start(worker)

    def _do_set_active(self) -> None:
        name = self._table.selected_name()
        if not name:
            self._status_label.setText("Select a model in the list first.")
            return
        self._pending_active = name
        worker = EngineWorker(self._orch, "set_active_model", name=name)
        worker.signals.finished.connect(self._on_set_active_done)
        worker.signals.error.connect(self._on_error)
        WorkerPool.instance().start(worker)

    def _on_set_active_done(self, data: Any) -> None:
        # Only report success once the worker has actually confirmed it.
        name = self._pending_active
        self._on_list(data)
        if name is not None:
            self._status_label.setText(f"Active model: {name}")

    def _do_remove(self) -> None:
        name = self._table.selected_name()
        if not name:
            self._status_label.setText("Select a model in the list first.")
            return
        answer = QMessageBox.question(
            self,
            "Remove model",
            f"Delete {name} from the models directory?\n"
            "This removes the file permanently.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        worker = EngineWorker(self._orch, "remove_model", name=name)
        worker.signals.finished.connect(self._on_list)
        worker.signals.error.connect(self._on_error)
        WorkerPool.instance().start(worker)

    def _open_folder(self) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        directory = str(getattr(self._orch.cfg, "models_dir_path", ""))
        if directory:
            QDesktopServices.openUrl(QUrl.fromLocalFile(directory))

    # ─── Signals ─────────────────────────────────────────────────────────

    def _on_progress(
        self, done: int, total: int, phase: str, overall: float | None = None
    ) -> None:
        self._progress.update_progress(done, total, phase, overall)

    def _on_downloaded(self, data: Any) -> None:
        self._progress.finish_operation()
        payload = as_payload(data)
        self._status_label.setText(
            f"Downloaded {payload.get('name', 'model')} "
            f"({payload.get('size_bytes', 0)} bytes)."
        )
        self._load()

    def _on_download_error(self, msg: str) -> None:
        self._progress.fail_operation("Failed")
        self._status_label.setText(f"Download failed: {msg}")

    def _on_error(self, msg: str) -> None:
        with suppress(RuntimeError):
            self._status_label.setText(f"Error: {msg}")
