"""Pick the model to launch when none is active.

The engine refuses to guess: ``launch`` runs the active model, and with
several ``.gguf`` files and no active one it says so and points at the Models
page. That is the right behaviour for a CLI, but as a *response* to a button it
just makes the user go and find the other page. This dialog closes the loop:
the Launch / Restart buttons open it when a choice is needed, and picking a row
sets that model active before the launch goes out.

It lists the same library the Models page shows (name, size, modified), with a
filter box, and never writes anything itself -- it returns the chosen name and
the caller runs the engine. Models may sit in sub-folders, so the row shows the
file name while the full relative name is what gets returned.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QLabel,
    QLineEdit,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..format import human_bytes as _human
from ..theme import COLORS

#: Qt role holding a row's full model name, which may differ from its label.
NAME_ROLE = int(Qt.ItemDataRole.UserRole) + 1


def _basename(name: str) -> str:
    """The file name out of a (possibly nested) relative model name."""
    return name.rsplit("/", 1)[-1]


def _as_int(model: dict[str, Any]) -> int:
    try:
        return int(model.get("size_bytes", 0) or 0)
    except TypeError, ValueError:
        return 0


class ModelPickerDialog(QDialog):
    """Choose one model from the library; the name comes back from the dialog."""

    def __init__(
        self, models: list[dict[str, Any]], parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Which model?")
        self.setMinimumSize(760, 520)

        layout = QVBoxLayout(self)

        intro = QLabel(
            "No model is set as active, so the server has nothing to launch. "
            "Pick the one to use — it stays active until you change it."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet(f"color: {COLORS['muted']};")
        layout.addWidget(intro)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Filter by name…")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._apply_filter)
        layout.addWidget(self._search)

        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels(["Model", "Size", "Modified"])
        self._table.verticalHeader().setVisible(False)
        self._table.setAlternatingRowColors(True)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._table.itemDoubleClicked.connect(self._on_double_clicked)
        layout.addWidget(self._table, stretch=1)

        buttons = QDialogButtonBox()
        use = buttons.addButton(QDialogButtonBox.StandardButton.Ok)
        use.setText("Use this model")
        use.setObjectName("PrimaryButton")
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.load(models)
        if self._table.rowCount():
            self._table.selectRow(0)

    def _on_double_clicked(self, item: QTableWidgetItem) -> None:
        """A double click is a choice: accept with that row."""
        del item
        self.accept()

    def load(self, models: list[dict[str, Any]]) -> None:
        """Fill the list from a ``list_models`` payload."""
        self._table.setRowCount(0)
        for model in models:
            name = str(model.get("name", ""))
            if not name:
                continue
            row = self._table.rowCount()
            self._table.insertRow(row)
            label = QTableWidgetItem(_basename(name))
            # The label is the file name; the full relative name is what the
            # engine stores, so it rides along in the row.
            label.setData(NAME_ROLE, name)
            label.setToolTip(name)
            self._table.setItem(row, 0, label)
            self._table.setItem(row, 1, QTableWidgetItem(_human(_as_int(model))))
            self._table.setItem(
                row, 2, QTableWidgetItem(str(model.get("modified", "")))
            )

    def selected_model(self) -> str | None:
        """The chosen model's full (possibly nested) name, or ``None``."""
        row = self._table.currentRow()
        item = self._table.item(row, 0) if row >= 0 else None
        if item is None:
            return None
        name = item.data(NAME_ROLE)
        return str(name) if name else None

    def _apply_filter(self) -> None:
        query = self._search.text().strip().lower()
        for row in range(self._table.rowCount()):
            item = self._table.item(row, 0)
            full = str(item.data(NAME_ROLE) if item is not None else "").lower()
            self._table.setRowHidden(row, bool(query) and query not in full)


__all__ = ["NAME_ROLE", "ModelPickerDialog"]
