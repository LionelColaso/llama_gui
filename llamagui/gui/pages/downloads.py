"""Downloads: every interrupted, resumable download in one place.

A single section listing each ``*.part`` (with its ``.meta``) the app knows
about — half-downloaded .gguf models *and* half-downloaded backend release
archives — so a flaky connection or an app restart never strands a multi-GB
download. Each pending item is offered with Resume (driven through the shared
progress widget, so Pause / Resume / Cancel keep working) and Discard (clean up
the partial + meta).
"""

from __future__ import annotations

from contextlib import suppress
from typing import Any

from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...download import DownloadControl
from ..payload import as_payload
from ..theme import COLORS
from ..widgets.download_runner import DownloadActionMixin
from ..widgets.progress_bar import ProgressWidget, _human
from ..worker_pool import EngineWorker, WorkerPool


class _PendingRow(QWidget):
    """One interrupted download: kind + name, progress, and actions."""

    def __init__(
        self,
        task: dict[str, Any],
        on_resume: Any,
        on_discard: Any,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.task = task
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        name = QLabel(f"{task.get('kind', 'model')} · {task.get('name', '?')}")
        name.setWordWrap(True)
        head.addWidget(name, stretch=1)
        done = int(task.get("done", 0) or 0)
        total = int(task.get("total", 0) or 0)
        size_text = f"{_human(done)} / unknown size"
        if total:
            size_text = f"{_human(done)} / {_human(total)}"
        size_label = QLabel(size_text)
        size_label.setStyleSheet(f"color: {COLORS['muted']};")
        head.addWidget(size_label)
        layout.addLayout(head)

        bar = QProgressBar()
        bar.setRange(0, 100)
        bar.setValue(int(task.get("percent", 0) or 0))
        bar.setFormat("%p%")
        layout.addWidget(bar)

        url_label = QLabel(str(task.get("url", "") or ""))
        url_label.setStyleSheet(f"color: {COLORS['muted']};")
        url_label.setWordWrap(True)
        layout.addWidget(url_label)

        actions = QHBoxLayout()
        resume_btn = QPushButton("Resume")
        resume_btn.setObjectName("GhostButton")
        resume_btn.clicked.connect(lambda: on_resume(self.task))
        actions.addWidget(resume_btn)
        discard_btn = QPushButton("Discard")
        discard_btn.setObjectName("GhostButton")
        discard_btn.clicked.connect(lambda: on_discard(self.task))
        actions.addWidget(discard_btn)
        actions.addStretch()
        layout.addLayout(actions)


class DownloadsPage(DownloadActionMixin, QWidget):
    """The downloads hub: pending resume/discard plus one active download bar."""

    def __init__(self, orch: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._orch = orch
        self._rows: list[_PendingRow] = []

        layout = QVBoxLayout(self)

        title = QLabel("Downloads")
        title.setObjectName("PageTitle")
        layout.addWidget(title)

        intro = QLabel(
            "Interrupted downloads that can be resumed. If a large model or "
            "backend archive was cut short, finish it here — Pause / Resume / "
            "Cancel work while it downloads, and it survives app restarts."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet(f"color: {COLORS['muted']};")
        layout.addWidget(intro)

        self._progress = ProgressWidget()
        layout.addWidget(self._progress)

        self._empty_label = QLabel("No interrupted downloads.")
        self._empty_label.setStyleSheet(f"color: {COLORS['muted']};")
        layout.addWidget(self._empty_label)

        self._list_layout = QVBoxLayout()
        layout.addLayout(self._list_layout)

        btn_row = QHBoxLayout()
        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self._load)
        btn_row.addWidget(refresh_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._status_label = QLabel()
        self._status_label.setWordWrap(True)
        layout.addWidget(self._status_label)

        self._load()

    # ─── Loading ─────────────────────────────────────────────────────────

    def _load(self) -> None:
        worker = EngineWorker(self._orch, "pending_downloads")
        worker.signals.finished.connect(self._on_list)
        worker.signals.error.connect(self._on_error)
        WorkerPool.instance().start(worker)

    def _clear_rows(self) -> None:
        while self._list_layout.count():
            item = self._list_layout.takeAt(0)
            if item is None:
                continue
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._rows = []

    def _on_list(self, data: Any) -> None:
        with suppress(RuntimeError):
            payload = as_payload(data)
            tasks = list(payload.get("tasks") or [])
            self._clear_rows()
            self._empty_label.setVisible(not tasks)
            for task in tasks:
                row = _PendingRow(
                    dict(task),
                    on_resume=self._resume_task,
                    on_discard=self._discard_task,
                )
                self._list_layout.addWidget(row)
                self._rows.append(row)
            if tasks:
                self._status_label.setText(
                    f"{len(tasks)} interrupted download{'s' if len(tasks) != 1 else ''}."
                )
            else:
                self._status_label.setText("")

    # ─── Actions ──────────────────────────────────────────────────────────

    def _resume_task(self, task: dict[str, Any]) -> None:
        if task.get("kind") == "backend":
            # Re-obtain the missing backend: the partial archive resumes via
            # stream_download, then extraction + cudart re-run as needed.
            control = DownloadControl()
            self._progress.start_operation(
                f"Resuming {task.get('name', 'download')}…", control=control
            )
            worker = EngineWorker(
                self._orch,
                "bootstrap",
                progress_callback=self._on_progress,
                control=control,
            )
            worker.signals.finished.connect(self._on_downloaded)
            worker.signals.error.connect(self._on_download_error)
            WorkerPool.instance().start(worker)
        else:
            self._start_download_worker(
                str(task.get("url", "")),
                status_text=f"Resuming {task.get('name', 'download')}…",
            )

    def _discard_task(self, task: dict[str, Any]) -> None:
        worker = EngineWorker(
            self._orch, "discard_download", dest=str(task.get("dest", ""))
        )
        worker.signals.finished.connect(self._on_list)
        worker.signals.error.connect(self._on_error)
        WorkerPool.instance().start(worker)

    # ─── Signals ──────────────────────────────────────────────────────────

    def _on_downloaded(self, data: Any) -> None:
        self._progress.finish_operation()
        self._status_label.setText("Resume complete.")
        self._load()

    def _on_error(self, msg: str) -> None:
        self._status_label.setText(f"Error: {msg}")
