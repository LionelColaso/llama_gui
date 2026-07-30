from __future__ import annotations

import contextlib
import platform as _platform
from pathlib import Path
from unittest.mock import patch

import pytest

from llamagui.config import AppConfig, PointedPaths
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


def test_status_resolved_matches_dashboard_contract(tmp_path: Path) -> None:
    # Regression guard for the bug where status() keyed the `resolved` dict by
    # backend name (path=None) instead of the binary names the dashboard reads
    # ("llama_server"/"llama_swap"), so it never showed a real path and `ready`
    # was permanently False.
    bin_dir = tmp_path / "bins"
    bin_dir.mkdir()
    (bin_dir / f"llama-server{_EXE_SUFFIX}").write_text("", encoding="utf-8")
    (bin_dir / f"llama-swap{_EXE_SUFFIX}").write_text("", encoding="utf-8")

    cfg = AppConfig(root=str(tmp_path))
    cfg.pointed = PointedPaths(folder=str(bin_dir))
    orch = Orchestrator(cfg)
    s = orch.status()

    assert set(s.resolved.keys()) == {"llama_server", "llama_swap"}
    # Pointed binaries are found and their paths/ sources are populated.
    assert s.resolved["llama_server"].path is not None
    assert s.resolved["llama_server"].source == "pointed"
    assert s.resolved["llama_swap"].path is not None
    assert s.ready is True


def test_resolve_invalid(tmp_path: Path) -> None:
    cfg = AppConfig(root=str(tmp_path))
    orch = Orchestrator(cfg)
    # Isolate from any binaries actually installed on the host (e.g. on PATH),
    # so resolution deterministically fails to find a usable llama-server.
    with patch("llamagui.resolver.shutil.which", return_value=None):
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

    The "No asset matching" path is reached via the prebuilt source, so we need
    a backend whose release actually exists (i.e. has a prebuilt) regardless of
    whether it is also buildable here.
    """
    from llamagui.models import BACKENDS
    from llamagui.paths import platform_key

    plat = platform_key()
    for b in BACKENDS:
        if b.has_prebuilt(plat):
            return b.name
    raise AssertionError(f"no prebuilt-capable backend on {plat}")


def _buildable_backend() -> str:
    """A backend that is BOTH buildable and has a prebuilt on this platform.

    Used for the build -> prebuilt fallback path: the build is attempted (and
    fails with no toolchain), then the orchestrator falls back to the prebuilt
    lookup, which (with the empty-release fixture) raises "No asset matching".
    Picking a backend with a prebuilt is essential -- on macOS a buildable-only
    backend like "cpu" has no darwin prebuilt and would raise a different error.
    """
    from llamagui.models import BACKENDS
    from llamagui.paths import platform_key

    plat = platform_key()
    for b in BACKENDS:
        if b.is_buildable(plat) and b.has_prebuilt(plat):
            return b.name
    raise AssertionError(f"no buildable+prebuilt backend on {plat}")


def _assert_install_fails_no_asset(
    tmp_path: Path, action: str, **kwargs: object
) -> None:
    # Drive the prebuilt path explicitly so the empty-release fixture reliably
    # triggers the "No asset matching" error on every platform (on macOS
    # "vulkan" has neither a prebuilt nor a build, which would raise a platform
    # error before the asset lookup runs).
    backend = _prebuilt_capable_backend()
    cfg = AppConfig(root=str(tmp_path))
    orch = Orchestrator(cfg)
    with (
        patch(
            "llamagui.backends.prebuilt.latest_release", return_value=_empty_release()
        ),
        pytest.raises(Exception, match="No asset matching"),
    ):
        method = getattr(orch, action)
        method([backend], source="prebuilt", **kwargs)


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
    assert obtained == [backend]
    assert (tmp_path / "state" / "active.txt").read_text(
        encoding="utf-8"
    ).strip() == backend


def test_install_source_resolves_from_config_and_override(tmp_path: Path) -> None:
    orch = Orchestrator(AppConfig(root=str(tmp_path), install_source="build"))
    assert orch._install_source(None) == "build"
    assert orch._install_source("prebuilt") == "prebuilt"


def test_install_build_without_toolchain_falls_back_to_prebuilt(tmp_path: Path) -> None:
    """No toolchain -> build is skipped -> prebuilt asset lookup still runs."""
    orch = Orchestrator(AppConfig(root=str(tmp_path)))
    backend = _buildable_backend()
    with (
        patch(
            "llamagui.backends.prebuilt.latest_release", return_value=_empty_release()
        ),
        pytest.raises(Exception, match="No asset matching"),
    ):
        orch.install([backend], source="build")


def test_try_build_without_toolchain_returns_none(tmp_path: Path) -> None:
    orch = Orchestrator(AppConfig(root=str(tmp_path)))
    # No vendored llama.cpp source and no toolchain => not possible.
    assert orch._try_build("vulkan") is None
