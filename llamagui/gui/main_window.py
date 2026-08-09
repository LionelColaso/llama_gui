from __future__ import annotations

from typing import Any

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
from .dialogs.first_run import FirstRunDialog
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
        self.resize(960, 640)
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

        root_path = self._orch.cfg.root_path
        self._models = ModelsPage(self._orch, root_path / "config.yaml")
        self._logs = LogsPage(root_path / "state")
        self._settings = SettingsPage(self._orch)

        for page in (
            self._dashboard,
            self._actions,
            self._resolver,
            self._models,
            self._logs,
            self._settings,
        ):
            self._pages.addWidget(page)
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

        self._tray = self._build_tray()

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

        # Offer first-run setup once nothing can be resolved.
        QTimer.singleShot(0, self._maybe_first_run)

    def _build_tray(self) -> QSystemTrayIcon | None:
        """Keep a persistent tray icon (also stops notifications being GC'd)."""
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return None
        tray = QSystemTrayIcon(self)
        tray.setIcon(QIcon.fromTheme("application-x-executable"))
        tray.setToolTip("llama-gui")
        menu = QMenu(self)
        show_action = QAction("Show", self)
        show_action.triggered.connect(self.showNormal)
        quit_action = QAction("Quit", self)
        quit_action.triggered.connect(self.close)
        menu.addAction(show_action)
        menu.addAction(quit_action)
        tray.setContextMenu(menu)
        tray.activated.connect(self._on_tray_activated)
        tray.show()
        return tray

    def _maybe_first_run(self) -> None:
        """Show the setup dialog when the app has nothing to run yet."""
        if getattr(self._orch.cfg, "first_run_complete", False):
            return
        try:
            status: Any = self._orch.status()
            ready = bool(getattr(status, "ready", False))
        except Exception:  # noqa: BLE001 - never block startup on a status error
            return
        if ready:
            return
        FirstRunDialog(self._orch, self).exec()
        self._dashboard.start_refresh()

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
        # Fully tear down so the process actually exits. Closing the window or
        # choosing Quit from the tray must terminate the app; an earlier version
        # called event.ignore() while a tray existed and only hid the window,
        # leaving the process alive forever.
        try:
            self._dashboard.stop_refresh()
        except Exception:  # noqa: BLE001, S110 - best-effort during shutdown
            pass
        try:
            self._update_timer.stop()
        except Exception:  # noqa: BLE001, S110
            pass
        # Stop any running router so we don't leave an orphaned server/port.
        try:
            self._orch.stop()
        except Exception:  # noqa: BLE001, S110 - never block shutdown on a stop error
            pass
        super().closeEvent(event)
