"""Dashboard home: a single scrolling page that fuses the Backends section (state
+ actions + resolved binary) and the Models section into one view.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QFrame, QScrollArea, QVBoxLayout, QWidget

from .backends import BackendsPage
from .models import ModelsPage


class DashboardHome(QScrollArea):
    """Single scrolling home page with Backends / Models sections."""

    def __init__(self, orch: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("DashboardHome")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)

        self.backends = BackendsPage(orch)
        self.models_page = ModelsPage(orch)

        content = QWidget()
        content.setObjectName("DashboardHomeContent")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(22)
        layout.addWidget(self.backends)
        layout.addWidget(self.models_page)
        layout.addStretch()
        self.setWidget(content)
