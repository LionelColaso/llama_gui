"""The Logs page: the stdout/stderr tabs and the open-folder button."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import QUrl
from pytestqt.qtbot import QtBot

from app.gui.pages.logs import LogsPage


def test_the_page_shows_both_log_tabs(qtbot: QtBot, tmp_path: Path) -> None:
    page = LogsPage(tmp_path)
    qtbot.addWidget(page)

    assert page._tabs.count() == 2
    assert page._tabs.tabText(0) == "stdout"
    assert page._tabs.tabText(1) == "stderr"


def test_open_folder_opens_the_state_dir(
    qtbot: QtBot, tmp_path: Path, monkeypatch: Any
) -> None:
    opened: list[str] = []

    class _FakeDesktopServices:
        @staticmethod
        def openUrl(url: QUrl) -> bool:
            opened.append(url.toString())
            return True

    monkeypatch.setattr("PySide6.QtGui.QDesktopServices", _FakeDesktopServices)
    page = LogsPage(tmp_path)
    qtbot.addWidget(page)

    page._open_folder()

    assert opened == [QUrl.fromLocalFile(str(tmp_path)).toString()]
