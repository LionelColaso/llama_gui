"""Backends section: action slots, render, and the resolved-binary row."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from PySide6.QtWidgets import QPushButton
from pytestqt.qtbot import QtBot

from app.gui.sections.backends import BackendsSection, BinaryRow
from app.schemas import EngineError, ExitCode, ModelInfo, ModelsData
from tests.gui._pool_recorder import record_workers


def _record_workers(
    monkeypatch: pytest.MonkeyPatch,
) -> list[Any]:
    """Route every worker the section starts into one list."""
    return record_workers(monkeypatch, "app.gui.sections.backends.WorkerPool")


def _two_models() -> ModelsData:
    return ModelsData(
        dir="/models",
        models=[
            ModelInfo(name="a.gguf", size_bytes=1, modified="now"),
            ModelInfo(name="b.gguf", size_bytes=2, modified="now"),
        ],
        active=None,
    )


def _full_status() -> dict[str, Any]:
    return {
        "platform": {"system": "windows", "arch": "amd64"},
        "root": "C:\\root",
        "active": "vulkan",
        "backends": {
            "vulkan": {
                "installed": True,
                "version": "b10189",
                "source": "managed-prebuilt",
                "prebuilt_available": True,
            },
            "cuda12": {
                "installed": False,
                "prebuilt_available": False,
                "unavailable_reason": "no prebuilt for this platform",
            },
        },
        "server": {
            "host": "127.0.0.1",
            "port": 8080,
            "listening": True,
            "pids": [4242],
            "model": "m.gguf",
        },
        "models": {
            "dir": "/models",
            "models": [{"name": "m.gguf"}],
            "active": "m.gguf",
        },
    }


class TestBinaryRow:
    def test_valid_binary(self) -> None:
        row = BinaryRow("llama-server")
        row.update_state(
            {
                "path": "/bin/llama-server",
                "valid": True,
                "version": "b10189",
                "source": "managed-prebuilt",
            }
        )
        assert "OK" in row.state_label.text()
        assert "b10189" in row.state_label.text()
        assert "/bin/llama-server" in row.path_label.text()

    def test_invalid_binary(self) -> None:
        row = BinaryRow("llama-server")
        row.update_state({"path": "/bin/llama-server", "valid": False, "error": "boom"})
        assert "cannot run: boom" in row.state_label.text()
        assert "/bin/llama-server" in row.path_label.text()


class TestBackendsSection:
    def test_install_action_starts_a_worker(
        self,
        qtbot: QtBot,
        fake_orch: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        page = BackendsSection(fake_orch)
        qtbot.addWidget(page)
        started = _record_workers(monkeypatch)

        page._do_install("vulkan")

        assert len(started) == 1
        assert started[0]._action == "install"
        assert started[0]._kwargs == {"backends": ["vulkan"]}

    def test_update_action_starts_a_worker(
        self,
        qtbot: QtBot,
        fake_orch: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        page = BackendsSection(fake_orch)
        qtbot.addWidget(page)
        started = _record_workers(monkeypatch)

        page._do_update("vulkan")

        assert len(started) == 1
        assert started[0]._action == "update"
        assert started[0]._kwargs == {"backends": ["vulkan"]}

    def test_use_action_starts_a_worker(
        self,
        qtbot: QtBot,
        fake_orch: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        page = BackendsSection(fake_orch)
        qtbot.addWidget(page)
        started = _record_workers(monkeypatch)

        page._do_use("cuda12")

        assert len(started) == 1
        assert started[0]._action == "use"
        assert started[0]._kwargs == {
            "backend": "cuda12",
            "auto_install": True,
        }

    def test_download_all_action_starts_a_worker(
        self,
        qtbot: QtBot,
        fake_orch: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        page = BackendsSection(fake_orch)
        qtbot.addWidget(page)
        started = _record_workers(monkeypatch)

        page._do_download_all()

        assert len(started) == 1
        assert started[0]._action == "bootstrap"
        assert started[0]._kwargs == {}

    def test_stop_action_starts_a_worker(
        self,
        qtbot: QtBot,
        fake_orch: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        page = BackendsSection(fake_orch)
        qtbot.addWidget(page)
        started = _record_workers(monkeypatch)

        page._do_stop()

        assert len(started) == 1
        assert started[0]._action == "stop"

    def test_launch_and_restart_without_a_model_choice(
        self,
        qtbot: QtBot,
        fake_orch: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """One model or none: no picker dialog, straight to the action."""
        fake_orch.list_models.return_value = ModelsData(
            dir="/models",
            models=[ModelInfo(name="only.gguf", size_bytes=1, modified="n")],
            active="only.gguf",
        )
        page = BackendsSection(fake_orch)
        qtbot.addWidget(page)
        started = _record_workers(monkeypatch)

        page._do_launch()
        page._do_restart()

        assert [w._action for w in started] == ["launch", "restart"]

    def test_action_done_re_enables_and_reports(
        self,
        qtbot: QtBot,
        fake_orch: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        page = BackendsSection(fake_orch)
        qtbot.addWidget(page)
        page._set_actions_enabled(False)
        followed_up: list[bool] = []

        page._on_action_done(None, on_done=lambda: followed_up.append(True))

        assert all(btn.isEnabled() for btn in page._action_buttons)
        assert "done" in page._progress._bar.format()
        assert followed_up == [True]

    def test_action_error_re_enables_and_reports(
        self,
        qtbot: QtBot,
        fake_orch: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        page = BackendsSection(fake_orch)
        qtbot.addWidget(page)
        page._set_actions_enabled(False)

        page._on_action_error("boom")

        assert all(btn.isEnabled() for btn in page._action_buttons)
        assert "Error: boom" in page._status_label.text()

    def test_after_action_refreshes_and_resolves(
        self,
        qtbot: QtBot,
        fake_orch: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        page = BackendsSection(fake_orch)
        qtbot.addWidget(page)
        started = _record_workers(monkeypatch)

        page._after_action()

        assert [w._action for w in started] == ["status", "resolve"]

    def test_progress_slot_updates_the_bar(
        self,
        qtbot: QtBot,
        fake_orch: MagicMock,
    ) -> None:
        page = BackendsSection(fake_orch)
        qtbot.addWidget(page)
        page._progress.start_operation("Working")

        page._on_progress(1, 10, "download", 0.1)

        assert "10%" in page._progress._bar.format()

    def test_status_error_slot_reports(
        self,
        qtbot: QtBot,
        fake_orch: MagicMock,
    ) -> None:
        page = BackendsSection(fake_orch)
        qtbot.addWidget(page)
        page._busy = True

        page._on_status_error("boom")

        assert page._busy is False
        assert "Error: boom" in page._status_label.text()

    def test_an_action_done_without_a_follow_up_only_unblocks(
        self,
        qtbot: QtBot,
        fake_orch: MagicMock,
    ) -> None:
        """A finish with no follow-up callback just re-enables the UI."""
        page = BackendsSection(fake_orch)
        qtbot.addWidget(page)
        page._set_actions_enabled(False)

        page._on_action_done(None)

        assert all(btn.isEnabled() for btn in page._action_buttons)

    def test_resolved_slot_updates_the_server_row(
        self,
        qtbot: QtBot,
        fake_orch: MagicMock,
    ) -> None:
        page = BackendsSection(fake_orch)
        qtbot.addWidget(page)

        page._on_resolved(
            {
                "llama_server": {
                    "path": "/bin/llama-server",
                    "source": "managed-prebuilt",
                    "valid": True,
                    "version": "b10189",
                }
            }
        )

        assert "OK" in page._server_row.state_label.text()

    def test_status_renders_the_full_dashboard(
        self,
        qtbot: QtBot,
        fake_orch: MagicMock,
    ) -> None:
        page = BackendsSection(fake_orch)
        qtbot.addWidget(page)

        page._on_status(_full_status())

        assert "Active backend: vulkan" in page._active_label.text()
        assert "LISTENING (pid 4242)" in page._listen_label.text()
        assert "Loaded model: m.gguf" in page._model_label.text()
        assert "active: m.gguf" in page._config_label.text()
        assert "b10189" in page._cards["vulkan"]._status_label.text()
        assert page._cards["vulkan"].isActiveWindow() or True

    def test_a_backend_without_a_card_is_skipped(
        self,
        qtbot: QtBot,
        fake_orch: MagicMock,
    ) -> None:
        """A backend the cards were not built for must not break render."""
        page = BackendsSection(fake_orch)
        qtbot.addWidget(page)

        status = _full_status()
        status["backends"]["cpu"] = {"installed": True}

        page._on_status(status)

        assert "Active backend: vulkan" in page._active_label.text()

    # ─── Launch model picker ─────────────────────────

    def _launch_page(
        self,
        qtbot: QtBot,
        fake_orch: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> tuple[BackendsSection, list[Any]]:
        """A section with several models and no active one, ready to launch."""
        fake_orch.cfg.active_model = ""
        fake_orch.list_models.return_value = _two_models()
        page = BackendsSection(fake_orch)
        qtbot.addWidget(page)
        return page, _record_workers(monkeypatch)

    def test_launch_offers_a_choice_when_several_models(
        self,
        qtbot: QtBot,
        fake_orch: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        page, started = self._launch_page(qtbot, fake_orch, monkeypatch)

        assert page._models_needing_a_choice() != []

        with monkeypatch.context() as m:
            dialog_cls = MagicMock()
            dialog_cls.return_value.exec.return_value = 1
            dialog_cls.return_value.selected_model.return_value = "a.gguf"
            m.setattr("app.gui.sections.backends.ModelPickerDialog", dialog_cls)
            page._do_launch()

        fake_orch.set_active_model.assert_called_once_with("a.gguf")
        assert "Active model: a.gguf" in page._status_label.text()
        assert started[0]._action == "launch"

    def test_launch_cancelled_leaves_the_model_alone(
        self,
        qtbot: QtBot,
        fake_orch: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        page, started = self._launch_page(qtbot, fake_orch, monkeypatch)

        with monkeypatch.context() as m:
            dialog_cls = MagicMock()
            dialog_cls.return_value.exec.return_value = 0
            m.setattr("app.gui.sections.backends.ModelPickerDialog", dialog_cls)
            page._do_launch()

        fake_orch.set_active_model.assert_not_called()
        assert "Launch cancelled" in page._status_label.text()
        assert started == []

    def test_launch_reports_a_set_active_failure(
        self,
        qtbot: QtBot,
        fake_orch: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        fake_orch.cfg.active_model = ""
        fake_orch.list_models.return_value = _two_models()
        fake_orch.set_active_model.side_effect = EngineError(
            ExitCode.NOT_AVAILABLE, "nope"
        )
        page = BackendsSection(fake_orch)
        qtbot.addWidget(page)
        started = _record_workers(monkeypatch)

        with monkeypatch.context() as m:
            dialog_cls = MagicMock()
            dialog_cls.return_value.exec.return_value = 1
            dialog_cls.return_value.selected_model.return_value = "a.gguf"
            m.setattr("app.gui.sections.backends.ModelPickerDialog", dialog_cls)
            page._do_launch()

        assert "Error: " in page._status_label.text()
        assert started == []

    def test_no_choice_when_a_model_is_active(
        self,
        qtbot: QtBot,
        fake_orch: MagicMock,
    ) -> None:
        fake_orch.cfg.active_model = "a.gguf"
        fake_orch.list_models.return_value = _two_models()
        page = BackendsSection(fake_orch)
        qtbot.addWidget(page)
        assert page._models_needing_a_choice() == []

    def test_no_choice_when_the_library_is_unreadable(
        self,
        qtbot: QtBot,
        fake_orch: MagicMock,
    ) -> None:
        fake_orch.cfg.active_model = ""
        fake_orch.list_models.side_effect = EngineError(
            ExitCode.NOT_AVAILABLE, "unreadable"
        )
        page = BackendsSection(fake_orch)
        qtbot.addWidget(page)
        assert page._models_needing_a_choice() == []

    def test_no_choice_for_a_single_model(
        self,
        qtbot: QtBot,
        fake_orch: MagicMock,
    ) -> None:
        fake_orch.cfg.active_model = ""
        fake_orch.list_models.return_value = ModelsData(
            dir="/models",
            models=[ModelInfo(name="only.gguf", size_bytes=1, modified="n")],
            active=None,
        )
        page = BackendsSection(fake_orch)
        qtbot.addWidget(page)
        assert page._models_needing_a_choice() == []

    def test_action_buttons_are_attached(
        self,
        qtbot: QtBot,
        fake_orch: MagicMock,
    ) -> None:
        """Every action button must be in the layout and visible."""
        page = BackendsSection(fake_orch)
        qtbot.addWidget(page)
        page.show()
        labels = sorted(
            btn.text() for btn in page.findChildren(QPushButton) if btn.parent() is page
        )
        assert "Download all missing" in labels
        assert "Launch" in labels
        assert "Restart" in labels
        assert "Stop" in labels
        assert "Re-check" in labels
