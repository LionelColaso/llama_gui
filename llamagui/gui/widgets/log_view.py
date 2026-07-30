from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QFileSystemWatcher
from PySide6.QtWidgets import QPlainTextEdit, QPushButton, QVBoxLayout, QWidget


class LogView(QWidget):
    def __init__(self, log_path: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._log_path = log_path
        self._pos = 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._editor = QPlainTextEdit()
        self._editor.setReadOnly(True)
        self._editor.setMaximumBlockCount(10_000)
        layout.addWidget(self._editor)

        btn_row = QVBoxLayout()
        self._follow_btn = QPushButton("Pause tail")
        self._follow_btn.setCheckable(True)
        self._follow_btn.toggled.connect(self._on_toggle_follow)
        btn_row.addWidget(self._follow_btn)

        clear_btn = QPushButton("Clear")
        clear_btn.clicked.connect(self._editor.clear)
        btn_row.addWidget(clear_btn)

        layout.addLayout(btn_row)

        self._watcher = QFileSystemWatcher()
        self._watcher.fileChanged.connect(self._tail)
        if self._log_path.exists():
            self._watcher.addPath(str(self._log_path))
            self._tail()

    def _on_toggle_follow(self, paused: bool) -> None:
        if paused:
            self._follow_btn.setText("Resume tail")
        else:
            self._follow_btn.setText("Pause tail")
            self._tail()

    def _tail(self) -> None:
        if self._follow_btn.isChecked():
            return
        try:
            with self._log_path.open("r", encoding="utf-8", errors="replace") as f:
                size = f.seek(0, 2)
                if self._pos > size:
                    self._pos = 0
                f.seek(self._pos)
                new_data = f.read()
                self._pos = f.tell()
                if new_data:
                    self._editor.appendPlainText(new_data.rstrip())
                    self._editor.moveCursor(self._editor.textCursor().MoveOperation.End)
        except FileNotFoundError:
            pass
