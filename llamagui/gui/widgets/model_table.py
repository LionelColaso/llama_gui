from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


class ModelTable(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["ID", "Command", "Group", "Flags"])
        self._table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        layout.addWidget(self._table)

        btn_row = QHBoxLayout()
        add_btn = QPushButton("Add")
        add_btn.clicked.connect(self._add_row)
        btn_row.addWidget(add_btn)

        remove_btn = QPushButton("Remove")
        remove_btn.clicked.connect(self._remove_selected)
        btn_row.addWidget(remove_btn)

        btn_row.addStretch()
        layout.addLayout(btn_row)

    def _add_row(self) -> None:
        row = self._table.rowCount()
        self._table.insertRow(row)
        self._table.setItem(row, 0, QTableWidgetItem(""))
        self._table.setItem(row, 1, QTableWidgetItem(""))
        self._table.setItem(row, 2, QTableWidgetItem(""))
        self._table.setItem(row, 3, QTableWidgetItem(""))

    def _remove_selected(self) -> None:
        for row in sorted(
            {i.row() for i in self._table.selectedIndexes()}, reverse=True
        ):
            self._table.removeRow(row)

    def load_models(self, models: list[dict[str, Any]]) -> None:
        self._table.setRowCount(0)
        for m in models:
            row = self._table.rowCount()
            self._table.insertRow(row)
            self._table.setItem(row, 0, QTableWidgetItem(m.get("id", "")))
            self._table.setItem(row, 1, QTableWidgetItem(m.get("cmd", "")))
            self._table.setItem(row, 2, QTableWidgetItem(m.get("group", "")))
            flags = " ".join(m.get("flags", []))
            self._table.setItem(row, 3, QTableWidgetItem(flags))

    def get_models(self) -> list[dict[str, Any]]:
        models: list[dict[str, Any]] = []
        for row in range(self._table.rowCount()):
            item_id = self._table.item(row, 0)
            item_cmd = self._table.item(row, 1)
            item_group = self._table.item(row, 2)
            item_flags = self._table.item(row, 3)
            mid = item_id.text().strip() if item_id else ""
            if not mid:
                continue
            flags_str = item_flags.text().strip() if item_flags else ""
            models.append(
                {
                    "id": mid,
                    "cmd": item_cmd.text().strip() if item_cmd else "",
                    "group": item_group.text().strip() if item_group else "",
                    "flags": flags_str.split() if flags_str else [],
                }
            )
        return models
