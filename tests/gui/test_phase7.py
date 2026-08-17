from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from PySide6.QtWidgets import QComboBox, QLineEdit
from pytestqt.qtbot import QtBot

from llamagui.gui.pages.models import ModelsPage
from llamagui.gui.pages.server_args import ServerArgsPage, _PathEdit
from llamagui.gui.pages.settings import SettingsPage
from llamagui.gui.widgets.model_table import ModelTable, _format_size


def _item_text(table: ModelTable, row: int, col: int) -> str:
    item = table.item(row, col)
    assert item is not None
    return item.text()


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


class TestModelTable:
    def test_empty(self, qtbot: QtBot) -> None:
        mt = ModelTable()
        qtbot.addWidget(mt)
        assert mt.rowCount() == 0
        assert mt.selected_name() is None

    def test_load_shows_rows_and_active(self, qtbot: QtBot) -> None:
        mt = ModelTable()
        qtbot.addWidget(mt)
        models: list[dict[str, Any]] = [
            {"name": "a.gguf", "size_bytes": 1024, "modified": "2026-01-01 00:00"},
            {"name": "b.gguf", "size_bytes": 2048, "modified": "2026-01-02 00:00"},
        ]
        mt.load_models(models, active="b.gguf")
        assert mt.rowCount() == 2
        assert _item_text(mt, 0, 0) == "a.gguf"
        assert _item_text(mt, 1, 0) == "b.gguf"
        assert _item_text(mt, 0, 3) == ""
        assert _item_text(mt, 1, 3) == "active"

    def test_selected_name(self, qtbot: QtBot) -> None:
        mt = ModelTable()
        qtbot.addWidget(mt)
        mt.load_models(
            [
                {"name": "a.gguf", "size_bytes": 1, "modified": ""},
                {"name": "b.gguf", "size_bytes": 2, "modified": ""},
            ]
        )
        mt.selectRow(1)
        assert mt.selected_name() == "b.gguf"


class TestFormatSize:
    def test_units(self) -> None:
        assert _format_size(512) == "512 B"
        assert _format_size(2048) == "2.0 KB"
        assert _format_size(5 * 1024 * 1024) == "5.0 MB"
        assert _format_size(1536 * 1024 * 1024) == "1.5 GB"


class TestModelsPage:
    def test_creates(self, qtbot: QtBot, fake_orch: MagicMock) -> None:
        page = ModelsPage(fake_orch)
        qtbot.addWidget(page)
        assert page._table is not None
        assert page._dir_label is not None
        assert page._server_label is not None

    def test_on_list_renders_models(self, qtbot: QtBot, fake_orch: MagicMock) -> None:
        page = ModelsPage(fake_orch)
        qtbot.addWidget(page)
        page._on_list(
            {
                "dir": "/models",
                "models": [{"name": "a.gguf", "size_bytes": 10, "modified": "now"}],
                "active": "a.gguf",
            }
        )
        assert page._table.rowCount() == 1
        assert _item_text(page._table, 0, 0) == "a.gguf"


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

    def test_collect_includes_server_options(
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
