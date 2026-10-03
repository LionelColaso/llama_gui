"""Dashboard home: a single scrolling page with the Backends section (state,
actions and the resolved binary). Models have their own tab.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QFrame, QScrollArea, QVBoxLayout, QWidget

from ..sections.backends import BackendsSection


class DashboardHome(QScrollArea):
    """Single scrolling home page: the Backends section.

    Models live in their own tab (``pages/models.py``) together with the
    interrupted-downloads rows; this page stays about the backend and the
    server it runs.
    """

    def __init__(self, orch: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("DashboardHome")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)

        self.backends = BackendsSection(orch)

        content = QWidget()
        content.setObjectName("DashboardHomeContent")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(22)
        layout.addWidget(self.backends)
        layout.addStretch()
        self.setWidget(content)

    def start_refresh(self) -> None:
        """Begin polling the sections. Safe to call when already running."""
        self.backends.start_refresh()

    def stop_refresh(self) -> None:
        """Stop polling. Safe to call when not running."""
        self.backends.stop_refresh()
