from __future__ import annotations

import contextlib
import platform as _platform
import threading
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.config import AppConfig
from app.locking import LockAcquisitionError, mutation_lock
from app.orchestrator import Orchestrator
from app.resolver import ResolvedBinary
from app.schemas import EngineError, InstallResultItem

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
