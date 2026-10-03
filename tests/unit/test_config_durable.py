"""Durable config: atomic save, corrupt-file recovery, unknown-key preservation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from llamagui.config import AppConfig, config_file


def test_save_then_load_round_trips(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.json"
    cfg = AppConfig(
        root=str(tmp_path / "root"),
        host="0.0.0.0",
        port=9999,
        bundle_cuda_runtime="auto",
        use_os_llama_server=True,
        first_run_complete=True,
    )
    cfg.save(cfg_path)

    loaded = AppConfig.load(cfg_path)
    assert loaded.root == cfg.root
    assert loaded.port == 9999
    assert loaded.use_os_llama_server is True
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


# ─── test isolation (autouse fixture in tests/conftest.py) ─────────────────


def test_default_config_path_is_inside_tmp_path(tmp_path: Path) -> None:
    """Regression: tests that called AppConfig.load() with no path read the
    developer's real %APPDATA%/llamagui/config.json, and the CLI writes it."""
    resolved = config_file()
    assert str(resolved).startswith(str(tmp_path)), (
        f"config_file() escaped the test sandbox: {resolved}"
    )


def test_default_root_is_inside_tmp_path(tmp_path: Path) -> None:
    """The managed root must not be the developer's real ~/.llamagui either."""
    from llamagui.paths import default_root

    root = default_root()
    assert str(root).startswith(str(tmp_path)), (
        f"default_root() escaped the test sandbox: {root}"
    )


def test_saving_the_default_config_writes_into_tmp_path(tmp_path: Path) -> None:
    """A test that saves the default config must not touch the real file."""
    cfg = AppConfig(root=str(tmp_path / "root"))
    cfg.save()
    written = config_file()
    assert written.is_file()
    assert str(written).startswith(str(tmp_path))


def test_load_missing_returns_defaults(tmp_path: Path) -> None:
    loaded = AppConfig.load(tmp_path / "absent" / "config.json")
    assert loaded.port == 8080


# ─── numeric clamping on load ──────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "key", "expected"),
    [
        (0, "port", 1),
        (-1, "port", 1),
        (99999, "port", 65535),
        (70000, "port", 65535),
        (-5000, "ctx_size", 0),
        (-7, "n_gpu_layers", -1),
        (0, "auto_update_interval_hours", 1),
        (-3, "auto_update_interval_hours", 1),
    ],
)
def test_out_of_range_numbers_are_clamped(
    tmp_path: Path, raw: int, key: str, expected: int
) -> None:
    """A hand-edited or corrupt file must not reach the port probe or the
    llama-server command line with an impossible value."""
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({key: raw}), encoding="utf-8")

    loaded = AppConfig.load(cfg_path)
    assert getattr(loaded, key) == expected


@pytest.mark.parametrize(
    ("key", "raw"),
    [
        ("port", 999_999),
        ("ctx_size", 999_999_999),
        ("n_gpu_layers", 999_999),
        ("auto_update_interval_hours", 999_999),
    ],
)
def test_clamping_is_reported_as_a_load_warning(
    tmp_path: Path, key: str, raw: int
) -> None:
    """Each key has its own range, so the value is chosen per key to exceed it."""
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({key: raw}), encoding="utf-8")

    loaded = AppConfig.load(cfg_path)
    assert any(key in w for w in loaded.load_warnings), (
        f"clamping {key} should be surfaced, got {loaded.load_warnings}"
    )


def test_valid_values_produce_no_clamp_warning(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(
        json.dumps({"port": 8080, "ctx_size": 4096, "n_gpu_layers": 999}),
        encoding="utf-8",
    )

    loaded = AppConfig.load(cfg_path)
    assert loaded.load_warnings == []


def test_ctx_size_zero_still_means_auto(tmp_path: Path) -> None:
    """0 is a legitimate value (llama.cpp default context), not out of range."""
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({"ctx_size": 0}), encoding="utf-8")

    loaded = AppConfig.load(cfg_path)
    assert loaded.ctx_size == 0
    assert loaded.load_warnings == []

    assert loaded.root


def test_config_path_default_location(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("LLAMAGUI_CONFIG_DIR", str(tmp_path))
    assert config_file() == tmp_path / "config.json"


def test_legacy_pointed_keys_preserved_but_ignored(tmp_path: Path) -> None:
    """``pointed`` / ``source_priority`` from old config files survive a save.

    The app no longer reads them (backend location + OS toggle model), but
    durability requires that an upgrade never deletes what was on disk.
    """
    cfg_path = tmp_path / "config.json"
    raw = {
        "root": str(tmp_path),
        "pointed": {"folder": "/a/bin", "llama_server": "/a/x"},
        "source_priority": ["pointed", "managed", "system"],
    }
    cfg_path.write_text(json.dumps(raw), encoding="utf-8")

    loaded = AppConfig.load(cfg_path)
    assert loaded.use_os_llama_server is False

    loaded.save(cfg_path)
    reparsed = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert reparsed["pointed"] == {"folder": "/a/bin", "llama_server": "/a/x"}
    assert reparsed["source_priority"] == ["pointed", "managed", "system"]


def test_token_is_never_persisted(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.json"
    cfg = AppConfig(token="super-secret")
    cfg.save(cfg_path)
    reparsed = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert reparsed.get("token") in (None, "")
