"""The engine: resolve, obtain, launch and stop llama-server; manage models.

Everything the GUI and the CLI can do goes through this one typed API so both
front-ends behave identically. Mutations take the single-writer lock; reads
never spawn a subprocess.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

from .backends.prebuilt import (
    install_backend,
    list_assets,
)
from .config import AppConfig
from .lifecycle import (
    build_llama_server_args,
    check_port,
    launch_llama_server,
    read_active_backend,
    read_component_version,
    read_junction_target,
    read_log_tail,
    running_pids,
    stop_processes,
)
from .locking import mutation_lock
from .model_store import (
    ModelDownloadError,
    download_model,
    list_models,
    remove_model,
)
from .models import (
    backend_availability,
    backend_table,
    get_backend,
    platform_backend_names,
    platform_default_backend,
)
from .paths import arch_key, config_file, exe_suffix, is_windows, platform_key
from .resolver import resolve_llama_server
from .schemas import (
    BackendInfo,
    BackendStatusData,
    BootstrapData,
    ConfigData,
    DescribeData,
    DownloadData,
    EngineError,
    ExitCode,
    InstallData,
    InstallResultItem,
    ListAssetsData,
    ModelsData,
    PlatformData,
    ResolveData,
    ResolvedBinaryData,
    ServerStatusData,
    StatusData,
    StopData,
    SwitchData,
)
from .serverargs import (
    DEDICATED_FLAGS,
    SERVER_ARGS,
    find_arg,
    validate_options,
    validate_value,
)

ACTIONS = (
    "describe",
    "status",
    "resolve",
    "bootstrap",
    "install",
    "update",
    "use",
    "list-models",
    "download-model",
    "set-model",
    "remove-model",
    "stop",
    "launch",
    "restart",
    "list-assets",
    "config",
    "server-args",
    "set-arg",
    "clear-args",
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
        """Backends with an official prebuilt on this platform."""
        return platform_backend_names()

    def describe(self) -> DescribeData:
        return DescribeData(
            backends=[BackendInfo(**row) for row in backend_table()],
            supported_sources=[
                "managed-prebuilt",
                "managed-build",
                "system",
            ],
            available_actions=list(ACTIONS),
            defaults={
                "host": self.cfg.host,
                "port": self.cfg.port,
                "backend": platform_default_backend(),
                "root": str(self.root),
                "models_dir": str(self.cfg.models_dir_path),
                "config_file": str(config_file()),
            },
            valid_exit_codes=[int(code) for code in ExitCode],
            platform=_platform_data(),
        )

    # ─── Reads ───────────────────────────────────────────────────────────

    def status(self) -> StatusData:
        # Fast path: no `--version` subprocesses, so the dashboard can poll.
        # The per-backend install state comes from the `.version` markers; the
        # resolved binary comes from resolve_llama_server(validate=False), which
        # only does an existence check (no subprocess) so the dashboard invariant
        # holds.
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
                unavailable_reason=str(row["unavailable_reason"]),
            )

        # Resolve the binary (file-existence only) so the dashboard can show the
        # real path and `ready` reflects what is actually present.
        server = resolve_llama_server(self.cfg, validate=False)
        models = self.list_models()
        return StatusData(
            backends=backends,
            active=read_active_backend(self.root),
            junction_target=read_junction_target(self.root),
            server=ServerStatusData(
                host=self.cfg.host,
                port=self.cfg.port,
                listening=check_port(self.cfg.host, self.cfg.port),
                pids=running_pids(self.root),
                model=models.active,
            ),
            models=models,
            resolved={"llama_server": _to_resolved(server)},
            platform=_platform_data(),
            root=str(self.root),
            config_file=str(config_file()),
            ready=bool(server.path),
            first_run_complete=self.cfg.first_run_complete,
        )

    def resolve(self) -> ResolveData:
        """Authoritative resolution: runs the binary to confirm it works."""
        return ResolveData(
            llama_server=_to_resolved(resolve_llama_server(self.cfg, validate=True))
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
        merged.update(data)

        token = data.get("token")
        updated = AppConfig.from_dict(merged)
        updated.token = token if isinstance(token, str) else self.cfg.token
        updated.save()
        self.cfg = updated
        return self.config()

    # ─── Obtain ──────────────────────────────────────────────────────────

    def install(
        self,
        backends: list[str] | None = None,
        force: bool = False,
    ) -> InstallData:
        with mutation_lock(self.root):
            return self._do_install(backends, force)

    def update(
        self,
        backends: list[str] | None = None,
        force: bool = True,
    ) -> InstallData:
        with mutation_lock(self.root):
            return self._do_install(backends, force)

    def bootstrap(
        self, backend: str | None = None, force: bool = False
    ) -> BootstrapData:
        """Make the app usable out of the box (requirement 2).

        Downloads the latest llama.cpp backend into the backend location only
        when it is not already available — anything the resolver already finds
        (the OS install when the toggle is on, or a previous download) is left
        alone.
        """
        with mutation_lock(self.root):
            target = backend or self._preferred_backend()
            self._require_known_backend(target)

            already = bool(resolve_llama_server(self.cfg, validate=False).path)
            performed: list[str] = []
            skipped: list[str] = []
            cpp_version: str | None = None

            if already and not force:
                skipped.append("llama.cpp (already available)")
                cpp_version = _marker_version(self.root, target)
            else:
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

            self.cfg.first_run_complete = True
            self.cfg.save()

            ready = bool(resolve_llama_server(self.cfg, validate=False).path)
            return BootstrapData(
                performed=performed,
                skipped=skipped,
                backend=target,
                llama_cpp_version=cpp_version,
                ready=ready,
                message="Ready to launch."
                if ready
                else "llama-server is still missing.",
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
        resolved = resolve_llama_server(self.cfg, validate=True)
        if not resolved.path or not resolved.valid:
            raise EngineError(
                ExitCode.NOT_AVAILABLE,
                "llama-server is not available: "
                + (
                    resolved.error
                    or "download a backend or enable the OS install toggle."
                ),
                self.log_tail(20),
            )
        model = self._resolve_model_path()
        # Reject a bad server_options value before spawning anything.
        errors = validate_options(self.cfg.server_options)
        if errors:
            first = next(iter(errors.values()))
            raise EngineError(
                ExitCode.BAD_ARGUMENT,
                f"Invalid server option: {first}",
                self.log_tail(20),
            )
        # A previously launched server still holding the port would make the new
        # one fail to bind, so stop our own instance first (never others').
        if check_port(self.cfg.host, self.cfg.port):
            stop_processes(self.root, host=self.cfg.host, port=self.cfg.port)
        cmd = self._server_args_for(resolved.path, str(model))
        return launch_llama_server(
            cmd,
            host=self.cfg.host,
            port=self.cfg.port,
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

    # ─── Models ──────────────────────────────────────────────────────────

    def _models_dir(self) -> Path:
        return self.cfg.models_dir_path

    def _resolve_model_path(self) -> Path:
        """Pick the model to launch: the active one, else the only one present."""
        models_dir = self._models_dir()
        if self.cfg.active_model:
            candidate = models_dir / self.cfg.active_model
            if candidate.is_file():
                return candidate
        models = list_models(models_dir)
        if len(models) == 1:
            return models_dir / models[0].name
        if not models:
            raise EngineError(
                ExitCode.NOT_AVAILABLE,
                f"No models in {models_dir}. Add one on the Models page "
                "(download a .gguf, or copy a file into that folder).",
            )
        names = ", ".join(m.name for m in models[:5])
        raise EngineError(
            ExitCode.NOT_AVAILABLE,
            f"{len(models)} models in {models_dir} — select one first "
            f"(Models page): {names}{'…' if len(models) > 5 else ''}",
        )

    def list_models(self) -> ModelsData:
        models_dir = self._models_dir()
        models = list_models(models_dir)
        active: str | None = self.cfg.active_model
        if active and not (models_dir / active).is_file():
            active = None
        return ModelsData(dir=str(models_dir), models=models, active=active)

    def download_model(self, url: str) -> DownloadData:
        with mutation_lock(self.root):
            try:
                return download_model(url, self._models_dir())
            except (ModelDownloadError, OSError) as e:
                raise EngineError(
                    ExitCode.NETWORK_ERROR, f"Model download failed: {e}"
                ) from e

    def set_active_model(self, name: str) -> ModelsData:
        """Mark a model as the one the server launches (persisted in config)."""
        if not (self._models_dir() / name).is_file():
            raise EngineError(ExitCode.NOT_AVAILABLE, f"No such model: {name}")
        self.save_config({"active_model": name})
        return self.list_models()

    def remove_model(self, name: str) -> ModelsData:
        with mutation_lock(self.root):
            try:
                remove_model(self._models_dir(), name)
            except FileNotFoundError as e:
                raise EngineError(ExitCode.NOT_AVAILABLE, str(e)) from e
            except ModelDownloadError as e:
                raise EngineError(ExitCode.BAD_ARGUMENT, str(e)) from e
            if self.cfg.active_model == name:
                self.save_config({"active_model": ""})
        return self.list_models()

    # ─── Server arguments ────────────────────────────────────────────────

    def describe_server_args(self, flag: str | None = None) -> dict[str, Any]:
        """The full options catalogue with the currently configured values."""
        rows: list[dict[str, Any]] = []
        for arg in SERVER_ARGS:
            if flag and arg.flag != flag and flag not in arg.aliases:
                continue
            value = (
                self._dedicated_value(arg.flag)
                if arg.flag in DEDICATED_FLAGS
                else self.cfg.server_options.get(arg.flag, "")
            )
            rows.append(
                {
                    "flag": arg.flag,
                    "aliases": list(arg.aliases),
                    "section": arg.section,
                    "kind": arg.kind.value,
                    "choices": list(arg.choices),
                    "default": arg.default,
                    "negated": arg.negated,
                    "env": arg.env,
                    "volatile": arg.volatile,
                    "app_managed": arg.app_managed,
                    "is_dir": arg.is_dir,
                    "deprecated": arg.deprecated,
                    "help": arg.help,
                    "value": value,
                }
            )
        return {"args": rows, "count": len(rows)}

    def set_server_arg(self, flag: str, value: str) -> dict[str, Any]:
        """Set one option ('' or 'default' resets it). Returns the option row."""
        arg = find_arg(flag)
        if arg is None:
            raise EngineError(
                ExitCode.BAD_ARGUMENT,
                f"Unknown option '{flag}'. List them with 'server-args'.",
            )
        if arg.volatile:
            raise EngineError(
                ExitCode.BAD_ARGUMENT,
                f"{arg.flag} is a one-shot flag ({arg.help}); "
                "it cannot be passed to a running server.",
            )
        canon = arg.flag
        if canon in DEDICATED_FLAGS:
            self.save_config(self._dedicated_values_for_set(canon, value))
        else:
            normalized = validate_value(arg, value)
            options = dict(self.cfg.server_options)
            if normalized:
                options[canon] = normalized
            else:
                options.pop(canon, None)
            self.save_config({"server_options": options})
        return self.describe_server_args(canon)

    def clear_server_args(self) -> dict[str, Any]:
        """Reset every catalogue option to its default ('' / omitted)."""
        self.save_config({"server_options": {}})
        return self.describe_server_args()

    def _server_args_for(self, exe_path: str, model_path: str) -> list[str]:
        """Build the llama-server command line from the current config."""
        return build_llama_server_args(
            exe_path=exe_path,
            model_path=model_path,
            host=self.cfg.host,
            port=self.cfg.port,
            ctx_size=self.cfg.ctx_size,
            n_gpu_layers=self.cfg.n_gpu_layers,
            extra_args=self.cfg.extra_server_args,
            server_options=self.cfg.server_options,
        )

    def preview_command(self) -> list[str]:
        """The exact command line ``launch`` would run (best-effort model path)."""
        resolved = resolve_llama_server(self.cfg, validate=False)
        exe = resolved.path or "llama-server"
        try:
            model = str(self._resolve_model_path())
        except EngineError:
            model = "<model>"
        return self._server_args_for(exe, model)

    def _dedicated_value(self, flag: str) -> str:
        """The current string value of a dedicated (non-catalogue) flag."""
        if flag == "--host":
            return self.cfg.host
        if flag == "--port":
            return str(self.cfg.port)
        if flag == "--ctx-size":
            return str(self.cfg.ctx_size) if self.cfg.ctx_size > 0 else ""
        if flag == "--n-gpu-layers":
            return str(self.cfg.n_gpu_layers)
        return ""

    def _dedicated_values_for_set(self, flag: str, value: str) -> dict[str, Any]:
        """Map a dedicated flag assignment onto the AppConfig fields."""
        value = value.strip()
        if flag == "--host":
            if not value:
                return {"host": "127.0.0.1"}
            return {"host": value}
        if flag == "--port":
            if not value:
                return {"port": 8080}
            try:
                return {"port": int(value)}
            except ValueError as exc:
                raise EngineError(
                    ExitCode.BAD_ARGUMENT, f"--port expects an integer, got '{value}'"
                ) from exc
        if flag == "--ctx-size":
            if not value or value in ("auto", "default"):
                return {"ctx_size": -1}
            try:
                return {"ctx_size": int(value)}
            except ValueError as exc:
                raise EngineError(
                    ExitCode.BAD_ARGUMENT,
                    f"--ctx-size expects an integer or 'auto', got '{value}'",
                ) from exc
        if flag == "--n-gpu-layers":
            if not value or value in ("auto", "all", "default"):
                return {"n_gpu_layers": -1}
            try:
                return {"n_gpu_layers": int(value)}
            except ValueError as exc:
                raise EngineError(
                    ExitCode.BAD_ARGUMENT,
                    f"--n-gpu-layers expects an integer, 'auto' or 'all', got '{value}'",
                ) from exc
        raise EngineError(ExitCode.BAD_ARGUMENT, f"Unknown dedicated flag '{flag}'")

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

    def _do_install(self, backends: list[str] | None, force: bool) -> InstallData:
        names = backends or [self._preferred_backend()]
        results: list[InstallResultItem] = []
        for name in names:
            self._require_known_backend(name)
            results.append(self._obtain_backend(name, force=force))

        return InstallData(
            release=next((r.version for r in results if r.version), None),
            results=results,
            summary={
                "updated": sum(1 for r in results if r.status == "ok"),
                "skipped": sum(1 for r in results if r.status == "skipped"),
                "failed": sum(1 for r in results if r.status == "failed"),
            },
        )

    def _obtain_backend(self, backend: str, force: bool) -> InstallResultItem:
        """Download the official prebuilt release for one backend."""
        availability = backend_availability(backend)
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

    def _activate(self, backend: str) -> None:
        """Point ``managed/current`` at a backend and record it in state."""
        target = self.managed_root / backend
        if not target.is_dir():
            return
        _link_current(self.managed_root / "current", target)
        state_file = self.cfg.state_dir / "active.txt"
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(backend + "\n", encoding="utf-8")

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
