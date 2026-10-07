from __future__ import annotations

from PySide6.QtWidgets import QLabel, QWidget

SOURCE_COLORS = {
    "managed-prebuilt": "#6366F1",  # indigo
    "managed-build": "#F59E0B",  # amber
    "system": "#94A3B8",  # slate
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
        color = SOURCE_COLORS.get(self._source or "", "#64748B")
        self.setText(f" {text} ")
        self.setStyleSheet(
            f"background-color: {color}; color: white; "
            f"border-radius: 999px; padding: 2px 9px; font-size: 11px; "
            f"font-weight: 600;"
        )
