"""The Downloads section: pending rows, resume/discard wiring."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from PySide6.QtWidgets import QLabel, QVBoxLayout
from pytestqt.qtbot import QtBot

from app.gui.sections.downloads import DownloadsSection, _PendingRow
from app.gui.widgets.progress_bar import _human
from tests.gui._pool_recorder import record_workers


def _noop(task: dict[str, Any]) -> None:
    """A do-nothing action callback for rows under construction."""


def _record_workers(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Route every worker the section (or its mixin) starts into one list."""
    return record_workers(
        monkeypatch,
        "app.gui.sections.downloads.WorkerPool",
        "app.gui.download_actions.WorkerPool",
    )


def _section(
    qtbot: QtBot, fake_orch: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> tuple[DownloadsSection, list[Any]]:
    started = _record_workers(monkeypatch)
    section = DownloadsSection(fake_orch)
    qtbot.addWidget(section)
    return section, started


def _row_texts(row: _PendingRow) -> list[str]:
    return [label.text() for label in row.findChildren(QLabel)]


# ─── Pending rows ───────────────────────────────────────────────


def test_a_row_with_a_total_shows_both_sizes() -> None:
    row = _PendingRow(
        {"kind": "model", "name": "a.gguf", "done": 500, "total": 1000},
        on_resume=_noop,
        on_discard=_noop,
    )

    texts = _row_texts(row)
    assert "model · a.gguf" in texts
    assert f"{_human(500)} / {_human(1000)}" in texts


def test_a_row_without_a_total_shows_an_unknown_size() -> None:
    row = _PendingRow(
        {"kind": "backend", "name": "cuda12", "done": 500},
        on_resume=_noop,
        on_discard=_noop,
    )

    assert f"{_human(500)} / unknown size" in _row_texts(row)


# ─── Loading ────────────────────────────────────────────────────


def test_the_list_populates_from_the_worker_result(
    qtbot: QtBot, fake_orch: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    section, _started = _section(qtbot, fake_orch, monkeypatch)

    section._on_list(
        {
            "tasks": [
                {"kind": "model", "name": "a.gguf", "url": "https://x/a"},
                {"kind": "backend", "name": "cuda12", "url": "https://y/b"},
            ]
        }
    )

    assert section._list_layout.count() == 2
    assert len(section._rows) == 2
    assert section._status_label.text() == "2 interrupted downloads."


def test_the_count_is_singular_for_one_row(
    qtbot: QtBot, fake_orch: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    section, _started = _section(qtbot, fake_orch, monkeypatch)

    section._on_list({"tasks": [{"kind": "model", "name": "a.gguf"}]})

    assert section._status_label.text() == "1 interrupted download."


def test_an_empty_list_clears_the_rows_and_the_status(
    qtbot: QtBot, fake_orch: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    section, _started = _section(qtbot, fake_orch, monkeypatch)
    section._on_list({"tasks": [{"kind": "backend", "name": "cuda12"}]})
    assert section._list_layout.count() == 1

    section._on_list({"tasks": []})

    assert section._list_layout.count() == 0
    assert section._rows == []
    assert section._status_label.text() == ""
    assert section._empty_label.isVisibleTo(section)


def test_a_non_dict_result_leaves_the_list_empty(
    qtbot: QtBot, fake_orch: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    section, _started = _section(qtbot, fake_orch, monkeypatch)

    section._on_list(object())

    assert section._list_layout.count() == 0


def test_showevent_reloads_on_every_visit(
    qtbot: QtBot, fake_orch: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    section, started = _section(qtbot, fake_orch, monkeypatch)
    assert len(started) == 1, "the first load ran in __init__"

    section.show()
    assert len(started) == 1, "the first show is not a revisit"

    section.hide()
    section.show()
    assert len(started) == 2, "a later visit re-scans for partials"


# ─── Actions ────────────────────────────────────────────────────


def test_resuming_a_backend_runs_the_bootstrap_action(
    qtbot: QtBot, fake_orch: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    section, started = _section(qtbot, fake_orch, monkeypatch)

    section._resume_task({"kind": "backend", "name": "cuda12"})

    worker = started[-1]
    assert worker._action == "bootstrap"
    assert worker._control is not None, "the resume is pausable/cancellable"


def test_resuming_a_model_runs_the_download_action(
    qtbot: QtBot, fake_orch: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    section, started = _section(qtbot, fake_orch, monkeypatch)

    section._resume_task({"kind": "model", "name": "a.gguf", "url": "https://x/a.gguf"})

    worker = started[-1]
    assert worker._action == "download_model"
    assert worker._kwargs["url"] == "https://x/a.gguf"


def test_a_completed_resume_reloads_the_list(
    qtbot: QtBot, fake_orch: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    section, started = _section(qtbot, fake_orch, monkeypatch)

    section._on_downloaded(None)

    assert section._status_label.text() == "Resume complete."
    assert len(started) == 2, "the list is re-scanned after a resume"


def test_discarding_runs_the_discard_action(
    qtbot: QtBot, fake_orch: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    section, started = _section(qtbot, fake_orch, monkeypatch)

    section._discard_task({"kind": "model", "dest": "/x/a.gguf.part"})

    worker = started[-1]
    assert worker._action == "discard_download"
    assert worker._kwargs == {"dest": "/x/a.gguf.part"}


# ─── Errors ─────────────────────────────────────────────────────


def test_clear_rows_tolerates_a_layout_that_comes_up_empty(
    qtbot: QtBot, fake_orch: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ``takeAt`` returning None mid-clear must not spin or crash."""
    section, _started = _section(qtbot, fake_orch, monkeypatch)
    section._on_list({"tasks": [{"kind": "model", "name": "a.gguf"}]})

    def _empty_take_at(_index: int) -> None:
        """A layout whose items vanish before they are taken."""

    counts = iter([1, 0])
    monkeypatch.setattr(section._list_layout, "count", lambda: next(counts))
    monkeypatch.setattr(section._list_layout, "takeAt", _empty_take_at)

    section._clear_rows()

    assert section._rows == []


def test_clear_rows_skips_items_without_a_widget(
    qtbot: QtBot, fake_orch: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A nested layout item carries no widget to delete."""
    section, _started = _section(qtbot, fake_orch, monkeypatch)
    section._list_layout.addLayout(QVBoxLayout())

    section._clear_rows()

    assert section._list_layout.count() == 0
    assert section._rows == []


def test_the_error_slot_names_the_error(
    qtbot: QtBot, fake_orch: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    section, _started = _section(qtbot, fake_orch, monkeypatch)

    section._on_error("boom")

    assert section._status_label.text() == "Error: boom"
