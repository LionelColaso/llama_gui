from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from .source_badge import SourceBadge


class BackendCard(QWidget):
    def __init__(
        self,
        name: str,
        installed: bool = False,
        version: str | None = None,
        source: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)

        name_row = QHBoxLayout()
        name_label = QLabel(name)
        name_label.setStyleSheet("font-weight: bold; font-size: 14px;")
        name_row.addWidget(name_label)
        self._badge = SourceBadge(source)
        name_row.addWidget(self._badge)
        name_row.addStretch()
        layout.addLayout(name_row)

        self._status_label = QLabel()
        layout.addWidget(self._status_label)
        self.refresh(installed, version, source)

    def refresh(
        self, installed: bool, version: str | None, source: str | None = None
    ) -> None:
        if source:
            self._badge.update_source(source)
        if installed:
            self._status_label.setText(f"v{version or '?'} — installed")
            self._status_label.setStyleSheet("color: #4CAF50;")
        else:
            self._status_label.setText("not installed")
            self._status_label.setStyleSheet("color: #757575;")
