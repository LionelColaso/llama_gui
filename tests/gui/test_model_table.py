"""ModelTable widget and its byte-size formatting."""

from __future__ import annotations

from typing import Any

from pytestqt.qtbot import QtBot

from app.gui.widgets.model_table import ModelTable, _format_size


def _item_text(table: ModelTable, row: int, col: int) -> str:
    item = table.item(row, col)
    assert item is not None
    return item.text()


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
        assert _format_size(1024**5) == "1024.0 TB"
