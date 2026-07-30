from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtGui import QAction, QCloseEvent, QIcon
from PySide6.QtWidgets import (
    QHBoxLayout,
    QListWidget,
    QMenu,
    QStackedWidget,
    QSystemTrayIcon,
    QWidget,
)

from ..config import AppConfig
from ..orchestrator import Orchestrator
from .pages.actions import ActionsPage
from .pages.dashboard import DashboardPage
from .pages.logs import LogsPage
from .pages.models import ModelsPage
from .pages.resolver import ResolverPage
from .pages.settings import SettingsPage
from .worker_pool import EngineWorker, WorkerPool


class MainWindow(QWidget):
    def __init__(self, cfg: AppConfig | None = None) -> None:
        super().__init__()
        self.setWindowTitle("llama-gui")
        self.resize(900, 600)
        self._cfg = cfg or AppConfig.load()
        self._orch = Orchestrator(self._cfg)

        layout = QHBoxLayout(self)

        self._nav = QListWidget()
        self._nav.addItems(
            ["Dashboard", "Actions", "Resolver", "Models", "Logs", "Settings"]
        )
        self._nav.setFixedWidth(150)
        self._nav.currentRowChanged.connect(self._switch_page)
        layout.addWidget(self._nav)

        self._pages = QStackedWidget()
        self._dashboard = DashboardPage(self._orch)
        self._actions = ActionsPage(self._orch)
        self._resolver = ResolverPage(self._orch)

        root_path = Path(self._cfg.root)
        state = root_path / "state"
        cfg_path = root_path / "config.yaml"

        self._models = ModelsPage(self._orch, cfg_path)
        self._logs = LogsPage(state)
        self._settings = SettingsPage(self._orch)

        self._pages.addWidget(self._dashboard)
        self._pages.addWidget(self._actions)
        self._pages.addWidget(self._resolver)
        self._pages.addWidget(self._models)
        self._pages.addWidget(self._logs)
        self._pages.addWidget(self._settings)
        layout.addWidget(self._pages)

        self._nav.setCurrentRow(0)
        self._dashboard.start_refresh()

        # Auto-update timer: when enabled in config, run a headless update
        # on the configured interval and notify via the system tray.
        self._update_timer = QTimer(self)
        self._update_timer.timeout.connect(self._auto_update)
        if self._cfg.auto_update:
            interval_ms = self._cfg.auto_update_interval_hours * 3600 * 1000
            self._update_timer.start(interval_ms)

        # Keep a persistent tray icon so notifications don't get GC'd, and to
        # allow restoring / quitting the app when minimized to the tray.
        self._tray: QSystemTrayIcon | None = None
        if QSystemTrayIcon.isSystemTrayAvailable():
            self._tray = QSystemTrayIcon(self)
            self._tray.setIcon(QIcon.fromTheme("application-x-executable"))
            self._tray.setToolTip("llama-gui")
            tray_menu = QMenu(self)
            show_action = QAction("Show", self)
            show_action.triggered.connect(self.showNormal)
            quit_action = QAction("Quit", self)
            quit_action.triggered.connect(self.close)
            tray_menu.addAction(show_action)
            tray_menu.addAction(quit_action)
            self._tray.setContextMenu(tray_menu)
            self._tray.activated.connect(self._on_tray_activated)
            self._tray.show()

        # "Launch router on start": fire-and-forget launch in the background.
        if self._cfg.launch_on_start:
            worker = EngineWorker(self._orch, "launch", verify=False)
            worker.signals.error.connect(self._notify_update_error)
            WorkerPool.instance().start(worker)

        # "Start minimized": hide the window instead of showing it.
        if self._cfg.start_minimized:
            self.hide()
        else:
            self.show()

    def _switch_page(self, index: int) -> None:
        self._dashboard.stop_refresh()
        self._pages.setCurrentIndex(index)
        if index == 0:
            self._dashboard.start_refresh()

    def _auto_update(self) -> None:
        worker = EngineWorker(self._orch, "update", progress_callback=None)
        worker.signals.finished.connect(self._notify_update)
        worker.signals.error.connect(self._notify_update_error)
        WorkerPool.instance().start(worker)

    def _notify_update(self, data: object) -> None:
        if self._tray is not None:
            self._tray.showMessage(
                "llama-gui",
                "Update check complete.",
                QSystemTrayIcon.MessageIcon.Information,
                3000,
            )

    def _notify_update_error(self, msg: str) -> None:
        if self._tray is not None:
            self._tray.showMessage(
                "llama-gui",
                f"Update failed: {msg}",
                QSystemTrayIcon.MessageIcon.Information,
                5000,
            )

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            if self.isVisible():
                self.hide()
            else:
                self.showNormal()
                self.raise_()

    def closeEvent(self, event: QCloseEvent) -> None:
        # When a system tray is available, keep running in the tray so the
        # router keeps serving and the app is still reachable from the tray.
        if self._tray is not None:
            event.ignore()
            self.hide()
            return
        self._dashboard.stop_refresh()
        self._update_timer.stop()
        super().closeEvent(event)
