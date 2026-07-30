from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

from .backends.build import (
    BUILDABLE_BACKENDS,
    ToolchainMissing,
    build_backend,
    build_llama_swap,
    detect_go,
    has_toolchain,
)
from .backends.prebuilt import (
    install_backend,
    install_llama_swap,
    list_assets,
)
from .config import AppConfig, PointedPaths
from .lifecycle import (
    LifecycleError,
    check_port,
    read_active_backend,
    read_component_version,
    read_junction_target,
    stop_processes,
)
from .locking import LockAcquisitionError, mutation_lock
from .models import BACKEND_TABLE
from .resolver import resolve_both
from .schemas import (
    BackendInfo,
    BackendStatusData,
    BuildData,
    DescribeData,
    ExitCode,
    InstallData,
    InstallResultItem,
    ListAssetsData,
    LlamaSwapStatusData,
    ResolveData,
    ResolvedBinaryData,
    RouterStatusData,
    StatusData,
    StopData,
    SwitchData,
)


class Orchestrator:
    def __init__(self, cfg: AppConfig | None = None) -> None:
        self.cfg = cfg or AppConfig.load()
        self.root = Path(self.cfg.root)

    def backend_names(self) -> list[str]:
        """Return the ordered list of backend names (data-driven, not hardcoded)."""
        return [b["name"] for b in BACKEND_TABLE]

    def describe(self) -> DescribeData:
        backends = [
            BackendInfo(
                name=b["name"],
                notes=b.get("notes", ""),
                needs_cudart=b.get("needs_cudart", False),
            )
            for b in BACKEND_TABLE
        ]
        return DescribeData(
            backends=backends,
            supported_sources=[
                "pointed",
                "managed-prebuilt",
                "managed-build",
                "system",
            ],
            available_actions=[
                "describe",
                "status",
                "resolve",
                "install",
                "update",
                "use",
                "stop",
                "launch",
                "restart",
                "list-assets",
                "build",
            ],
            defaults={"port": self.cfg.port, "listen_flag": self.cfg.listen_flag},
            valid_exit_codes=[0, 1, 2, 3, 4, 5, 6, 7],
        )

    def status(self) -> StatusData:
        # Fast path: don't spawn `--version` subprocesses on the GUI hot path
        # (the dashboard refreshes every 3s). Existence of the binary is enough
        # for a status read; the resolver page does the authoritative validation.
        resolved = resolve_both(self.cfg, validate=False)

        backends: dict[str, BackendStatusData] = {}
        for b in BACKEND_TABLE:
            name = b["name"]
            cv = read_component_version(self.root, name)
            backends[name] = BackendStatusData(
                installed=cv is not None,
                version=cv[0] if cv else None,
                source=cv[1] if cv else None,
            )

        ls_cv = read_component_version(self.root, "llama-swap")
        active = read_active_backend(self.root)
        junction_target = read_junction_target(self.root)

        return StatusData(
            backends=backends,
            active=active,
            junction_target=junction_target,
            router=RouterStatusData(
                port=self.cfg.port,
                listening=check_port("127.0.0.1", self.cfg.port),
            ),
            llama_swap=LlamaSwapStatusData(
                installed=ls_cv is not None,
                version=ls_cv[0] if ls_cv else None,
                source=ls_cv[1] if ls_cv else None,
            ),
            config_present=(self.root / "config.yaml").exists(),
            resolved={
                k: ResolvedBinaryData(
                    path=r.path,
                    source=r.source.value if r.source else None,
                    version=r.version,
                    valid=r.valid,
                    error=r.error,
                )
                for k, r in resolved.items()
            },
        )

    def resolve(self) -> ResolveData:
        resolved = resolve_both(self.cfg)
        ls = resolved["llama_server"]
        lsw = resolved["llama_swap"]
        return ResolveData(
            llama_server=ResolvedBinaryData(
                path=ls.path,
                source=ls.source.value if ls.source else None,
                version=ls.version,
                valid=ls.valid,
                error=ls.error,
            ),
            llama_swap=ResolvedBinaryData(
                path=lsw.path,
                source=lsw.source.value if lsw.source else None,
                version=lsw.version,
                valid=lsw.valid,
                error=lsw.error,
            ),
        )

    def install(
        self, backends: list[str] | None = None, force: bool = False
    ) -> InstallData:
        with mutation_lock(self.root):
            return self._do_install(backends, force)

    def update(
        self, backends: list[str] | None = None, force: bool = True
    ) -> InstallData:
        with mutation_lock(self.root):
            return self._do_install(backends, force)

    def use(self, backend: str, auto_install: bool = False) -> SwitchData:
        with mutation_lock(self.root):
            active_before = read_active_backend(self.root)
            auto_installed = False

            if auto_install:
                managed_root = self.root / "managed"
                marker = managed_root / backend / ".version"
                if not marker.exists():
                    self._do_install([backend], force=False)
                    auto_installed = True

            managed_root = self.root / "managed"
            target = managed_root / backend
            if not target.exists():
                msg = f"Backend '{backend}' is not installed. Install it first or use --auto-install."
                raise LockAcquisitionError(ExitCode.NOT_AVAILABLE, msg)

            current = managed_root / "current"
            if current.exists():
                current.unlink()
            _create_junction(current, str(target.resolve()))

            active_path = self.root / "state" / "active.txt"
            active_path.parent.mkdir(parents=True, exist_ok=True)
            active_path.write_text(backend + "\n", encoding="utf-8")

            active_after = read_active_backend(self.root)

            return SwitchData(
                backend=backend,
                active_before=active_before,
                active_after=active_after,
                auto_installed=auto_installed,
            )

    def stop(self) -> StopData:
        with mutation_lock(self.root):
            return StopData(**dict(stop_processes(self.root, port=self.cfg.port)))

    def launch(self, verify: bool = False) -> int | None:
        from .lifecycle import launch_llama_swap

        # Resolve the llama-swap binary through the resolver so pointed /
        # system sources work, not just the managed install (invariant: the
        # resolver is the single source of truth for binary locations).
        resolved = resolve_both(self.cfg)
        swap = resolved["llama_swap"]
        if not swap.path or not swap.valid:
            raise LifecycleError(
                ExitCode.NOT_AVAILABLE,
                "llama-swap binary not resolved. Install it, point at an "
                "existing install, or add it to PATH.",
            )
        return launch_llama_swap(
            exe_path=swap.path,
            config_path=str(self.root / "config.yaml"),
            host="127.0.0.1",
            port=self.cfg.port,
            listen_flag=self.cfg.listen_flag,
            root=self.root,
            verify=verify,
        )

    def restart(self, verify: bool = False) -> int | None:
        self.stop()
        return self.launch(verify=verify)

    def build(self, backends: list[str] | None = None) -> list[BuildData]:
        with mutation_lock(self.root):
            return self._do_build(backends)

    def _vendor_root(self) -> Path:
        # The submodules live at the repository root (a sibling of the
        # ``llamagui`` package), not relative to the user's managed root
        # (~/.llamagui). Derive the repo root from this package's location so
        # managed-build and submodule-based install find ``vendor/llama.cpp``
        # regardless of where the app stores its managed state.
        pkg_root = Path(__file__).resolve().parent
        candidate = pkg_root.parent / "vendor"
        if candidate.exists():
            return candidate
        # Fall back to a path relative to the managed root (legacy layout).
        return self.root / ".." / "vendor"

    def _do_build(self, backends: list[str] | None) -> list[BuildData]:
        managed_root = self.root / "managed"
        src_root = self._vendor_root()
        build_root = self.root / "build"
        build_root.mkdir(parents=True, exist_ok=True)

        if backends is None:
            backends = list(BUILDABLE_BACKENDS)

        results: list[BuildData] = []
        for name in backends:
            ll_src = src_root / "llama.cpp"
            if not ll_src.exists():
                results.append(
                    BuildData(name=name, status="skipped", source="no-submodule")
                )
                continue
            if not has_toolchain(name):
                results.append(
                    BuildData(
                        name=name,
                        status="skipped",
                        source="no-toolchain",
                    )
                )
                continue
            try:
                data = build_backend(name, managed_root, ll_src, build_root)
                results.append(BuildData(**data))
            except (ToolchainMissing, RuntimeError) as e:
                results.append(BuildData(name=name, status="failed", version=str(e)))

        ls_src = src_root / "llama-swap"
        if ls_src.exists() and detect_go():
            try:
                data = build_llama_swap(managed_root, ls_src, build_root)
                results.append(BuildData(**data))
            except (ToolchainMissing, RuntimeError):
                pass

        return results

    def _do_install(self, backends: list[str] | None, force: bool) -> InstallData:
        managed_root = self.root / "managed"
        dls = self.root / "downloads"
        token = self._github_token()
        src_root = self._vendor_root()
        build_root = self.root / "build"

        if backends is None:
            backends = self.backend_names()

        results: list[InstallResultItem] = []
        for name in backends:
            ll_src = src_root / "llama.cpp"
            if ll_src.exists() and has_toolchain(name):
                try:
                    build_result = build_backend(name, managed_root, ll_src, build_root)
                    results.append(
                        InstallResultItem(
                            name=build_result["name"],
                            status=build_result["status"],
                            version=build_result.get("version"),
                            bytes=build_result.get("bytes"),
                        )
                    )
                    continue
                except (ToolchainMissing, RuntimeError):
                    pass
            result = install_backend(name, managed_root, dls, token, force)
            results.append(
                InstallResultItem(
                    name=result["name"],
                    status=result["status"],
                    version=result.get("version"),
                    bytes=result.get("bytes"),
                )
            )

        ls_src = src_root / "llama-swap"
        swap_result: dict[str, Any] = {}
        if ls_src.exists() and detect_go():
            try:
                swap_result = build_llama_swap(managed_root, ls_src, build_root)
            except (ToolchainMissing, RuntimeError):
                # Fall back to the prebuilt release if the submodule build fails.
                swap_result = install_llama_swap(managed_root, dls, token, force)
        else:
            swap_result = install_llama_swap(managed_root, dls, token, force)

        updated = sum(1 for r in results if r.status == "ok")
        skipped = sum(1 for r in results if r.status == "skipped")
        failed = sum(1 for r in results if r.status == "failed")
        return InstallData(
            release=results[0].version if results else None,
            results=results,
            llama_swap=swap_result,
            summary={"updated": updated, "skipped": skipped, "failed": failed},
        )

    def _github_token(self) -> str | None:
        """Return the GitHub token from config or the OS keyring (never plaintext on disk)."""
        if self.cfg.token:
            return self.cfg.token
        try:
            from .gui.token import get_token

            return get_token()
        except ImportError:
            return None

    def save_config(self, data: dict[str, Any]) -> None:
        """Save configuration from a dict (called by the GUI settings page)."""
        pointed = data.get("pointed", {})
        self.cfg = AppConfig(
            root=data.get("root", str(self.cfg.root)),
            port=data.get("port", self.cfg.port),
            listen_flag=data.get("listen_flag", self.cfg.listen_flag),
            default_backend=data.get("default_backend", self.cfg.default_backend),
            source_priority=data.get("source_priority", self.cfg.source_priority),
            pointed=PointedPaths(
                folder=pointed.get("folder"),
                llama_server=pointed.get("llama_server"),
                llama_swap=pointed.get("llama_swap"),
            ),
            auto_update=data.get("auto_update", self.cfg.auto_update),
            auto_update_interval_hours=data.get(
                "auto_update_interval_hours", self.cfg.auto_update_interval_hours
            ),
            launch_on_start=data.get("launch_on_start", self.cfg.launch_on_start),
            start_minimized=data.get("start_minimized", self.cfg.start_minimized),
            theme=data.get("theme", self.cfg.theme),
            token=data.get("token", self.cfg.token),
        )
        self.root = Path(self.cfg.root)
        self.cfg.save()

    def list_assets(self) -> ListAssetsData:
        result = list_assets(token=self._github_token())
        return ListAssetsData(**result)


def _create_junction(link: Path, target: str) -> None:
    if sys.platform == "win32":
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), target],
            check=True,
            capture_output=True,
        )
    else:
        link.symlink_to(target, target_is_directory=True)
