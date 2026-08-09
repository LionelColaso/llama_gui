"""Actions page: obtain, switch, launch and stop.

The backend list is built from the engine's ``describe`` output, so a backend
that has no prebuilt on this platform is shown with the reason instead of
failing halfway through a download.
"""

from __future__ import annotations

from typing import Any, cast

from PySide6.QtWidgets import (
    QCheckBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..widgets.progress_bar import ProgressWidget
from ..worker_pool import EngineWorker, WorkerPool


class ActionsPage(QWidget):
    def __init__(self, orch: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._orch = orch
        self._checkboxes: dict[str, QCheckBox] = {}

        layout = QVBoxLayout(self)

        title = QLabel("Actions")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        layout.addWidget(title)

        self._progress = ProgressWidget()
        layout.addWidget(self._progress)

        self._status_label = QLabel()
        self._status_label.setWordWrap(True)
        layout.addWidget(self._status_label)

        layout.addWidget(self._build_backend_group())
        layout.addLayout(self._build_obtain_row())
        layout.addLayout(self._build_run_row())
        layout.addStretch()

    # ─── Construction ────────────────────────────────────────────────────

    def _build_backend_group(self) -> QGroupBox:
        group = QGroupBox("Backends")
        grid = QGridLayout(group)
        for row, info in enumerate(self._backend_infos()):
            name = str(info["name"])
            checkbox = QCheckBox(name)
            available = bool(info.get("prebuilt_available")) or bool(
                info.get("buildable")
            )
            checkbox.setEnabled(available)
            if not available:
                checkbox.setToolTip(str(info.get("unavailable_reason", "")))
            self._checkboxes[name] = checkbox
            grid.addWidget(checkbox, row, 0)

            note = QLabel(
                str(info.get("notes", ""))
                if available
                else str(info.get("unavailable_reason", ""))
            )
            note.setStyleSheet("color: #757575;" if available else "color: #B0846A;")
            note.setWordWrap(True)
            grid.addWidget(note, row, 1)
        return group

    def _backend_infos(self) -> list[dict[str, Any]]:
        """Backend rows from ``describe`` (falls back to plain names)."""
        try:
            described = self._orch.describe()
            data: dict[str, Any] = (
                described.model_dump() if hasattr(described, "model_dump") else {}
            )
            backends = data.get("backends")
            if isinstance(backends, list):
                return [dict(item) for item in cast("list[dict[str, Any]]", backends)]
        except Exception:  # noqa: BLE001, S110 - the fallback below always works
            pass
        return [
            {"name": name, "prebuilt_available": True, "buildable": True, "notes": ""}
            for name in self._orch.backend_names()
        ]

    def _build_obtain_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        for label, handler in (
            ("Get started (download missing)", self._do_bootstrap),
            ("Install selected", self._do_install),
            ("Update selected", self._do_update),
            ("Build selected from source", self._do_build),
        ):
            button = QPushButton(label)
            button.clicked.connect(handler)
            row.addWidget(button)
        row.addStretch()
        return row

    def _build_run_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        self._auto_install_check = QCheckBox("auto-install when switching")
        self._auto_install_check.setChecked(True)
        row.addWidget(self._auto_install_check)
        for label, handler in (
            ("Switch to selected", self._do_use),
            ("Launch", self._do_launch),
            ("Restart", self._do_restart),
            ("Stop", self._do_stop),
        ):
            button = QPushButton(label)
            button.clicked.connect(handler)
            row.addWidget(button)
        row.addStretch()
        return row

    # ─── Helpers ─────────────────────────────────────────────────────────

    def _selected_backends(self) -> list[str]:
        return [name for name, cb in self._checkboxes.items() if cb.isChecked()]

    def _start_worker(self, action: str, label: str, **kwargs: Any) -> None:
        self._progress.start_operation(label)
        worker = EngineWorker(
            self._orch, action, progress_callback=self._on_progress, **kwargs
        )
        worker.signals.finished.connect(self._on_result)
        worker.signals.error.connect(self._on_error)
        WorkerPool.instance().start(worker)

    # ─── Actions ─────────────────────────────────────────────────────────

    def _do_bootstrap(self) -> None:
        self._start_worker("bootstrap", "Downloading what is missing…")

    def _do_install(self) -> None:
        self._start_worker(
            "install", "Installing…", backends=self._selected_backends() or None
        )

    def _do_update(self) -> None:
        self._start_worker(
            "update", "Updating…", backends=self._selected_backends() or None
        )

    def _do_build(self) -> None:
        self._start_worker(
            "build", "Building from source…", backends=self._selected_backends() or None
        )

    def _do_use(self) -> None:
        selected = self._selected_backends()
        if not selected:
            self._status_label.setText("Select a backend first.")
            return
        self._start_worker(
            "use",
            f"Switching to {selected[0]}…",
            backend=selected[0],
            auto_install=self._auto_install_check.isChecked(),
        )

    def _do_stop(self) -> None:
        self._start_worker("stop", "Stopping…")

    def _do_launch(self) -> None:
        self._start_worker("launch", "Launching…")

    def _do_restart(self) -> None:
        self._start_worker("restart", "Restarting…")

    # ─── Signals ─────────────────────────────────────────────────────────

    def _on_progress(self, done: int, total: int, phase: str) -> None:
        self._progress.update_progress(done, total, phase)

    def _on_result(self, data: Any) -> None:
        self._progress.finish_operation()
        raw = data.model_dump() if hasattr(data, "model_dump") else data
        payload: dict[str, Any] = cast(
            "dict[str, Any]", raw if isinstance(raw, dict) else {}
        )
        message = str(payload.get("message", ""))
        self._status_label.setText(message or "Done.")

    def _on_error(self, msg: str) -> None:
        self._progress.fail_operation("Failed")
        self._status_label.setText(f"Error: {msg}")
        if "Toolchain not found" in msg:
            QMessageBox.information(
                self,
                "No build toolchain",
                f"{msg}\n\nUse “Install selected” to download the official "
                "prebuilt release instead.",
            )
