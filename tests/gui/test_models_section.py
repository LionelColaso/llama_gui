"""The Models page: table wiring and the deferred resume prompt."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from PySide6.QtWidgets import QMessageBox, QPushButton
from pytestqt.qtbot import QtBot

from app.gui.sections.models import ModelsSection, _ignore_error
from app.gui.widgets.model_table import ModelTable
from tests.gui._pool_recorder import record_workers


def _item_text(table: ModelTable, row: int, col: int) -> str:
    item = table.item(row, col)
    assert item is not None
    return item.text()


def _stub_resume_question(monkeypatch: pytest.MonkeyPatch, asked: list[bool]) -> None:
    """Record any modal resume prompt, answering "No" so nothing starts."""

    def _question(*args: object, **kwargs: object) -> QMessageBox.StandardButton:
        asked.append(True)
        return QMessageBox.StandardButton.No

    monkeypatch.setattr("app.gui.sections.models.QMessageBox.question", _question)


def _record_workers(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Route every worker the section starts into one list."""
    return record_workers(
        monkeypatch,
        "app.gui.sections.models.WorkerPool",
        "app.gui.download_actions.WorkerPool",
    )


def _resumable_without_url(_models_dir: Path) -> list[dict[str, object]]:
    return [{"total": 100}]


def _resumable_with_url(_models_dir: Path) -> list[dict[str, object]]:
    return [{"url": "https://x/big.gguf", "total": 1000}]


def _stub_question(
    monkeypatch: pytest.MonkeyPatch, answer: QMessageBox.StandardButton
) -> None:
    monkeypatch.setattr(
        "app.gui.sections.models.QMessageBox.question",
        staticmethod(lambda *args, **kwargs: answer),
    )


def _stub_get_text(monkeypatch: pytest.MonkeyPatch, answer: tuple[str, bool]) -> None:
    class _FakeInputDialog:
        @staticmethod
        def getText(*args: object, **kwargs: object) -> tuple[str, bool]:
            return answer

    monkeypatch.setattr("app.gui.sections.models.QInputDialog", _FakeInputDialog)


def _load_one_model(page: ModelsSection) -> None:
    page._table.load_models([{"name": "big.gguf"}], active=None)
    page._table.selectRow(0)


class TestModelsSection:
    def test_creates(self, qtbot: QtBot, fake_orch: MagicMock) -> None:
        page = ModelsSection(fake_orch)
        qtbot.addWidget(page)
        assert page._table is not None
        assert page._dir_label is not None
        assert page._server_label is not None

    def test_on_list_renders_models(self, qtbot: QtBot, fake_orch: MagicMock) -> None:
        page = ModelsSection(fake_orch)
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

        A QMessageBox shown while the widget tree (and MainWindow itself)
        is still being built is fragile and made startup depend on a
        yes/no answer. It must be deferred to the first show.
        """
        asked: list[bool] = []
        _stub_resume_question(monkeypatch, asked)

        ModelsSection(fake_orch)  # must not prompt

        assert asked == [], "the resume prompt must not run in the constructor"

    def test_resume_prompt_runs_once_on_first_show(
        self, qtbot: QtBot, fake_orch: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        offered: list[bool] = []

        page = ModelsSection(fake_orch)
        qtbot.addWidget(page)
        monkeypatch.setattr(page, "_offer_resume", lambda: offered.append(True))

        page.show()
        qtbot.wait(50)
        page.hide()
        page.show()  # a revisit: the prompt must not fire again
        qtbot.wait(50)

        assert offered == [True], "the prompt should happen on first show, once"

    def test_server_options_button_asks_for_the_selected_model(
        self, qtbot: QtBot, fake_orch: MagicMock
    ) -> None:
        page = ModelsSection(fake_orch)
        qtbot.addWidget(page)
        page._table.load_models([{"name": "big.gguf"}], active=None)
        page._table.selectRow(0)

        asked: list[str] = []
        page.server_options_requested.connect(asked.append)
        page._server_options_btn.click()

        assert asked == ["big.gguf"]

    def test_server_options_button_needs_a_selection(
        self, qtbot: QtBot, fake_orch: MagicMock
    ) -> None:
        page = ModelsSection(fake_orch)
        qtbot.addWidget(page)
        page._table.load_models([{"name": "big.gguf"}], active=None)

        asked: list[str] = []
        page.server_options_requested.connect(asked.append)
        page._server_options_btn.click()

        assert asked == []
        assert "Select a model" in page._status_label.text()

    def test_every_action_button_is_in_the_layout(
        self, qtbot: QtBot, fake_orch: MagicMock
    ) -> None:
        """A built-but-unattached row is invisible: the buttons must be reachable."""
        page = ModelsSection(fake_orch)
        qtbot.addWidget(page)
        page.show()

        labels = sorted(
            btn.text() for btn in page.findChildren(QPushButton) if btn.parent() is page
        )
        assert labels == [
            "Download…",
            "Open folder",
            "Refresh",
            "Remove",
            "Server options…",
            "Set active",
        ]
        # And visible ones, so a hidden row cannot pass on a stray parent.
        assert all(
            btn.isVisible()
            for btn in page.findChildren(QPushButton)
            if btn.parent() is page
        )

    # ─── Resume prompt ───────────────────────────────────────

    def test_offer_resume_skips_tasks_without_a_url(
        self, qtbot: QtBot, fake_orch: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        page = ModelsSection(fake_orch)
        qtbot.addWidget(page)
        monkeypatch.setattr(
            "app.gui.sections.models.resumable_tasks",
            _resumable_without_url,
        )
        asked: list[bool] = []
        _stub_resume_question(monkeypatch, asked)
        started = _record_workers(monkeypatch)

        page._offer_resume()

        assert asked == [] and started == []

    def _resume_page(
        self,
        qtbot: QtBot,
        fake_orch: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
        answer: QMessageBox.StandardButton,
    ) -> tuple[ModelsSection, list[Any]]:
        """A models page with one resumable task and a stubbed prompt."""
        page = ModelsSection(fake_orch)
        qtbot.addWidget(page)
        monkeypatch.setattr(
            "app.gui.sections.models.resumable_tasks",
            _resumable_with_url,
        )
        _stub_question(monkeypatch, answer)
        return page, _record_workers(monkeypatch)

    def test_offer_resume_starts_a_worker_on_yes(
        self, qtbot: QtBot, fake_orch: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        page, started = self._resume_page(
            qtbot, fake_orch, monkeypatch, QMessageBox.StandardButton.Yes
        )

        page._offer_resume()

        assert len(started) == 1
        assert started[0]._action == "download_model"
        assert started[0]._kwargs["url"] == "https://x/big.gguf"
        assert "Resuming big.gguf" in page._progress._bar.format()

    def test_offer_resume_does_nothing_on_no(
        self, qtbot: QtBot, fake_orch: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        page, started = self._resume_page(
            qtbot, fake_orch, monkeypatch, QMessageBox.StandardButton.No
        )

        page._offer_resume()

        assert started == []

    def test_offer_resume_is_best_effort(
        self, qtbot: QtBot, fake_orch: MagicMock
    ) -> None:
        """A broken config must never make the prompt fatal."""
        page = ModelsSection(fake_orch)
        qtbot.addWidget(page)
        page._orch.cfg = MagicMock()  # Path() of this raises TypeError
        page._offer_resume()  # must not raise

    def test_offer_resume_survives_a_config_that_raises(
        self, qtbot: QtBot, fake_orch: MagicMock
    ) -> None:
        """A config property that blows up must not break the page."""

        class _BrokenCfg:
            @property
            def models_dir_path(self) -> str:
                raise RuntimeError("config is gone")

        page = ModelsSection(fake_orch)
        qtbot.addWidget(page)
        page._orch.cfg = _BrokenCfg()

        page._offer_resume()  # must not raise

    def test_on_set_active_done_without_a_pending_choice(
        self, qtbot: QtBot, fake_orch: MagicMock
    ) -> None:
        """A stale confirm without a pending pick says nothing."""
        page = ModelsSection(fake_orch)
        qtbot.addWidget(page)

        page._on_set_active_done({"models": [], "active": None})

        assert "Active model" not in page._status_label.text()

    # ─── Action slots ────────────────────────────────────────

    def test_do_download_cancel_starts_nothing(
        self, qtbot: QtBot, fake_orch: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        page = ModelsSection(fake_orch)
        qtbot.addWidget(page)
        _stub_get_text(monkeypatch, ("", False))
        started = _record_workers(monkeypatch)

        page._do_download()

        assert started == []

    def test_do_download_blank_url_starts_nothing(
        self, qtbot: QtBot, fake_orch: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        page = ModelsSection(fake_orch)
        qtbot.addWidget(page)
        _stub_get_text(monkeypatch, ("   ", True))
        started = _record_workers(monkeypatch)

        page._do_download()

        assert started == []

    def test_do_download_starts_a_worker(
        self, qtbot: QtBot, fake_orch: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        page = ModelsSection(fake_orch)
        qtbot.addWidget(page)
        _stub_get_text(monkeypatch, ("https://x/big.gguf", True))
        started = _record_workers(monkeypatch)

        page._do_download()

        assert len(started) == 1
        assert started[0]._action == "download_model"
        assert started[0]._kwargs["url"] == "https://x/big.gguf"
        assert "Downloading big.gguf" in page._progress._bar.format()

    def test_do_set_active_needs_a_selection(
        self, qtbot: QtBot, fake_orch: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        page = ModelsSection(fake_orch)
        qtbot.addWidget(page)
        started = _record_workers(monkeypatch)

        page._do_set_active()

        assert started == []
        assert "Select a model" in page._status_label.text()

    def test_do_set_active_starts_a_worker(
        self, qtbot: QtBot, fake_orch: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        page = ModelsSection(fake_orch)
        qtbot.addWidget(page)
        _load_one_model(page)
        started = _record_workers(monkeypatch)

        page._do_set_active()

        assert len(started) == 1
        assert started[0]._action == "set_active_model"
        assert started[0]._kwargs == {"name": "big.gguf"}

    def test_on_set_active_done_reports_the_model(
        self, qtbot: QtBot, fake_orch: MagicMock
    ) -> None:
        page = ModelsSection(fake_orch)
        qtbot.addWidget(page)
        page._pending_active = "big.gguf"

        page._on_set_active_done({"models": [], "active": "big.gguf"})

        assert "Active model: big.gguf" in page._status_label.text()

    def test_do_remove_needs_a_selection(
        self, qtbot: QtBot, fake_orch: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        page = ModelsSection(fake_orch)
        qtbot.addWidget(page)
        started = _record_workers(monkeypatch)

        page._do_remove()

        assert started == []
        assert "Select a model" in page._status_label.text()

    def test_do_remove_cancel_starts_nothing(
        self, qtbot: QtBot, fake_orch: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        page = ModelsSection(fake_orch)
        qtbot.addWidget(page)
        _load_one_model(page)
        _stub_question(monkeypatch, QMessageBox.StandardButton.No)
        started = _record_workers(monkeypatch)

        page._do_remove()

        assert started == []

    def test_do_remove_starts_a_worker(
        self, qtbot: QtBot, fake_orch: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        page = ModelsSection(fake_orch)
        qtbot.addWidget(page)
        _load_one_model(page)
        _stub_question(monkeypatch, QMessageBox.StandardButton.Yes)
        started = _record_workers(monkeypatch)

        page._do_remove()

        assert len(started) == 1
        assert started[0]._action == "remove_model"
        assert started[0]._kwargs == {"name": "big.gguf"}

    def test_open_folder_opens_the_models_directory(
        self, qtbot: QtBot, fake_orch: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        opened: list[str] = []

        class _FakeDesktopServices:
            @staticmethod
            def openUrl(url: object) -> None:
                opened.append(str(url))

        monkeypatch.setattr("PySide6.QtGui.QDesktopServices", _FakeDesktopServices)
        page = ModelsSection(fake_orch)
        qtbot.addWidget(page)

        page._open_folder()

        assert len(opened) == 1
        assert "models" in opened[0]

    def test_open_folder_without_a_models_directory(
        self, qtbot: QtBot, fake_orch: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No models directory configured: nothing to open."""
        opened: list[str] = []

        class _FakeDesktopServices:
            @staticmethod
            def openUrl(url: object) -> None:
                opened.append(str(url))

        monkeypatch.setattr("PySide6.QtGui.QDesktopServices", _FakeDesktopServices)

        class _NoDirCfg:
            models_dir_path = ""

        page = ModelsSection(fake_orch)
        qtbot.addWidget(page)
        page._orch.cfg = _NoDirCfg()

        page._open_folder()

        assert opened == []

    # ─── Signal slots ────────────────────────────────────────

    def test_on_downloaded_reports_and_reloads(
        self, qtbot: QtBot, fake_orch: MagicMock
    ) -> None:
        page = ModelsSection(fake_orch)
        qtbot.addWidget(page)
        reloaded: list[bool] = []
        page._load = lambda: reloaded.append(True)  # type: ignore[method-assign]

        page._on_downloaded({"name": "big.gguf", "size_bytes": 1234})

        assert "Downloaded big.gguf" in page._status_label.text()
        assert reloaded == [True]

    def test_on_error_shows_the_message(
        self, qtbot: QtBot, fake_orch: MagicMock
    ) -> None:
        page = ModelsSection(fake_orch)
        qtbot.addWidget(page)

        page._on_error("boom")

        assert "Error: boom" in page._status_label.text()

    def test_ignore_error_is_swallowed(self) -> None:
        _ignore_error("boom")  # must not raise

    # ─── Mixin progress slots ────────────────────────────────

    def test_on_progress_forwards_to_the_progress_widget(
        self, qtbot: QtBot, fake_orch: MagicMock
    ) -> None:
        page = ModelsSection(fake_orch)
        qtbot.addWidget(page)
        page._progress.start_operation("Working")

        page._on_progress(1, 10, "download", 0.1)

        assert "10%" in page._progress._bar.format()
        page._progress.finish_operation()

    def test_on_download_error_reports_the_failure(
        self, qtbot: QtBot, fake_orch: MagicMock
    ) -> None:
        page = ModelsSection(fake_orch)
        qtbot.addWidget(page)

        page._on_download_error("boom")

        assert "Download failed: boom" in page._status_label.text()
