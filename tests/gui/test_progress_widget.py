"""Behavior of the :class:`ProgressWidget` state machine.

Covers the full lifecycle — hidden at idle, indeterminate on start, determinate
(with a percentage) once a total is known, clamping, hidden again on finish, and
a full danger-colored bar on failure that :meth:`reset` clears.
"""

from __future__ import annotations

from typing import Any

from pytestqt.qtbot import QtBot

from llamagui.gui.theme import COLORS
from llamagui.gui.widgets.progress_bar import ProgressWidget, _fmt_duration, _human


def test_hidden_until_started(qtbot: QtBot) -> None:
    widget = ProgressWidget()
    qtbot.addWidget(widget)
    assert widget._bar.isHidden()


def test_start_shows_indeterminate(qtbot: QtBot) -> None:
    widget = ProgressWidget()
    qtbot.addWidget(widget)
    widget.start_operation("Downloading…")
    assert not widget._bar.isHidden()
    # range 0..0 is Qt's indeterminate / busy animation
    assert widget._bar.minimum() == 0
    assert widget._bar.maximum() == 0
    assert "Downloading" in widget._bar.format()


def test_update_determinate_shows_value_and_percent(qtbot: QtBot) -> None:
    widget = ProgressWidget()
    qtbot.addWidget(widget)
    widget.start_operation("Downloading…")
    widget.update_progress(5000, 10000, "download")
    assert widget._bar.minimum() == 0
    assert widget._bar.maximum() == 10000
    assert widget._bar.value() == 5000
    assert "download" in widget._bar.format().lower()
    assert "50%" in widget._bar.format()


def test_update_clamps_overshoot_and_negative(qtbot: QtBot) -> None:
    widget = ProgressWidget()
    qtbot.addWidget(widget)
    widget.start_operation("Downloading…")
    widget.update_progress(99999, 100, "download")
    assert widget._bar.value() == 100
    widget.update_progress(-5, 100, "download")
    assert widget._bar.value() == 0


def test_update_keeps_busy_without_total(qtbot: QtBot) -> None:
    widget = ProgressWidget()
    qtbot.addWidget(widget)
    widget.start_operation("Downloading…")
    widget.update_progress(0, 0, "waiting for size")
    assert widget._bar.maximum() == 0
    assert "waiting for size" in widget._bar.format()


def test_finish_hides_bar(qtbot: QtBot) -> None:
    widget = ProgressWidget()
    qtbot.addWidget(widget)
    widget.start_operation("Downloading…")
    widget.update_progress(1, 2, "download")
    widget.finish_operation()
    assert widget._bar.isHidden()
    assert widget._bar.styleSheet() == ""


def test_fail_fills_bar_with_danger(qtbot: QtBot) -> None:
    widget = ProgressWidget()
    qtbot.addWidget(widget)
    widget.start_operation("Downloading…")
    widget.update_progress(3, 10, "download")
    widget.fail_operation("Failed")
    assert not widget._bar.isHidden()
    assert widget._bar.value() == 100
    assert "Failed" in widget._bar.format()
    assert COLORS["danger"] in widget._bar.styleSheet()


def test_start_after_failure_restores_accent(qtbot: QtBot) -> None:
    widget = ProgressWidget()
    qtbot.addWidget(widget)
    widget.start_operation("Downloading…")
    widget.fail_operation("Failed")
    widget.start_operation("Retrying…")
    assert widget._bar.styleSheet() == ""
    assert "Retrying" in widget._bar.format()


def test_reset_clears_failure(qtbot: QtBot) -> None:
    widget = ProgressWidget()
    qtbot.addWidget(widget)
    widget.start_operation("Downloading…")
    widget.fail_operation("Failed")
    widget.reset()
    assert widget._bar.isHidden()
    assert widget._bar.styleSheet() == ""
    assert widget._bar.value() == 0


def test_overall_fills_normalized_bar(qtbot: QtBot) -> None:
    widget = ProgressWidget()
    qtbot.addWidget(widget)
    widget.start_operation("Installing…")
    widget.update_progress(0, 0, "download", 0.5)
    # overall drives a smooth 0..1000 range so the fill animates between ints.
    assert widget._bar.maximum() == 1000
    assert widget._bar.value() == 500
    assert "50%" in widget._bar.format()


def test_overall_clamps_out_of_range(qtbot: QtBot) -> None:
    widget = ProgressWidget()
    qtbot.addWidget(widget)
    widget.update_progress(0, 0, "extract", 5.0)
    assert widget._bar.value() == 1000
    widget.update_progress(0, 0, "extract", -1.0)
    assert widget._bar.value() == 0


def test_overall_shows_human_bytes_for_phase(qtbot: QtBot) -> None:
    widget = ProgressWidget()
    qtbot.addWidget(widget)
    widget.start_operation("Installing…")
    done = 512 * 1024 * 1024
    total = int(1.2 * 1024**3)
    widget.update_progress(done, total, "download", 0.3)
    fmt = widget._bar.format()
    assert "Downloading" in fmt
    assert "512.0 MB / 1.2 GB" in fmt
    assert "30%" in fmt


def test_speed_and_eta_reported(qtbot: QtBot, monkeypatch: Any) -> None:
    import time as _time

    ticks = iter([0.0, 1.0, 2.0])
    monkeypatch.setattr(_time, "monotonic", lambda: next(ticks))
    widget = ProgressWidget()
    qtbot.addWidget(widget)
    widget.start_operation("Downloading…")
    widget.update_progress(0, 100_000_000, "download")
    widget.update_progress(50_000_000, 100_000_000, "download")
    fmt = widget._bar.format()
    assert "MB/s" in fmt
    assert "left" in fmt


def test_human_bytes() -> None:
    assert _human(512) == "512 B"
    assert _human(1024) == "1.0 KB"
    assert _human(1.5 * 1024 * 1024) == "1.5 MB"
    assert _human(1.2 * 1024**3) == "1.2 GB"


def test_fmt_duration() -> None:
    assert _fmt_duration(5) == "5s"
    assert _fmt_duration(125) == "2m 5s"
    assert _fmt_duration(3720) == "1h 2m"
