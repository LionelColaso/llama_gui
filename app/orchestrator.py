"""The engine: resolve, obtain, launch and stop llama-server; manage models.

Everything the GUI and the CLI can do goes through this one typed API so both
front-ends behave identically. Mutations take the single-writer lock; reads
never spawn a subprocess.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable, Generator, Mapping
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any

from .backends.catalogue import (
    backend_availability,
    backend_table,
    get_backend,
    platform_backend_names,
    platform_default_backend,
)
from .backends.prebuilt import (
    clear_release_cache,
    emit_progress,
    install_backend,
    list_assets,
)
from .config import AppConfig
from .download import (
    discard_pending as _discard_pending,
)
from .download import (
    pending_downloads as _scan_pending_downloads,
)
from .lifecycle import (
    GLOBAL_ONLY_DEDICATED,
    build_llama_server_args,
    dedicated_value_text,
    launch_llama_server,
    launch_settings,
    model_server_options,
    read_log_tail,
    running_pids,
    stop_processes,
    uses_global_server_config,
)
from .links import link_current, remove_link
from .locking import mutation_lock
from .model_store import (
    ModelDownloadError,
    clear_model_cache,
    download_model,
    list_models,
    list_models_cached,
    remove_model,
)
from .paths import arch_key, config_file, exe_suffix, platform_key
from .relocate import Transfer, contains, is_empty_dir, scan
from .relocate import transfer as transfer_tree
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
    PendingDownloadInfo,
    PendingDownloadsData,
    PlatformData,
    RelocationData,
    RelocationItem,
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
    ServerArg,
    find_arg,
    validate_options,
    validate_value,
)
from .state import (
    check_port,
    read_active_backend,
    read_component_version,
    read_junction_target,
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
    "pending-downloads",
    "discard-download",
    "config",
    "server-args",
    "set-arg",
    "clear-args",
)

#: Human-readable names for the path settings that can be reset to their
#: default, used in the "Use default" confirmation message.
_PATH_LABELS = {"root": "Managed root", "models_dir": "Models directory"}


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

    def first_run_needed(self) -> bool:
        """Whether the first-run setup dialog should be shown.

        Unlike :meth:`status`, this is allowed to *run* the binary: it is
        called once at startup rather than on every dashboard poll, so the
        ``--version`` subprocess is affordable here and nowhere else.

        ``status.ready`` only means "a file exists at the expected path", so
        keying the dialog off it would suppress setup for a binary that
        cannot actually run -- wrong architecture, missing CUDA runtime, or
        still quarantined on macOS -- leaving the user in a dead UI with no
        offered way out. Verifying first means the dialog appears precisely
        when the app is genuinely unusable.
        """
        if self.cfg.first_run_complete:
            return False
        try:
            resolved = resolve_llama_server(self.cfg, validate=True)
        except Exception:  # noqa: BLE001 - a probe failure must not block startup
            return True
        return not (resolved.path and resolved.valid)

    def resolve(self) -> ResolveData:
        """Authoritative resolution: runs the binary to confirm it works."""
        return ResolveData(
            llama_server=_to_resolved(resolve_llama_server(self.cfg, validate=True))
        )

    def log_tail(self, lines: int = 200) -> list[str]:
        return read_log_tail(self.root, lines=lines)

    # ─── Settings ────────────────────────────────────────────────────────

    def config(self) -> ConfigData:
        values = self.cfg.to_dict()
        # ``to_dict`` omits ``root``/``models_dir`` when they are the platform
        # default (so the file stores no override), but a *read* of the settings
        # must still say which paths are in effect.
        values["root"] = self.cfg.root
        values["models_dir"] = str(self.cfg.models_dir_path)
        return ConfigData(
            config_file=str(config_file()),
            values=values,
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

    def reset_root(self) -> ConfigData:
        """Drop the saved root override so the app follows the platform default.

        See :meth:`_reset_path_setting`; the root's default comes from
        :func:`default_root`, which moves when a legacy ``~/.llamagui`` appears
        or disappears.

        Not a CLI action: it exists for the Settings page's "Use default" action
        and is deliberately not part of the ``ACTIONS`` contract.
        """
        return self._reset_path_setting("root")

    def reset_models_dir(self) -> ConfigData:
        """Drop the saved models directory override (back to ``<root>/models``).

        Not a CLI action, for the same reason as :meth:`reset_root`.
        """
        return self._reset_path_setting("models_dir")

    def _reset_path_setting(self, key: str) -> ConfigData:
        """Clear one path override and persist it as an absent key.

        The key is *removed* from the settings file rather than rewritten with
        today's value, so the choice keeps following the default (see
        :meth:`AppConfig.to_dict`). Nothing is deleted from disk: an existing
        model library or backend tree stays where it is until the user points
        the setting back at it.
        """
        previous = str(getattr(self.cfg, key))
        data = self.cfg.to_dict()
        data.pop(key, None)
        updated = AppConfig.from_dict(data)
        updated.token = self.cfg.token
        updated.save()
        self.cfg = updated
        result = self.config()
        current = str(getattr(self.cfg, key))
        result.warnings.append(
            f"{_PATH_LABELS[key]} reset to {current} (was {previous}). "
            "Restart the app for every view to pick it up."
        )
        return result

    # ─── Relocation ───────────────────────────────────────────────────────

    def plan_relocation(
        self, *, root: str | None = None, models_dir: str | None = None
    ) -> RelocationData:
        """What changing the managed root and/or models directory would move.

        Both trees are planned independently, so changing only one of them (or
        changing both at once, which is what moving the root usually implies for
        a default models directory) is handled without special cases. Nothing is
        written or touched — this only reads the two trees.

        ``root``/``models_dir`` are the raw values from the settings form, where
        an empty string means "follow the default".
        """
        plan, _ = self._relocation(root, models_dir)
        if plan.models is not None and not plan.models.blocked:
            old_models = self.cfg.models_dir_path
            parts, _ = scan(old_models, suffixes=(".part",))
            if parts:
                plan.notes.append(
                    f"{parts} interrupted model download(s) stay behind in "
                    f"{old_models}."
                )
        return plan

    def relocate_data(
        self,
        *,
        root: str | None = None,
        models_dir: str | None = None,
        transfer: Transfer = "move",
        move_backends: bool = False,
        move_models: bool = False,
    ) -> RelocationData:
        """Move or copy existing backends and/or models to a newly chosen location.

        Each tree is transferred only when the caller asked for it, so the user
        can relocate the backends and leave the model library alone (or vice
        versa), and either both at once. ``transfer`` picks move (the old copy is
        dropped) or copy (it is kept as a backup); a blocked tree is skipped
        rather than forced.

        The settings file is **not** written here: the caller persists the new
        paths once the transfer succeeded, so a failure leaves the config pointing
        at the data that is still there.

        Like :meth:`reset_root`, this is a GUI-only action and not part of the
        ``ACTIONS`` contract.
        """
        plan, pending = self._relocation(root, models_dir)
        with _relocation_locks(self.root, pending.root_path):
            if move_backends and plan.backends and not plan.backends.blocked:
                self._transfer_backends(
                    self.managed_root, pending.managed_dir, transfer
                )
                plan.backends.transfer = transfer
            if move_models and plan.models and not plan.models.blocked:
                transfer_tree(
                    self.cfg.models_dir_path,
                    pending.models_dir_path,
                    suffixes=(".gguf",),
                    emit=_progress_relocator("models"),
                    remove_source=transfer == "move",
                )
                plan.models.transfer = transfer
        return plan

    def _relocation(
        self, root: str | None, models_dir: str | None
    ) -> tuple[RelocationData, AppConfig]:
        """Plan a pending path change together with the config it would produce.

        Each tree is decided on its own, so a change to one location never
        disturbs the other and both can move in a single step.
        """
        pending = self._pending_config(root, models_dir)
        plan = RelocationData()
        if not _same_location(self.root, pending.root_path):
            plan.backends = self._plan_item(
                "Backends", self.managed_root, pending.managed_dir
            )
        if not _same_location(self.cfg.models_dir_path, pending.models_dir_path):
            plan.models = self._plan_item(
                "Models",
                self.cfg.models_dir_path,
                pending.models_dir_path,
                suffixes=(".gguf",),
            )
        return plan, pending

    def _transfer_backends(
        self, source: Path, destination: Path, transfer: Transfer
    ) -> None:
        """Relocate the backend tree and re-point ``managed/current`` at it.

        ``managed/current`` cannot simply travel with the tree: it is a link with
        an absolute target, so it is dropped before a move (a copy leaves the old
        location completely untouched) and re-created against the new one in both
        cases. The active-backend marker (``state/active.txt``) travels so the
        user's selection survives; the pid file and the logs stay behind, because
        they describe the old location and a server that may still be running.
        """
        old_root = source.parent
        new_root = destination.parent
        name = read_active_backend(old_root) or _linked_backend(source)
        if transfer == "move":
            remove_link(source / "current")
        transfer_tree(
            source,
            destination,
            emit=_progress_relocator("backends"),
            remove_source=transfer == "move",
        )
        if not name or not (destination / name).is_dir():
            return
        link_current(destination / "current", destination / name)
        marker = old_root / "state" / "active.txt"
        target = new_root / "state" / "active.txt"
        if marker.is_file() and not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(marker, target)

    def _plan_item(
        self,
        label: str,
        source: Path,
        destination: Path,
        *,
        suffixes: tuple[str, ...] | None = None,
    ) -> RelocationItem | None:
        """Describe one candidate move, or ``None`` when there is nothing to move."""
        files, total = scan(source, suffixes=suffixes)
        if files == 0:
            return None
        return RelocationItem(
            label=label,
            source=str(source),
            destination=str(destination),
            files=files,
            total_bytes=total,
            blocked=_blocked_reason(source, destination),
        )

    def _pending_config(self, root: str | None, models_dir: str | None) -> AppConfig:
        """The config as it would be *after* the pending path change.

        Resolved by the same code that loads the real settings, so the preview
        and the move can never disagree about where things end up.
        """
        data = self.cfg.to_dict()
        data["root"] = root or ""
        data["models_dir"] = models_dir or ""
        return AppConfig.from_dict(data)

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
        force: bool = False,
    ) -> InstallData:
        # An update is explicitly a request for the *newest* release, so any
        # cached metadata is dropped first: a long-running GUI (auto_update on a
        # timer) would otherwise keep re-resolving whatever was fetched at
        # startup and never notice a newly published release.
        clear_release_cache()
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
        """Start llama-server, holding the mutation lock (invariant #15).

        Launch mutates state: it can stop a previous instance, writes
        ``state/pids.json`` and spawns a process. Running it unlocked lets a
        concurrent ``install`` run ``wipe_and_extract`` underneath the binary
        that is being spawned.
        """
        with mutation_lock(self.root):
            return self._launch_locked(verify=verify)

    def _launch_locked(self, verify: bool) -> int | None:
        """Launch while the mutation lock is already held.

        Split out from :meth:`launch` because the POSIX lock file is created
        ``O_EXCL`` and is therefore *not* reentrant: ``restart`` must take the
        lock exactly once around both its stop and its launch.
        """
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
            self._stop_locked()
        cmd = self._server_args_for(resolved.path, str(model))
        return launch_llama_server(
            cmd,
            host=self.cfg.host,
            port=self.cfg.port,
            root=self.root,
            verify=verify,
        )

    def stop(self, grace: float | None = None) -> StopData:
        """Stop the server. ``grace`` caps how long each process may take to exit.

        The default is the engine's normal grace period. GUI shutdown passes a
        short one so closing the window cannot stall the event loop.
        """
        with mutation_lock(self.root):
            return self._stop_locked(grace=grace)

    def _stop_locked(self, grace: float | None = None) -> StopData:
        """Stop while the mutation lock is already held."""
        kwargs: dict[str, Any] = {}
        if grace is not None:
            kwargs["grace"] = grace
        result = stop_processes(
            self.root, host=self.cfg.host, port=self.cfg.port, **kwargs
        )
        return StopData(**result)

    def restart(self, verify: bool = False) -> int | None:
        """Stop then launch, taking the mutation lock exactly once.

        Taking it once (rather than delegating to the individually-locking
        ``stop`` and ``launch``) keeps the two steps atomic with respect to other
        mutations and avoids re-entering the non-reentrant POSIX lock file.
        """
        with mutation_lock(self.root):
            self._stop_locked()
            return self._launch_locked(verify=verify)

    def list_assets(self) -> ListAssetsData:
        return ListAssetsData(**list_assets(token=self._github_token()))

    def pending_downloads(self) -> PendingDownloadsData:
        """Every interrupted download (models + backend cache) offered for resume.

        A single screen therefore covers both .gguf model downloads and
        half-downloaded backend release archives across app restarts.
        """
        return PendingDownloadsData(
            models_dir=str(self._models_dir()),
            downloads_dir=str(self.cfg.downloads_dir),
            tasks=[
                PendingDownloadInfo(**task)
                for task in _scan_pending_downloads(
                    [
                        ("model", self._models_dir()),
                        ("backend", self.cfg.downloads_dir),
                    ]
                )
            ],
        )

    def discard_download(self, dest: str) -> PendingDownloadsData:
        """Delete an interrupted download's ``.part`` and meta, then re-list.

        ``dest`` must resolve inside the models dir or the downloads dir —
        anything outside the app's write surface is rejected (invariant #0).
        """
        with mutation_lock(self.root):
            target = Path(dest).resolve()
            roots = (self._models_dir().resolve(), self.cfg.downloads_dir.resolve())
            if not any(_is_within(target, base) for base in roots):
                raise EngineError(
                    ExitCode.BAD_ARGUMENT,
                    f"Refusing to discard outside managed dirs: {dest}",
                )
            _discard_pending(target)
            return self.pending_downloads()

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
        """The model library, using the cached listing (see list_models_cached).

        The dashboard polls ``status()`` every few seconds and it calls this,
        so re-listing a large .gguf library on each poll hammered the disk.
        The cache is invalidated by fingerprint, so an added, removed or
        completed model still shows up immediately.
        """
        models_dir = self._models_dir()
        models = list_models_cached(models_dir)
        active: str | None = self.cfg.active_model
        if active and not (models_dir / active).is_file():
            active = None
        return ModelsData(dir=str(models_dir), models=models, active=active)

    def download_model(self, url: str) -> DownloadData:
        with mutation_lock(self.root):
            try:
                result = download_model(url, self._models_dir())
            except (ModelDownloadError, OSError) as e:
                raise EngineError(
                    ExitCode.NETWORK_ERROR, f"Model download failed: {e}"
                ) from e
            clear_model_cache()
            return result

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
            clear_model_cache()
            if self.cfg.active_model == name:
                self.save_config({"active_model": ""})
        return self.list_models()

    # ─── Server arguments ────────────────────────────────────────────────

    def describe_server_args(
        self, flag: str | None = None, model: str | None = None
    ) -> dict[str, Any]:
        """The full options catalogue with the values that apply.

        With no ``model`` this is the global configuration every model follows
        unless it has its own; with one it is that model's configuration, and
        ``mode`` says whether it follows the globals (``global``) or runs on
        its own values (``own``).
        """
        options = model_server_options(self.cfg, model)
        own = model is not None and not uses_global_server_config(self.cfg, model)
        rows: list[dict[str, Any]] = []
        for arg in SERVER_ARGS:
            if flag and arg.flag != flag and flag not in arg.aliases:
                continue
            value = (
                self._dedicated_value(arg.flag, model)
                if arg.flag in DEDICATED_FLAGS
                else str(options.get(arg.flag, ""))
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
                    # In a model scope, whether this value is the global default
                    # (the model follows it) or the model's own setting.
                    "inherited": not own,
                }
            )
        return {
            "args": rows,
            "count": len(rows),
            "scope": "model" if model else "global",
            "mode": "own" if own else "global",
            "model": model or "",
        }

    def set_server_arg(
        self, flag: str, value: str, model: str | None = None
    ) -> dict[str, Any]:
        """Set one option ('' or 'default' resets it). Returns the option row.

        With ``model`` the value lands in that model's own configuration, which
        is created (seeded from the globals, so setting one option is a minimal
        change rather than a wipe) the first time it is used.
        """
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
        if model:
            self._reject_global_only(canon)
            options = self._model_options_for_edit(model)
            try:
                normalized = validate_value(arg, value)
            except ValueError as e:
                raise EngineError(ExitCode.BAD_ARGUMENT, str(e)) from e
            if normalized:
                options[canon] = normalized
            else:
                options.pop(canon, None)
            self._write_model_server_options(model, options)
        elif canon in DEDICATED_FLAGS:
            self.save_config(self._dedicated_values_for_set(canon, value))
        else:
            self._set_global_server_arg(canon, arg, value)
        return self.describe_server_args(canon, model)

    def _set_global_server_arg(self, flag: str, arg: ServerArg, value: str) -> None:
        """Write one catalogue option into the global configuration."""
        try:
            normalized = validate_value(arg, value)
        except ValueError as e:
            # The CLI/GUI contract is EngineError with a documented exit
            # code; a bare ValueError would surface as UNEXPECTED_ERROR
            # and read like an engine failure rather than bad input.
            raise EngineError(ExitCode.BAD_ARGUMENT, str(e)) from e
        options = dict(self.cfg.server_options)
        if normalized:
            options[flag] = normalized
        else:
            options.pop(flag, None)
        self.save_config({"server_options": options})

    def _reject_global_only(self, flag: str) -> None:
        """Refuse a per-model ``--host``/``--port``: it would be dead config."""
        if flag in GLOBAL_ONLY_DEDICATED:
            raise EngineError(
                ExitCode.BAD_ARGUMENT,
                f"{flag} cannot be set per model: the app probes one "
                "host/port for health, status and stop. Set it globally.",
            )

    def _model_options_for_edit(self, model: str) -> dict[str, str]:
        """The model's own options, seeded from the globals the first time.

        Seeding (rather than starting empty) means setting a single option for a
        model changes exactly that option: the model keeps the behaviour it had
        as far as everything else goes.
        """
        entry = self.cfg.model_server_options.get(model)
        if entry is not None:
            return dict(entry)
        return dict(self.cfg.server_options)

    def _write_model_server_options(
        self, model: str, options: Mapping[str, str]
    ) -> None:
        """Persist one model's own options, leaving every other model alone."""
        merged = {
            name: dict(entry)
            for name, entry in self.cfg.model_server_options.items()
            if name != model
        }
        merged[model] = dict(options)
        self.save_config({"model_server_options": merged})

    def save_model_server_config(
        self, model: str, options: Mapping[str, str]
    ) -> dict[str, Any]:
        """Replace one model's own server configuration (the popup's *Save*).

        An empty ``options`` is a valid, meaningful state: the model asked for
        its own settings with everything at the default. Validated exactly like
        the global scope, so nothing bad reaches the launch path.
        """
        cleaned: dict[str, str] = {}
        for flag, value in options.items():
            arg = find_arg(flag)
            if arg is None:
                raise EngineError(
                    ExitCode.BAD_ARGUMENT,
                    f"Unknown option '{flag}'. List them with 'server-args'.",
                )
            self._reject_global_only(arg.flag)
            try:
                normalized = validate_value(arg, str(value))
            except ValueError as e:
                raise EngineError(ExitCode.BAD_ARGUMENT, str(e)) from e
            if normalized:
                cleaned[arg.flag] = normalized
        self._write_model_server_options(model, cleaned)
        return self.describe_server_args(None, model)

    def reset_model_server_config(self, model: str) -> dict[str, Any]:
        """Put one model back on the global configuration.

        The model keeps no own entry, so it follows the globals again — and it
        follows their *later* edits too, which is the whole point of the mode.
        """
        merged = {
            name: dict(entry)
            for name, entry in self.cfg.model_server_options.items()
            if name != model
        }
        self.save_config({"model_server_options": merged})
        return self.describe_server_args(None, model)

    def clear_server_args(
        self, model: str | None = None, use_global: bool = False
    ) -> dict[str, Any]:
        """Reset every catalogue option to its default ('' / omitted).

        With ``model`` this is the popup's *Reset server config*: the model gets
        its own configuration with everything at the default, deliberately *not*
        the globals — those may be edited later, and a reset must not silently
        start following them. Pass ``use_global=True`` to go back to following
        them instead.
        """
        if not model:
            self.save_config({"server_options": {}})
            return self.describe_server_args()
        if use_global:
            return self.reset_model_server_config(model)
        self._write_model_server_options(model, {})
        return self.describe_server_args(None, model)

    def _server_args_for(self, exe_path: str, model_path: str) -> list[str]:
        """Build the llama-server command line from the current config.

        The model name picks its configuration: the globals, or its own values
        when it has them.
        """
        settings = launch_settings(self.cfg, Path(model_path).name)
        return build_llama_server_args(
            exe_path=exe_path,
            model_path=model_path,
            host=settings.host,
            port=settings.port,
            ctx_size=settings.ctx_size,
            n_gpu_layers=settings.n_gpu_layers,
            extra_args=settings.extra_args,
            server_options=settings.options,
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

    def _dedicated_value(self, flag: str, model: str | None = None) -> str:
        """The current string value of a dedicated (non-catalogue) flag.

        ``host``/``port`` are always global; ``ctx-size``/``n-gpu-layers`` show
        the value that applies to that model: its own when it has a server
        configuration, the global one when it follows the globals.
        """
        if flag == "--host":
            return self.cfg.host
        if flag == "--port":
            return str(self.cfg.port)
        if flag == "--ctx-size":
            value = launch_settings(self.cfg, model).ctx_size
            return dedicated_value_text(flag, value)
        if flag == "--n-gpu-layers":
            value = launch_settings(self.cfg, model).n_gpu_layers
            return dedicated_value_text(flag, value)
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
        link_current(self.managed_root / "current", target)
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


def _same_location(left: Path, right: Path) -> bool:
    """True when two paths name the same place on this platform.

    Compared on the absolute normalised form first (which settles the Windows
    case-insensitivity and ``C:/x`` vs ``C:\\x``) and then on the resolved form,
    so two different spellings of one directory never look like a change.
    """

    def normalised(path: Path) -> str:
        return os.path.normcase(os.path.abspath(str(path)))

    if normalised(left) == normalised(right):
        return True
    try:
        return left.expanduser().resolve() == right.expanduser().resolve()
    except OSError:
        return False


def _blocked_reason(source: Path, destination: Path) -> str:
    """Why this move must not run, or ``""`` when it is safe to offer."""
    if contains(source, destination):
        return "the new location is inside the current one"
    if not is_empty_dir(destination):
        return "the new location already contains files"
    return ""


def _linked_backend(managed: Path) -> str | None:
    """The backend name ``managed/current`` points at, if it is set."""
    target = read_junction_target(managed.parent)
    return Path(target).name if target else None


def _progress_relocator(
    component: str,
) -> Callable[[int, int, str, float | None], None]:
    """Adapt the engine's progress channel to :func:`app.relocate.transfer`."""

    def emit(done: int, total: int, phase: str, overall: float | None) -> None:
        emit_progress(component, done, total, phase, overall)

    return emit


@contextmanager
def _relocation_locks(*roots: Path) -> Generator[None]:
    """Hold the mutation lock of every root a relocation touches.

    A relocation reads from one root and writes into another, so both are locked
    (invariant #15). Sorting the roots makes the acquisition order identical for
    every caller, which is what stops two concurrent relocations from
    deadlocking against each other.
    """
    with ExitStack() as stack:
        for root in sorted(set(roots), key=lambda path: str(path).lower()):
            stack.enter_context(mutation_lock(root))
        yield


def _is_within(child: Path, base: Path) -> bool:
    """True when ``child`` is ``base`` itself or a directory below it."""
    return child == base or base in child.parents


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


__all__ = ["ACTIONS", "Orchestrator"]
