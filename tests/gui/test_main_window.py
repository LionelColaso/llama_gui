from __future__ import annotations

from unittest.mock import MagicMock

from pytestqt.qtbot import QtBot

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


def test_main_window_creates(qtbot: QtBot, fake_orch: MagicMock) -> None:
    from unittest.mock import patch

    with patch("app.gui.main_window.Orchestrator", return_value=fake_orch):
        w = MainWindow()
        qtbot.addWidget(w)
        assert w.windowTitle() == "llama-gui"


def test_navigation_switches_pages(qtbot: QtBot, fake_orch: MagicMock) -> None:
    from unittest.mock import patch

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


def _auto_update_toast_message(
    qtbot: QtBot, fake_orch: MagicMock, data: InstallData
) -> str:
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
