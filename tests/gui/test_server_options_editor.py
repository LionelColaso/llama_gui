"""The shared server-options editor: rows, filter, preview, browse."""

from __future__ import annotations

from typing import cast
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtWidgets import QWidget
from pytestqt.qtbot import QtBot

from app.gui.widgets.server_options_editor import ServerOptionsEditor, _PathEdit
from app.serverargs import SERVER_ARGS, ArgKind, ServerArg


def _editor(qtbot: QtBot, fake_orch: MagicMock) -> ServerOptionsEditor:
    editor = ServerOptionsEditor(fake_orch)
    qtbot.addWidget(editor)
    return editor


def _first_cell(editor: ServerOptionsEditor, row: int) -> str:
    item = editor._table.item(row, 0)
    assert item is not None
    return item.text()


def _visible_rows(editor: ServerOptionsEditor) -> list[int]:
    return [
        row
        for row in range(editor._table.rowCount())
        if not editor._table.isRowHidden(row)
    ]


# ─── Path rows: the Browse button ───────────────────────────────


def test_browse_picks_a_directory(qtbot: QtBot, fake_orch: MagicMock) -> None:
    editor = _editor(qtbot, fake_orch)
    path_edit = cast(_PathEdit, editor.row("--video-ffmpeg-dir"))

    with patch(
        "app.gui.widgets.server_options_editor.QFileDialog.getExistingDirectory",
        return_value="/some/dir",
    ):
        path_edit._browse()

    assert path_edit.value() == "/some/dir"


def test_browse_picks_a_file(qtbot: QtBot, fake_orch: MagicMock) -> None:
    editor = _editor(qtbot, fake_orch)
    path_edit = cast(_PathEdit, editor.row("--lora"))

    with patch(
        "app.gui.widgets.server_options_editor.QFileDialog.getOpenFileName",
        return_value=("/some/adapter.safetensors", ""),
    ):
        path_edit._browse()

    assert path_edit.value() == "/some/adapter.safetensors"


def test_browse_with_nothing_chosen_leaves_the_row_alone(
    qtbot: QtBot, fake_orch: MagicMock
) -> None:
    editor = _editor(qtbot, fake_orch)
    path_edit = cast(_PathEdit, editor.row("--lora"))
    path_edit.setValue("/already/there.gguf")

    with patch(
        "app.gui.widgets.server_options_editor.QFileDialog.getOpenFileName",
        return_value=("", ""),
    ):
        path_edit._browse()

    assert path_edit.value() == "/already/there.gguf"


# ─── Read-only mode ─────────────────────────────────────────────


def test_is_read_only_round_trips(qtbot: QtBot, fake_orch: MagicMock) -> None:
    editor = _editor(qtbot, fake_orch)

    assert editor.is_read_only() is False
    editor.set_read_only(True)
    assert editor.is_read_only() is True


# ─── Unknown rows ───────────────────────────────────────────────


def test_row_of_an_unknown_flag_raises(qtbot: QtBot, fake_orch: MagicMock) -> None:
    editor = _editor(qtbot, fake_orch)

    with pytest.raises(KeyError):
        editor.row("--no-such-flag")


def test_a_foreign_editor_widget_reads_as_empty(
    qtbot: QtBot, fake_orch: MagicMock
) -> None:
    """Only combos, path edits and line edits exist — anything else reads ''."""
    editor = _editor(qtbot, fake_orch)
    stranger = ServerArg("--stranger", "server", ArgKind.STRING, "A stranger.")
    editor._rows.append((stranger, QWidget()))

    assert editor.value_of("--stranger") == ""
    editor._set_editor_value(QWidget(), "ignored")


# ─── Collection: validation ─────────────────────────────────────


def test_collect_global_rejects_a_non_integer_port(
    qtbot: QtBot, fake_orch: MagicMock
) -> None:
    editor = _editor(qtbot, fake_orch)
    editor.set_value_of("--port", "abc")

    with pytest.raises(ValueError, match="--port expects an integer"):
        editor.collect_global()


def test_collect_global_rejects_a_non_integer_ctx(
    qtbot: QtBot, fake_orch: MagicMock
) -> None:
    editor = _editor(qtbot, fake_orch)
    editor.set_value_of("--ctx-size", "big")

    with pytest.raises(ValueError, match="--ctx-size expects an integer"):
        editor.collect_global()


# ─── Preview ────────────────────────────────────────────────────


def test_the_preview_pending_slot_refreshes(qtbot: QtBot, fake_orch: MagicMock) -> None:
    editor = _editor(qtbot, fake_orch)

    editor._preview_pending()

    assert "llama-server" in editor._preview.toPlainText()


def test_the_preview_reports_invalid_input_instead_of_crashing(
    qtbot: QtBot, fake_orch: MagicMock
) -> None:
    editor = _editor(qtbot, fake_orch)
    editor.set_value_of("--port", "abc")

    editor._refresh_preview()

    assert editor._preview.toPlainText().startswith("invalid:")


def test_the_preview_degrades_to_a_placeholder_without_a_model(
    qtbot: QtBot, fake_orch: MagicMock
) -> None:
    editor = _editor(qtbot, fake_orch)
    fake_orch._resolve_model_path.side_effect = RuntimeError("gone")

    editor._refresh_preview()

    assert "<model>" in editor._preview.toPlainText()


# ─── Filtering ──────────────────────────────────────────────────


def test_the_search_filter_hides_non_matching_rows(
    qtbot: QtBot, fake_orch: MagicMock
) -> None:
    editor = _editor(qtbot, fake_orch)

    editor._search.setText("lookup-cache-static")
    editor._apply_filter()

    visible = _visible_rows(editor)
    assert [_first_cell(editor, row) for row in visible] == ["--lookup-cache-static"]
    assert editor._count_label.text() == "1 options"


def test_the_search_filter_finds_rows_by_alias(
    qtbot: QtBot, fake_orch: MagicMock
) -> None:
    editor = _editor(qtbot, fake_orch)

    editor._search.setText("--spec-draft-model")
    editor._apply_filter()

    visible = _visible_rows(editor)
    assert [_first_cell(editor, row) for row in visible] == ["--model-draft"]


def test_the_search_filter_finds_rows_by_help_text(
    qtbot: QtBot, fake_orch: MagicMock
) -> None:
    editor = _editor(qtbot, fake_orch)

    editor._search.setText("Path to LoRA adapter")
    editor._apply_filter()

    visible = _visible_rows(editor)
    assert [_first_cell(editor, row) for row in visible] == ["--lora"]


def test_the_section_filter_hides_other_sections(
    qtbot: QtBot, fake_orch: MagicMock
) -> None:
    editor = _editor(qtbot, fake_orch)
    sampling = {arg.flag for arg in SERVER_ARGS if arg.section == "sampling"}

    editor._section_combo.setCurrentText("sampling")
    editor._apply_filter()

    visible = _visible_rows(editor)
    assert visible, "the sampling section has rows"
    assert len(visible) < editor._table.rowCount(), "other sections are hidden"
    for row in visible:
        assert _first_cell(editor, row) in sampling


def test_clearing_the_search_shows_everything_again(
    qtbot: QtBot, fake_orch: MagicMock
) -> None:
    editor = _editor(qtbot, fake_orch)
    editor._search.setText("--lora")

    editor._search.setText("")
    editor._apply_filter()

    assert _visible_rows(editor) == list(range(editor._table.rowCount()))
