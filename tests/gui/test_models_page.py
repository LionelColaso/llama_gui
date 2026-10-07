"""The Models tab: the library and the interrupted downloads share one page."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from pytestqt.qtbot import QtBot

from app.gui.pages.models import ModelsPage


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


def test_models_page_hosts_library_and_downloads(
    qtbot: QtBot, fake_orch: MagicMock
) -> None:
    page = ModelsPage(fake_orch)
    qtbot.addWidget(page)

    assert page.models._table is not None
    assert page.downloads is not None


def test_downloads_section_renders_pending(qtbot: QtBot, fake_orch: MagicMock) -> None:
    page = ModelsPage(fake_orch)
    qtbot.addWidget(page)

    payload = {
        "tasks": [
            _task("model", "model.gguf", "/m/model.gguf", 300, 1000),
            _task("backend", "llama-bin.zip", "/d/llama-bin.zip", 2000, 2000),
        ]
    }
    page.downloads._on_list(payload)
    assert len(page.downloads._rows) == 2
    assert page.downloads._rows[0].task["kind"] == "model"
    assert page.downloads._empty_label.isHidden()  # hidden once tasks exist


def test_downloads_section_shows_empty_state(
    qtbot: QtBot, fake_orch: MagicMock
) -> None:
    page = ModelsPage(fake_orch)
    qtbot.addWidget(page)
    page.downloads._on_list({"tasks": []})
    assert not page.downloads._rows
    assert not page.downloads._empty_label.isHidden()  # visible when nothing pending


def test_discard_task_forwards_dest(qtbot: QtBot, fake_orch: MagicMock) -> None:
    page = ModelsPage(fake_orch)
    qtbot.addWidget(page)
    task = _task("model", "model.gguf", "/m/model.gguf", 5, 10)
    page.downloads._discard_task(task)
    assert fake_orch.discard_download.called
