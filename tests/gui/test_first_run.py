"""First-run setup: when it is shown, and what it persists.

The dialog is the user's one chance to get a working install, so the decision
to show it must key off something trustworthy (a binary that actually runs),
and both exits -- setup and skip -- must record that the prompt happened, or
the user is nagged on every launch.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

from PySide6.QtWidgets import QDialog
from pytestqt.qtbot import QtBot

from app.config import AppConfig
from app.gui.dialogs.first_run import FirstRunDialog
from app.orchestrator import Orchestrator
from app.resolver import ResolvedBinary
from app.schemas import BootstrapData, ResolveData, ResolvedBinaryData


def _resolved(path: str | None, valid: bool) -> ResolvedBinary:
    return ResolvedBinary(path, None, "b1" if valid else None, valid, None)


# ─── the decision: Orchestrator.first_run_needed ──────────────────────────


def test_dialog_shown_when_nothing_resolves(tmp_path: Any) -> None:
    orch = Orchestrator(AppConfig(root=str(tmp_path)))
    with patch(
        "app.orchestrator.resolve_llama_server",
        return_value=_resolved(None, False),
    ):
        assert orch.first_run_needed() is True


def test_dialog_shown_when_the_binary_cannot_run(tmp_path: Any) -> None:
    """A binary that exists but fails validation must still prompt."""
    orch = Orchestrator(AppConfig(root=str(tmp_path)))
    with patch(
        "app.orchestrator.resolve_llama_server",
        return_value=_resolved("/x/llama-server", False),
    ):
        assert orch.first_run_needed() is True


def test_dialog_not_shown_for_a_working_binary(tmp_path: Any) -> None:
    orch = Orchestrator(AppConfig(root=str(tmp_path)))
    with patch(
        "app.orchestrator.resolve_llama_server",
        return_value=_resolved("/x/llama-server", True),
    ):
        assert orch.first_run_needed() is False


def test_skip_persists_first_run_complete(tmp_path: Any) -> None:
    """Skipping must stick, or the user is asked again on every launch."""
    orch = Orchestrator(AppConfig(root=str(tmp_path)))
    orch.save_config({"first_run_complete": True})

    def _boom(*args: object, **kwargs: object) -> ResolvedBinary:
        raise AssertionError("a completed first run must not probe the binary")

    with patch("app.orchestrator.resolve_llama_server", _boom):
        assert orch.first_run_needed() is False


# ─── the dialog itself ────────────────────────────────────────────────────


def test_skip_marks_the_prompt_done(qtbot: QtBot, fake_orch: MagicMock) -> None:
    dialog = FirstRunDialog(fake_orch)
    qtbot.addWidget(dialog)
    dialog._skip()
    assert fake_orch.save_config.call_args.args[0] == {"first_run_complete": True}
    assert dialog.result() != QDialog.DialogCode.Accepted


def test_use_os_enables_the_toggle_and_completes(
    qtbot: QtBot, fake_orch: MagicMock
) -> None:
    fake_orch.resolve.return_value = ResolveData(
        llama_server=ResolvedBinaryData(path="/x/llama-server", valid=True)
    )
    dialog = FirstRunDialog(fake_orch)
    qtbot.addWidget(dialog)
    dialog._use_os()

    saved = fake_orch.save_config.call_args.args[0]
    assert saved["use_os_llama_server"] is True
    assert saved["first_run_complete"] is True


def test_use_os_disables_its_button_while_checking(
    qtbot: QtBot, fake_orch: MagicMock
) -> None:
    dialog = FirstRunDialog(fake_orch)
    qtbot.addWidget(dialog)
    dialog._use_os()
    assert not dialog._use_os_btn.isEnabled(), "a double-click must not re-save"


def test_successful_download_closes_the_dialog(
    qtbot: QtBot, fake_orch: MagicMock
) -> None:
    dialog = FirstRunDialog(fake_orch)
    qtbot.addWidget(dialog)
    dialog._on_finished(BootstrapData(ready=True, message="done"))
    assert dialog.result() == QDialog.DialogCode.Accepted


def test_failed_download_keeps_the_dialog_open(
    qtbot: QtBot, fake_orch: MagicMock
) -> None:
    dialog = FirstRunDialog(fake_orch)
    qtbot.addWidget(dialog)
    dialog._on_finished(BootstrapData(ready=False, message="could not download"))
    assert dialog.result() != QDialog.DialogCode.Accepted
    assert "could not download" in dialog._status.text()


def test_worker_error_is_surfaced(qtbot: QtBot, fake_orch: MagicMock) -> None:
    dialog = FirstRunDialog(fake_orch)
    qtbot.addWidget(dialog)
    dialog._on_error("network unreachable")
    assert "network unreachable" in dialog._status.text()
    assert dialog._download_btn.isEnabled(), "the user must be able to retry"


def test_unresolvable_os_install_keeps_the_dialog_open(
    qtbot: QtBot, fake_orch: MagicMock
) -> None:
    """Choosing the OS install must verify it actually worked."""
    fake_orch.resolve.return_value = ResolveData(
        llama_server=ResolvedBinaryData(valid=False, error="not on PATH")
    )
    dialog = FirstRunDialog(fake_orch)
    qtbot.addWidget(dialog)
    dialog._on_resolved(fake_orch.resolve.return_value)
    assert dialog.result() != QDialog.DialogCode.Accepted
    assert "not on PATH" in dialog._status.text()


def _pool_recorder(monkeypatch: Any) -> list[Any]:
    """Route every worker the dialog starts into one list."""
    started: list[Any] = []

    class _PoolRecorder:
        @classmethod
        def instance(cls) -> Any:
            return cls

        @classmethod
        def start(cls, worker: Any) -> None:
            started.append(worker)

    monkeypatch.setattr("app.gui.dialogs.first_run.WorkerPool", _PoolRecorder)
    return started


def test_download_starts_a_resumable_bootstrap(
    qtbot: QtBot, fake_orch: MagicMock, monkeypatch: Any
) -> None:
    started = _pool_recorder(monkeypatch)
    dialog = FirstRunDialog(fake_orch)
    qtbot.addWidget(dialog)

    dialog._download()

    assert not dialog._download_btn.isEnabled()
    assert not dialog._progress._bar.isHidden()
    worker = started[-1]
    assert worker._action == "bootstrap"
    assert worker._control is not None, "the download is pausable"


def test_progress_ticks_advance_the_bar(qtbot: QtBot, fake_orch: MagicMock) -> None:
    dialog = FirstRunDialog(fake_orch)
    qtbot.addWidget(dialog)

    dialog._on_progress(50, 100, "download")

    assert dialog._progress._bar.value() == 50
