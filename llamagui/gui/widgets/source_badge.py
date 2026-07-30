from __future__ import annotations

from PySide6.QtWidgets import QLabel, QWidget

SOURCE_COLORS = {
    "pointed": "#4CAF50",
    "managed-prebuilt": "#2196F3",
    "managed-build": "#FF9800",
    "system": "#9E9E9E",
}


class SourceBadge(QLabel):
    def __init__(self, source: str | None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._source = source
        self._render()

    def update_source(self, source: str | None) -> None:
        self._source = source
        self._render()

    def _render(self) -> None:
        text = self._source or "unknown"
        color = SOURCE_COLORS.get(self._source or "", "#757575")
        self.setText(f" [{text}] ")
        self.setStyleSheet(
            f"background-color: {color}; color: white; "
            f"border-radius: 3px; padding: 1px 4px; font-size: 11px;"
        )
