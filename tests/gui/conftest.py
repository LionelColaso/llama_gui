from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from pytestqt.qtbot import QtBot


def _describe_dict() -> dict[str, Any]:
    from pathlib import Path

    return {
        "root": str(Path.home() / ".llamagui"),
        "port": 8080,
        "default_backend": "vulkan",
        "use_os_llama_server": False,
        "auto_update": False,
        "auto_update_interval_hours": 24,
        "launch_on_start": False,
        "start_minimized": False,
        "theme": "system",
    }


@pytest.fixture
def fake_orch() -> MagicMock:
    from llamagui.config import AppConfig

    orch = MagicMock()
    d = _describe_dict()
    mock_describe = MagicMock()
    mock_describe.model_dump.return_value = d
    orch.describe.return_value = mock_describe
    orch.status.return_value = MagicMock()
    orch.resolve.return_value = MagicMock()
    orch.save_config = MagicMock()
    orch.backend_names.return_value = ["vulkan", "cuda13", "cuda12"]
    # Provide a real AppConfig so the SettingsPage can bind its form fields.
    orch.cfg = AppConfig(
        root=d["root"],
        port=d["port"],
        default_backend=d["default_backend"],
        use_os_llama_server=d["use_os_llama_server"],
        auto_update=d["auto_update"],
        auto_update_interval_hours=d["auto_update_interval_hours"],
        launch_on_start=d["launch_on_start"],
        start_minimized=d["start_minimized"],
        theme=d["theme"],
    )
    return orch


@pytest.fixture(autouse=True)
def _reset_worker_pool(qtbot: QtBot) -> Generator[None, None, None]:
    from PySide6.QtWidgets import QApplication

    from llamagui.gui.worker_pool import WorkerPool

    yield
    # Stop dashboard timers to prevent workers from spawning during GC
    app = QApplication.instance()
    if app is not None:
        qapp = cast(QApplication, app)
        for w in qapp.allWidgets():
            if hasattr(w, "_refresh_timer"):
                w._refresh_timer.stop()
    qtbot.wait(50)
    WorkerPool.reset()


@pytest.fixture
def tmp_root(tmp_path: Path) -> str:
    return str(tmp_path)
