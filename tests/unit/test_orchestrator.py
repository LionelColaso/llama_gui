from __future__ import annotations

import contextlib
import json
import platform as _platform
import threading
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.config import AppConfig, config_file
from app.locking import LockAcquisitionError, mutation_lock
from app.orchestrator import Orchestrator
from app.resolver import ResolvedBinary
from app.schemas import EngineError, ExitCode, InstallResultItem
from app.state import read_junction_target

_SYSTEM = _platform.system().lower()
_EXE_SUFFIX = ".exe" if _SYSTEM == "windows" else ""


def test_describe(tmp_path: Path) -> None:
    cfg = AppConfig(root=str(tmp_path))
    orch = Orchestrator(cfg)
    d = orch.describe()
    names = [b.name for b in d.backends]
    # The catalogue is data-driven and platform-aware: every backend is listed
    # with its availability, so the GUI never hardcodes the list.
    assert names[:3] == ["vulkan", "cuda13", "cuda12"]
    assert {"cpu", "metal"} <= set(names)
    assert "describe" in d.available_actions
    assert d.platform.system in ("win32", "linux", "darwin")


def test_describe_marks_unavailable_backends(tmp_path: Path) -> None:
    orch = Orchestrator(AppConfig(root=str(tmp_path)))
    by_name = {b.name: b for b in orch.describe().backends}
    unusable = "metal" if _SYSTEM != "darwin" else "cuda12"
    assert by_name[unusable].prebuilt_available is False
    assert by_name[unusable].unavailable_reason


# ─── "Use default" root reset ─────────────────────────────────────────────


def test_reset_root_removes_the_override_and_follows_the_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.paths import default_root

    sandbox = tmp_path / "default-root"
    monkeypatch.setattr("app.paths.LEGACY_ROOT", sandbox)
    monkeypatch.setenv("LLAMAGUI_CONFIG_DIR", str(tmp_path / "cfg"))

    custom = tmp_path / "custom-root"
    orch = Orchestrator(AppConfig(root=str(custom), root_is_default=False))
    orch.save_config({"host": "0.0.0.0"})
    assert json.loads(config_file().read_text(encoding="utf-8"))["root"] == str(custom)

    data = orch.reset_root()

    assert orch.cfg.root == str(default_root())
    assert "root" not in json.loads(config_file().read_text(encoding="utf-8"))
    # Unrelated settings survive the reset.
    assert orch.cfg.host == "0.0.0.0"
    assert data.values["root"] == str(default_root())
    assert any("restart" in w.lower() for w in data.warnings)


def test_reset_root_on_an_already_default_root_is_a_no_op(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.paths import default_root

    monkeypatch.setattr("app.paths.LEGACY_ROOT", tmp_path / "absent")
    monkeypatch.setenv("LLAMAGUI_CONFIG_DIR", str(tmp_path / "cfg"))

    orch = Orchestrator(AppConfig())
    orch.reset_root()

    assert orch.cfg.root == str(default_root())
    assert orch.cfg.root_is_default is True


# ─── "Use default" models-directory reset ──────────────────────────────────


def test_reset_models_dir_removes_the_override_and_follows_the_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LLAMAGUI_CONFIG_DIR", str(tmp_path / "cfg"))

    custom = tmp_path / "my models"
    orch = Orchestrator(
        AppConfig(
            root=str(tmp_path / "root"), root_is_default=False, models_dir=str(custom)
        )
    )
    orch.save_config({"host": "0.0.0.0"})
    stored = json.loads(config_file().read_text(encoding="utf-8"))
    assert stored["models_dir"] == str(custom)

    data = orch.reset_models_dir()

    expected = tmp_path / "root" / "models"
    assert orch.cfg.models_dir == ""
    assert orch.cfg.models_dir_path == expected
    assert "models_dir" not in json.loads(config_file().read_text(encoding="utf-8"))
    # Unrelated settings survive the reset.
    assert orch.cfg.host == "0.0.0.0"
    # A read of the settings still reports the directory in effect.
    assert data.values["models_dir"] == str(expected)
    assert any("restart" in w.lower() for w in data.warnings)


def test_reset_models_dir_moves_models_back_with_a_root_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reset, then move the root: the models directory must follow it.

    This is the regression the absent-key storage exists for — storing the
    resolved path would leave the library behind in the old location.
    """
    monkeypatch.setenv("LLAMAGUI_CONFIG_DIR", str(tmp_path / "cfg"))

    orch = Orchestrator(AppConfig(models_dir=str(tmp_path / "my models")))
    orch.reset_models_dir()
    orch.save_config({"root": str(tmp_path / "moved")})

    assert orch.cfg.models_dir_path == tmp_path / "moved" / "models"


# ─── Relocating existing data when a path changes ──────────────────────────


def _backend_tree(root: Path, name: str = "vulkan") -> Path:
    """A managed root holding one installed backend, as install would leave it."""
    backend = root / "managed" / name
    backend.mkdir(parents=True)
    (backend / "llama-server.exe").write_bytes(b"binary")
    (backend / ".version").write_text("b1\nmanaged-prebuilt\n", encoding="utf-8")
    (root / "state").mkdir(parents=True, exist_ok=True)
    (root / "state" / "active.txt").write_text(f"{name}\n", encoding="utf-8")
    return backend


def _model(root: Path, name: str = "model.gguf", size: int = 8) -> Path:
    """Add a model to ``<root>/models`` and return that directory."""
    models = root / "models"
    models.mkdir(parents=True, exist_ok=True)
    (models / name).write_bytes(b"m" * size)
    return models


def test_plan_offers_nothing_when_the_paths_do_not_change(tmp_path: Path) -> None:
    root = tmp_path / "root"
    _backend_tree(root)
    orch = Orchestrator(AppConfig(root=str(root), root_is_default=False))

    plan = orch.plan_relocation(root=str(root), models_dir="")

    assert plan.backends is None
    assert plan.models is None
    assert plan.requires_choice is False


def test_plan_reports_backends_and_models_for_a_root_change(tmp_path: Path) -> None:
    """Changing the root drags the default models directory along with it."""
    root = tmp_path / "root"
    _backend_tree(root)
    _model(root)
    orch = Orchestrator(AppConfig(root=str(root), root_is_default=False))

    plan = orch.plan_relocation(root=str(tmp_path / "elsewhere"), models_dir="")

    assert plan.backends is not None
    assert plan.backends.source == str(root / "managed")
    assert plan.backends.destination == str(tmp_path / "elsewhere" / "managed")
    assert plan.backends.files == 2
    assert plan.models is not None
    assert plan.models.source == str(root / "models")
    assert plan.models.destination == str(tmp_path / "elsewhere" / "models")
    assert plan.requires_choice is True


def test_plan_reports_only_models_when_only_the_models_dir_moves(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    _backend_tree(root)
    _model(root)
    orch = Orchestrator(AppConfig(root=str(root), root_is_default=False))
    elsewhere = str(tmp_path / "gguf")

    plan = orch.plan_relocation(root=str(root), models_dir=elsewhere)

    assert plan.backends is None
    assert plan.models is not None
    assert plan.models.destination == elsewhere


def test_plan_reports_both_when_both_move_at_once(tmp_path: Path) -> None:
    root = tmp_path / "root"
    _backend_tree(root)
    _model(root)
    orch = Orchestrator(AppConfig(root=str(root), root_is_default=False))

    plan = orch.plan_relocation(
        root=str(tmp_path / "new-root"), models_dir=str(tmp_path / "new-models")
    )

    assert plan.backends is not None
    assert plan.models is not None


def _blocked_destination(tmp_path: Path) -> tuple[Orchestrator, Path]:
    """A models directory, plus a destination that already holds a model."""
    root = tmp_path / "root"
    _model(root)
    occupied = tmp_path / "occupied"
    occupied.mkdir()
    (occupied / "other.gguf").write_bytes(b"x")
    orch = Orchestrator(AppConfig(root=str(root), root_is_default=False))
    return orch, occupied


def test_plan_blocks_a_destination_that_already_holds_files(
    tmp_path: Path,
) -> None:
    orch, occupied = _blocked_destination(tmp_path)

    plan = orch.plan_relocation(root=orch.cfg.root, models_dir=str(occupied))

    assert plan.models is not None
    assert "already contains files" in plan.models.blocked


def test_plan_blocks_a_destination_inside_the_source(tmp_path: Path) -> None:
    root = tmp_path / "root"
    _model(root)
    orch = Orchestrator(AppConfig(root=str(root), root_is_default=False))

    plan = orch.plan_relocation(root=str(root), models_dir=str(root / "models" / "sub"))

    assert plan.models is not None
    assert "inside" in plan.models.blocked


def test_plan_notes_interrupted_downloads_that_stay_behind(tmp_path: Path) -> None:
    root = tmp_path / "root"
    models = _model(root)
    (models / "model.gguf.part").write_bytes(b"p")
    orch = Orchestrator(AppConfig(root=str(root), root_is_default=False))

    plan = orch.plan_relocation(root=str(root), models_dir=str(tmp_path / "new"))

    assert any("interrupted" in note for note in plan.notes)


def test_relocate_moves_the_backends_and_the_models_together(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LLAMAGUI_CONFIG_DIR", str(tmp_path / "cfg"))

    root = tmp_path / "root"
    _backend_tree(root)
    _model(root)
    new_root = tmp_path / "new-root"
    orch = Orchestrator(AppConfig(root=str(root), root_is_default=False))

    plan = orch.relocate_data(
        root=str(new_root), models_dir="", move_backends=True, move_models=True
    )

    assert plan.backends is not None and plan.backends.transfer == "move"
    assert plan.models is not None and plan.models.transfer == "move"
    assert (new_root / "managed" / "vulkan" / "llama-server.exe").is_file()
    assert (new_root / "managed" / "vulkan" / ".version").is_file()
    assert (new_root / "models" / "model.gguf").is_file()
    # The backend tree is gone from the old root; its ``state/`` marker and the
    # models directory itself stay (a models dir belongs to the user).
    assert not (root / "managed").exists()
    assert not (root / "models" / "model.gguf").exists()
    # The active-backend selection travels with the backends.
    assert (new_root / "state" / "active.txt").read_text().strip() == "vulkan"
    # The settings file is untouched: the caller saves only after a good move.
    assert orch.cfg.root == str(root)


def test_relocate_repoints_the_current_link_at_the_new_location(
    tmp_path: Path, fake_root_with_junction: Path
) -> None:
    """``managed/current`` is an absolute link, so it cannot just travel along."""
    new_root = tmp_path / "new-root"
    orch = Orchestrator(AppConfig(root=str(fake_root_with_junction)))

    orch.relocate_data(root=str(new_root), move_backends=True, move_models=False)

    current = new_root / "managed" / "current"
    assert current.exists()
    target = read_junction_target(new_root)
    assert target is not None
    assert Path(target).name == "vulkan"
    assert current.resolve() == (new_root / "managed" / "vulkan").resolve()
    assert not (fake_root_with_junction / "managed" / "current").exists()


def test_copy_keeps_the_old_location_intact(
    tmp_path: Path, fake_root_with_junction: Path
) -> None:
    """Copy & save: the new location is filled and the old one is still there."""
    root = fake_root_with_junction
    _model(root)
    new_root = tmp_path / "new-root"
    orch = Orchestrator(AppConfig(root=str(root)))

    plan = orch.relocate_data(
        root=str(new_root),
        models_dir="",
        transfer="copy",
        move_backends=True,
        move_models=True,
    )

    assert plan.copied is True
    assert {item.transfer for item in plan.transferred} == {"copy"}
    assert (new_root / "managed" / "vulkan" / ".version").is_file()
    assert (new_root / "models" / "model.gguf").is_file()
    # The destination gets its own link, and the source keeps everything.
    assert (new_root / "managed" / "current").exists()
    assert (root / "managed" / "current").exists()
    assert (root / "managed" / "vulkan" / ".version").is_file()
    assert (root / "models" / "model.gguf").is_file()


def test_relocate_can_move_only_the_models(tmp_path: Path) -> None:
    root = tmp_path / "root"
    _backend_tree(root)
    _model(root)
    orch = Orchestrator(AppConfig(root=str(root), root_is_default=False))
    elsewhere = tmp_path / "new-models"

    orch.relocate_data(root=str(root), models_dir=str(elsewhere), move_models=True)

    assert (elsewhere / "model.gguf").is_file()
    # The backends were not asked for, so they stay exactly where they were.
    assert (root / "managed" / "vulkan" / "llama-server.exe").is_file()
    assert not (root / "models" / "model.gguf").exists()
    assert root.is_dir()


def test_relocate_skips_a_blocked_move_instead_of_forcing_it(
    tmp_path: Path,
) -> None:
    orch, occupied = _blocked_destination(tmp_path)

    plan = orch.relocate_data(
        root=orch.cfg.root,
        models_dir=str(occupied),
        move_backends=True,
        move_models=True,
    )

    assert plan.models is not None
    assert plan.models.transfer == ""
    assert (orch.cfg.root_path / "models" / "model.gguf").is_file()
    assert (occupied / "other.gguf").read_bytes() == b"x"


def test_relocate_of_an_empty_location_moves_nothing(tmp_path: Path) -> None:
    """A root with no backends and no models has nothing to offer."""
    root = tmp_path / "root"
    (root / "state").mkdir(parents=True)
    orch = Orchestrator(AppConfig(root=str(root), root_is_default=False))

    plan = orch.relocate_data(
        root=str(tmp_path / "new"), move_backends=True, move_models=True
    )

    assert plan.items == []
    assert plan.transferred == []


# ─── first_run_needed validates, unlike status().ready ──────────────────────


def _resolved(path: str | None, valid: bool) -> ResolvedBinary:
    return ResolvedBinary(path, None, "b1" if valid else None, valid, None)


def test_first_run_needed_when_nothing_resolves(tmp_path: Path) -> None:
    orch = Orchestrator(AppConfig(root=str(tmp_path)))
    with patch(
        "app.orchestrator.resolve_llama_server",
        return_value=_resolved(None, False),
    ):
        assert orch.first_run_needed() is True


def test_first_run_needed_when_binary_exists_but_cannot_run(tmp_path: Path) -> None:
    """Regression: a present-but-broken binary used to read as ready.

    ``status().ready`` is only an existence check, so keying the dialog off it
    suppressed setup for a binary that cannot run (wrong arch, missing CUDA
    runtime, quarantine), stranding the user in a dead UI.
    """
    orch = Orchestrator(AppConfig(root=str(tmp_path)))
    exists_but_broken = "/does/not/matter/llama-server"
    with patch(
        "app.orchestrator.resolve_llama_server",
        return_value=_resolved(exists_but_broken, False),
    ):
        assert orch.first_run_needed() is True


def test_first_run_not_needed_for_a_working_binary(tmp_path: Path) -> None:
    orch = Orchestrator(AppConfig(root=str(tmp_path)))
    with patch(
        "app.orchestrator.resolve_llama_server",
        return_value=_resolved("/x/llama-server", True),
    ):
        assert orch.first_run_needed() is False


def test_first_run_not_needed_once_complete(tmp_path: Path) -> None:
    """An explicit skip must stick, with no binary probe performed."""
    cfg = AppConfig(root=str(tmp_path), first_run_complete=True)
    orch = Orchestrator(cfg)

    def _boom(*args: object, **kwargs: object) -> ResolvedBinary:
        raise AssertionError("first_run_needed must not probe once complete")

    with patch("app.orchestrator.resolve_llama_server", _boom):
        assert orch.first_run_needed() is False


def test_first_run_needed_survives_a_probe_error(tmp_path: Path) -> None:
    """A crashing probe must still offer setup, not silence it."""
    orch = Orchestrator(AppConfig(root=str(tmp_path)))

    def _boom(*args: object, **kwargs: object) -> ResolvedBinary:
        raise RuntimeError("probe exploded")

    with patch("app.orchestrator.resolve_llama_server", _boom):
        assert orch.first_run_needed() is True


def test_describe_defaults(tmp_path: Path) -> None:
    cfg = AppConfig(root=str(tmp_path))
    orch = Orchestrator(cfg)
    d = orch.describe()
    assert d.defaults["port"] == 8080
    assert d.defaults["host"] == "127.0.0.1"


def test_status_empty(tmp_path: Path) -> None:
    cfg = AppConfig(root=str(tmp_path))
    orch = Orchestrator(cfg)
    s = orch.status()
    for backend in s.backends.values():
        assert backend.installed is False
    assert s.active is None
    assert s.junction_target is None
    assert s.server.listening is False


def test_status_with_backend_version(tmp_path: Path) -> None:
    cfg = AppConfig(root=str(tmp_path))
    managed = tmp_path / "managed" / "vulkan"
    managed.mkdir(parents=True)
    (managed / ".version").write_text("b12345\nmanaged-prebuilt\n", encoding="utf-8")

    orch = Orchestrator(cfg)
    s = orch.status()
    assert s.backends["vulkan"].installed is True
    assert s.backends["vulkan"].version == "b12345"


def test_status_with_active_backend(tmp_path: Path) -> None:
    cfg = AppConfig(root=str(tmp_path))
    state = tmp_path / "state"
    state.mkdir(parents=True)
    (state / "active.txt").write_text("vulkan\n", encoding="utf-8")

    orch = Orchestrator(cfg)
    s = orch.status()
    assert s.active == "vulkan"


def test_status_resolved_matches_dashboard_contract(tmp_path: Path) -> None:
    # Regression guard for the bug where status() keyed the `resolved` dict by
    # backend name (path=None) instead of the binary name the dashboard reads
    # ("llama_server"), so it never showed a real path and `ready` was
    # permanently False.
    bin_dir = tmp_path / "bins"
    bin_dir.mkdir()
    (bin_dir / f"llama-server{_EXE_SUFFIX}").write_text("", encoding="utf-8")

    cfg = AppConfig(root=str(tmp_path), use_os_llama_server=True)
    orch = Orchestrator(cfg)
    server = str(bin_dir / f"llama-server{_EXE_SUFFIX}")
    with patch("app.resolver.shutil.which", return_value=server):
        s = orch.status()

    assert set(s.resolved.keys()) == {"llama_server"}
    # OS-installed binaries are found and their paths/sources are populated.
    assert s.resolved["llama_server"].path is not None
    assert s.resolved["llama_server"].source == "system"
    assert s.ready is True


def test_resolve_invalid(tmp_path: Path) -> None:
    cfg = AppConfig(root=str(tmp_path))
    orch = Orchestrator(cfg)
    # Isolate from any binaries actually installed on the host (e.g. on PATH),
    # so resolution deterministically fails to find a usable llama-server.
    with patch("app.resolver.shutil.which", return_value=None):
        r = orch.resolve()
    assert r.llama_server.valid is False
    assert r.llama_server.path is None


def test_list_assets_fails_without_network(tmp_path: Path) -> None:
    cfg = AppConfig(root=str(tmp_path))
    orch = Orchestrator(cfg)
    with contextlib.suppress(Exception):
        orch.list_assets()


def test_use_uninstalled_backend(tmp_path: Path) -> None:
    cfg = AppConfig(root=str(tmp_path))
    orch = Orchestrator(cfg)
    with pytest.raises(Exception, match="not installed"):
        orch.use("vulkan")


def test_use_installed_backend(tmp_path: Path) -> None:
    cfg = AppConfig(root=str(tmp_path))
    managed_root = tmp_path / "managed"
    backend_dir = managed_root / "vulkan"
    backend_dir.mkdir(parents=True)
    (backend_dir / f"llama-server{_EXE_SUFFIX}").write_text("", encoding="utf-8")

    orch = Orchestrator(cfg)
    result = orch.use("vulkan")
    assert result.backend == "vulkan"
    assert result.active_after == "vulkan"

    active = (tmp_path / "state" / "active.txt").read_text(encoding="utf-8").strip()
    assert active == "vulkan"


def test_use_switches_backend(tmp_path: Path) -> None:
    cfg = AppConfig(root=str(tmp_path))
    managed_root = tmp_path / "managed"
    for name in ("vulkan", "cuda13"):
        (managed_root / name).mkdir(parents=True)
        (managed_root / name / f"llama-server{_EXE_SUFFIX}").write_text(
            "", encoding="utf-8"
        )

    orch = Orchestrator(cfg)
    orch.use("vulkan")
    r2 = orch.use("cuda13")
    assert r2.active_before == "vulkan"
    assert r2.active_after == "cuda13"


def test_stop_empty(tmp_path: Path) -> None:
    cfg = AppConfig(root=str(tmp_path))
    orch = Orchestrator(cfg)
    result = orch.stop()
    assert result.stopped_pids == []


def _empty_release() -> dict[str, object]:
    """Return a release dict with no assets, so install fails with 'No asset matching'."""
    return {"tag_name": "b00000", "assets": []}


def _prebuilt_capable_backend() -> str:
    """A backend that has a prebuilt release on the current platform.

    The "No asset matching" path runs the prebuilt download, so we need a
    backend whose asset pattern exists for this platform.
    """
    from app.backends.catalogue import BACKENDS
    from app.paths import platform_key

    plat = platform_key()
    for b in BACKENDS:
        if b.has_prebuilt(plat):
            return b.name
    raise AssertionError(f"no prebuilt-capable backend on {plat}")


def _assert_install_fails_no_asset(
    tmp_path: Path, action: str, **kwargs: object
) -> None:
    # The empty-release fixture triggers the "No asset matching" error on a
    # backend whose asset pattern exists for this platform.
    backend = _prebuilt_capable_backend()
    cfg = AppConfig(root=str(tmp_path))
    orch = Orchestrator(cfg)
    with (
        patch("app.backends.prebuilt.latest_release", return_value=_empty_release()),
        pytest.raises(Exception, match="No asset matching"),
    ):
        method = getattr(orch, action)
        method([backend], **kwargs)


def test_install_with_fake_root(tmp_path: Path) -> None:
    _assert_install_fails_no_asset(tmp_path, "install")


def test_force_update_fails_without_network(tmp_path: Path) -> None:
    _assert_install_fails_no_asset(tmp_path, "update", force=True)


def test_use_without_auto_install_raises(tmp_path: Path) -> None:
    orch = Orchestrator(AppConfig(root=str(tmp_path)))
    with pytest.raises(Exception, match="not installed"):
        # "vulkan" is never installed in this fixture, so this must raise.
        orch.use("vulkan")


def test_use_with_auto_install_obtains_backend(tmp_path: Path) -> None:
    orch = Orchestrator(AppConfig(root=str(tmp_path)))
    backend = _prebuilt_capable_backend()
    target = tmp_path / "managed" / backend
    obtained: list[str] = []

    def fake_obtain(self: object, name: str, force: bool = False) -> object:
        target.mkdir(parents=True, exist_ok=True)
        (target / f"llama-server{_EXE_SUFFIX}").write_text("", encoding="utf-8")
        obtained.append(name)
        return None

    with patch.object(Orchestrator, "_obtain_backend", fake_obtain):
        result = orch.use(backend, auto_install=True)

    assert result.auto_installed is True
    assert obtained == [backend]
    assert (tmp_path / "state" / "active.txt").read_text(
        encoding="utf-8"
    ).strip() == backend


# ─── launch / restart hold the mutation lock (invariant #15) ────────────────


class _LockSpy:
    """Records whether the mutation lock is held, probed from another thread.

    Two traps make this less obvious than it looks:

    * The Windows implementation is a named mutex, which leaves no lock *file*,
      so probing for one proves nothing.
    * A Win32 mutex is **reentrant**: the owning thread can re-acquire it, so a
      same-thread probe would always succeed even when the lock is held.

    So the probe runs on a separate thread, where a held lock refuses with
    LockAcquisitionError (exit 4) and an unlocked action lets it through.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self.observed: list[bool] = []

    def sample(self) -> None:
        outcome: list[bool] = []

        def _probe() -> None:
            try:
                with mutation_lock(self.root, timeout=0.0):
                    outcome.append(False)
            except LockAcquisitionError:
                outcome.append(True)

        thread = threading.Thread(target=_probe, daemon=True)
        thread.start()
        thread.join(timeout=5.0)
        self.observed.append(outcome[0] if outcome else False)


def _launch_patches(
    orch: Orchestrator, spy: _LockSpy
) -> list[AbstractContextManager[object]]:
    """Patch everything ``_launch_locked`` touches, so no real process starts.

    Returns the context managers to enter.
    """
    resolved = ResolvedBinary("llama-server", None, "b1", True, None)

    def _spawn(*args: object, **kwargs: object) -> int:
        spy.sample()
        return 4242

    def _stop(*args: object, **kwargs: object) -> dict[str, object]:
        return {"stopped_pids": [], "port_free": True, "still_listening": False}

    return [
        patch("app.orchestrator.resolve_llama_server", return_value=resolved),
        patch("app.orchestrator.check_port", return_value=False),
        patch("app.orchestrator.launch_llama_server", side_effect=_spawn),
        patch("app.orchestrator.stop_processes", side_effect=_stop),
        patch.object(orch, "_resolve_model_path", return_value=orch.root / "m.gguf"),
        patch.object(orch, "_server_args_for", return_value=["llama-server"]),
    ]


def test_launch_holds_the_mutation_lock(tmp_path: Path) -> None:
    """launch() must hold the lock while it spawns the server.

    Regression: launch used to run unlocked, so a concurrent install could
    wipe_and_extract the backend directory out from under the binary being
    spawned (invariant #15).
    """
    orch = Orchestrator(AppConfig(root=str(tmp_path)))
    spy = _LockSpy(tmp_path)
    with contextlib.ExitStack() as stack:
        for ctx in _launch_patches(orch, spy):
            stack.enter_context(ctx)
        pid = orch.launch()

    assert pid == 4242
    assert spy.observed == [True], "launch spawned the server without the lock held"


def test_stop_holds_the_mutation_lock(tmp_path: Path) -> None:
    orch = Orchestrator(AppConfig(root=str(tmp_path)))
    spy = _LockSpy(tmp_path)
    seen: list[bool] = []

    def _stop(*args: object, **kwargs: object) -> dict[str, object]:
        spy.sample()
        return {"stopped_pids": [], "port_free": True, "still_listening": False}

    with patch("app.orchestrator.stop_processes", side_effect=_stop):
        orch.stop()
    seen.extend(spy.observed)
    assert seen == [True], "stop terminated processes without the lock held"


def test_restart_takes_the_lock_once(tmp_path: Path) -> None:
    """restart() must not re-enter the lock, which is not reentrant on POSIX.

    The lock file is created O_EXCL, so a nested acquire from the same thread
    would raise LockAcquisitionError. restart therefore wraps both its stop and
    its launch in a single ``with mutation_lock(...)``.
    """
    orch = Orchestrator(AppConfig(root=str(tmp_path)))
    spy = _LockSpy(tmp_path)
    stop_observed: list[bool] = []

    with contextlib.ExitStack() as stack:
        for ctx in _launch_patches(orch, spy):
            stack.enter_context(ctx)
        real_stop = orch._stop_locked

        def _tracked_stop() -> object:
            spy.sample()
            stop_observed.append(spy.observed[-1])
            return real_stop()

        stack.enter_context(
            patch.object(orch, "_stop_locked", side_effect=_tracked_stop)
        )
        pid = orch.restart()

    assert pid == 4242
    assert stop_observed == [True], "restart stopped without the lock held"
    assert spy.observed == [True, True], "restart launched without the lock held"


def test_restart_does_not_reenter_the_lock(tmp_path: Path) -> None:
    """A nested acquire would fail outright; prove restart takes it once.

    The POSIX lock file is created O_EXCL, so re-entering it from the same
    thread raises LockAcquisitionError. This counts the acquires restart
    performs: exactly one means the guard is neither skipped nor re-entered.
    """
    orch = Orchestrator(AppConfig(root=str(tmp_path)))
    spy = _LockSpy(tmp_path)
    acquires: list[int] = []
    real_lock = mutation_lock

    def _counting(root: Path, timeout: float = 0.0) -> AbstractContextManager[None]:
        """Wrap the real lock so each acquire is counted, without re-entering it."""
        acquires.append(1)
        return real_lock(root, timeout)

    with contextlib.ExitStack() as stack:
        for ctx in _launch_patches(orch, spy):
            stack.enter_context(ctx)
        stack.enter_context(patch("app.orchestrator.mutation_lock", _counting))
        pid = orch.restart()

    assert pid == 4242
    assert len(acquires) == 1, (
        f"restart acquired the lock {len(acquires)} times, expected 1"
    )


# ─── server-arg actions ───────────────────────────────────────────────────


def _row(result: dict[str, Any], index: int = 0) -> dict[str, Any]:
    """The nth option row of a describe_server_args() result."""
    args: list[dict[str, Any]] = result["args"]
    return args[index]


def test_describe_server_args_lists_the_whole_catalogue(tmp_path: Path) -> None:
    from app.serverargs import SERVER_ARGS

    orch = Orchestrator(AppConfig(root=str(tmp_path)))
    result = orch.describe_server_args()
    assert result["count"] == len(SERVER_ARGS)
    assert len(result["args"]) == len(SERVER_ARGS)


def test_describe_server_args_carries_row_metadata(tmp_path: Path) -> None:
    orch = Orchestrator(AppConfig(root=str(tmp_path)))
    row = _row(orch.describe_server_args("--jinja"))
    for key in ("flag", "section", "kind", "help", "value", "volatile"):
        assert key in row, f"row is missing {key}"
    assert row["flag"] == "--jinja"


def test_describe_server_args_filters_by_flag(tmp_path: Path) -> None:
    orch = Orchestrator(AppConfig(root=str(tmp_path)))
    result = orch.describe_server_args("--jinja")
    assert result["count"] == 1
    assert result["args"][0]["flag"] == "--jinja"


def test_describe_server_args_resolves_an_alias(tmp_path: Path) -> None:
    orch = Orchestrator(AppConfig(root=str(tmp_path)))
    assert orch.describe_server_args("-c")["args"][0]["flag"] == "--ctx-size"


def test_describe_server_args_reflects_configured_values(tmp_path: Path) -> None:
    cfg = AppConfig(root=str(tmp_path), server_options={"--jinja": "on"})
    orch = Orchestrator(cfg)
    assert _row(orch.describe_server_args("--jinja"))["value"] == "on"


def test_describe_server_args_reads_dedicated_values_from_config(
    tmp_path: Path,
) -> None:
    """--port is a dedicated flag; its value comes from cfg.port."""
    cfg = AppConfig(root=str(tmp_path), port=9999, host="0.0.0.0")
    orch = Orchestrator(cfg)
    assert _row(orch.describe_server_args("--port"))["value"] == "9999"
    assert _row(orch.describe_server_args("--host"))["value"] == "0.0.0.0"


def test_set_server_arg_stores_a_normalised_value(tmp_path: Path) -> None:
    orch = Orchestrator(AppConfig(root=str(tmp_path)))
    orch.set_server_arg("--jinja", "TRUE")
    assert orch.cfg.server_options["--jinja"] == "on"


def test_set_server_arg_accepts_an_alias(tmp_path: Path) -> None:
    """Setting by alias must update the canonical field, not store the alias."""
    orch = Orchestrator(AppConfig(root=str(tmp_path)))
    orch.set_server_arg("-c", "8192")
    assert "--ctx-size" not in orch.cfg.server_options
    assert orch.cfg.ctx_size == 8192, "a dedicated alias must update the config field"


def test_set_server_arg_rejects_unknown_flags(tmp_path: Path) -> None:
    orch = Orchestrator(AppConfig(root=str(tmp_path)))
    with pytest.raises(EngineError, match="Unknown option"):
        orch.set_server_arg("--not-real", "1")


def test_set_server_arg_rejects_volatile_flags(tmp_path: Path) -> None:
    """--help prints and exits; passing it to a running server is meaningless."""
    orch = Orchestrator(AppConfig(root=str(tmp_path)))
    with pytest.raises(EngineError, match="one-shot"):
        orch.set_server_arg("--help", "on")


def test_set_server_arg_rejects_an_invalid_value(tmp_path: Path) -> None:
    orch = Orchestrator(AppConfig(root=str(tmp_path)))
    with pytest.raises(EngineError):
        orch.set_server_arg("--jinja", "perhaps")


@pytest.mark.parametrize("reset_value", ["", "   "])
def test_setting_a_blank_value_resets_the_option(
    tmp_path: Path, reset_value: str
) -> None:
    cfg = AppConfig(root=str(tmp_path), server_options={"--jinja": "on"})
    orch = Orchestrator(cfg)
    orch.set_server_arg("--jinja", reset_value)
    assert "--jinja" not in orch.cfg.server_options


def test_set_dedicated_port_updates_the_config_field(tmp_path: Path) -> None:
    orch = Orchestrator(AppConfig(root=str(tmp_path), port=8080))
    orch.set_server_arg("--port", "9000")
    assert orch.cfg.port == 9000


def test_set_dedicated_host_updates_the_config_field(tmp_path: Path) -> None:
    orch = Orchestrator(AppConfig(root=str(tmp_path)))
    orch.set_server_arg("--host", "0.0.0.0")
    assert orch.cfg.host == "0.0.0.0"


def test_set_dedicated_n_gpu_layers_updates_the_config_field(tmp_path: Path) -> None:
    orch = Orchestrator(AppConfig(root=str(tmp_path)))
    orch.set_server_arg("--n-gpu-layers", "33")
    assert orch.cfg.n_gpu_layers == 33


def test_set_dedicated_ctx_size_updates_the_config_field(tmp_path: Path) -> None:
    orch = Orchestrator(AppConfig(root=str(tmp_path), ctx_size=4096))
    orch.set_server_arg("--ctx-size", "16384")
    assert orch.cfg.ctx_size == 16384


def _two_models(tmp_path: Path) -> Orchestrator:
    """An orchestrator where two models have their own context size."""
    orch = Orchestrator(AppConfig(root=str(tmp_path), ctx_size=4096))
    orch.set_server_arg("--ctx-size", "16384", model="big.gguf")
    orch.set_server_arg("--ctx-size", "2048", model="small.gguf")
    return orch


class TestModelScopedServerArgs:
    """A model either follows the global defaults or owns its own values."""

    def test_an_own_config_leaves_the_global_default_alone(
        self, tmp_path: Path
    ) -> None:
        orch = Orchestrator(AppConfig(root=str(tmp_path), ctx_size=4096))
        orch.set_server_arg("--ctx-size", "16384", model="big.gguf")
        assert orch.cfg.model_server_options == {"big.gguf": {"--ctx-size": "16384"}}
        assert orch.cfg.ctx_size == 4096, "the global default must not move"

    def test_the_model_scope_is_reported_back(self, tmp_path: Path) -> None:
        orch = Orchestrator(AppConfig(root=str(tmp_path), ctx_size=4096))
        result = orch.set_server_arg("--ctx-size", "16384", model="big.gguf")
        assert result["scope"] == "model"
        assert result["model"] == "big.gguf"
        assert result["mode"] == "own"
        assert result["args"][0]["value"] == "16384"
        assert result["args"][0]["inherited"] is False

    def test_describe_defaults_to_the_global_scope(self, tmp_path: Path) -> None:
        orch = Orchestrator(AppConfig(root=str(tmp_path), ctx_size=4096))
        orch.set_server_arg("--ctx-size", "16384", model="big.gguf")
        result = orch.describe_server_args("--ctx-size")
        assert result["scope"] == "global"
        assert result["args"][0]["value"] == "4096"

    def test_a_model_without_a_config_reports_the_global_mode(
        self, tmp_path: Path
    ) -> None:
        orch = Orchestrator(AppConfig(root=str(tmp_path), ctx_size=4096))
        result = orch.describe_server_args("--ctx-size", "small.gguf")
        assert result["mode"] == "global"
        assert result["args"][0]["value"] == "4096"
        assert result["args"][0]["inherited"] is True

    def test_a_model_with_its_own_config_ignores_the_globals(
        self, tmp_path: Path
    ) -> None:
        """Its values alone decide; a flag it omits is the binary's default."""
        orch = Orchestrator(
            AppConfig(
                root=str(tmp_path), ctx_size=4096, server_options={"--jinja": "on"}
            )
        )
        orch.save_model_server_config("big.gguf", {"--flash-attn": "on"})
        row = orch.describe_server_args("--jinja", "big.gguf")["args"][0]
        assert row["value"] == ""
        assert row["inherited"] is False

    def test_the_launch_line_carries_the_models_context(self, tmp_path: Path) -> None:
        orch = Orchestrator(AppConfig(root=str(tmp_path), ctx_size=4096))
        orch.set_server_arg("--ctx-size", "16384", model="big.gguf")
        cmd = orch._server_args_for("llama-server", str(tmp_path / "big.gguf"))
        assert cmd[cmd.index("-c") + 1] == "16384"

    def test_another_model_launches_with_the_global_context(
        self, tmp_path: Path
    ) -> None:
        orch = Orchestrator(AppConfig(root=str(tmp_path), ctx_size=4096))
        orch.set_server_arg("--ctx-size", "16384", model="big.gguf")
        cmd = orch._server_args_for("llama-server", str(tmp_path / "small.gguf"))
        assert cmd[cmd.index("-c") + 1] == "4096"

    def test_clearing_a_value_leaves_the_own_config_at_the_defaults(
        self, tmp_path: Path
    ) -> None:
        """An emptied flag is 'not set' — the model keeps its own config."""
        orch = Orchestrator(AppConfig(root=str(tmp_path), ctx_size=4096))
        orch.set_server_arg("--ctx-size", "16384", model="big.gguf")
        orch.set_server_arg("--ctx-size", "", model="big.gguf")
        assert orch.cfg.model_server_options == {"big.gguf": {}}

    def test_clearing_a_model_writes_empty_values_not_the_globals(
        self, tmp_path: Path
    ) -> None:
        orch = _two_models(tmp_path)

        orch.clear_server_args("big.gguf")

        assert orch.cfg.model_server_options == {
            "small.gguf": {"--ctx-size": "2048"},
            "big.gguf": {},
        }
        assert orch.cfg.ctx_size == 4096

    def test_clearing_globally_keeps_the_model_configs(self, tmp_path: Path) -> None:
        orch = Orchestrator(
            AppConfig(root=str(tmp_path), server_options={"--jinja": "on"})
        )
        orch.set_server_arg("--ctx-size", "16384", model="big.gguf")
        orch.clear_server_args()
        assert orch.cfg.server_options == {}
        assert "--ctx-size" in orch.cfg.model_server_options["big.gguf"]

    def test_resetting_a_model_hands_it_back_to_the_globals(
        self, tmp_path: Path
    ) -> None:
        orch = _two_models(tmp_path)

        orch.reset_model_server_config("big.gguf")

        assert orch.cfg.model_server_options == {"small.gguf": {"--ctx-size": "2048"}}
        assert orch.describe_server_args("--ctx-size", "big.gguf")["mode"] == "global"

    def test_saving_an_empty_config_keeps_the_model_on_its_own(
        self, tmp_path: Path
    ) -> None:
        orch = Orchestrator(AppConfig(root=str(tmp_path), ctx_size=4096))

        orch.save_model_server_config("big.gguf", {})

        assert orch.cfg.model_server_options == {"big.gguf": {}}
        assert orch.describe_server_args(None, "big.gguf")["mode"] == "own"
        cmd = orch._server_args_for("llama-server", str(tmp_path / "big.gguf"))
        assert "-c" not in cmd

    def test_the_port_cannot_be_moved_to_one_model(self, tmp_path: Path) -> None:
        """The app probes one host:port for health, status and stop."""
        orch = Orchestrator(AppConfig(root=str(tmp_path), port=8080))
        with pytest.raises(EngineError) as excinfo:
            orch.set_server_arg("--port", "9999", model="big.gguf")
        assert excinfo.value.exit_code == ExitCode.BAD_ARGUMENT
        assert orch.cfg.port == 8080

        with pytest.raises(EngineError):
            orch.save_model_server_config("big.gguf", {"--port": "9999"})
        assert orch.cfg.model_server_options == {}

    def test_a_bad_value_is_rejected_before_it_is_stored(self, tmp_path: Path) -> None:
        orch = Orchestrator(AppConfig(root=str(tmp_path)))
        with pytest.raises(EngineError):
            orch.set_server_arg("--ctx-size", "huge", model="big.gguf")
        assert orch.cfg.model_server_options == {}

        with pytest.raises(EngineError):
            orch.save_model_server_config("big.gguf", {"--ctx-size": "huge"})
        assert orch.cfg.model_server_options == {}


def test_dedicated_values_are_not_stored_in_server_options(tmp_path: Path) -> None:
    """Dedicated flags live in their own AppConfig fields, not the options map."""
    orch = Orchestrator(AppConfig(root=str(tmp_path)))
    orch.set_server_arg("--port", "9000")
    assert orch.cfg.server_options == {}


def test_set_server_arg_returns_the_updated_row(tmp_path: Path) -> None:
    orch = Orchestrator(AppConfig(root=str(tmp_path)))
    result = orch.set_server_arg("--jinja", "on")
    assert result["count"] == 1
    assert result["args"][0]["value"] == "on"


def test_clear_server_args_resets_every_option(tmp_path: Path) -> None:
    cfg = AppConfig(
        root=str(tmp_path),
        server_options={"--jinja": "on", "--cache-prompt": "on"},
    )
    orch = Orchestrator(cfg)
    orch.clear_server_args()
    assert orch.cfg.server_options == {}


def test_clear_server_args_leaves_dedicated_fields_alone(tmp_path: Path) -> None:
    """Clearing the catalogue must not reset host/port/ctx/ngl."""
    cfg = AppConfig(root=str(tmp_path), port=9000, host="0.0.0.0")
    orch = Orchestrator(cfg)
    orch.clear_server_args()
    assert orch.cfg.port == 9000
    assert orch.cfg.host == "0.0.0.0"


def test_server_options_reach_the_command_line(tmp_path: Path) -> None:
    """The whole point: a set option must appear in the built command line."""
    cfg = AppConfig(root=str(tmp_path), server_options={"--jinja": "on"})
    orch = Orchestrator(cfg)
    cmd = orch._server_args_for("llama-server", str(tmp_path / "m.gguf"))
    assert "--jinja" in cmd


def test_update_skips_when_already_current(tmp_path: Path) -> None:
    backend = _prebuilt_capable_backend()
    tag = "b10331"
    backend_dir = tmp_path / "managed" / backend
    backend_dir.mkdir(parents=True)
    (backend_dir / f"llama-server{_EXE_SUFFIX}").write_text("", encoding="utf-8")
    (backend_dir / ".version").write_text(
        f"{tag}\nmanaged-prebuilt\n", encoding="utf-8"
    )

    release: dict[str, Any] = {"tag_name": tag, "assets": []}

    cfg = AppConfig(root=str(tmp_path))
    orch = Orchestrator(cfg)
    with (
        patch("app.backends.prebuilt.latest_release", return_value=release),
        patch("app.orchestrator.clear_release_cache") as mock_clear,
    ):
        result = orch.update([backend], force=False)

    assert result.summary["skipped"] == 1
    assert result.results[0].status == "skipped"
    assert result.results[0].version == tag
    mock_clear.assert_called_once()


def _patch_update(
    backend: str, version: str = "b10331", bytes: int = 0
) -> tuple[MagicMock, MagicMock]:
    mock_clear = patch("app.orchestrator.clear_release_cache").start()
    mock_obtain = patch("app.orchestrator.Orchestrator._obtain_backend").start()
    mock_obtain.return_value = InstallResultItem(
        name=backend, status="ok", version=version, bytes=bytes
    )
    return mock_clear, mock_obtain


def test_update_force_re_downloads(tmp_path: Path) -> None:
    backend = _prebuilt_capable_backend()
    cfg = AppConfig(root=str(tmp_path))
    orch = Orchestrator(cfg)
    try:
        mock_clear, _ = _patch_update(backend, version="b10400", bytes=1500)
        result = orch.update([backend], force=True)
    finally:
        patch.stopall()

    mock_clear.assert_called_once()
    assert result.summary["updated"] == 1
    assert result.results[0].version == "b10400"


def test_update_clears_release_cache(tmp_path: Path) -> None:
    backend = _prebuilt_capable_backend()
    cfg = AppConfig(root=str(tmp_path))
    orch = Orchestrator(cfg)
    try:
        mock_clear, _ = _patch_update(backend)
        orch.update([backend], force=False)
    finally:
        patch.stopall()

    mock_clear.assert_called_once()
