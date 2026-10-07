from __future__ import annotations

from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtWidgets import QSystemTrayIcon
from pytestqt.qtbot import QtBot

from app.config import AppConfig
from app.gui.main_window import MainWindow
from app.gui.pages.dashboard import DashboardHome
from app.gui.pages.logs import LogsPage
from app.gui.pages.models import ModelsPage
from app.gui.pages.server_args import ServerArgsPage
from app.gui.pages.settings import SettingsPage
from app.gui.sections.backends import BackendsSection
from app.gui.widgets.backend_card import BackendCard
from app.gui.widgets.source_badge import SourceBadge
from app.schemas import InstallData
from tests.gui._pool_recorder import record_workers


def _record_main_window_workers(
    monkeypatch: pytest.MonkeyPatch,
) -> list[Any]:
    return record_workers(monkeypatch, "app.gui.main_window.WorkerPool")


def test_main_window_creates(qtbot: QtBot, fake_orch: MagicMock) -> None:
    with patch("app.gui.main_window.Orchestrator", return_value=fake_orch):
        w = MainWindow()
        qtbot.addWidget(w)
        assert w.windowTitle() == "llama-gui"


def test_navigation_switches_pages(qtbot: QtBot, fake_orch: MagicMock) -> None:
    with patch("app.gui.main_window.Orchestrator", return_value=fake_orch):
        w = MainWindow()
        qtbot.addWidget(w)

        assert w._pages.currentIndex() == 0
        assert isinstance(w._pages.currentWidget(), DashboardHome)

        w._nav.setCurrentRow(1)
        assert w._pages.currentIndex() == 1
        assert isinstance(w._pages.currentWidget(), ModelsPage)

        w._nav.setCurrentRow(2)
        assert w._pages.currentIndex() == 2
        assert isinstance(w._pages.currentWidget(), ServerArgsPage)

        w._nav.setCurrentRow(3)
        assert w._pages.currentIndex() == 3
        assert isinstance(w._pages.currentWidget(), LogsPage)

        w._nav.setCurrentRow(4)
        assert w._pages.currentIndex() == 4
        assert isinstance(w._pages.currentWidget(), SettingsPage)


def test_the_tray_uses_a_themed_icon_when_one_is_available(
    qtbot: QtBot, fake_orch: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A freedesktop icon theme wins over the built-in fallback."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QIcon, QPixmap

    fake_tray_cls = MagicMock()
    fake_tray_cls.isSystemTrayAvailable.return_value = True
    monkeypatch.setattr("app.gui.main_window.QSystemTrayIcon", fake_tray_cls)

    def _themed_icon(name: str, *args: object) -> QIcon:
        pixmap = QPixmap(4, 4)
        pixmap.fill(Qt.GlobalColor.red)
        return QIcon(pixmap)

    monkeypatch.setattr("app.gui.main_window.QIcon.fromTheme", _themed_icon)
    with patch("app.gui.main_window.Orchestrator", return_value=fake_orch):
        w = MainWindow()
        qtbot.addWidget(w)

    tray = cast("MagicMock", fake_tray_cls.return_value)
    assert w._tray is tray
    icon = tray.setIcon.call_args.args[0]
    assert not icon.isNull(), "the themed icon is used, not the fallback"


def test_models_and_downloads_share_one_tab(qtbot: QtBot, fake_orch: MagicMock) -> None:
    """The library and its downloads are one tab, not two (regression)."""
    from unittest.mock import patch

    with patch("app.gui.main_window.Orchestrator", return_value=fake_orch):
        w = MainWindow()
        qtbot.addWidget(w)

    labels = [w._nav.item(i).text() for i in range(w._nav.count())]
    assert labels == ["Dashboard", "Models", "Server options", "Logs", "Settings"]
    assert "Downloads" not in labels
    page = w._pages.widget(1)
    assert isinstance(page, ModelsPage)
    assert page.models is not None and page.downloads is not None
    # Models moved off the dashboard, which is backends-only now.
    assert not hasattr(w._dashboard, "models_section")


def test_dashboard_has_backend_cards(qtbot: QtBot, fake_orch: MagicMock) -> None:
    from unittest.mock import patch

    with patch("app.gui.main_window.Orchestrator", return_value=fake_orch):
        w = MainWindow()
        qtbot.addWidget(w)

        page = w._dashboard.backends
        assert "vulkan" in page._cards
        assert "cuda13" in page._cards
        assert "cuda12" in page._cards


def test_source_badge_shows_label() -> None:
    badge = SourceBadge("system")
    assert "system" in badge.text()

    badge2 = SourceBadge(None)
    assert "unknown" in badge2.text()


def test_backend_card_shows_installed() -> None:
    card = BackendCard("vulkan", installed=True, version="b12345")
    assert "b12345" in card._status_label.text()

    card2 = BackendCard("vulkan", installed=False)
    assert "not installed" in card2._status_label.text()


def test_backend_cards_have_actions(qtbot: QtBot, fake_orch: MagicMock) -> None:
    page = BackendsSection(fake_orch)
    qtbot.addWidget(page)
    assert len(page._cards) == 3
    for card in page._cards.values():
        assert card._install_btn is not None
        assert card._update_btn is not None
        assert card._use_btn is not None


def test_resolver_page_creates(qtbot: QtBot, fake_orch: MagicMock) -> None:
    page = BackendsSection(fake_orch)
    qtbot.addWidget(page)
    assert page._server_row is not None


def test_the_models_tab_opens_the_per_model_popup(
    qtbot: QtBot, fake_orch: MagicMock
) -> None:
    """The button opens the popup for the selected model, leaving the page alone."""
    from unittest.mock import patch

    with (
        patch("app.gui.main_window.Orchestrator", return_value=fake_orch),
        patch("app.gui.main_window.ModelServerOptionsDialog") as dialog_cls,
    ):
        dialog_cls.return_value.exec.return_value = 0
        w = MainWindow()
        qtbot.addWidget(w)

        w._models.server_options_requested.emit("big.gguf")

    assert dialog_cls.call_args.args[1] == "big.gguf"
    assert w._server_args._scope is None, "the global page stays the global page"


def test_closing_the_popup_resyncs_the_server_options_page(
    qtbot: QtBot, fake_orch: MagicMock
) -> None:
    """The model may now have its own config, which the page's scope shows."""
    from unittest.mock import patch

    with (
        patch("app.gui.main_window.Orchestrator", return_value=fake_orch),
        patch("app.gui.main_window.ModelServerOptionsDialog") as dialog_cls,
    ):
        dialog = dialog_cls.return_value
        dialog.exec.return_value = 1
        dialog.model = "big.gguf"
        w = MainWindow()
        qtbot.addWidget(w)

        w._models.server_options_requested.emit("big.gguf")

    assert w._server_args._scope == "big.gguf"
    assert isinstance(w, MainWindow)


def test_engine_worker_runs(qtbot: QtBot, fake_orch: MagicMock) -> None:
    from app.gui.worker_pool import EngineWorker

    worker = EngineWorker(fake_orch, "describe")

    def _ignore(_: object) -> None:
        return None

    worker.signals.finished.connect(_ignore)
    worker.run()
    assert fake_orch.describe.called


def test_close_stops_router_and_tears_down(qtbot: QtBot, fake_orch: MagicMock) -> None:
    """Closing the window must terminate the app, not hide it (regression).

    Previously the tray path called ``event.ignore()`` and only hid the window,
    so the process lingered forever and Quit did nothing.
    """
    from unittest.mock import patch

    with patch("app.gui.main_window.Orchestrator", return_value=fake_orch):
        w = MainWindow()
        qtbot.addWidget(w)
        accepted = w.close()
    assert accepted is True
    assert fake_orch.stop.called


def _auto_update_toast_message(qtbot: QtBot, fake_orch: MagicMock, data: object) -> str:
    """Build a MainWindow with a mock tray and return the toast message text."""
    from unittest.mock import patch

    with patch("app.gui.main_window.Orchestrator", return_value=fake_orch):
        w = MainWindow()
        qtbot.addWidget(w)
        w._tray = MagicMock()
        w._notify_update(data)
        return str(w._tray.showMessage.call_args.args[1])


def test_auto_update_toast_reports_updated_backends(
    qtbot: QtBot, fake_orch: MagicMock
) -> None:
    from app.schemas import InstallData, InstallResultItem

    data = InstallData(
        release="b10331",
        results=[
            InstallResultItem(name="vulkan", status="ok", version="b10331"),
            InstallResultItem(name="cuda12", status="skipped", version="b10331"),
        ],
        summary={"updated": 1, "skipped": 1, "failed": 0},
    )
    message = _auto_update_toast_message(qtbot, fake_orch, data)
    assert "vulkan → b10331" in message
    assert "cuda12" not in message  # skipped, so not reported as updated


def test_auto_update_toast_when_nothing_changed(
    qtbot: QtBot, fake_orch: MagicMock
) -> None:
    from app.schemas import InstallData, InstallResultItem

    data = InstallData(
        release="b10331",
        results=[InstallResultItem(name="vulkan", status="skipped", version="b10331")],
        summary={"updated": 0, "skipped": 1, "failed": 0},
    )
    message = _auto_update_toast_message(qtbot, fake_orch, data)
    assert "Already up to date" in message


def test_auto_update_toast_with_non_install_data(
    qtbot: QtBot, fake_orch: MagicMock
) -> None:
    """A worker result that is not InstallData still toasts sensibly."""
    message = _auto_update_toast_message(qtbot, fake_orch, {"not": "install"})
    assert "Already up to date" in message


def test_auto_update_config_starts_the_timer(
    qtbot: QtBot, fake_orch: MagicMock, tmp_path: Path
) -> None:
    cfg = AppConfig(root=str(tmp_path), auto_update=True, auto_update_interval_hours=1)
    with patch("app.gui.main_window.Orchestrator", return_value=fake_orch):
        w = MainWindow(cfg)
        qtbot.addWidget(w)
    assert w._update_timer.isActive()


def test_launch_on_start_fires_a_launch_worker(
    qtbot: QtBot,
    fake_orch: MagicMock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = _record_main_window_workers(monkeypatch)
    cfg = AppConfig(root=str(tmp_path), launch_on_start=True)
    with patch("app.gui.main_window.Orchestrator", return_value=fake_orch):
        w = MainWindow(cfg)
        qtbot.addWidget(w)

    assert len(started) == 1
    assert started[0]._action == "launch"
    assert started[0]._kwargs == {"verify": False}


def test_start_minimized_hides_the_window(
    qtbot: QtBot, fake_orch: MagicMock, tmp_path: Path
) -> None:
    cfg = AppConfig(root=str(tmp_path), start_minimized=True)
    with patch("app.gui.main_window.Orchestrator", return_value=fake_orch):
        w = MainWindow(cfg)
        qtbot.addWidget(w)
    assert not w.isVisible()


def test_auto_update_starts_an_update_worker(
    qtbot: QtBot, fake_orch: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = _record_main_window_workers(monkeypatch)
    with patch("app.gui.main_window.Orchestrator", return_value=fake_orch):
        w = MainWindow()
        qtbot.addWidget(w)

    w._auto_update()

    assert len(started) == 1
    assert started[0]._action == "update"


def test_notify_update_without_a_tray_is_silent(
    qtbot: QtBot, fake_orch: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Offscreen there is no tray; the toast must be skipped, not crash."""
    monkeypatch.setattr(
        "app.gui.main_window.QSystemTrayIcon.isSystemTrayAvailable",
        lambda: False,
    )
    with patch("app.gui.main_window.Orchestrator", return_value=fake_orch):
        w = MainWindow()
        qtbot.addWidget(w)
    assert w._tray is None
    w._notify_update(InstallData(release="b1", results=[], summary={}))


def test_notify_update_error_with_a_tray(qtbot: QtBot, fake_orch: MagicMock) -> None:
    with patch("app.gui.main_window.Orchestrator", return_value=fake_orch):
        w = MainWindow()
        qtbot.addWidget(w)
        w._tray = MagicMock()

        w._notify_update_error("boom")

    assert "Update failed: boom" in w._tray.showMessage.call_args.args[1]


def test_notify_update_error_without_a_tray_is_silent(
    qtbot: QtBot, fake_orch: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "app.gui.main_window.QSystemTrayIcon.isSystemTrayAvailable",
        lambda: False,
    )
    with patch("app.gui.main_window.Orchestrator", return_value=fake_orch):
        w = MainWindow()
        qtbot.addWidget(w)
    assert w._tray is None
    w._notify_update_error("boom")  # must not raise


def test_tray_is_built_when_available(
    qtbot: QtBot, fake_orch: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_tray_cls = MagicMock()
    fake_tray_cls.isSystemTrayAvailable.return_value = True
    monkeypatch.setattr("app.gui.main_window.QSystemTrayIcon", fake_tray_cls)
    with patch("app.gui.main_window.Orchestrator", return_value=fake_orch):
        w = MainWindow()
        qtbot.addWidget(w)

    tray = cast("MagicMock", fake_tray_cls.return_value)
    assert w._tray is tray
    tray.setToolTip.assert_called_once_with("llama-gui")
    tray.setContextMenu.assert_called_once()
    tray.show.assert_called_once()
    tray.activated.connect.assert_called_once_with(w._on_tray_activated)


def _shown_window(qtbot: QtBot, fake_orch: MagicMock) -> MainWindow:
    """A MainWindow whose event loop has ticked once."""
    with patch("app.gui.main_window.Orchestrator", return_value=fake_orch):
        w = MainWindow()
        qtbot.addWidget(w)
        w.show()
        qtbot.wait(50)
    return w


def test_tray_activation_hides_a_visible_window(
    qtbot: QtBot, fake_orch: MagicMock
) -> None:
    w = _shown_window(qtbot, fake_orch)

    w._on_tray_activated(QSystemTrayIcon.ActivationReason.Trigger)

    assert not w.isVisible()


def test_tray_activation_restores_a_minimized_window(
    qtbot: QtBot, fake_orch: MagicMock
) -> None:
    with patch("app.gui.main_window.Orchestrator", return_value=fake_orch):
        w = MainWindow()
        qtbot.addWidget(w)
        w.showMinimized()
        qtbot.wait(50)

        w._on_tray_activated(QSystemTrayIcon.ActivationReason.DoubleClick)

    assert w.isVisible()


def test_tray_activation_other_reason_keeps_the_window(
    qtbot: QtBot, fake_orch: MagicMock
) -> None:
    w = _shown_window(qtbot, fake_orch)

    w._on_tray_activated(QSystemTrayIcon.ActivationReason.Context)

    assert w.isVisible()


def test_first_run_dialog_shows_when_nothing_resolves(
    qtbot: QtBot, fake_orch: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_orch.first_run_needed.return_value = True
    with (
        patch("app.gui.main_window.Orchestrator", return_value=fake_orch),
        patch("app.gui.main_window.FirstRunDialog") as dialog_cls,
    ):
        dialog_cls.return_value.exec.return_value = 0
        w = MainWindow()
        qtbot.addWidget(w)
        qtbot.wait(50)  # let the singleShot first-run check fire

    assert dialog_cls.called
    assert dialog_cls.return_value.exec.called


def test_close_survives_a_failing_refresh_stop(
    qtbot: QtBot, fake_orch: MagicMock
) -> None:
    with patch("app.gui.main_window.Orchestrator", return_value=fake_orch):
        w = MainWindow()
        qtbot.addWidget(w)

        def _boom() -> None:
            raise RuntimeError("refresh stop failed")

        w._dashboard.stop_refresh = _boom  # type: ignore[method-assign]
        assert w.close()


def test_close_survives_a_failing_timer_stop(
    qtbot: QtBot, fake_orch: MagicMock
) -> None:
    with patch("app.gui.main_window.Orchestrator", return_value=fake_orch):
        w = MainWindow()
        qtbot.addWidget(w)

        def _boom() -> None:
            raise RuntimeError("timer stop failed")

        w._update_timer.stop = _boom  # type: ignore[method-assign]
        assert w.close()


def test_close_survives_a_failing_server_stop(
    qtbot: QtBot, fake_orch: MagicMock
) -> None:
    fake_orch.stop.side_effect = RuntimeError("stop failed")
    with patch("app.gui.main_window.Orchestrator", return_value=fake_orch):
        w = MainWindow()
        qtbot.addWidget(w)
        assert w.close()
    assert fake_orch.stop.called
