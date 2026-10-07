"""The GUI entry point: logging, application, theme, window."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest


class _FakeQApp:
    """Stand-in QApplication: records the calls run() makes."""

    def __init__(self, argv: list[str]) -> None:
        self._argv = list(argv)
        self.properties: dict[str, Any] = {}

    def setApplicationName(self, name: str) -> None:
        self.app_name = name

    def setProperty(self, key: str, value: object) -> None:
        self.properties[key] = value

    def exec(self) -> int:
        return 3


class _FakeConfig:
    """Stand-in for the loaded settings."""

    theme = "dark"

    @staticmethod
    def load() -> _FakeConfig:
        return _FakeConfig()


def test_run_builds_the_window_and_exits_with_the_loop_code(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    themed: list[tuple[Any, str]] = []
    window_cls = MagicMock()
    qapp_holder: list[_FakeQApp] = []

    def _fake_qapp(argv: list[str]) -> _FakeQApp:
        qapp = _FakeQApp(argv)
        qapp_holder.append(qapp)
        return qapp

    def _skip_configure(*_args: object, **_kwargs: object) -> None:
        pass

    def _record_theme(app: object, theme: str) -> None:
        themed.append((app, theme))

    monkeypatch.setattr("app.gui.bootstrap.QApplication", _fake_qapp)
    monkeypatch.setattr("app.gui.bootstrap.AppConfig", _FakeConfig)
    monkeypatch.setattr("app.gui.bootstrap.MainWindow", window_cls)
    monkeypatch.setattr("app.gui.bootstrap.default_root", lambda: tmp_path)
    monkeypatch.setattr("app.gui.bootstrap.configure_logging", _skip_configure)
    monkeypatch.setattr("app.gui.bootstrap.install_excepthook", lambda: None)
    monkeypatch.setattr("app.gui.bootstrap.apply_theme", _record_theme)

    from app.gui.bootstrap import run

    with pytest.raises(SystemExit) as excinfo:
        run()

    qapp = qapp_holder[0]
    assert qapp.app_name == "llama-gui"
    assert themed == [(qapp, "dark")], "the configured theme is applied"
    assert qapp.properties["_main_window"] is window_cls.return_value
    assert excinfo.value.code == 3, "the event loop's code becomes the exit code"
