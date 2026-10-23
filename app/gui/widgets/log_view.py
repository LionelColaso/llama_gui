from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QFileSystemWatcher, QTimer
from PySide6.QtWidgets import QPlainTextEdit, QPushButton, QVBoxLayout, QWidget


class LogView(QWidget):
    def __init__(
        self,
        log_path: Path,
        parent: QWidget | None = None,
        *,
        latest_glob: str | None = None,
    ) -> None:
        super().__init__(parent)
        self._log_path = log_path
        self._latest_glob = latest_glob
        self._current = self._resolve_path()
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
        self._watcher.fileChanged.connect(self._on_file_changed)
        self._watcher.directoryChanged.connect(self._on_directory_changed)
        self._watch_directory()
        self._tail()

        # A watcher can miss a file replaced by a rename on Windows, and Qt
        # drops a file watch once the watched path is replaced. Polling is the
        # fallback that keeps the pane following while the user is watching it.
        self._poll = QTimer(self)
        self._poll.setInterval(1000)
        self._poll.timeout.connect(self._tail)
        self._poll.start()

    def _resolve_path(self) -> Path | None:
        """The file to tail now: the newest rotated log, else the fixed path."""
        if self._latest_glob is not None:
            matches = sorted(self._log_path.parent.glob(self._latest_glob))
            if matches:
                return matches[-1]
        return self._log_path if self._log_path.exists() else None

    def _watch_file(self, path: Path) -> None:
        if path.exists() and str(path) not in self._watcher.files():
            self._watcher.addPath(str(path))

    def _watch_directory(self) -> None:
        parent = self._log_path.parent
        if parent.is_dir() and str(parent) not in self._watcher.directories():
            self._watcher.addPath(str(parent))

    def _on_file_changed(self, path: str) -> None:
        # Qt removes a path from the watcher when it is replaced; re-add it so
        # the next append is seen. A removed file is handled by _tail switching
        # to the newest rotated log.
        self._watch_directory()
        self._watch_file(Path(path))
        self._tail()

    def _on_directory_changed(self, _path: str) -> None:
        # A new launch created (or pruned) a log pair.
        self._watch_directory()
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
        target = self._resolve_path()
        if target != self._current:
            self._current = target
            self._pos = 0
        if self._current is None:
            return
        self._watch_file(self._current)
        try:
            with self._current.open("r", encoding="utf-8", errors="replace") as f:
                size = f.seek(0, 2)
                if self._pos > size:
                    self._pos = 0
                f.seek(self._pos)
                new_data = f.read()
                self._pos = f.tell()
                if new_data:
                    self._editor.appendPlainText(new_data.rstrip())
                    self._editor.moveCursor(self._editor.textCursor().MoveOperation.End)
        except OSError:
            pass
