"""The Server options page: the global configuration, plus one model scope."""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import MagicMock

from PySide6.QtWidgets import QLineEdit
from pytestqt.qtbot import QtBot

from app.gui.pages.server_args import (
    GLOBAL_SCOPE_LABEL,
    ServerArgsPage,
)
from app.gui.widgets.server_options_editor import (
    EXTRA_ARGS_FLAG,
    GLOBAL_ONLY_FLAGS,
)
from app.serverargs import ArgKind, ServerArg

CONTEXT_FLAG = "--ctx-size"


def _editor(page: ServerArgsPage, flag: str) -> Any:
    """The row's editor widget (combo, line edit or path picker)."""
    return page._editor.row(flag)


def _set_value(page: ServerArgsPage, flag: str, value: str) -> None:
    page._editor.set_value_of(flag, value)


def _value(page: ServerArgsPage, flag: str) -> str:
    """The row's text, with "(default)" read as *not set*."""
    return page._editor.value_of(flag)


def _saved(fake_orch: MagicMock) -> dict[str, Any]:
    return cast("dict[str, Any]", fake_orch.save_config.call_args.args[0])


def test_the_page_starts_on_the_global_scope(
    qtbot: QtBot, fake_orch: MagicMock
) -> None:
    page = ServerArgsPage(fake_orch)
    qtbot.addWidget(page)

    assert page._scope is None
    assert page._scope_combo.currentText() == GLOBAL_SCOPE_LABEL


def test_the_global_scope_saves_the_global_map(
    qtbot: QtBot, fake_orch: MagicMock
) -> None:
    page = ServerArgsPage(fake_orch)
    qtbot.addWidget(page)

    _set_value(page, "--jinja", "on")
    page._save()

    assert _saved(fake_orch)["server_options"] == {"--jinja": "on"}


def test_a_model_scope_offers_the_model(qtbot: QtBot, fake_orch: MagicMock) -> None:
    page = ServerArgsPage(fake_orch)
    qtbot.addWidget(page)

    page.set_scope("big.gguf")

    assert page._scope == "big.gguf"
    assert page._scope_combo.currentText() == "big.gguf"
    assert "big.gguf" in page._intro.text()


def test_a_model_scope_follows_the_global_values_until_it_has_its_own(
    qtbot: QtBot, fake_orch: MagicMock
) -> None:
    """A model with no own config shows the defaults it would run with."""
    fake_orch.cfg.ctx_size = 4096
    fake_orch.cfg.server_options = {"--jinja": "on"}
    page = ServerArgsPage(fake_orch)
    qtbot.addWidget(page)

    page.set_scope("big.gguf")

    assert _value(page, CONTEXT_FLAG) == "4096"
    assert _value(page, "--jinja") == "on"
    assert "follows the global defaults" in page._intro.text()


def test_a_model_with_its_own_config_shows_only_its_own_values(
    qtbot: QtBot, fake_orch: MagicMock
) -> None:
    """Its own config decides: an absent flag is the binary default, not the global."""
    fake_orch.cfg.ctx_size = 4096
    fake_orch.cfg.server_options = {"--jinja": "on"}
    fake_orch.cfg.model_server_options = {"big.gguf": {"--flash-attn": "on"}}
    page = ServerArgsPage(fake_orch)
    qtbot.addWidget(page)

    page.set_scope("big.gguf")

    assert _value(page, "--flash-attn") == "on"
    assert _value(page, "--jinja") == "", "the global default must not leak in"
    assert _value(page, CONTEXT_FLAG) == "", "no ctx of its own means 'auto'"
    assert "runs on the values below" in page._intro.text()


def test_saving_a_model_scope_never_touches_the_globals(
    qtbot: QtBot, fake_orch: MagicMock
) -> None:
    fake_orch.cfg.ctx_size = 4096
    page = ServerArgsPage(fake_orch)
    qtbot.addWidget(page)
    page.set_scope("big.gguf")

    _set_value(page, CONTEXT_FLAG, "16384")
    page._save()

    fake_orch.save_model_server_config.assert_called_once_with(
        "big.gguf", {"--ctx-size": "16384"}
    )
    fake_orch.save_config.assert_not_called()


def test_another_models_config_is_preserved(qtbot: QtBot, fake_orch: MagicMock) -> None:
    fake_orch.cfg.model_server_options = {"small.gguf": {"--jinja": "on"}}
    page = ServerArgsPage(fake_orch)
    qtbot.addWidget(page)
    page.set_scope("big.gguf")

    _set_value(page, CONTEXT_FLAG, "16384")
    page._save()

    options = cast(
        "dict[str, str]",
        fake_orch.save_model_server_config.call_args.args[1],
    )
    assert options == {"--ctx-size": "16384"}


def test_a_cosmetic_difference_is_not_stored(
    qtbot: QtBot, fake_orch: MagicMock
) -> None:
    """ "all" and 999 both mean "as many as fit", so only one is written."""
    fake_orch.cfg.ctx_size = -1
    fake_orch.cfg.n_gpu_layers = -1
    page = ServerArgsPage(fake_orch)
    qtbot.addWidget(page)
    page.set_scope("big.gguf")

    _set_value(page, "--n-gpu-layers", "all")
    page._save()

    options = cast(
        "dict[str, str]",
        fake_orch.save_model_server_config.call_args.args[1],
    )
    assert options == {}, "auto/all is the same as not setting it"


def test_the_port_row_is_locked_in_a_model_scope(
    qtbot: QtBot, fake_orch: MagicMock
) -> None:
    """The app probes one host/port for health, status and stop."""
    page = ServerArgsPage(fake_orch)
    qtbot.addWidget(page)

    page.set_scope("big.gguf")

    assert _editor(page, "--port").isEnabled() is False
    assert _editor(page, EXTRA_ARGS_FLAG).isEnabled() is False

    page.set_scope(None)
    assert _editor(page, "--port").isEnabled() is True
    assert "--host" in GLOBAL_ONLY_FLAGS


def test_the_preview_shows_the_models_command_line(
    qtbot: QtBot, fake_orch: MagicMock
) -> None:
    fake_orch.cfg.ctx_size = 4096
    page = ServerArgsPage(fake_orch)
    qtbot.addWidget(page)

    page.set_scope("big.gguf")
    _set_value(page, CONTEXT_FLAG, "16384")
    page._editor._refresh_preview()

    preview = page._editor._preview.toPlainText()
    assert "big.gguf" in preview
    assert "-c 16384" in preview


def test_reset_clears_the_models_own_values(qtbot: QtBot, fake_orch: MagicMock) -> None:
    """A reset is 'no values', not 'go back to the globals'."""
    page = ServerArgsPage(fake_orch)
    qtbot.addWidget(page)
    page.set_scope("big.gguf")

    page._reset_all()

    fake_orch.reset_model_server_config.assert_called_once_with("big.gguf")


def test_reset_in_the_global_scope_only_clears_the_form(
    qtbot: QtBot, fake_orch: MagicMock
) -> None:
    fake_orch.cfg.server_options = {"--jinja": "on"}
    page = ServerArgsPage(fake_orch)
    qtbot.addWidget(page)
    _set_value(page, "--jinja", "on")

    page._reset_all()

    assert _value(page, "--jinja") == ""
    fake_orch.save_config.assert_not_called()


def test_switching_scope_shows_that_scopes_values(
    qtbot: QtBot, fake_orch: MagicMock
) -> None:
    fake_orch.cfg.server_options = {"--jinja": "on"}
    fake_orch.cfg.model_server_options = {"big.gguf": {"--jinja": "off"}}
    page = ServerArgsPage(fake_orch)
    qtbot.addWidget(page)

    page.set_scope("big.gguf")
    assert _value(page, "--jinja") == "off"

    page.set_scope(None)
    assert _value(page, "--jinja") == "on"


def test_the_scope_selector_drives_the_scope(
    qtbot: QtBot, fake_orch: MagicMock
) -> None:
    """The combo box — not just set_scope — switches what is edited."""
    fake_orch.cfg.model_server_options = {"big.gguf": {"--jinja": "on"}}
    page = ServerArgsPage(fake_orch)
    qtbot.addWidget(page)

    page._scope_combo.setCurrentText("big.gguf")
    assert page._scope == "big.gguf"

    page._scope_combo.setCurrentText(GLOBAL_SCOPE_LABEL)
    assert page._scope is None


def test_the_error_slot_names_the_error(qtbot: QtBot, fake_orch: MagicMock) -> None:
    page = ServerArgsPage(fake_orch)
    qtbot.addWidget(page)

    page._on_error("boom")

    assert page._status_label.text() == "Error: boom"


def test_saving_an_invalid_port_names_the_error(
    qtbot: QtBot, fake_orch: MagicMock
) -> None:
    page = ServerArgsPage(fake_orch)
    qtbot.addWidget(page)
    _set_value(page, "--port", "abc")

    page._save()

    assert "--port expects an integer" in page._status_label.text()
    fake_orch.save_config.assert_not_called()


def test_saving_an_unknown_option_is_rejected(
    qtbot: QtBot, fake_orch: MagicMock
) -> None:
    """A row the catalogue does not know: nothing is written."""
    page = ServerArgsPage(fake_orch)
    qtbot.addWidget(page)
    page._editor._rows.append(
        (
            ServerArg("--bogus", "server", ArgKind.STRING, "Bogus."),
            QLineEdit("value"),
        )
    )

    page._save()

    assert "unknown option '--bogus'" in page._status_label.text()
    fake_orch.save_config.assert_not_called()
