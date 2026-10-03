"""Dashboard home: a single scrolling page that fuses the Backends section (state
+ actions + resolved binary) and the Models section into one view.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QFrame, QScrollArea, QVBoxLayout, QWidget

from ..sections.backends import BackendsSection
from ..sections.models import ModelsSection


class DashboardHome(QScrollArea):
    """Single scrolling home page with Backends / Models sections."""

    def __init__(self, orch: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("DashboardHome")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)

        self.backends = BackendsSection(orch)
        self.models_section = ModelsSection(orch)

        content = QWidget()
        content.setObjectName("DashboardHomeContent")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(22)
        layout.addWidget(self.backends)
        layout.addWidget(self.models_section)
        layout.addStretch()
        self.setWidget(content)

    def start_refresh(self) -> None:
        """Begin polling the sections. Safe to call when already running."""
        self.backends.start_refresh()

    def stop_refresh(self) -> None:
        """Stop polling. Safe to call when not running."""
        self.backends.stop_refresh()
