"""GUI entry point: logging, QApplication, theme and the main window.

Kept separate from :mod:`app.__main__` (which dispatches ``gui`` vs the CLI) so
the Qt stack is only imported when the GUI is actually requested.
"""

from __future__ import annotations

import sys

from loguru import logger
from PySide6.QtWidgets import QApplication

from ..applog import configure_logging, install_excepthook
from ..config import AppConfig
from ..paths import default_root
from .main_window import MainWindow
from .theme import apply_theme


def run() -> None:
    # Reconfigure here (idempotent) so the GUI also logs when entered via
    # ``app gui`` or an embedded call that skipped ``__main__``; stderr
    # mirroring helps when the app was launched from a terminal.
    configure_logging(default_root() / "logs", to_stderr=True)
    install_excepthook()
    logger.info("GUI starting")

    qapp = QApplication(sys.argv)
    qapp.setApplicationName("llama-gui")

    cfg = AppConfig.load()
    apply_theme(qapp, cfg.theme)

    # MainWindow shows or hides itself based on the "start minimized" setting.
    # Keep a reference so the window isn't garbage-collected during qapp.exec().
    window = MainWindow(cfg)
    qapp.setProperty("_main_window", window)

    code = qapp.exec()
    # Drain the enqueued log queue so a last-second error survives the exit.
    logger.complete()
    sys.exit(code)
