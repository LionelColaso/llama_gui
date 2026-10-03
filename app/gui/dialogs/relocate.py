"""Offer to move existing data when a path setting changes.

Shown by the Settings page when saving would point the managed root and/or the
models directory at a new location that already holds data somewhere else.
Changing a setting moves nothing on its own, so this dialog asks: one checkbox
per tree that can move, then *Move & save* (the old location is emptied),
*Copy & save* (it is kept as a backup), *Save paths only* (every file stays put)
or *Cancel* (nothing changes at all) — plus whatever notes the engine produced
(e.g. interrupted downloads that stay behind).

The dialog never touches the filesystem itself; it only reports what the user
picked back to the page, which drives the worker and the config save.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractButton,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...schemas import RelocationData, RelocationItem
from ..theme import COLORS

#: Qt property carrying an action choice from a button back to the dialog.
CHOICE_PROPERTY = "relocateChoice"


def _human(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"  # pragma: no cover - loop always returns


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _wrapped(label: QLabel) -> QLabel:
    """Make a label wrap *and* be laid out for the height it wraps to.

    ``setWordWrap`` alone is not enough: without the height-for-width size
    policy the layout still reserves a single line, so everything past the first
    line is silently cut off.
    """
    label.setWordWrap(True)
    policy = label.sizePolicy()
    policy.setHeightForWidth(True)
    label.setSizePolicy(policy)
    return label


class RelocateDialog(QDialog):
    """Ask whether the existing backends/models should follow the new paths."""

    #: Move the ticked trees to the new location and save.
    MOVE = "move"
    #: Duplicate them, keeping the old location as a backup, and save.
    COPY = "copy"
    #: Save the new paths and leave every existing file where it is.
    SAVE_PATHS_ONLY = "save-only"

    def __init__(self, plan: RelocationData, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Move existing data?")
        self.setMinimumWidth(620)
        self._choice = self.SAVE_PATHS_ONLY

        layout = QVBoxLayout(self)

        intro = _wrapped(
            QLabel(
                "The location changed. Move what is already there to the new "
                "location, keep a copy of it, or just save the change and leave "
                "the files where they are."
            )
        )
        layout.addWidget(intro)

        self._checks: dict[str, QCheckBox] = {}
        for item in plan.items:
            layout.addWidget(self._build_row(item))
        for note in plan.notes:
            label = _wrapped(QLabel(f"Note: {note}"))
            label.setStyleSheet(f"color: {COLORS['muted']};")
            layout.addWidget(label)

        buttons = QDialogButtonBox()
        for text, choice, tip in (
            ("Move & save", self.MOVE, "Move the ticked items to the new location."),
            (
                "Copy & save",
                self.COPY,
                "Copy the ticked items, keeping the originals where they are.",
            ),
            (
                "Save paths only",
                self.SAVE_PATHS_ONLY,
                "Save the new paths and leave every existing file untouched.",
            ),
        ):
            button = QPushButton(text)
            button.setToolTip(tip)
            button.setDefault(choice == self.MOVE)
            # One primary action (*Move & save*); the alternatives are ghosts so
            # the default is unmistakable.
            if choice != self.MOVE:
                button.setObjectName("GhostButton")
            button.setProperty(CHOICE_PROPERTY, choice)
            buttons.addButton(button, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        buttons.clicked.connect(self._on_button_clicked)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_button_clicked(self, button: QAbstractButton) -> None:
        """Accept with the chosen action, or cancel from the Cancel button."""
        choice = button.property(CHOICE_PROPERTY)
        if choice is None:
            self.reject()
            return
        self._choice = str(choice)
        self.accept()

    def _build_row(self, item: RelocationItem) -> QWidget:
        box = QGroupBox(item.label)
        layout = QVBoxLayout(box)

        # Checked by default: moving the data is what the user almost always
        # wants, and a blocked tree clears itself below.
        check = QCheckBox(
            f"Move {_plural(item.files, 'file')}, {_human(item.total_bytes)}"
        )
        check.setChecked(True)
        layout.addWidget(check)
        self._checks[item.label] = check

        paths = _wrapped(QLabel(f"from {item.source}\nto {item.destination}"))
        paths.setStyleSheet(f"color: {COLORS['muted']};")
        paths.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        # The source and the destination are two deliberate lines, so reserve
        # two lines even when the layout would otherwise settle for one.
        paths.setMinimumHeight(2 * paths.fontMetrics().height())
        layout.addWidget(paths)

        if item.blocked:
            check.setEnabled(False)
            check.setChecked(False)
            blocked = _wrapped(QLabel(f"Cannot move: {item.blocked}."))
            blocked.setStyleSheet(f"color: {COLORS['warning']};")
            layout.addWidget(blocked)
        return box

    # ─── Result ──────────────────────────────────────────────────────────

    def move_backends(self) -> bool:
        check = self._checks.get("Backends")
        return bool(check is not None and check.isChecked())

    def move_models(self) -> bool:
        check = self._checks.get("Models")
        return bool(check is not None and check.isChecked())

    @property
    def choice(self) -> str:
        """The action the user picked: ``MOVE``, ``COPY`` or ``SAVE_PATHS_ONLY``."""
        return self._choice

    @property
    def moves_anything(self) -> bool:
        """True when at least one tree is actually ticked."""
        return self.move_backends() or self.move_models()


__all__ = ["RelocateDialog"]
