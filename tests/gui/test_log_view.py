"""The log viewer: tailing, pausing, rotation and vanished logs."""

from __future__ import annotations

from pathlib import Path

from pytestqt.qtbot import QtBot

from app.gui.widgets.log_view import LogView


def _log(tmp_path: Path, name: str = "llamagui.log") -> Path:
    log = tmp_path / name
    log.write_text("", encoding="utf-8")
    return log


def test_a_missing_log_starts_idle(qtbot: QtBot, tmp_path: Path) -> None:
    view = LogView(tmp_path / "nope.log")
    qtbot.addWidget(view)

    assert view._editor.toPlainText() == ""


def test_the_view_tails_the_log(qtbot: QtBot, tmp_path: Path) -> None:
    log = _log(tmp_path)
    log.write_text("first line\n", encoding="utf-8")
    view = LogView(log)
    qtbot.addWidget(view)

    assert "first line" in view._editor.toPlainText()

    log.write_text("first line\nsecond line\n", encoding="utf-8")
    view._tail()

    assert "second line" in view._editor.toPlainText()


def test_pausing_holds_the_tail(qtbot: QtBot, tmp_path: Path) -> None:
    log = _log(tmp_path)
    log.write_text("one\n", encoding="utf-8")
    view = LogView(log)
    qtbot.addWidget(view)

    view._follow_btn.setChecked(True)
    assert view._follow_btn.text() == "Resume tail"

    log.write_text("one\ntwo\n", encoding="utf-8")
    view._tail()
    assert "two" not in view._editor.toPlainText()

    view._follow_btn.setChecked(False)
    assert view._follow_btn.text() == "Pause tail"
    assert "two" in view._editor.toPlainText()


def test_a_rotated_log_restarts_from_the_top(qtbot: QtBot, tmp_path: Path) -> None:
    log = _log(tmp_path)
    log.write_text("a much longer first line\nsecond\n", encoding="utf-8")
    view = LogView(log)
    qtbot.addWidget(view)
    assert view._pos > 0

    log.write_text("short\n", encoding="utf-8")
    view._tail()

    assert "short" in view._editor.toPlainText()


def test_a_tail_with_no_new_data_appends_nothing(qtbot: QtBot, tmp_path: Path) -> None:
    log = _log(tmp_path)
    log.write_text("one\n", encoding="utf-8")
    view = LogView(log)
    qtbot.addWidget(view)

    view._tail()

    assert view._editor.toPlainText() == "one"


def test_a_vanished_log_is_tolerated(qtbot: QtBot, tmp_path: Path) -> None:
    log = _log(tmp_path)
    log.write_text("one\n", encoding="utf-8")
    view = LogView(log)
    qtbot.addWidget(view)

    log.unlink()
    view._tail()

    assert "one" in view._editor.toPlainText()


def _launch_log(tmp_path: Path, stamp: str, text: str) -> Path:
    path = tmp_path / f"llama-server-{stamp}.out.log"
    path.write_text(text, encoding="utf-8")
    return path


def test_the_newest_launch_log_is_followed(qtbot: QtBot, tmp_path: Path) -> None:
    _launch_log(tmp_path, "20260101-000000", "old\n")
    view = LogView(
        tmp_path / "llama-server.out.log",
        latest_glob="llama-server-*.out.log",
    )
    qtbot.addWidget(view)
    assert "old" in view._editor.toPlainText()

    newest = _launch_log(tmp_path, "20260102-000000", "new\n")
    view._tail()

    assert view._current == newest
    assert "new" in view._editor.toPlainText()


def test_a_new_launch_log_is_picked_up_without_a_reload(
    qtbot: QtBot, tmp_path: Path
) -> None:
    view = LogView(
        tmp_path / "llama-server.out.log",
        latest_glob="llama-server-*.out.log",
    )
    qtbot.addWidget(view)
    assert view._editor.toPlainText() == ""

    _launch_log(tmp_path, "20260101-000000", "arrived\n")
    view._on_directory_changed(str(tmp_path))

    assert "arrived" in view._editor.toPlainText()


def test_a_replaced_log_is_watched_again(qtbot: QtBot, tmp_path: Path) -> None:
    log = _log(tmp_path)
    view = LogView(log)
    qtbot.addWidget(view)

    log.unlink()
    log.write_text("replaced\n", encoding="utf-8")
    view._on_file_changed(str(log))

    assert any(Path(watched) == log for watched in view._watcher.files())
