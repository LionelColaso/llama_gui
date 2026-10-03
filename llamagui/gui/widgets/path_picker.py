"""Reusable "path + Browse" row used by the Settings and Backends sections."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QWidget,
)


class PathPicker(QWidget):
    """A line edit with a Browse button (and an optional extra action).

    ``mode`` is ``"file"`` or ``"directory"``. The picker never rewrites what
    the user typed: an explicit path always wins, which is what makes
    "point at an install I already have" reliable.
    """

    def __init__(
        self,
        mode: str = "file",
        caption: str = "Select",
        placeholder: str = "",
        action_label: str | None = None,
        on_action: Callable[[str], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._mode = mode
        self._caption = caption

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._edit = QLineEdit()
        self._edit.setPlaceholderText(placeholder)
        layout.addWidget(self._edit)

        self._browse_btn = QPushButton("Browse…")
        self._browse_btn.clicked.connect(self._browse)
        layout.addWidget(self._browse_btn)

        self._action_btn: QPushButton | None = None
        if action_label and on_action is not None:
            button = QPushButton(action_label)
            button.clicked.connect(lambda: on_action(self.text()))
            layout.addWidget(button)
            self._action_btn = button

    # ─── Value ───────────────────────────────────────────────────────────

    def text(self) -> str:
        return self._edit.text().strip()

    def value(self) -> str | None:
        return self.text() or None

    def setText(self, value: str | None) -> None:
        self._edit.setText(value or "")

    # ─── Browse ──────────────────────────────────────────────────────────

    def _start_dir(self) -> str:
        current = self.text()
        if not current:
            return str(Path.home())
        path = Path(current).expanduser()
        if path.is_dir():
            return str(path)
        if path.parent.exists():
            return str(path.parent)
        return str(Path.home())

    def _browse(self) -> None:
        if self._mode == "directory":
            chosen = QFileDialog.getExistingDirectory(
                self, self._caption, self._start_dir()
            )
        else:
            chosen, _ = QFileDialog.getOpenFileName(
                self, self._caption, self._start_dir()
            )
        if chosen:
            self._edit.setText(chosen)
