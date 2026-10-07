"""Per-model server options popup — the *Server options…* action on Models.

One model, one configuration, opened from the Models tab for the selected model.
The dialog offers exactly one decision and nothing else needs explaining:

* **Use global server config** (checked by default, and for every model that has
  never been given its own settings) — the model follows the global server
  options, including any edit made to them later. The rows stay visible but
  read-only, so it is obvious *what* the model would run with.
* Unchecked — the model owns its settings: every row becomes editable and *Save*
  stores them as this model's configuration. Nothing else changes; the globals
  are left alone.
* **Reset server config** — put this model's own options back to the binary's
  defaults. It does *not* hand the model back to the globals: a reset means "no
  values", not "inherit". Ticking the checkbox is how a model goes back to
  following the globals.

The dialog writes through the engine on Save/Reset; it never touches the config
file itself, so the CLI and the page see exactly what the popup wrote.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...lifecycle import uses_global_server_config
from ..theme import COLORS
from ..widgets.server_options_editor import ServerOptionsEditor

#: Label of the checkbox that keeps a model on the global configuration.
USE_GLOBAL_LABEL = "Use global server config"

#: Label of the button that clears this model's own options.
RESET_LABEL = "Reset server config"


class ModelServerOptionsDialog(QDialog):
    """Edit one model's server configuration, or let it follow the globals."""

    def __init__(self, orch: Any, model: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._orch = orch
        self._model = model
        self.setWindowTitle(f"Server options — {model}")
        self.setMinimumSize(880, 640)

        layout = QVBoxLayout(self)

        title = QLabel(f"Server options for {model}")
        title.setObjectName("PageTitle")
        layout.addWidget(title)

        self._use_global = QCheckBox(USE_GLOBAL_LABEL)
        self._use_global.setChecked(uses_global_server_config(orch.cfg, model))
        self._use_global.setToolTip(
            "Checked: this model follows the global server options, including "
            "changes made to them later. Unchecked: it keeps the settings on "
            "this page as its own."
        )
        self._use_global.toggled.connect(self._apply_mode)
        layout.addWidget(self._use_global)

        self._status = QLabel()
        self._status.setWordWrap(True)
        self._status.setStyleSheet(f"color: {COLORS['muted']};")
        layout.addWidget(self._status)

        self._editor = ServerOptionsEditor(orch)
        self._editor.set_scope(model)
        layout.addWidget(self._editor, stretch=1)

        buttons = QDialogButtonBox()
        self._reset_button = QPushButton(RESET_LABEL)
        self._reset_button.setToolTip(
            "Clear this model's own options, so every one is back at the "
            "binary's default."
        )
        self._reset_button.clicked.connect(self._reset)
        buttons.addButton(self._reset_button, QDialogButtonBox.ButtonRole.ResetRole)
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        save = buttons.addButton("Save", QDialogButtonBox.ButtonRole.AcceptRole)
        save.setObjectName("PrimaryButton")
        save.setToolTip("Store this model's server options")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._apply_mode()

    # ─── Mode ───────────────────────────────────────────────────────────

    @property
    def model(self) -> str:
        return self._model

    def uses_global(self) -> bool:
        """True when the model will follow the global configuration."""
        return self._use_global.isChecked()

    def _apply_mode(self, *_: Any) -> None:
        """Show the followed configuration read-only, or the editable own one."""
        following = self.uses_global()
        # Nothing of its own is shown while the model follows the globals: it
        # runs with the global values, so show exactly those (read-only) rather
        # than whatever it had stored before.
        self._editor.set_scope(None if following else self._model)
        self._editor.set_read_only(following)
        self._status.setText(
            f"{self._model} follows the global server options. Untick the box to "
            "give it settings of its own."
            if following
            else f"{self._model} has its own server options; empty values are "
            "omitted so the binary's default wins."
        )
        self._reset_button.setEnabled(not following)

    # ─── Actions ────────────────────────────────────────────────────────

    def _save(self) -> None:
        if self.uses_global():
            self._orch.reset_model_server_config(self._model)
        else:
            try:
                options = self._editor.collect_model()
            except ValueError as exc:  # pragma: no cover - editors validate first
                self._status.setText(str(exc))
                return
            self._orch.save_model_server_config(self._model, options)
        self.accept()

    def _reset(self) -> None:
        self._orch.reset_model_server_config(self._model)
        self.accept()


__all__ = ["RESET_LABEL", "USE_GLOBAL_LABEL", "ModelServerOptionsDialog"]
