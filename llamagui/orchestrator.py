"""The engine: resolve, obtain, switch, launch and stop the servers.

Everything the GUI and the CLI can do goes through this one typed API so both
front-ends behave identically. Mutations take the single-writer lock; reads
never spawn a subprocess.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any, cast

from .backends.build import (
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
    check_port,
    launch_llama_swap,
    read_active_backend,
    read_component_version,
    read_junction_target,
    read_log_tail,
    running_pids,
    stop_processes,
)
from .locking import mutation_lock
from .models import (
    backend_availability,
    backend_table,
    get_backend,
    platform_backend_names,
    platform_default_backend,
)
from .paths import arch_key, config_file, exe_suffix, is_windows, platform_key
from .resolver import resolve_both, validate_binary
from .schemas import (
    BackendInfo,
    BackendStatusData,
    BootstrapData,
    BuildData,
    ConfigData,
    DescribeData,
    EngineError,
    ExitCode,
    InstallData,
    InstallResultItem,
    ListAssetsData,
    LlamaSwapStatusData,
    PlatformData,
    ResolveData,
    ResolvedBinaryData,
    RouterStatusData,
    StatusData,
    StopData,
    SwitchData,
)

ACTIONS = (
    "describe",
    "status",
    "resolve",
    "bootstrap",
    "install",
    "update",
    "build",
    "use",
    "stop",
    "launch",
    "restart",
    "list-assets",
    "config",
)


class Orchestrator:
    def __init__(self, cfg: AppConfig | None = None) -> None:
        self.cfg = cfg or AppConfig.load()

    # ─── Paths ───────────────────────────────────────────────────────────

    @property
    def root(self) -> Path:
        return self.cfg.root_path

    @property
    def managed_root(self) -> Path:
        return self.cfg.managed_dir

    # ─── Catalogue ───────────────────────────────────────────────────────

    def backend_names(self) -> list[str]:
        """Backends usable on this platform (prebuilt available or buildable)."""
        return platform_backend_names()

    def describe(self) -> DescribeData:
        return DescribeData(
            backends=[BackendInfo(**row) for row in backend_table()],
            supported_sources=[
                "pointed",
                "managed-prebuilt",
                "managed-build",
                "system",
            ],
            available_actions=list(ACTIONS),
            defaults={
                "host": self.cfg.host,
                "port": self.cfg.port,
                "listen_flag": self.cfg.listen_flag,
                "backend": platform_default_backend(),
                "root": str(self.root),
                "config_file": str(config_file()),
            },
            valid_exit_codes=[int(code) for code in ExitCode],
            platform=_platform_data(),
        )

    # ─── Reads ───────────────────────────────────────────────────────────

    def status(self) -> StatusData:
        # Fast path: no `--version` subprocesses, so the dashboard can poll.
        # The per-backend install state comes from the `.version` markers; the
        # resolved binaries come from resolve_both(validate=False), which only
        # does existence checks (no subprocess) so the dashboard invariant holds.
        backends: dict[str, BackendStatusData] = {}
        for row in backend_table():
            name = str(row["name"])
            marker = read_component_version(self.root, name)
            version = marker[0] if marker else None
            source = marker[1] if marker else None
            backends[name] = BackendStatusData(
                installed=marker is not None,
                version=version,
                source=source,
                prebuilt_available=bool(row["prebuilt_available"]),
                buildable=bool(row["buildable"]),
                unavailable_reason=str(row["unavailable_reason"]),
            )

        swap_marker = read_component_version(self.root, "llama-swap")
        # Resolve the actual binaries (file-existence only) so the dashboard can
        # show the real paths and `ready` reflects what is actually present.
        resolved = {
            name: _to_resolved(binary)
            for name, binary in resolve_both(self.cfg, validate=False).items()
        }
        return StatusData(
            backends=backends,
            active=read_active_backend(self.root),
            junction_target=read_junction_target(self.root),
            router=RouterStatusData(
                host=self.cfg.host,
                port=self.cfg.port,
                listening=check_port(self.cfg.host, self.cfg.port),
                pids=running_pids(self.root),
            ),
            llama_swap=LlamaSwapStatusData(
                installed=swap_marker is not None,
                version=swap_marker[0] if swap_marker else None,
                source=swap_marker[1] if swap_marker else None,
            ),
            config_present=self.cfg.models_config_path.exists(),
            resolved=resolved,
            platform=_platform_data(),
            root=str(self.root),
            config_file=str(config_file()),
            ready=all(r.path for r in resolved.values()),
            first_run_complete=self.cfg.first_run_complete,
        )

    def resolve(self) -> ResolveData:
        """Authoritative resolution: runs each binary to confirm it works."""
        resolved = resolve_both(self.cfg, validate=True)
        return ResolveData(
            llama_server=_to_resolved(resolved["llama_server"]),
            llama_swap=_to_resolved(resolved["llama_swap"]),
        )

    def validate_path(self, path: str) -> ResolvedBinaryData:
        """Probe an arbitrary path (used by the Settings "Validate" button)."""
        valid, version, error = validate_binary(path)
        return ResolvedBinaryData(
            path=path, source="pointed", version=version, valid=valid, error=error
        )

    def log_tail(self, lines: int = 200) -> list[str]:
        return read_log_tail(self.root, lines=lines)

    # ─── Settings ────────────────────────────────────────────────────────

    def config(self) -> ConfigData:
        return ConfigData(
            config_file=str(config_file()),
            values=self.cfg.to_dict(),
            warnings=list(self.cfg.load_warnings),
        )

    def save_config(self, data: dict[str, Any]) -> ConfigData:
        """Merge a partial settings dict into the saved config and persist it.

        Unknown keys already on disk are preserved, and the file is written
        atomically, so a settings change can never lose the rest of the config.
        """
        merged = self.cfg.to_dict()
        for key, value in data.items():
            if key == "pointed" and isinstance(value, dict):
                pointed: dict[str, Any] = cast(
                    "dict[str, Any]", dict(merged.get("pointed") or {})
                )
                pointed.update(cast("dict[str, Any]", value))
                merged["pointed"] = pointed
            else:
                merged[key] = value

        token = data.get("token")
        updated = AppConfig.from_dict(merged)
        updated.token = token if isinstance(token, str) else self.cfg.token
        updated.save()
        self.cfg = updated
        return self.config()

    def set_pointed_paths(
        self,
        folder: str | None = None,
        llama_server: str | None = None,
        llama_swap: str | None = None,
    ) -> ConfigData:
        """Point the resolver at binaries the user already has (requirement 3)."""
        return self.save_config(
            {
                "pointed": PointedPaths(
                    folder=folder, llama_server=llama_server, llama_swap=llama_swap
                ).to_dict()
            }
        )

    # ─── Obtain ──────────────────────────────────────────────────────────

    def install(
        self,
        backends: list[str] | None = None,
        force: bool = False,
        source: str | None = None,
    ) -> InstallData:
        with mutation_lock(self.root):
            return self._do_install(backends, force, source)

    def update(
        self,
        backends: list[str] | None = None,
        force: bool = True,
        source: str | None = None,
    ) -> InstallData:
        with mutation_lock(self.root):
            return self._do_install(backends, force, source)

    def build(self, backends: list[str] | None = None) -> list[BuildData]:
        """Build from the vendored submodules; no silent prebuilt fallback."""
        with mutation_lock(self.root):
            return self._do_build(backends)

    def bootstrap(
        self, backend: str | None = None, force: bool = False
    ) -> BootstrapData:
        """Make the app usable out of the box (requirement 2).

        Downloads the latest llama.cpp backend and llama-swap release only for
        the pieces that are not already available — anything the resolver
        already finds (pointed, system, or a previous managed install) is left
        alone.
        """
        with mutation_lock(self.root):
            target = backend or self._preferred_backend()
            self._require_known_backend(target)

            resolved = resolve_both(self.cfg, validate=False)
            performed: list[str] = []
            skipped: list[str] = []
            cpp_version: str | None = None
            swap_version: str | None = None

            # Always obtain the backend through the installer so that the
            # returned version is the single source of truth (no second
            # filesystem read of the .version marker).
            result = self._obtain_backend(target, force=force)
            cpp_version = result.version
            (performed if result.status == "ok" else skipped).append(
                f"llama.cpp:{target}"
            )
            if result.status == "ok":
                self._activate(target)

            if force or not resolved["llama_swap"].path:
                swap = self._obtain_llama_swap(force=force)
                swap_version = str(swap.get("version") or "") or None
                (performed if swap.get("status") == "ok" else skipped).append(
                    "llama-swap"
                )
            else:
                skipped.append("llama-swap (already available)")
                swap_version = _marker_version(self.root, "llama-swap")

            self.cfg.first_run_complete = True
            self.cfg.save()

            ready = all(r.path for r in resolve_both(self.cfg, validate=False).values())
            return BootstrapData(
                performed=performed,
                skipped=skipped,
                backend=target,
                llama_cpp_version=cpp_version,
                llama_swap_version=swap_version,
                ready=ready,
                message=(
                    "Ready to launch." if ready else "Some binaries are still missing."
                ),
            )

    # ─── Switch ──────────────────────────────────────────────────────────

    def use(self, backend: str, auto_install: bool = False) -> SwitchData:
        """Switch the active backend (vulkan / cuda12 / cuda13 / ...)."""
        with mutation_lock(self.root):
            self._require_known_backend(backend)
            active_before = read_active_backend(self.root)
            auto_installed = False

            target = self.managed_root / backend
            if not _has_payload(target):
                if not auto_install:
                    raise EngineError(
                        ExitCode.NOT_AVAILABLE,
                        f"Backend '{backend}' is not installed. "
                        "Install it first, or switch with auto-install enabled.",
                    )
                self._obtain_backend(backend, force=False)
                auto_installed = True

            self._activate(backend)
            return SwitchData(
                backend=backend,
                active_before=active_before,
                active_after=read_active_backend(self.root),
                auto_installed=auto_installed,
            )

    # ─── Run ─────────────────────────────────────────────────────────────

    def launch(self, verify: bool = False) -> int | None:
        resolved = resolve_both(self.cfg, validate=True)
        swap = resolved["llama_swap"]
        if not swap.path or not swap.valid:
            raise EngineError(
                ExitCode.NOT_AVAILABLE,
                "llama-swap is not available: "
                + (swap.error or "install it, point at it, or add it to PATH."),
            )
        config_path = self.cfg.models_config_path
        if not config_path.exists():
            raise EngineError(
                ExitCode.NOT_AVAILABLE,
                f"No llama-swap config at {config_path}. Add a model on the "
                "Models page first.",
            )
        return launch_llama_swap(
            exe_path=swap.path,
            config_path=str(config_path),
            host=self.cfg.host,
            port=self.cfg.port,
            listen_flag=self.cfg.listen_flag,
            root=self.root,
            verify=verify,
        )

    def stop(self) -> StopData:
        with mutation_lock(self.root):
            result = stop_processes(self.root, host=self.cfg.host, port=self.cfg.port)
            return StopData(**result)

    def restart(self, verify: bool = False) -> int | None:
        self.stop()
        return self.launch(verify=verify)

    def list_assets(self) -> ListAssetsData:
        return ListAssetsData(**list_assets(token=self._github_token()))

    # ─── Internals ───────────────────────────────────────────────────────

    def _preferred_backend(self) -> str:
        configured = self.cfg.default_backend
        if configured in self.backend_names():
            return configured
        return platform_default_backend()

    def _require_known_backend(self, backend: str) -> None:
        if get_backend(backend) is None:
            raise EngineError(
                ExitCode.BAD_ARGUMENT,
                f"Unknown backend '{backend}'. Known: {', '.join(self.backend_names())}",
            )

    def _install_source(self, override: str | None = None) -> str:
        return override or self.cfg.install_source

    def _do_install(
        self, backends: list[str] | None, force: bool, source: str | None
    ) -> InstallData:
        names = backends or [self._preferred_backend()]
        results: list[InstallResultItem] = []
        for name in names:
            self._require_known_backend(name)
            results.append(self._obtain_backend(name, force=force, source=source))

        swap = self._obtain_llama_swap(force=force, source=source)
        return InstallData(
            release=next((r.version for r in results if r.version), None),
            results=results,
            llama_swap=swap,
            summary={
                "updated": sum(1 for r in results if r.status == "ok"),
                "skipped": sum(1 for r in results if r.status == "skipped"),
                "failed": sum(1 for r in results if r.status == "failed"),
            },
        )

    def _obtain_backend(
        self, backend: str, force: bool, source: str | None = None
    ) -> InstallResultItem:
        """Download (default) or build one backend, with a sensible fallback."""
        want = self._install_source(source)
        availability = backend_availability(backend)

        if want == "build" or not availability["prebuilt"]:
            built = self._try_build(backend)
            if built is not None:
                return built
            if not availability["prebuilt"]:
                raise EngineError(ExitCode.NOT_AVAILABLE, availability["reason"])

        result = install_backend(
            backend,
            self.managed_root,
            self.cfg.downloads_dir,
            self._github_token(),
            force,
            bundle_cuda_runtime=self.cfg.bundle_cuda_runtime,
        )
        return InstallResultItem(
            name=result["name"],
            status=result["status"],
            version=result.get("version"),
            bytes=result.get("bytes"),
        )

    def _try_build(self, backend: str) -> InstallResultItem | None:
        """Attempt a from-source build; return None when it is not possible."""
        source_dir = self._vendor_root() / "llama.cpp"
        if not source_dir.exists() or not has_toolchain(backend):
            return None
        try:
            data = build_backend(
                backend, self.managed_root, source_dir, self.root / "build"
            )
        except (ToolchainMissing, RuntimeError):
            return None
        return InstallResultItem(
            name=data["name"],
            status=data["status"],
            version=data.get("version"),
            bytes=data.get("bytes"),
        )

    def _obtain_llama_swap(
        self, force: bool, source: str | None = None
    ) -> dict[str, Any]:
        if self._install_source(source) == "build":
            source_dir = self._vendor_root() / "llama-swap"
            if source_dir.exists() and detect_go():
                try:
                    return build_llama_swap(
                        self.managed_root, source_dir, self.root / "build"
                    )
                except (ToolchainMissing, RuntimeError):
                    pass  # fall through to the prebuilt release
        return install_llama_swap(
            self.managed_root, self.cfg.downloads_dir, self._github_token(), force
        )

    def _do_build(self, backends: list[str] | None) -> list[BuildData]:
        source_root = self._vendor_root()
        build_root = self.root / "build"
        build_root.mkdir(parents=True, exist_ok=True)
        names = backends or [self._preferred_backend()]

        results: list[BuildData] = []
        missing_toolchain: list[str] = []
        for name in names:
            self._require_known_backend(name)
            source_dir = source_root / "llama.cpp"
            if not source_dir.exists():
                results.append(
                    BuildData(name=name, status="skipped", source="no-submodule")
                )
                continue
            if not has_toolchain(name):
                # Reported per backend so a mixed request still builds what it
                # can; the CLI turns an all-skipped run into exit 7.
                missing_toolchain.append(name)
                results.append(
                    BuildData(name=name, status="skipped", source="no-toolchain")
                )
                continue
            try:
                results.append(
                    BuildData(
                        **build_backend(name, self.managed_root, source_dir, build_root)
                    )
                )
            except RuntimeError as e:
                results.append(BuildData(name=name, status="failed", version=str(e)))

        swap_src = source_root / "llama-swap"
        if swap_src.exists() and detect_go():
            try:
                results.append(
                    BuildData(
                        **build_llama_swap(self.managed_root, swap_src, build_root)
                    )
                )
            except (ToolchainMissing, RuntimeError) as e:
                results.append(
                    BuildData(name="llama-swap", status="failed", version=str(e))
                )

        if missing_toolchain and len(missing_toolchain) == len(names):
            raise ToolchainMissing(
                "cmake/compiler",
                "No build toolchain found for "
                f"{', '.join(missing_toolchain)}. Download the prebuilt release "
                "instead (Actions → Install).",
            )
        return results

    def _activate(self, backend: str) -> None:
        """Point ``managed/current`` at a backend and record it in state."""
        target = self.managed_root / backend
        if not target.is_dir():
            return
        _link_current(self.managed_root / "current", target)
        state_file = self.cfg.state_dir / "active.txt"
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(backend + "\n", encoding="utf-8")

    def _vendor_root(self) -> Path:
        """Locate the ``vendor/`` submodules next to the installed package."""
        candidate = Path(__file__).resolve().parent.parent / "vendor"
        if candidate.exists():
            return candidate
        return self.root / "vendor"

    def _github_token(self) -> str | None:
        if self.cfg.token:
            return self.cfg.token
        env_token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        if env_token:
            return env_token
        try:
            from .gui.token import get_token
        except ImportError:  # pragma: no cover - keyring backend missing
            return None
        return get_token()


# ─── Module helpers ───────────────────────────────────────────────────────


def _platform_data() -> PlatformData:
    return PlatformData(system=platform_key(), arch=arch_key(), exe_suffix=exe_suffix())


def _to_resolved(binary: Any) -> ResolvedBinaryData:
    return ResolvedBinaryData(
        path=binary.path,
        source=binary.source.value if binary.source else None,
        version=binary.version,
        valid=binary.valid,
        error=binary.error,
    )


def _marker_version(root: Path, name: str) -> str | None:
    marker = read_component_version(root, name)
    return marker[0] if marker else None


def _has_payload(directory: Path) -> bool:
    """True when a managed backend directory actually holds something."""
    return directory.is_dir() and any(directory.iterdir())


def _link_current(link: Path, target: Path) -> None:
    """Point ``link`` at ``target`` using the best mechanism per OS.

    POSIX gets an atomically replaced symlink. Windows prefers a symlink (when
    Developer Mode is on) and falls back to a directory junction, which needs
    no elevation.
    """
    link.parent.mkdir(parents=True, exist_ok=True)

    if not is_windows():
        temp_link = link.with_name(link.name + ".new")
        _remove_link(temp_link)
        try:
            os.symlink(str(target), str(temp_link), target_is_directory=True)
            os.replace(str(temp_link), str(link))
            return
        except (OSError, NotImplementedError, ValueError) as e:
            _remove_link(temp_link)
            raise EngineError(
                ExitCode.UNEXPECTED_ERROR,
                f"Could not point 'current' at {target}: {e}",
            ) from e

    # Windows: replacing a directory link is not atomic, so drop it first.
    _remove_link(link)
    try:
        os.symlink(str(target), str(link), target_is_directory=True)
        return
    except (OSError, NotImplementedError, ValueError):
        _remove_link(link)
    try:
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError) as e:
        raise EngineError(
            ExitCode.UNEXPECTED_ERROR,
            f"Could not point 'current' at {target}: {e}",
        ) from e


def _remove_link(link: Path) -> None:
    """Delete a link without ever touching the directory it points at."""
    if link.is_symlink():
        link.unlink(missing_ok=True)
        return
    if not link.exists():
        return
    try:
        link.rmdir()  # junction / empty dir: removes the link, not the target
    except OSError:
        link.unlink(missing_ok=True)


__all__ = ["ACTIONS", "Orchestrator"]
