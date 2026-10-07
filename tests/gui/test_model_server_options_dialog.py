"""The per-model *Server options…* popup: follow the globals, or own them."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from pytestqt.qtbot import QtBot

from app.gui.dialogs.model_server_options import (
    RESET_LABEL,
    USE_GLOBAL_LABEL,
    ModelServerOptionsDialog,
)
from app.gui.widgets.server_options_editor import EXTRA_ARGS_FLAG

MODEL = "big.gguf"


def _dialog(qtbot: QtBot, orch: MagicMock) -> ModelServerOptionsDialog:
    dialog = ModelServerOptionsDialog(orch, MODEL)
    qtbot.addWidget(dialog)
    return dialog


def _editor(dialog: ModelServerOptionsDialog, flag: str) -> Any:
    return dialog._editor.row(flag)


def _value(dialog: ModelServerOptionsDialog, flag: str) -> str:
    """The row's text, with "(default)" read as *not set*."""
    return dialog._editor.value_of(flag)


def _set_value(dialog: ModelServerOptionsDialog, flag: str, value: str) -> None:
    dialog._editor.set_value_of(flag, value)


def test_a_model_follows_the_globals_by_default(
    qtbot: QtBot, fake_orch: MagicMock
) -> None:
    dialog = _dialog(qtbot, fake_orch)

    assert dialog.uses_global() is True
    assert dialog._use_global.text() == USE_GLOBAL_LABEL


def test_following_the_globals_is_read_only(qtbot: QtBot, fake_orch: MagicMock) -> None:
    fake_orch.cfg.ctx_size = 4096
    dialog = _dialog(qtbot, fake_orch)

    assert _value(dialog, "--ctx-size") == "4096"
    assert _editor(dialog, "--ctx-size").isEnabled() is False


def test_a_model_with_its_own_config_opens_unticked(
    qtbot: QtBot, fake_orch: MagicMock
) -> None:
    fake_orch.cfg.model_server_options = {MODEL: {"--ctx-size": "16384"}}
    dialog = _dialog(qtbot, fake_orch)

    assert dialog.uses_global() is False
    assert _value(dialog, "--ctx-size") == "16384"
    assert _editor(dialog, "--ctx-size").isEnabled() is True


def test_unticking_starts_from_the_global_values(
    qtbot: QtBot, fake_orch: MagicMock
) -> None:
    """Untick → edit a copy of what the model ran with, not a blank form."""
    fake_orch.cfg.ctx_size = 4096
    fake_orch.cfg.server_options = {"--jinja": "on"}
    dialog = _dialog(qtbot, fake_orch)

    dialog._use_global.setChecked(False)

    assert _value(dialog, "--ctx-size") == "4096"
    assert _value(dialog, "--jinja") == "on"


def test_reticking_shows_the_globals_again(qtbot: QtBot, fake_orch: MagicMock) -> None:
    """The read-only rows are the configuration the model would run with."""
    fake_orch.cfg.ctx_size = 4096
    fake_orch.cfg.model_server_options = {MODEL: {"--ctx-size": "16384"}}
    dialog = _dialog(qtbot, fake_orch)

    dialog._use_global.setChecked(True)

    assert _value(dialog, "--ctx-size") == "4096"


def test_saving_while_unticked_stores_the_models_own_values(
    qtbot: QtBot, fake_orch: MagicMock
) -> None:
    fake_orch.cfg.ctx_size = 4096
    dialog = _dialog(qtbot, fake_orch)
    dialog._use_global.setChecked(False)
    _set_value(dialog, "--ctx-size", "16384")

    dialog._save()

    fake_orch.save_model_server_config.assert_called_once_with(
        MODEL, {"--ctx-size": "16384"}
    )
    fake_orch.reset_model_server_config.assert_not_called()


def test_saving_while_ticked_hands_the_model_back_to_the_globals(
    qtbot: QtBot, fake_orch: MagicMock
) -> None:
    """No own config to keep, so saving just confirms following the globals."""
    dialog = _dialog(qtbot, fake_orch)
    assert dialog.uses_global() is True

    dialog._save()

    fake_orch.reset_model_server_config.assert_called_once_with(MODEL)
    fake_orch.save_model_server_config.assert_not_called()


def test_reticking_and_saving_drops_the_models_own_config(
    qtbot: QtBot, fake_orch: MagicMock
) -> None:
    fake_orch.cfg.model_server_options = {MODEL: {"--ctx-size": "16384"}}
    dialog = _dialog(qtbot, fake_orch)
    assert dialog.uses_global() is False

    dialog._use_global.setChecked(True)
    dialog._save()

    fake_orch.reset_model_server_config.assert_called_once_with(MODEL)
    fake_orch.save_model_server_config.assert_not_called()


def test_reset_writes_empty_values_not_the_globals(
    qtbot: QtBot, fake_orch: MagicMock
) -> None:
    fake_orch.cfg.ctx_size = 4096
    fake_orch.cfg.server_options = {"--jinja": "on"}
    fake_orch.cfg.model_server_options = {MODEL: {"--ctx-size": "16384"}}
    dialog = _dialog(qtbot, fake_orch)
    assert dialog._reset_button.text() == RESET_LABEL

    dialog._reset()

    fake_orch.reset_model_server_config.assert_called_once_with(MODEL)
    fake_orch.save_model_server_config.assert_not_called()


def test_reset_is_unavailable_while_following_the_globals(
    qtbot: QtBot, fake_orch: MagicMock
) -> None:
    dialog = _dialog(qtbot, fake_orch)
    assert dialog._reset_button.isEnabled() is False

    dialog._use_global.setChecked(False)
    assert dialog._reset_button.isEnabled() is True


def test_the_host_and_port_rows_stay_locked_when_unticked(
    qtbot: QtBot, fake_orch: MagicMock
) -> None:
    dialog = _dialog(qtbot, fake_orch)
    dialog._use_global.setChecked(False)

    assert _editor(dialog, "--port").isEnabled() is False
    assert _editor(dialog, EXTRA_ARGS_FLAG).isEnabled() is False


def test_the_dialog_names_the_model(qtbot: QtBot, fake_orch: MagicMock) -> None:
    dialog = _dialog(qtbot, fake_orch)

    assert dialog.model == MODEL
    assert MODEL in dialog.windowTitle()


def test_the_preview_names_the_model(qtbot: QtBot, fake_orch: MagicMock) -> None:
    dialog = _dialog(qtbot, fake_orch)
    dialog._use_global.setChecked(False)

    dialog._editor._refresh_preview()

    preview = dialog._editor._preview.toPlainText()
    assert preview.startswith(f"# {MODEL}")
    assert dialog._status.text(), "the dialog explains which mode it is in"
