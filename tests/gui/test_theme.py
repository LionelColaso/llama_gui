from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QGuiApplication, QPalette
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from app.gui import theme
from app.gui.theme import COLORS, apply_theme, get_stylesheet


def test_colors_are_hex_strings() -> None:
    for value in COLORS.values():
        assert value.startswith("#")
        assert len(value) == 7


def test_get_stylesheet_light() -> None:
    qss = get_stylesheet("light")
    assert "#F4F5F7" in qss  # light background
    assert "#1B1F27" in qss  # light text


def test_get_stylesheet_dark() -> None:
    qss = get_stylesheet("dark")
    assert "#0F1115" in qss  # dark background
    assert "#E6E8EC" in qss  # dark text


def test_get_stylesheet_unknown_theme_falls_back_to_dark() -> None:
    assert get_stylesheet("bogus") == get_stylesheet("dark")


def test_get_stylesheet_system() -> None:
    qss = get_stylesheet("system")
    assert "QWidget" in qss


def test_resolve_tokens_dark_scheme(monkeypatch: pytest.MonkeyPatch) -> None:
    hints = MagicMock()
    hints.colorScheme.return_value = Qt.ColorScheme.Dark
    monkeypatch.setattr(QGuiApplication, "styleHints", lambda: hints)
    assert theme._resolve_tokens("system") is theme._DARK


def test_resolve_tokens_light_scheme(monkeypatch: pytest.MonkeyPatch) -> None:
    hints = MagicMock()
    hints.colorScheme.return_value = Qt.ColorScheme.Light
    monkeypatch.setattr(QGuiApplication, "styleHints", lambda: hints)
    assert theme._resolve_tokens("system") is theme._LIGHT


def test_resolve_tokens_scheme_unknown_falls_back_to_dark(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hints = MagicMock()
    hints.colorScheme.return_value = Qt.ColorScheme.Unknown
    monkeypatch.setattr(QGuiApplication, "styleHints", lambda: hints)
    assert theme._resolve_tokens("system") is theme._DARK


def test_resolve_tokens_headless_falls_back_to_dark(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _no_hints() -> MagicMock:
        raise RuntimeError("no style hints headless")

    monkeypatch.setattr(QGuiApplication, "styleHints", _no_hints)
    assert theme._resolve_tokens("system") is theme._DARK


def test_apply_theme_sets_palette_and_stylesheet(qtbot: QtBot) -> None:
    app = QApplication.instance()
    assert isinstance(app, QApplication)
    apply_theme(app, "dark")
    assert app.palette().color(QPalette.ColorRole.Window) == QColor("#171A21")
    assert "#0F1115" in app.styleSheet()
    qtbot.wait(0)
