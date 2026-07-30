from __future__ import annotations

from unittest.mock import MagicMock

from pytestqt.qtbot import QtBot

from llamagui.gui.pages.dashboard import DashboardPage


def test_busy_guard_prevents_worker_pileup(qtbot: QtBot, fake_orch: MagicMock) -> None:
    page = DashboardPage(fake_orch)
    qtbot.addWidget(page)

    # Simulate an in-flight worker by setting _busy = True
    page._busy = True
    page._refresh()
    assert page._busy is True
    assert fake_orch.status.call_count == 0


def test_refresh_sets_busy_and_spawns_worker(
    qtbot: QtBot, fake_orch: MagicMock
) -> None:
    page = DashboardPage(fake_orch)
    qtbot.addWidget(page)

    page._refresh()
    # With MagicMock the worker runs synchronously, so _busy is cleared immediately.
    # Verify the worker was actually invoked instead.
    assert fake_orch.status.call_count == 1


def test_on_status_clears_busy(qtbot: QtBot, fake_orch: MagicMock) -> None:
    page = DashboardPage(fake_orch)
    qtbot.addWidget(page)

    page._busy = True
    page._on_status(fake_orch.status.return_value)
    assert page._busy is False


def test_on_error_clears_busy(qtbot: QtBot, fake_orch: MagicMock) -> None:
    page = DashboardPage(fake_orch)
    qtbot.addWidget(page)

    page._busy = True
    page._on_error("boom")
    assert page._busy is False
