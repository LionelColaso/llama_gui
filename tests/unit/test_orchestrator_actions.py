"""Orchestrator: the actions and branches the main tests skip.

Covers the error exits and plumbing around the happy paths: the
launch precondition ladder, dedicated-flag parsing, relocation
edge cases, and the small module-level helpers.
"""

from __future__ import annotations

import socket
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.backends.catalogue import (
    backend_availability,
    backend_table,
    platform_default_backend,
)
from app.config import AppConfig
from app.model_store import ModelDownloadError
from app.orchestrator import (
    Orchestrator,
    _is_within,
    _linked_backend,
    _marker_version,
    _same_location,
)
from app.resolver import ResolvedBinary
from app.schemas import EngineError, ExitCode


def _valid_binary() -> ResolvedBinary:
    return ResolvedBinary("/bin/llama-server", None, "b10189", True, None)


def _one_model(tmp_path: Path) -> Path:
    models = tmp_path / "models"
    models.mkdir(parents=True, exist_ok=True)
    (models / "m.gguf").write_text("x", encoding="utf-8")
    return models


def _unavailable_backend() -> str:
    for row in backend_table():
        if not backend_availability(row["name"])["prebuilt"]:
            return str(row["name"])
    raise AssertionError("every backend has a prebuilt here")


def _resolve_valid(cfg: AppConfig, validate: bool = False) -> ResolvedBinary:
    return _valid_binary()


def _resolve_nothing(cfg: AppConfig, validate: bool = False) -> ResolvedBinary:
    return ResolvedBinary("", None, None, False)


def _launch_returns_42(*_args: object, **_kwargs: object) -> int:
    return 42


def _stop_locked_noop(_self: object, grace: float | None = None) -> None:
    return None


def _download_model_stub(_url: str, _directory: str) -> MagicMock:
    return MagicMock()


def _install_backend_stub(*_args: object, **_kwargs: object) -> dict[str, object]:
    return {
        "name": "vulkan",
        "status": "ok",
        "version": "b10189",
        "bytes": 10,
    }


# ─── Reads ─────────────────────────────────────────────────


def test_log_tail_is_empty_without_a_log(tmp_path: Path) -> None:
    orch = Orchestrator(AppConfig(root=str(tmp_path)))
    assert orch.log_tail() == []


def test_pending_downloads_is_empty_without_interrupted_files(
    tmp_path: Path,
) -> None:
    orch = Orchestrator(AppConfig(root=str(tmp_path)))
    data = orch.pending_downloads()
    assert data.tasks == []
    assert data.models_dir == str(tmp_path / "models")


# ─── Launch ladder ─────────────────────────────────────────


def test_launch_without_a_binary_raises(tmp_path: Path) -> None:
    orch = Orchestrator(AppConfig(root=str(tmp_path)))
    with pytest.raises(EngineError) as excinfo:
        orch.launch()
    assert excinfo.value.exit_code == ExitCode.NOT_AVAILABLE


def test_launch_rejects_invalid_server_options(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _one_model(tmp_path)
    orch = Orchestrator(AppConfig(root=str(tmp_path)))
    monkeypatch.setattr(
        "app.orchestrator.resolve_llama_server",
        _resolve_valid,
    )
    orch.save_config({"server_options": {"--ctx-size": "banana"}})
    with pytest.raises(EngineError) as excinfo:
        orch.launch()
    assert excinfo.value.exit_code == ExitCode.BAD_ARGUMENT


def test_launch_stops_the_previous_instance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A held port means our own instance is still up: stop it first."""
    _one_model(tmp_path)
    orch = Orchestrator(AppConfig(root=str(tmp_path)))
    monkeypatch.setattr(
        "app.orchestrator.resolve_llama_server",
        _resolve_valid,
    )
    monkeypatch.setattr(
        "app.orchestrator.launch_llama_server",
        _launch_returns_42,
    )
    monkeypatch.setattr(Orchestrator, "_stop_locked", _stop_locked_noop)
    with socket.socket() as holder:
        holder.bind((orch.cfg.host, 0))
        holder.listen(1)
        orch.cfg.port = holder.getsockname()[1]
        assert orch.launch() == 42


def test_stop_passes_the_grace_period(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, float | None] = {}

    def _fake_stop(
        root: Path,
        host: str | None = None,
        port: int | None = None,
        grace: float | None = None,
    ) -> dict[str, object]:
        seen["grace"] = grace
        return {}

    monkeypatch.setattr("app.orchestrator.stop_processes", _fake_stop)
    orch = Orchestrator(AppConfig(root=str(tmp_path)))
    orch.stop(grace=0.5)
    assert seen["grace"] == 0.5


# ─── Model picking ─────────────────────────────────────────


def test_resolve_model_path_prefers_the_active_model(tmp_path: Path) -> None:
    models = _one_model(tmp_path)
    (models / "other.gguf").write_text("x", encoding="utf-8")
    orch = Orchestrator(AppConfig(root=str(tmp_path)))
    orch.save_config({"active_model": "other.gguf"})
    assert orch._resolve_model_path() == models / "other.gguf"


def test_resolve_model_path_picks_the_only_model(tmp_path: Path) -> None:
    models = _one_model(tmp_path)
    orch = Orchestrator(AppConfig(root=str(tmp_path)))
    assert orch._resolve_model_path() == models / "m.gguf"


def test_resolve_model_path_without_models_raises(tmp_path: Path) -> None:
    (tmp_path / "models").mkdir()
    orch = Orchestrator(AppConfig(root=str(tmp_path)))
    with pytest.raises(EngineError) as excinfo:
        orch._resolve_model_path()
    assert excinfo.value.exit_code == ExitCode.NOT_AVAILABLE


def test_resolve_model_path_with_several_models_raises(
    tmp_path: Path,
) -> None:
    models = _one_model(tmp_path)
    for name in ("a.gguf", "b.gguf"):
        (models / name).write_text("x", encoding="utf-8")
    orch = Orchestrator(AppConfig(root=str(tmp_path)))
    with pytest.raises(EngineError) as excinfo:
        orch._resolve_model_path()
    assert excinfo.value.exit_code == ExitCode.NOT_AVAILABLE


def test_list_models_drops_a_missing_active_model(tmp_path: Path) -> None:
    _one_model(tmp_path)
    orch = Orchestrator(AppConfig(root=str(tmp_path)))
    orch.save_config({"active_model": "gone.gguf"})
    assert orch.list_models().active is None


# ─── Model actions ─────────────────────────────────────────


def test_download_model_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _one_model(tmp_path)
    orch = Orchestrator(AppConfig(root=str(tmp_path)))
    monkeypatch.setattr(
        "app.orchestrator.download_model",
        _download_model_stub,
    )
    assert orch.download_model("https://x/m.gguf") is not None


def test_download_model_failure_maps_to_network_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _one_model(tmp_path)
    orch = Orchestrator(AppConfig(root=str(tmp_path)))

    def _fail(url: str, directory: Path) -> MagicMock:
        raise ModelDownloadError("boom")

    monkeypatch.setattr("app.orchestrator.download_model", _fail)
    with pytest.raises(EngineError) as excinfo:
        orch.download_model("https://x/m.gguf")
    assert excinfo.value.exit_code == ExitCode.NETWORK_ERROR


def test_set_active_model_unknown_raises(tmp_path: Path) -> None:
    _one_model(tmp_path)
    orch = Orchestrator(AppConfig(root=str(tmp_path)))
    with pytest.raises(EngineError) as excinfo:
        orch.set_active_model("nope.gguf")
    assert excinfo.value.exit_code == ExitCode.NOT_AVAILABLE


def test_set_active_model_persists(tmp_path: Path) -> None:
    _one_model(tmp_path)
    orch = Orchestrator(AppConfig(root=str(tmp_path)))
    data = orch.set_active_model("m.gguf")
    assert data.active == "m.gguf"


def test_remove_model_missing_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _one_model(tmp_path)
    orch = Orchestrator(AppConfig(root=str(tmp_path)))

    def _missing(directory: Path, name: str) -> None:
        raise FileNotFoundError(name)

    monkeypatch.setattr("app.orchestrator.remove_model", _missing)
    with pytest.raises(EngineError) as excinfo:
        orch.remove_model("nope.gguf")
    assert excinfo.value.exit_code == ExitCode.NOT_AVAILABLE


def test_remove_model_rejects_a_bad_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _one_model(tmp_path)
    orch = Orchestrator(AppConfig(root=str(tmp_path)))

    def _rejected(directory: Path, name: str) -> None:
        raise ModelDownloadError("../escape")

    monkeypatch.setattr("app.orchestrator.remove_model", _rejected)
    with pytest.raises(EngineError) as excinfo:
        orch.remove_model("../escape")
    assert excinfo.value.exit_code == ExitCode.BAD_ARGUMENT


def test_remove_model_clears_the_active_model(tmp_path: Path) -> None:
    models = _one_model(tmp_path)
    orch = Orchestrator(AppConfig(root=str(tmp_path)))
    orch.save_config({"active_model": "m.gguf"})
    orch.remove_model("m.gguf")
    assert not (models / "m.gguf").exists()
    assert orch.cfg.active_model in (None, "")


# ─── Pending downloads ─────────────────────────────────────


def test_discard_download_refuses_outside_dirs(tmp_path: Path) -> None:
    orch = Orchestrator(AppConfig(root=str(tmp_path)))
    outside = tmp_path / "outside.part"
    with pytest.raises(EngineError) as excinfo:
        orch.discard_download(str(outside))
    assert excinfo.value.exit_code == ExitCode.BAD_ARGUMENT


def test_discard_download_removes_a_pending_file(tmp_path: Path) -> None:
    """The API takes the final path; the engine derives the ``.part``."""
    models = _one_model(tmp_path)
    part = models / "m.gguf.part"
    part.write_text("partial", encoding="utf-8")
    orch = Orchestrator(AppConfig(root=str(tmp_path)))
    data = orch.discard_download(str(models / "m.gguf"))
    assert not part.exists()
    assert data.models_dir == str(models)


# ─── Server arguments ──────────────────────────────────────


def test_save_model_server_config_rejects_unknown_flags(
    tmp_path: Path,
) -> None:
    orch = Orchestrator(AppConfig(root=str(tmp_path)))
    with pytest.raises(EngineError) as excinfo:
        orch.save_model_server_config("m.gguf", {"--nope": "1"})
    assert excinfo.value.exit_code == ExitCode.BAD_ARGUMENT


def test_save_model_server_config_ignores_blank_values(
    tmp_path: Path,
) -> None:
    orch = Orchestrator(AppConfig(root=str(tmp_path)))
    orch.save_model_server_config("m.gguf", {"--ctx-size": ""})
    assert orch.cfg.model_server_options == {"m.gguf": {}}


def test_clear_server_args_with_use_global_returns_to_the_globals(
    tmp_path: Path,
) -> None:
    orch = Orchestrator(AppConfig(root=str(tmp_path)))
    orch.set_server_arg("--ctx-size", "2048", model="m.gguf")
    data = orch.clear_server_args(model="m.gguf", use_global=True)
    assert "m.gguf" not in orch.cfg.model_server_options
    assert data["mode"] == "global"


def test_preview_command_without_a_model(tmp_path: Path) -> None:
    orch = Orchestrator(AppConfig(root=str(tmp_path)))
    cmd = orch.preview_command()
    assert cmd[0] == "llama-server"
    assert "<model>" in cmd


def test_dedicated_value_of_an_unknown_flag_is_empty(tmp_path: Path) -> None:
    orch = Orchestrator(AppConfig(root=str(tmp_path)))
    assert orch._dedicated_value("--nope") == ""


def test_set_dedicated_host_blank_resets_to_default(tmp_path: Path) -> None:
    orch = Orchestrator(AppConfig(root=str(tmp_path)))
    orch.set_server_arg("--host", "")
    assert orch.cfg.host == "127.0.0.1"


def test_set_dedicated_port_blank_resets_to_default(tmp_path: Path) -> None:
    orch = Orchestrator(AppConfig(root=str(tmp_path)))
    orch.set_server_arg("--port", "")
    assert orch.cfg.port == 8080


def test_set_dedicated_port_rejects_non_integers(tmp_path: Path) -> None:
    orch = Orchestrator(AppConfig(root=str(tmp_path)))
    with pytest.raises(EngineError) as excinfo:
        orch.set_server_arg("--port", "http")
    assert excinfo.value.exit_code == ExitCode.BAD_ARGUMENT


def test_set_dedicated_ctx_size_auto_resets(tmp_path: Path) -> None:
    """ "auto" is stored as 0: the config clamps negatives, and the
    launch path omits ``-c`` for any non-positive size."""
    orch = Orchestrator(AppConfig(root=str(tmp_path)))
    orch.set_server_arg("--ctx-size", "auto")
    assert orch.cfg.ctx_size == 0


def test_set_dedicated_ctx_size_rejects_non_integers(
    tmp_path: Path,
) -> None:
    orch = Orchestrator(AppConfig(root=str(tmp_path)))
    with pytest.raises(EngineError) as excinfo:
        orch.set_server_arg("--ctx-size", "huge")
    assert excinfo.value.exit_code == ExitCode.BAD_ARGUMENT


def test_set_dedicated_n_gpu_layers_auto_resets(tmp_path: Path) -> None:
    orch = Orchestrator(AppConfig(root=str(tmp_path)))
    orch.set_server_arg("--n-gpu-layers", "all")
    assert orch.cfg.n_gpu_layers == -1


def test_set_dedicated_n_gpu_layers_rejects_non_integers(
    tmp_path: Path,
) -> None:
    orch = Orchestrator(AppConfig(root=str(tmp_path)))
    with pytest.raises(EngineError) as excinfo:
        orch.set_server_arg("--n-gpu-layers", "many")
    assert excinfo.value.exit_code == ExitCode.BAD_ARGUMENT


# ─── Obtain / switch ───────────────────────────────────────


def test_preferred_backend_falls_back_to_the_platform_default(
    tmp_path: Path,
) -> None:
    orch = Orchestrator(AppConfig(root=str(tmp_path)))
    orch.cfg.default_backend = "not-a-backend"
    assert orch._preferred_backend() == platform_default_backend()


def test_install_rejects_an_unknown_backend(tmp_path: Path) -> None:
    orch = Orchestrator(AppConfig(root=str(tmp_path)))
    with pytest.raises(EngineError) as excinfo:
        orch.install(["not-a-backend"])
    assert excinfo.value.exit_code == ExitCode.BAD_ARGUMENT


def test_obtain_backend_without_a_prebuilt_raises(tmp_path: Path) -> None:
    orch = Orchestrator(AppConfig(root=str(tmp_path)))
    with pytest.raises(EngineError) as excinfo:
        orch._obtain_backend(_unavailable_backend(), force=False)
    assert excinfo.value.exit_code == ExitCode.NOT_AVAILABLE


# ─── Bootstrap ─────────────────────────────────────────────


def test_bootstrap_downloads_when_nothing_resolves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "app.orchestrator.resolve_llama_server",
        _resolve_nothing,
    )
    monkeypatch.setattr(
        "app.orchestrator.install_backend",
        _install_backend_stub,
    )
    orch = Orchestrator(AppConfig(root=str(tmp_path)))
    data = orch.bootstrap()
    # The default backend follows the platform (vulkan on Windows/Linux,
    # metal on macOS), so the downloaded backend is the platform default.
    assert data.performed == [f"llama.cpp:{platform_default_backend()}"]
    assert data.skipped == []
    assert data.llama_cpp_version == "b10189"
    assert orch.cfg.first_run_complete


def test_bootstrap_skips_an_available_binary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "app.orchestrator.resolve_llama_server",
        _resolve_valid,
    )
    orch = Orchestrator(AppConfig(root=str(tmp_path)))
    data = orch.bootstrap()
    assert data.skipped == ["llama.cpp (already available)"]
    assert data.performed == []
    assert data.llama_cpp_version is None


# ─── Relocation edge cases ─────────────────────────────────


def test_relocate_without_an_active_backend_moves_the_tree(
    tmp_path: Path,
) -> None:
    """No active marker and no ``current`` link: the tree still moves."""
    root_a = tmp_path / "a"
    (root_a / "managed" / "vulkan").mkdir(parents=True)
    (root_a / "managed" / "vulkan" / "llama-server.exe").write_text(
        "x", encoding="utf-8"
    )
    orch = Orchestrator(AppConfig(root=str(root_a)))
    root_b = tmp_path / "b"
    orch.relocate_data(root=str(root_b), move_backends=True)
    assert not (root_a / "managed" / "vulkan").exists()
    assert (root_b / "managed" / "vulkan" / "llama-server.exe").exists()


def test_relocate_repoints_the_link_without_a_marker(
    tmp_path: Path,
) -> None:
    """A ``current`` link travels, but no marker means none is copied."""
    root_a = tmp_path / "a"
    managed = root_a / "managed"
    (managed / "vulkan").mkdir(parents=True)
    (managed / "vulkan" / "llama-server.exe").write_text("x", encoding="utf-8")
    (managed / "current").symlink_to(managed / "vulkan", target_is_directory=True)
    orch = Orchestrator(AppConfig(root=str(root_a)))
    root_b = tmp_path / "b"
    orch.relocate_data(root=str(root_b), move_backends=True)
    assert (root_b / "managed" / "current").exists()
    assert not (root_b / "state" / "active.txt").exists()


# ─── Token resolution ──────────────────────────────────────


def test_github_token_prefers_the_config(tmp_path: Path) -> None:
    orch = Orchestrator(AppConfig(root=str(tmp_path)))
    orch.cfg.token = "secret"
    assert orch._github_token() == "secret"


def test_github_token_falls_back_to_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    orch = Orchestrator(AppConfig(root=str(tmp_path)))
    monkeypatch.setenv("GITHUB_TOKEN", "env-token")
    monkeypatch.delenv("GH_TOKEN", raising=False)
    assert orch._github_token() == "env-token"


def test_github_token_tolerates_no_keyring(tmp_path: Path) -> None:
    orch = Orchestrator(AppConfig(root=str(tmp_path)))
    orch._github_token()  # must not raise either way


# ─── Module helpers ────────────────────────────────────────


def test_same_location_survives_an_unresolvable_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _unresolvable(self: Path) -> Path:
        raise OSError("gone")

    monkeypatch.setattr(Path, "resolve", _unresolvable)
    assert not _same_location(tmp_path / "a", tmp_path / "b")


def test_linked_backend_without_a_link(tmp_path: Path) -> None:
    managed = tmp_path / "managed"
    managed.mkdir()
    assert _linked_backend(managed) is None


def test_linked_backend_reads_the_current_link(tmp_path: Path) -> None:
    managed = tmp_path / "managed"
    (managed / "vulkan").mkdir(parents=True)
    (managed / "current").symlink_to(managed / "vulkan", target_is_directory=True)
    assert _linked_backend(managed) == "vulkan"


def test_is_within(tmp_path: Path) -> None:
    base = tmp_path / "a"
    assert _is_within(base, base)
    assert _is_within(base / "b", base)
    assert not _is_within(tmp_path / "c", base)


def test_marker_version(tmp_path: Path) -> None:
    managed = tmp_path / "managed" / "vulkan"
    managed.mkdir(parents=True)
    assert _marker_version(tmp_path, "vulkan") is None
    (managed / ".version").write_text("b10189\nmanaged-prebuilt\n", encoding="utf-8")
    assert _marker_version(tmp_path, "vulkan") == "b10189"


# ─── branch coverage: bootstrap, model resolution, removal ──


def test_bootstrap_skips_activation_when_nothing_was_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An install that changed nothing must not activate the backend."""

    class _Result:
        status = "skipped"
        version = "b1"

    def _skip_obtain(self: Orchestrator, name: str, force: bool = False) -> _Result:
        return _Result()

    def _nothing_resolves(cfg: AppConfig, validate: bool = True) -> ResolvedBinary:
        return ResolvedBinary(None, None, None, False, "none")

    monkeypatch.setattr(Orchestrator, "_obtain_backend", _skip_obtain)
    monkeypatch.setattr("app.orchestrator.resolve_llama_server", _nothing_resolves)
    orch = Orchestrator(AppConfig(root=str(tmp_path / "root")))

    data = orch.bootstrap("cpu")

    assert "llama.cpp:cpu" in data.skipped
    assert data.performed == []


def test_resolve_model_path_falls_back_when_the_active_model_vanished(
    tmp_path: Path,
) -> None:
    models = tmp_path / "models"
    models.mkdir(parents=True)
    (models / "real.gguf").write_bytes(b"x")
    cfg = AppConfig(
        root=str(tmp_path / "root"),
        models_dir=str(models),
        active_model="ghost.gguf",
    )
    orch = Orchestrator(cfg)

    assert orch._resolve_model_path() == models / "real.gguf"


def test_remove_model_of_another_model_keeps_the_active_one(
    tmp_path: Path,
) -> None:
    models = tmp_path / "models"
    models.mkdir(parents=True)
    (models / "a.gguf").write_bytes(b"x")
    (models / "b.gguf").write_bytes(b"x")
    cfg = AppConfig(
        root=str(tmp_path / "root"),
        models_dir=str(models),
        active_model="a.gguf",
    )
    orch = Orchestrator(cfg)

    remaining = orch.remove_model("b.gguf")

    assert [m.name for m in remaining.models] == ["a.gguf"]


def test_dedicated_values_reject_an_unknown_flag(tmp_path: Path) -> None:
    orch = Orchestrator(AppConfig(root=str(tmp_path / "root")))
    with pytest.raises(EngineError, match="Unknown dedicated flag"):
        orch._dedicated_values_for_set("--bogus", "x")
