"""Application settings: durable, platform-native, never silently lost.

Durability rules (requirement: "settings must not be lost"):

* The settings file lives in the OS config dir (see :mod:`llamagui.paths`), so
  changing the managed *root* in the UI can never orphan the settings.
* Writes are atomic (temp file + ``os.replace``) and fsynced, so a crash or a
  power cut can never leave a half-written file.
* Unknown keys written by a newer version are round-tripped untouched.
* A corrupt or unreadable file is **preserved** as ``config.corrupt-<ts>.json``
  (and reported through :attr:`AppConfig.load_warnings`) instead of being
  silently overwritten with defaults.
* A legacy ``~/.llamagui/config.json`` is migrated on first load.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from .paths import (
    config_file,
    default_root,
    legacy_config_file,
)

DEFAULT_PORT = 8080
DEFAULT_HOST = "127.0.0.1"

#: Default context size passed to ``llama-server -c``.
DEFAULT_CTX_SIZE = 4096
#: Default GPU layers passed to ``llama-server -ngl`` (999 = offload all).
DEFAULT_N_GPU_LAYERS = 999

#: When to ship the CUDA runtime DLLs alongside a CUDA backend.
CUDA_RUNTIME_MODES = ("auto", "always", "never")


def default_backend() -> str:
    """Default backend for the running platform (data-driven)."""
    from .models import platform_default_backend

    return platform_default_backend()


def _clean_str(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


@dataclass
class AppConfig:
    root: str = field(default_factory=lambda: str(default_root()))
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    default_backend: str = field(default_factory=default_backend)
    #: Directory holding the user's .gguf models. Empty = ``<root>/models``.
    models_dir: str = ""
    #: File name of the model the server launches (empty = none selected).
    active_model: str = ""
    #: ``-c`` context size passed to llama-server.
    ctx_size: int = DEFAULT_CTX_SIZE
    #: ``-ngl`` GPU layers passed to llama-server (999 = offload all).
    n_gpu_layers: int = DEFAULT_N_GPU_LAYERS
    #: Extra raw CLI args appended to the llama-server command line.
    extra_server_args: str = ""
    #: Values for the data-driven server-options catalogue: ``{flag: value}``
    #: (see :mod:`llamagui.serverargs`). A blank/absent value means "leave the
    #: flag out and let the binary use its default". Dedicated flags
    #: (``host``/``port``/``ctx_size``/``n_gpu_layers``) are never stored here.
    server_options: dict[str, str] = field(
        default_factory=lambda: cast("dict[str, str]", {})
    )
    #: When True, prefer a ``llama-server`` found on ``PATH`` (the OS
    #: install) over the downloaded backend in the backend location.
    use_os_llama_server: bool = False
    bundle_cuda_runtime: str = "auto"
    auto_update: bool = False
    auto_update_interval_hours: int = 24
    launch_on_start: bool = False
    start_minimized: bool = False
    first_run_complete: bool = False
    theme: str = "system"
    token: str = ""

    #: Keys read from disk that this version does not know about. They are
    #: written back verbatim so a downgrade/upgrade cycle never drops settings.
    extra: dict[str, Any] = field(
        default_factory=lambda: cast("dict[str, Any]", {}), repr=False
    )
    #: Non-fatal problems encountered while loading (surfaced by the GUI/CLI).
    load_warnings: list[str] = field(
        default_factory=lambda: cast("list[str]", []), repr=False
    )

    # ─── Load ────────────────────────────────────────────────────────────

    @staticmethod
    def config_path() -> Path:
        """Absolute path of the settings file for this platform."""
        return config_file()

    @classmethod
    def load(cls, path: Path | None = None) -> AppConfig:
        """Read settings, tolerating a missing, legacy or corrupt file."""
        target = path or cls.config_path()
        warnings: list[str] = []

        source = target
        if not target.exists():
            legacy = legacy_config_file()
            if path is None and legacy.exists() and legacy != target:
                source = legacy
                warnings.append(f"Migrated settings from {legacy}")
            else:
                return cls(load_warnings=warnings)

        raw = _read_json(source, warnings)
        if raw is None:
            cfg = cls(load_warnings=warnings)
            return cfg

        cfg = cls.from_dict(raw)
        cfg.load_warnings = warnings
        if source != target:
            # Persist the migrated copy at the new location, keeping the old
            # file in place as a backup.
            with suppress(OSError):
                cfg.save(target)
        return cfg

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AppConfig:
        """Build a config from a settings dict, coercing invalid values."""
        known = {
            "root",
            "host",
            "port",
            "listen_flag",  # legacy (llama-swap era); read but ignored
            "default_backend",
            "models_dir",
            "active_model",
            "ctx_size",
            "n_gpu_layers",
            "extra_server_args",
            "server_options",
            "bundle_cuda_runtime",
            "auto_update",
            "auto_update_interval_hours",
            "launch_on_start",
            "start_minimized",
            "first_run_complete",
            "theme",
            "token",
        }
        # Legacy keys (``pointed``, ``source_priority``) are no longer read;
        # they fall into ``extra`` below and are written back verbatim, so an
        # upgrade never deletes what the user had on disk.
        #
        # The GitHub token is never persisted in plaintext; it lives in the OS
        # keyring (gui/token.py). A legacy plaintext token is ignored on load.
        return cls(
            root=_clean_str(data.get("root")) or str(default_root()),
            host=_clean_str(data.get("host")) or DEFAULT_HOST,
            port=_coerce_int(data.get("port"), DEFAULT_PORT),
            default_backend=_clean_str(data.get("default_backend"))
            or default_backend(),
            use_os_llama_server=bool(data.get("use_os_llama_server", False)),
            models_dir=_clean_str(data.get("models_dir")) or "",
            active_model=_clean_str(data.get("active_model")) or "",
            ctx_size=_coerce_int(data.get("ctx_size"), DEFAULT_CTX_SIZE),
            n_gpu_layers=_coerce_int(data.get("n_gpu_layers"), DEFAULT_N_GPU_LAYERS),
            extra_server_args=_clean_str(data.get("extra_server_args")) or "",
            server_options=_clean_server_options(data.get("server_options")),
            bundle_cuda_runtime=_coerce_choice(
                data.get("bundle_cuda_runtime"), CUDA_RUNTIME_MODES, "auto"
            ),
            auto_update=bool(data.get("auto_update", False)),
            auto_update_interval_hours=_coerce_int(
                data.get("auto_update_interval_hours"), 24
            ),
            launch_on_start=bool(data.get("launch_on_start", False)),
            start_minimized=bool(data.get("start_minimized", False)),
            first_run_complete=bool(data.get("first_run_complete", False)),
            theme=_clean_str(data.get("theme")) or "system",
            token="",
            extra={k: v for k, v in data.items() if k not in known},
        )

    # ─── Save ────────────────────────────────────────────────────────────

    def save(self, path: Path | None = None) -> None:
        """Atomically persist the settings (never partially written)."""
        target = path or self.config_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self.to_dict(), indent=2, sort_keys=False)

        fd, tmp_name = tempfile.mkstemp(
            prefix=".config-", suffix=".json", dir=str(target.parent)
        )
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload + "\n")
                handle.flush()
                with suppress(OSError, AttributeError):
                    os.fsync(handle.fileno())
            tmp.replace(target)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        _fsync_dir(target.parent)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = dict(self.extra)
        data.update(
            {
                "root": self.root,
                "host": self.host,
                "port": self.port,
                "default_backend": self.default_backend,
                "use_os_llama_server": self.use_os_llama_server,
                "models_dir": self.models_dir,
                "active_model": self.active_model,
                "ctx_size": self.ctx_size,
                "n_gpu_layers": self.n_gpu_layers,
                "extra_server_args": self.extra_server_args,
                "server_options": dict(self.server_options),
                "bundle_cuda_runtime": self.bundle_cuda_runtime,
                "auto_update": self.auto_update,
                "auto_update_interval_hours": self.auto_update_interval_hours,
                "launch_on_start": self.launch_on_start,
                "start_minimized": self.start_minimized,
                "first_run_complete": self.first_run_complete,
                "theme": self.theme,
                # Never persist the token to disk (keyring only).
                "token": "",
            }
        )
        return data

    # ─── Derived paths ───────────────────────────────────────────────────

    @property
    def root_path(self) -> Path:
        return Path(self.root).expanduser()

    @property
    def managed_dir(self) -> Path:
        return self.root_path / "managed"

    @property
    def downloads_dir(self) -> Path:
        return self.root_path / "downloads"

    @property
    def state_dir(self) -> Path:
        return self.root_path / "state"

    @property
    def models_dir_path(self) -> Path:
        """Directory holding .gguf models (user-configurable, default under root)."""
        if self.models_dir:
            return Path(self.models_dir).expanduser()
        return self.root_path / "models"


def _coerce_int(value: Any, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _coerce_choice(value: Any, allowed: tuple[str, ...], fallback: str) -> str:
    return value if isinstance(value, str) and value in allowed else fallback


def _clean_server_options(value: Any) -> dict[str, str]:
    """Keep only well-formed ``{flag: value}`` string pairs from a loaded file."""
    if not isinstance(value, dict):
        return {}
    raw = cast("dict[str, Any]", value)
    cleaned: dict[str, str] = {}
    for key, item in raw.items():
        if key.startswith("-") and isinstance(item, str):
            cleaned[key] = item
    return cleaned


def _read_json(path: Path, warnings: list[str]) -> dict[str, Any] | None:
    """Parse ``path``; on corruption keep a copy and report it."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        warnings.append(f"Could not read {path}: {exc}")
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        backup = _preserve_corrupt(path)
        warnings.append(
            f"Settings file {path} is corrupt ({exc.msg}); "
            f"kept a copy at {backup} and started from defaults."
        )
        return None
    if not isinstance(data, dict):
        backup = _preserve_corrupt(path)
        warnings.append(
            f"Settings file {path} did not contain an object; "
            f"kept a copy at {backup} and started from defaults."
        )
        return None
    return cast("dict[str, Any]", data)


def _preserve_corrupt(path: Path) -> Path:
    """Move a corrupt settings file aside so its contents are never lost."""
    backup = path.with_name(f"{path.stem}.corrupt-{int(time.time())}{path.suffix}")
    try:
        path.replace(backup)
    except OSError:
        return path
    return backup


def _fsync_dir(directory: Path) -> None:
    """Flush the directory entry so the rename survives a crash (POSIX)."""
    if not hasattr(os, "O_DIRECTORY"):
        return
    try:
        fd = os.open(str(directory), os.O_DIRECTORY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


__all__ = [
    "CUDA_RUNTIME_MODES",
    "DEFAULT_CTX_SIZE",
    "DEFAULT_HOST",
    "DEFAULT_N_GPU_LAYERS",
    "DEFAULT_PORT",
    "AppConfig",
    "config_file",
    "default_backend",
]
