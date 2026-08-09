from __future__ import annotations

import contextlib
import platform as _platform
from pathlib import Path
from unittest.mock import patch

import pytest

from llamagui.config import AppConfig
from llamagui.orchestrator import Orchestrator

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


def test_describe_defaults(tmp_path: Path) -> None:
    cfg = AppConfig(root=str(tmp_path))
    orch = Orchestrator(cfg)
    d = orch.describe()
    assert d.defaults["port"] == 8080
    assert d.defaults["listen_flag"] == "--listen"


def test_status_empty(tmp_path: Path) -> None:
    cfg = AppConfig(root=str(tmp_path))
    orch = Orchestrator(cfg)
    s = orch.status()
    for backend in s.backends.values():
        assert backend.installed is False
    assert s.active is None
    assert s.junction_target is None
    assert s.router.listening is False


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


def test_resolve_invalid(tmp_path: Path) -> None:
    cfg = AppConfig(root=str(tmp_path))
    orch = Orchestrator(cfg)
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


def _assert_install_fails_no_asset(
    tmp_path: Path, action: str, **kwargs: object
) -> None:
    cfg = AppConfig(root=str(tmp_path))
    orch = Orchestrator(cfg)
    with (
        patch(
            "llamagui.backends.prebuilt.latest_release", return_value=_empty_release()
        ),
        pytest.raises(Exception, match="No asset matching"),
    ):
        method = getattr(orch, action)
        method(["vulkan"], **kwargs)


def test_install_with_fake_root(tmp_path: Path) -> None:
    _assert_install_fails_no_asset(tmp_path, "install")


def test_force_update_fails_without_network(tmp_path: Path) -> None:
    _assert_install_fails_no_asset(tmp_path, "update", force=True)


def test_use_without_auto_install_raises(tmp_path: Path) -> None:
    orch = Orchestrator(AppConfig(root=str(tmp_path)))
    with pytest.raises(Exception, match="not installed"):
        orch.use("vulkan")


def test_use_with_auto_install_obtains_backend(tmp_path: Path) -> None:
    orch = Orchestrator(AppConfig(root=str(tmp_path)))
    backend = "vulkan"
    target = tmp_path / "managed" / backend
    obtained: list[str] = []

    def fake_obtain(
        self: object, name: str, force: bool = False, source: str | None = None
    ) -> object:
        target.mkdir(parents=True, exist_ok=True)
        (target / f"llama-server{_EXE_SUFFIX}").write_text("", encoding="utf-8")
        obtained.append(name)
        return None

    with patch.object(Orchestrator, "_obtain_backend", fake_obtain):
        result = orch.use(backend, auto_install=True)

    assert result.auto_installed is True
    assert obtained == ["vulkan"]
    assert (tmp_path / "state" / "active.txt").read_text(
        encoding="utf-8"
    ).strip() == "vulkan"


def test_install_source_resolves_from_config_and_override(tmp_path: Path) -> None:
    orch = Orchestrator(AppConfig(root=str(tmp_path), install_source="build"))
    assert orch._install_source(None) == "build"
    assert orch._install_source("prebuilt") == "prebuilt"


def test_install_build_without_toolchain_falls_back_to_prebuilt(tmp_path: Path) -> None:
    """No toolchain -> build is skipped -> prebuilt asset lookup still runs."""
    orch = Orchestrator(AppConfig(root=str(tmp_path)))
    with (
        patch(
            "llamagui.backends.prebuilt.latest_release", return_value=_empty_release()
        ),
        pytest.raises(Exception, match="No asset matching"),
    ):
        orch.install(["vulkan"], source="build")


def test_try_build_without_toolchain_returns_none(tmp_path: Path) -> None:
    orch = Orchestrator(AppConfig(root=str(tmp_path)))
    # No vendored llama.cpp source and no toolchain => not possible.
    assert orch._try_build("vulkan") is None
