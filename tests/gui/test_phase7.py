from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from pytestqt.qtbot import QtBot

from llamagui.config_yaml import ConfigYaml
from llamagui.gui.pages.models import ModelsPage
from llamagui.gui.pages.settings import SettingsPage
from llamagui.gui.widgets.model_table import ModelTable


class TestConfigYaml:
    def test_load_missing(self, tmp_path: Path) -> None:
        cfg = ConfigYaml(tmp_path / "config.yaml")
        cfg.load()
        assert cfg.get_models() == []

    def test_save_and_round_trip(self, tmp_path: Path) -> None:
        path = tmp_path / "config.yaml"
        cfg = ConfigYaml(path)
        cfg.data["active"] = "vulkan"
        cfg.data["models"] = [
            {"id": "test", "cmd": "llama-server", "group": "", "flags": []},
        ]
        cfg.save()

        cfg2 = ConfigYaml(path)
        cfg2.load()
        assert cfg2.get_active() == "vulkan"
        models = cfg2.get_models()
        assert len(models) == 1
        assert models[0]["id"] == "test"

    def test_add_remove_model(self, tmp_path: Path) -> None:
        cfg = ConfigYaml(tmp_path / "config.yaml")
        cfg.load()
        cfg.add_model({"id": "m1", "cmd": "/usr/bin/llama-server"})
        cfg.add_model({"id": "m2", "cmd": "/usr/local/bin/llama-server"})
        assert len(cfg.get_models()) == 2

        cfg.remove_model(0)
        models = cfg.get_models()
        assert len(models) == 1
        assert models[0]["id"] == "m2"

    def test_update_model(self, tmp_path: Path) -> None:
        cfg = ConfigYaml(tmp_path / "config.yaml")
        cfg.load()
        cfg.add_model({"id": "m1", "cmd": "old"})
        cfg.update_model(0, {"id": "m1", "cmd": "new"})
        assert cfg.get_models()[0]["cmd"] == "new"

    def test_set_active(self, tmp_path: Path) -> None:
        cfg = ConfigYaml(tmp_path / "config.yaml")
        cfg.load()
        assert cfg.get_active() is None
        cfg.set_active("cuda12")
        assert cfg.get_active() == "cuda12"


class TestModelTable:
    def test_empty(self, qtbot: QtBot) -> None:
        mt = ModelTable()
        mt.show()
        qtbot.addWidget(mt)
        assert mt.get_models() == []

    def test_load_and_get(self, qtbot: QtBot) -> None:
        mt = ModelTable()
        qtbot.addWidget(mt)
        models: list[dict[str, Any]] = [
            {"id": "a", "cmd": "cmd_a", "group": "g1", "flags": ["--flag1"]},
            {"id": "b", "cmd": "cmd_b", "group": "", "flags": []},
        ]
        mt.load_models(models)
        result = mt.get_models()
        assert len(result) == 2
        assert result[0]["id"] == "a"
        assert result[0]["flags"] == ["--flag1"]
        assert result[1]["id"] == "b"

    def test_load_skips_empty_id(self, qtbot: QtBot) -> None:
        mt = ModelTable()
        qtbot.addWidget(mt)
        mt.load_models([{"id": "", "cmd": "x"}, {"id": "valid", "cmd": "y"}])
        result = mt.get_models()
        assert len(result) == 1

    def test_get_models_after_edit(self, qtbot: QtBot) -> None:
        mt = ModelTable()
        qtbot.addWidget(mt)
        mt.load_models([{"id": "x", "cmd": "cmd_x", "group": "", "flags": []}])
        items = mt.get_models()
        assert len(items) == 1
        assert items[0]["id"] == "x"


class TestModelsPage:
    def test_creates(self, qtbot: QtBot, fake_orch: MagicMock, tmp_path: Path) -> None:
        cfg_path = tmp_path / "config.yaml"
        page = ModelsPage(fake_orch, cfg_path)
        qtbot.addWidget(page)
        assert page._table is not None
        assert page._active_label is not None


class TestSettingsPage:
    def test_creates(self, qtbot: QtBot, fake_orch: MagicMock) -> None:
        page = SettingsPage(fake_orch)
        qtbot.addWidget(page)
        assert page._root_edit is not None
