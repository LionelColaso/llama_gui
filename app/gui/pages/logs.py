from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..widgets.log_view import LogView


class LogsPage(QWidget):
    def __init__(self, state_dir: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._state_dir = state_dir

        layout = QVBoxLayout(self)

        title = QLabel("Logs")
        title.setObjectName("PageTitle")
        layout.addWidget(title)

        self._tabs = QTabWidget()
        self._out_view = LogView(state_dir / "llama-server.out.log")
        self._err_view = LogView(state_dir / "llama-server.err.log")
        self._tabs.addTab(self._out_view, "stdout")
        self._tabs.addTab(self._err_view, "stderr")
        layout.addWidget(self._tabs)

        btn_row = QHBoxLayout()
        open_btn = QPushButton("Open log folder")
        open_btn.clicked.connect(self._open_folder)
        btn_row.addWidget(open_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

    def _open_folder(self) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._state_dir)))
