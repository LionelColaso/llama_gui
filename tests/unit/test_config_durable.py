"""Durable config: atomic save, corrupt-file recovery, unknown-key preservation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from llamagui.config import AppConfig, PointedPaths, config_file


def test_save_then_load_round_trips(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.json"
    cfg = AppConfig(
        root=str(tmp_path / "root"),
        host="0.0.0.0",
        port=9999,
        install_source="prebuilt",
        bundle_cuda_runtime="auto",
        pointed=PointedPaths(llama_server="/opt/llama-server"),
        source_priority=["pointed", "managed", "system"],
        first_run_complete=True,
    )
    cfg.save(cfg_path)

    loaded = AppConfig.load(cfg_path)
    assert loaded.root == cfg.root
    assert loaded.port == 9999
    assert loaded.install_source == "prebuilt"
    assert loaded.pointed.llama_server == "/opt/llama-server"
    assert loaded.first_run_complete is True


def test_save_is_atomic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A crash mid-save must not leave a corrupt (partial) config file."""
    cfg_path = tmp_path / "config.json"
    AppConfig().save(cfg_path)

    real_replace = Path.replace

    def _boom(self: Path, target: str | Path) -> Path:
        raise RuntimeError("simulated crash")

    monkeypatch.setattr(Path, "replace", _boom)
    with pytest.raises(RuntimeError):
        AppConfig(port=1234).save(cfg_path)
    monkeypatch.setattr(Path, "replace", real_replace)

    # Original file is untouched and still valid.
    assert AppConfig.load(cfg_path).port == 8080


def test_corrupt_config_is_backed_up(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text("{ this is not valid json ", encoding="utf-8")

    loaded = AppConfig.load(cfg_path)
    assert loaded is not None  # falls back to defaults

    backups = list(tmp_path.glob("config.corrupt-*.json"))
    assert backups, "expected a .corrupt backup of the bad file"
    # load() never silently overwrites the corrupt file; it preserves it and
    # returns defaults. The good file is restored only on the next explicit save.
    assert not cfg_path.is_file()


def test_unknown_keys_are_preserved(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.json"
    raw = {
        "root": str(tmp_path),
        "port": 8080,
        "future_setting": {"nested": True},
        "experimental_flag": 7,
    }
    cfg_path.write_text(json.dumps(raw), encoding="utf-8")

    loaded = AppConfig.load(cfg_path)
    assert loaded.root == str(tmp_path)

    loaded.save(cfg_path)
    reparsed = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert reparsed["future_setting"] == {"nested": True}
    assert reparsed["experimental_flag"] == 7


def test_load_missing_returns_defaults(tmp_path: Path) -> None:
    loaded = AppConfig.load(tmp_path / "absent" / "config.json")
    assert loaded.port == 8080
    assert loaded.root


def test_config_path_default_location(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("LLAMAGUI_CONFIG_DIR", str(tmp_path))
    assert config_file() == tmp_path / "config.json"


def test_save_with_pointed_separate_paths(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.json"
    cfg = AppConfig(pointed=PointedPaths(llama_server="/a/x", llama_swap="/b/y"))
    cfg.save(cfg_path)
    loaded = AppConfig.load(cfg_path)
    assert loaded.pointed.llama_server == "/a/x"
    assert loaded.pointed.llama_swap == "/b/y"


def test_token_is_never_persisted(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.json"
    cfg = AppConfig(token="super-secret")
    cfg.save(cfg_path)
    reparsed = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert reparsed.get("token") in (None, "")
