"""Launching with no active model: the picker, and when it is needed at all."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from typing import Any
from unittest.mock import patch

import pytest
from PySide6.QtWidgets import QDialog
from pytestqt.qtbot import QtBot

from app.gui.dialogs.model_picker import ModelPickerDialog
from app.gui.sections.backends import BackendsSection
from app.schemas import EngineError, ExitCode, ModelInfo, ModelsData

MODELS = [
    {"name": "a/big.gguf", "size_bytes": 4 * 1024**3, "modified": "2026-01-02"},
    {"name": "small.gguf", "size_bytes": 1024**2, "modified": "2026-01-03"},
]


def _library(names: list[str]) -> ModelsData:
    return ModelsData(dir="C:/models", models=[ModelInfo(name=n) for n in names])


def _orch_with(*names: str) -> Any:
    """An orchestrator with no active model and ``names`` in the library."""
    from unittest.mock import MagicMock

    orch = MagicMock()
    orch.cfg.active_model = ""
    orch.list_models.return_value = _library(list(names))
    return orch


@contextmanager
def _recorded_launches(section: BackendsSection) -> Generator[list[str]]:
    """Capture the actions ``section`` starts instead of launching anything."""
    started: list[str] = []

    def _record(_self: object, action: str, *_args: object, **_kwargs: object) -> None:
        started.append(action)

    with patch.object(BackendsSection, "_start", autospec=True) as start:
        start.side_effect = _record
        yield started


def _launch(section: BackendsSection, *, accepted: bool) -> list[str]:
    """Click Launch with the picker stubbed to accept (or cancel), and report
    which actions it went on to start."""
    with (
        _recorded_launches(section) as started,
        patch.object(ModelPickerDialog, "exec", return_value=1 if accepted else 0),
    ):
        section._do_launch()
    return started


class TestModelPickerDialog:
    def test_the_first_model_is_preselected(self, qtbot: QtBot) -> None:
        dialog = ModelPickerDialog(MODELS)
        qtbot.addWidget(dialog)

        assert dialog.selected_model() == "a/big.gguf"

    def test_a_row_returns_its_full_nested_name(self, qtbot: QtBot) -> None:
        dialog = ModelPickerDialog(MODELS)
        qtbot.addWidget(dialog)

        dialog._table.selectRow(1)

        assert dialog.selected_model() == "small.gguf"

    def test_the_row_label_is_the_file_name(self, qtbot: QtBot) -> None:
        dialog = ModelPickerDialog(MODELS)
        qtbot.addWidget(dialog)

        label = dialog._table.item(0, 0)
        assert label is not None
        assert label.text() == "big.gguf"

    def test_the_filter_matches_the_whole_path(self, qtbot: QtBot) -> None:
        dialog = ModelPickerDialog(MODELS)
        qtbot.addWidget(dialog)

        dialog._search.setText("a/big")

        assert dialog._table.isRowHidden(0) is False
        assert dialog._table.isRowHidden(1) is True

    def test_a_row_without_a_name_is_skipped(self, qtbot: QtBot) -> None:
        dialog = ModelPickerDialog([{"name": ""}, {"name": "ok.gguf"}])
        qtbot.addWidget(dialog)

        assert dialog._table.rowCount() == 1
        assert dialog.selected_model() == "ok.gguf"

    def test_a_size_it_cannot_read_is_shown_as_zero(self, qtbot: QtBot) -> None:
        dialog = ModelPickerDialog([{"name": "a.gguf", "size_bytes": "huge"}])
        qtbot.addWidget(dialog)

        size = dialog._table.item(0, 1)
        assert size is not None
        assert size.text() == "0 B"

    def test_an_empty_library_preselects_nothing(self, qtbot: QtBot) -> None:
        dialog = ModelPickerDialog([])
        qtbot.addWidget(dialog)

        assert dialog._table.rowCount() == 0
        assert dialog.selected_model() is None

    def test_a_double_click_is_a_choice(self, qtbot: QtBot) -> None:
        dialog = ModelPickerDialog(MODELS)
        qtbot.addWidget(dialog)

        item = dialog._table.item(0, 0)
        assert item is not None
        dialog._on_double_clicked(item)

        assert dialog.result() == QDialog.DialogCode.Accepted


class TestLaunchNeedsAModel:
    """The engine refuses to guess; the section asks instead of forwarding it."""

    def test_several_models_and_no_active_one_ask(self, qtbot: QtBot) -> None:
        section = BackendsSection(_orch_with("a.gguf", "b.gguf"))
        qtbot.addWidget(section)

        assert len(section._models_needing_a_choice()) == 2

    def test_an_active_model_means_no_question(self, qtbot: QtBot) -> None:
        orch = _orch_with("a.gguf", "b.gguf")
        orch.cfg.active_model = "a.gguf"
        section = BackendsSection(orch)
        qtbot.addWidget(section)

        assert section._models_needing_a_choice() == []

    def test_a_single_model_needs_no_question(self, qtbot: QtBot) -> None:
        """The engine launches the only model on its own, so a dialog would be a
        pointless extra click."""
        section = BackendsSection(_orch_with("only.gguf"))
        qtbot.addWidget(section)

        assert section._models_needing_a_choice() == []

    def test_choosing_a_model_makes_it_active_then_launches(self, qtbot: QtBot) -> None:
        orch = _orch_with("a.gguf", "b.gguf")
        section = BackendsSection(orch)
        qtbot.addWidget(section)

        started = _launch(section, accepted=True)

        orch.set_active_model.assert_called_once_with("a.gguf")
        assert started == ["launch"]
        assert "a.gguf" in section._status_label.text()

    def test_cancelling_the_picker_launches_nothing(self, qtbot: QtBot) -> None:
        orch = _orch_with("a.gguf", "b.gguf")
        section = BackendsSection(orch)
        qtbot.addWidget(section)

        started = _launch(section, accepted=False)

        orch.set_active_model.assert_not_called()
        assert started == []
        assert "cancelled" in section._status_label.text()

    def test_restart_asks_the_same_question(self, qtbot: QtBot) -> None:
        section = BackendsSection(_orch_with("a.gguf", "b.gguf"))
        qtbot.addWidget(section)

        with (
            _recorded_launches(section) as started,
            patch.object(ModelPickerDialog, "exec", return_value=0),
        ):
            section._do_restart()

        assert started == [], "restart needs a model just as launch does"

    @pytest.mark.parametrize(
        "broken", [False, True], ids=["already-active", "bad-list"]
    )
    def test_no_picker_when_the_launch_needs_no_decision(
        self, qtbot: QtBot, broken: bool
    ) -> None:
        """An active model, or a library too broken to list, launches straight
        away — a dialog over either would be noise."""
        orch = _orch_with("a.gguf", "b.gguf")
        if broken:
            orch.list_models.side_effect = EngineError(
                ExitCode.NOT_AVAILABLE, "models dir unreadable"
            )
        else:
            orch.cfg.active_model = "a.gguf"
        section = BackendsSection(orch)
        qtbot.addWidget(section)

        with (
            _recorded_launches(section) as started,
            patch.object(ModelPickerDialog, "exec") as exec_,
        ):
            section._do_launch()

        exec_.assert_not_called()
        assert started == ["launch"]

    def test_the_models_carry_size_and_modified(self, qtbot: QtBot) -> None:
        """The picker reads a real ModelsData payload, not a hand-built dict."""
        orch = _orch_with("a.gguf", "b.gguf")
        orch.list_models.return_value = ModelsData(
            dir="C:/models",
            models=[
                ModelInfo(name="a.gguf", size_bytes=1024**3, modified="2026-01-01"),
                ModelInfo(name="b.gguf"),
            ],
        )
        section = BackendsSection(orch)
        qtbot.addWidget(section)

        rows = section._models_needing_a_choice()

        assert rows[0]["size_bytes"] == 1024**3
