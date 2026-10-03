"""The Settings and Server options pages: collecting and persisting values."""

from __future__ import annotations

from unittest.mock import MagicMock

from PySide6.QtWidgets import QComboBox, QLineEdit
from pytestqt.qtbot import QtBot

from app.gui.pages.server_args import ServerArgsPage, _PathEdit
from app.gui.pages.settings import SettingsPage


def _set_row_value(page: ServerArgsPage, flag: str, value: str) -> None:
    """Set ``value`` on the editor row for ``flag``, whatever its widget kind."""
    for arg, editor in page._rows:
        if arg.flag != flag:
            continue
        if isinstance(editor, QComboBox):
            index = editor.findText(value)
            if index >= 0:
                editor.setCurrentIndex(index)
        elif isinstance(editor, _PathEdit):
            editor.setValue(value)
        elif isinstance(editor, QLineEdit):
            editor.setText(value)
        return


class TestSettingsPage:
    def test_creates(self, qtbot: QtBot, fake_orch: MagicMock) -> None:
        page = SettingsPage(fake_orch)
        qtbot.addWidget(page)
        assert page._root_picker is not None

    def test_collect_round_trips_os_toggle(
        self, qtbot: QtBot, fake_orch: MagicMock
    ) -> None:
        page = SettingsPage(fake_orch)
        qtbot.addWidget(page)
        page._os_llama_check.setChecked(True)

        collected = page.collect()
        assert collected["use_os_llama_server"] is True
        # The legacy pointed-path model is gone from the settings form.
        assert "pointed" not in collected
        assert "source_priority" not in collected

    def test_save_persists_through_the_orchestrator(
        self, qtbot: QtBot, fake_orch: MagicMock
    ) -> None:
        page = SettingsPage(fake_orch)
        qtbot.addWidget(page)
        page._port_spin.setValue(9099)
        page._save()

        saved = fake_orch.save_config.call_args.args[0]
        assert saved["port"] == 9099
        # Unrelated settings travel with the save so nothing is dropped.
        assert "use_os_llama_server" in saved
        assert "pointed" not in saved


class TestServerArgsPage:
    def test_collect_includes_dedicated_and_extra_args(
        self, qtbot: QtBot, fake_orch: MagicMock
    ) -> None:
        page = ServerArgsPage(fake_orch)
        qtbot.addWidget(page)

        _set_row_value(page, "--ctx-size", "8192")
        _set_row_value(page, "--n-gpu-layers", "33")
        _set_row_value(page, "__extra_args__", "--threads 8")

        collected = page.collect()
        # ServerArgsPage returns dedicated fields separately
        assert collected["ctx_size"] == 8192
        assert collected["n_gpu_layers"] == 33
        assert collected["extra_server_args"] == "--threads 8"
        assert "listen_flag" not in collected
