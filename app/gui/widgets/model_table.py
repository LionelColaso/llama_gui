from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import (
    QHeaderView,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)


def _format_size(size_bytes: int) -> str:
    """Human-readable byte count (1.2 GB)."""
    value = float(size_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024.0 or unit == "TB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{size_bytes} B"


class ModelTable(QTableWidget):
    """Read-only listing of .gguf files: name, size, modified, active."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(0, 4, parent)
        self.setHorizontalHeaderLabels(["Model", "Size", "Modified", "Status"])
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Interactive
        )
        self.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.setAlternatingRowColors(True)

    def load_models(
        self, models: list[dict[str, Any]], active: str | None = None
    ) -> None:
        """Fill the table from ``list_models`` rows (dicts with name/size/modified)."""
        self.setRowCount(0)
        for m in models:
            row = self.rowCount()
            self.insertRow(row)
            name = str(m.get("name", ""))
            self.setItem(row, 0, QTableWidgetItem(name))
            self.setItem(
                row, 1, QTableWidgetItem(_format_size(int(m.get("size_bytes", 0))))
            )
            self.setItem(row, 2, QTableWidgetItem(str(m.get("modified", ""))))
            self.setItem(row, 3, QTableWidgetItem("active" if name == active else ""))

    def selected_name(self) -> str | None:
        """File name of the selected row, or None."""
        row = self.currentRow()
        if row < 0:
            return None
        item = self.item(row, 0)
        return item.text() if item and item.text() else None
