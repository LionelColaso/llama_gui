"""PathPicker: value handling, the browse dialogs, and the action button."""

from __future__ import annotations

from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from app.gui.widgets.path_picker import PathPicker


def test_value_empty_is_none(qtbot: QtBot) -> None:
    picker = PathPicker()
    qtbot.addWidget(picker)
    assert picker.value() is None


def test_value_returns_stripped_text(qtbot: QtBot) -> None:
    picker = PathPicker()
    qtbot.addWidget(picker)
    picker.setText("  /models  ")
    assert picker.value() == "/models"


def test_set_text_none_clears(qtbot: QtBot) -> None:
    picker = PathPicker()
    qtbot.addWidget(picker)
    picker.setText("/models")
    picker.setText(None)
    assert picker.text() == ""


def test_set_placeholder_text(qtbot: QtBot) -> None:
    picker = PathPicker(placeholder="hint")
    qtbot.addWidget(picker)
    picker.setPlaceholderText("default")
    assert picker._edit.placeholderText() == "default"


def test_no_action_button_without_callback(qtbot: QtBot) -> None:
    picker = PathPicker(action_label="Use default")
    qtbot.addWidget(picker)
    assert picker._action_btn is None


def test_action_button_invokes_the_callback_with_the_text(
    qtbot: QtBot,
) -> None:
    calls: list[str] = []
    picker = PathPicker(action_label="Use default", on_action=calls.append)
    qtbot.addWidget(picker)
    picker.setText("/models")
    btn = picker._action_btn
    assert btn is not None
    btn.click()
    assert calls == ["/models"]


class TestStartDir:
    def test_empty_uses_home(self, qtbot: QtBot) -> None:
        picker = PathPicker()
        qtbot.addWidget(picker)
        assert picker._start_dir() == str(Path.home())

    def test_directory_uses_itself(self, qtbot: QtBot, tmp_path: Path) -> None:
        picker = PathPicker(mode="directory")
        qtbot.addWidget(picker)
        picker.setText(str(tmp_path))
        assert picker._start_dir() == str(tmp_path)

    def test_file_uses_its_parent(self, qtbot: QtBot, tmp_path: Path) -> None:
        model = tmp_path / "m.gguf"
        model.write_text("x", encoding="utf-8")
        picker = PathPicker()
        qtbot.addWidget(picker)
        picker.setText(str(model))
        assert picker._start_dir() == str(tmp_path)

    def test_missing_with_missing_parent_uses_home(
        self, qtbot: QtBot, tmp_path: Path
    ) -> None:
        picker = PathPicker()
        qtbot.addWidget(picker)
        picker.setText(str(tmp_path / "nope" / "deeper"))
        assert picker._start_dir() == str(Path.home())


class _FakeFileDialogs:
    """Canned answers for the two native dialogs PathPicker opens."""

    directory: str = ""
    file: tuple[str, str] = ("", "")

    @staticmethod
    def getExistingDirectory(*_args: object, **_kwargs: object) -> str:
        return _FakeFileDialogs.directory

    @staticmethod
    def getOpenFileName(*_args: object, **_kwargs: object) -> tuple[str, str]:
        return _FakeFileDialogs.file


class TestBrowse:
    def test_directory_mode_picks_a_directory(
        self, qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _FakeFileDialogs.directory = str(tmp_path)
        monkeypatch.setattr("app.gui.widgets.path_picker.QFileDialog", _FakeFileDialogs)
        picker = PathPicker(mode="directory", caption="Pick a dir")
        qtbot.addWidget(picker)
        picker._browse()
        assert picker.text() == str(tmp_path)

    def test_file_mode_picks_a_file(
        self, qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        chosen = str(tmp_path / "m.gguf")
        _FakeFileDialogs.file = (chosen, "")
        monkeypatch.setattr("app.gui.widgets.path_picker.QFileDialog", _FakeFileDialogs)
        picker = PathPicker(mode="file", caption="Pick a file")
        qtbot.addWidget(picker)
        picker._browse()
        assert picker.text() == chosen

    def test_empty_choice_keeps_the_current_text(
        self, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _FakeFileDialogs.directory = ""
        monkeypatch.setattr("app.gui.widgets.path_picker.QFileDialog", _FakeFileDialogs)
        picker = PathPicker(mode="directory")
        qtbot.addWidget(picker)
        picker.setText("/keep")
        picker._browse()
        assert picker.text() == "/keep"
