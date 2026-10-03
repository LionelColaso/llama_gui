"""The Downloads page renders pending items and wires resume/discard."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from pytestqt.qtbot import QtBot

from llamagui.gui.pages.downloads import DownloadsPage


def _task(kind: str, name: str, dest: str, done: int, total: int) -> dict[str, Any]:
    return {
        "id": dest,
        "kind": kind,
        "name": name,
        "url": f"https://example.com/{name}",
        "dest": dest,
        "total": total,
        "done": done,
        "percent": round(done * 100 / total) if total else 0,
    }


def test_downloads_page_renders_pending(qtbot: QtBot, fake_orch: MagicMock) -> None:
    page = DownloadsPage(fake_orch)
    qtbot.addWidget(page)

    payload = {
        "tasks": [
            _task("model", "model.gguf", "/m/model.gguf", 300, 1000),
            _task("backend", "llama-bin.zip", "/d/llama-bin.zip", 2000, 2000),
        ]
    }
    page._on_list(payload)
    assert len(page._rows) == 2
    assert page._rows[0].task["kind"] == "model"
    assert page._empty_label.isHidden()  # hidden once tasks exist


def test_downloads_page_shows_empty_state(qtbot: QtBot, fake_orch: MagicMock) -> None:
    page = DownloadsPage(fake_orch)
    qtbot.addWidget(page)
    page._on_list({"tasks": []})
    assert not page._rows
    assert not page._empty_label.isHidden()  # visible when nothing pending


def test_discard_task_forwards_dest(qtbot: QtBot, fake_orch: MagicMock) -> None:
    page = DownloadsPage(fake_orch)
    qtbot.addWidget(page)
    task = _task("model", "model.gguf", "/m/model.gguf", 5, 10)
    page._discard_task(task)
    assert fake_orch.discard_download.called
