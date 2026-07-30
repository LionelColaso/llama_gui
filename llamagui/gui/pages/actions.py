from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
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

        layout = QVBoxLayout(self)

        title = QLabel("Actions")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        layout.addWidget(title)

        self._progress = ProgressWidget()
        layout.addWidget(self._progress)

        self._status_label = QLabel()
        layout.addWidget(self._status_label)

        backend_row = QHBoxLayout()
        self._checkboxes: dict[str, QCheckBox] = {}
        for name in self._orch.backend_names():
            cb = QCheckBox(name)
            self._checkboxes[name] = cb
            backend_row.addWidget(cb)
        layout.addLayout(backend_row)

        btn_row = QHBoxLayout()

        install_btn = QPushButton("Install selected")
        install_btn.clicked.connect(self._do_install)
        btn_row.addWidget(install_btn)

        update_btn = QPushButton("Update selected")
        update_btn.clicked.connect(self._do_update)
        btn_row.addWidget(update_btn)

        use_btn = QPushButton("Use selected (switch)")
        use_btn.clicked.connect(self._do_use)
        btn_row.addWidget(use_btn)

        stop_btn = QPushButton("Stop")
        stop_btn.clicked.connect(self._do_stop)
        btn_row.addWidget(stop_btn)

        launch_btn = QPushButton("Launch")
        launch_btn.clicked.connect(self._do_launch)
        btn_row.addWidget(launch_btn)

        restart_btn = QPushButton("Restart")
        restart_btn.clicked.connect(self._do_restart)
        btn_row.addWidget(restart_btn)

        layout.addLayout(btn_row)
        layout.addStretch()

    def _selected_backends(self) -> list[str]:
        return [name for name, cb in self._checkboxes.items() if cb.isChecked()]

    def _start_worker(self, action: str, **kwargs: Any) -> None:
        worker = EngineWorker(
            self._orch, action, progress_callback=self._on_progress, **kwargs
        )
        worker.signals.finished.connect(self._on_result)
        worker.signals.error.connect(self._on_error)
        WorkerPool.instance().start(worker)

    def _do_install(self) -> None:
        backends = self._selected_backends()
        self._progress.start_operation("Installing...")
        self._start_worker("install", backends=backends or None)

    def _do_update(self) -> None:
        backends = self._selected_backends()
        self._progress.start_operation("Updating...")
        self._start_worker("update", backends=backends or None)

    def _do_use(self) -> None:
        backends = self._selected_backends()
        if not backends:
            self._status_label.setText("Select a backend first")
            return
        self._progress.start_operation("Switching...")
        self._start_worker("use", backend=backends[0])

    def _do_stop(self) -> None:
        self._progress.start_operation("Stopping...")
        self._start_worker("stop")

    def _do_launch(self) -> None:
        self._progress.start_operation("Launching...")
        self._start_worker("launch")

    def _do_restart(self) -> None:
        self._progress.start_operation("Restarting...")
        self._start_worker("restart")

    def _on_progress(self, done: int, total: int, phase: str) -> None:
        self._progress.update_progress(done, total, phase)

    def _on_result(self, data: Any) -> None:
        self._progress.finish_operation()
        self._status_label.setText("Operation completed")

    def _on_error(self, msg: str) -> None:
        self._progress.fail_operation("Failed")
        self._status_label.setText(f"Error: {msg}")
