from __future__ import annotations

from collections.abc import Callable

from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..theme import COLORS
from .source_badge import SourceBadge


class BackendCard(QWidget):
    def __init__(
        self,
        name: str,
        installed: bool = False,
        version: str | None = None,
        source: str | None = None,
        *,
        on_install: Callable[[], None] | None = None,
        on_update: Callable[[], None] | None = None,
        on_use: Callable[[], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self._on_install = on_install
        self._on_update = on_update
        self._on_use = on_use
        layout = QVBoxLayout(self)

        name_row = QHBoxLayout()
        name_label = QLabel(name)
        name_label.setStyleSheet("font-weight: bold; font-size: 14px;")
        name_row.addWidget(name_label)
        self._badge = SourceBadge(source)
        name_row.addWidget(self._badge)
        self._active_label = QLabel("● active")
        self._active_label.setObjectName("PageHint")
        self._active_label.setVisible(False)
        name_row.addWidget(self._active_label)
        name_row.addStretch()
        layout.addLayout(name_row)

        self._status_label = QLabel()
        layout.addWidget(self._status_label)

        btn_row = QHBoxLayout()
        self._install_btn = QPushButton("Install")
        if on_install is not None:
            self._install_btn.clicked.connect(on_install)
        self._update_btn = QPushButton("Update")
        if on_update is not None:
            self._update_btn.clicked.connect(on_update)
        self._use_btn = QPushButton("Use")
        if on_use is not None:
            self._use_btn.clicked.connect(on_use)
        for btn in (self._install_btn, self._update_btn, self._use_btn):
            btn_row.addWidget(btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self.refresh(installed, version, source)

    def set_active(self, active: bool) -> None:
        self._active_label.setVisible(active)

    def refresh(
        self,
        installed: bool,
        version: str | None,
        source: str | None = None,
        unavailable_reason: str = "",
    ) -> None:
        if source:
            self._badge.update_source(source)
        if unavailable_reason:
            self._status_label.setText("unavailable here")
            self._status_label.setToolTip(unavailable_reason)
            self._status_label.setStyleSheet(f"color: {COLORS['warning']};")
            self._install_btn.setVisible(False)
            self._update_btn.setVisible(False)
            self._use_btn.setVisible(False)
            return
        self._status_label.setToolTip("")
        if installed:
            self._status_label.setText(f"v{version or '?'} — installed")
            self._status_label.setStyleSheet(f"color: {COLORS['success']};")
        else:
            self._status_label.setText("not installed")
            self._status_label.setStyleSheet(f"color: {COLORS['muted']};")
        # Show only the actions that make sense for the current state.
        self._install_btn.setVisible(not installed)
        self._update_btn.setVisible(bool(installed))
        self._use_btn.setVisible(bool(installed))
