"""Models tab: the .gguf library plus every download in flight or interrupted.

The library and its downloads are one subject, so they are one tab: the
**Models** table (list / download / set-active / remove) on top, and the
**Interrupted downloads** rows — including half-downloaded backend archives —
below, each with Resume / Discard. Both sections drive the same engine actions
through the worker pool, so the GUI and the CLI stay on one code path.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QFrame, QLabel, QScrollArea, QVBoxLayout, QWidget

from ..sections.downloads import DownloadsSection
from ..sections.models import ModelsSection


class ModelsPage(QScrollArea):
    """Single scrolling page with the Models library / Downloads sections."""

    def __init__(self, orch: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ModelsPage")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)

        self.models = ModelsSection(orch)
        self.downloads = DownloadsSection(orch)

        content = QWidget()
        content.setObjectName("ModelsPageContent")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(22)

        title = QLabel("Models")
        title.setObjectName("PageTitle")
        layout.addWidget(title)

        layout.addWidget(self.models)
        layout.addWidget(self.downloads)
        layout.addStretch()
        self.setWidget(content)


__all__ = ["ModelsPage"]
