"""The Models page: table wiring and the deferred resume prompt."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from PySide6.QtWidgets import QMessageBox
from pytestqt.qtbot import QtBot

from llamagui.gui.pages.models import ModelsPage
from llamagui.gui.widgets.model_table import ModelTable


def _item_text(table: ModelTable, row: int, col: int) -> str:
    item = table.item(row, col)
    assert item is not None
    return item.text()


def _stub_resume_question(monkeypatch: pytest.MonkeyPatch, asked: list[bool]) -> None:
    """Record any modal resume prompt, answering "No" so nothing starts."""

    def _question(*args: object, **kwargs: object) -> QMessageBox.StandardButton:
        asked.append(True)
        return QMessageBox.StandardButton.No

    monkeypatch.setattr("llamagui.gui.pages.models.QMessageBox.question", _question)


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

    def test_resume_prompt_is_not_shown_during_construction(
        self, qtbot: QtBot, fake_orch: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Regression: the modal prompt used to fire from __init__.

        A QMessageBox shown while the widget tree (and MainWindow itself) is
        still being built is fragile and made startup depend on a yes/no
        answer. It must be deferred to the first show.
        """
        asked: list[bool] = []
        _stub_resume_question(monkeypatch, asked)

        ModelsPage(fake_orch)  # must not prompt

        assert asked == [], "the resume prompt must not run in the constructor"

    def test_resume_prompt_runs_once_on_first_show(
        self, qtbot: QtBot, fake_orch: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        offered: list[bool] = []

        page = ModelsPage(fake_orch)
        qtbot.addWidget(page)
        monkeypatch.setattr(page, "_offer_resume", lambda: offered.append(True))

        page.show()
        qtbot.wait(50)
        page.show()  # already shown once; must not prompt again
        qtbot.wait(50)

        assert offered == [True], "the prompt should happen on first show, once"
