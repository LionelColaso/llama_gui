from __future__ import annotations

import sys

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from ..config import AppConfig
from .main_window import MainWindow


def _apply_theme(app: QApplication, theme: str) -> None:
    """Apply the requested color theme to the QApplication palette."""
    if theme == "dark":
        palette = QPalette()
        dark = QColor(45, 45, 45)
        darker = QColor(30, 30, 30)
        text = QColor(220, 220, 220)
        accent = QColor(0x4CAF50)
        palette.setColor(QPalette.ColorRole.Window, dark)
        palette.setColor(QPalette.ColorRole.WindowText, text)
        palette.setColor(QPalette.ColorRole.Base, darker)
        palette.setColor(QPalette.ColorRole.AlternateBase, dark)
        palette.setColor(QPalette.ColorRole.ToolTipBase, text)
        palette.setColor(QPalette.ColorRole.ToolTipText, text)
        palette.setColor(QPalette.ColorRole.Text, text)
        palette.setColor(QPalette.ColorRole.Button, dark)
        palette.setColor(QPalette.ColorRole.ButtonText, text)
        palette.setColor(QPalette.ColorRole.Highlight, accent)
        palette.setColor(QPalette.ColorRole.HighlightedText, QColor(0, 0, 0))
        palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(170, 170, 170))
        app.setPalette(palette)
    elif theme == "light":
        palette = QPalette()
        light = QColor(240, 240, 240)
        palette.setColor(QPalette.ColorRole.Window, light)
        palette.setColor(QPalette.ColorRole.WindowText, QColor(0, 0, 0))
        palette.setColor(QPalette.ColorRole.Base, QColor(255, 255, 255))
        palette.setColor(QPalette.ColorRole.AlternateBase, QColor(245, 245, 245))
        palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(0, 0, 0))
        palette.setColor(QPalette.ColorRole.ToolTipText, QColor(0, 0, 0))
        palette.setColor(QPalette.ColorRole.Text, QColor(0, 0, 0))
        palette.setColor(QPalette.ColorRole.Button, light)
        palette.setColor(QPalette.ColorRole.ButtonText, QColor(0, 0, 0))
        palette.setColor(QPalette.ColorRole.Highlight, QColor(0x2196F3))
        palette.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))
        palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(120, 120, 120))
        app.setPalette(palette)
    # "system" — leave the default platform palette


def run() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("llama-gui")

    cfg = AppConfig.load()
    _apply_theme(app, cfg.theme)

    # MainWindow shows or hides itself based on the "start minimized" setting.
    # Keep a reference so the window isn't garbage-collected during app.exec().
    window = MainWindow(cfg)
    app.setProperty("_main_window", window)

    sys.exit(app.exec())
