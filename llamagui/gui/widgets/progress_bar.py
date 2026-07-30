from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QProgressBar, QVBoxLayout, QWidget


class ProgressWidget(QWidget):
    progress_updated = Signal(int, str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        self._bar = QProgressBar()
        self._bar.setRange(0, 0)
        self._bar.hide()
        layout.addWidget(self._bar)

    def start_operation(self, label: str = "") -> None:
        self._bar.setRange(0, 0)
        self._bar.setFormat(label)
        self._bar.show()

    def update_progress(self, done: int, total: int, phase: str) -> None:
        if total > 0:
            self._bar.setRange(0, total)
            self._bar.setValue(min(done, total))
            self._bar.setFormat(f"{phase} — {done}/{total}")
        else:
            self._bar.setRange(0, 0)
            self._bar.setFormat(phase)
        self._bar.show()

    def finish_operation(self) -> None:
        self._bar.setRange(0, 100)
        self._bar.setValue(100)
        self._bar.setFormat("done")
        self._bar.hide()

    def fail_operation(self, message: str) -> None:
        self._bar.setRange(0, 100)
        self._bar.setValue(0)
        self._bar.setFormat(message)
