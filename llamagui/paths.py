"""Platform-native locations and small OS helpers.

Every OS-specific path decision lives here so the rest of the engine stays
platform-agnostic (Windows, Linux, macOS are all first-class targets).

Layout per OS:

===========  ==================================  ==========================================
             config file                          data root (managed binaries, state, logs)
===========  ==================================  ==========================================
Windows      ``%APPDATA%\\llamagui``               ``%LOCALAPPDATA%\\llamagui``
Linux        ``$XDG_CONFIG_HOME/llamagui``        ``$XDG_DATA_HOME/llamagui``
macOS        ``~/Library/Preferences/llamagui``   ``~/Library/Application Support/llamagui``
===========  ==================================  ==========================================

The legacy location (``~/.llamagui``) is still honoured when it already exists
so that an existing install never loses its settings or managed binaries
(config durability requirement).
"""

from __future__ import annotations

import os
import platform
import sys
from pathlib import Path

APP_NAME = "llamagui"
CONFIG_FILENAME = "config.json"

#: Legacy single-directory layout used before platform-native paths existed.
LEGACY_ROOT = Path.home() / ".llamagui"


# ─── OS predicates ────────────────────────────────────────────────────────


def platform_key() -> str:
    """Return ``"win32"``, ``"darwin"`` or ``"linux"`` for the running OS.

    Anything that is not Windows or macOS (BSDs included) is treated as Linux,
    because those platforms consume the same release assets and POSIX
    primitives.
    """
    system = platform.system().lower()
    if system == "windows":
        return "win32"
    if system == "darwin":
        return "darwin"
    return "linux"


def is_windows() -> bool:
    return platform_key() == "win32"


def is_macos() -> bool:
    return platform_key() == "darwin"


def is_linux() -> bool:
    return platform_key() == "linux"


def arch_key() -> str:
    """Return ``"arm64"`` or ``"x64"`` for the running CPU architecture."""
    machine = platform.machine().lower()
    if machine in ("arm64", "aarch64"):
        return "arm64"
    return "x64"


def exe_suffix() -> str:
    """Return the executable suffix for the running OS (``.exe`` on Windows)."""
    return ".exe" if is_windows() else ""


def exe_name(stem: str) -> str:
    """Return ``stem`` with the platform executable suffix applied."""
    return f"{stem}{exe_suffix()}"


# ─── Base directories ─────────────────────────────────────────────────────


def _env_dir(var: str) -> Path | None:
    """Return ``$var`` as an absolute Path, or None when unset/relative."""
    raw = os.environ.get(var, "").strip()
    if not raw:
        return None
    candidate = Path(raw)
    return candidate if candidate.is_absolute() else None


def app_config_dir() -> Path:
    """Directory holding the app's own settings file."""
    if is_windows():
        base = _env_dir("APPDATA") or Path.home() / "AppData" / "Roaming"
        return base / APP_NAME
    if is_macos():
        return Path.home() / "Library" / "Preferences" / APP_NAME
    base = _env_dir("XDG_CONFIG_HOME") or Path.home() / ".config"
    return base / APP_NAME


def app_data_dir() -> Path:
    """Directory holding managed binaries, downloads, state and logs."""
    if is_windows():
        base = _env_dir("LOCALAPPDATA") or Path.home() / "AppData" / "Local"
        return base / APP_NAME
    if is_macos():
        return Path.home() / "Library" / "Application Support" / APP_NAME
    base = _env_dir("XDG_DATA_HOME") or Path.home() / ".local" / "share"
    return base / APP_NAME


def default_root() -> Path:
    """Default managed root.

    An existing legacy ``~/.llamagui`` install wins so that upgrading the app
    never orphans already-downloaded backends.
    """
    if LEGACY_ROOT.exists():
        return LEGACY_ROOT
    return app_data_dir()


def config_file() -> Path:
    """Absolute path of the settings file.

    The settings file deliberately does **not** live under the managed root:
    changing the root in Settings must never make the saved settings
    unreachable.
    """
    override = _env_dir("LLAMAGUI_CONFIG_DIR")
    if override is not None:
        return override / CONFIG_FILENAME
    return app_config_dir() / CONFIG_FILENAME


def legacy_config_file() -> Path:
    """Path of the pre-platform-paths settings file (may not exist)."""
    return LEGACY_ROOT / CONFIG_FILENAME


# ─── Filesystem helpers ───────────────────────────────────────────────────


def make_executable(path: Path) -> None:
    """Grant owner/group/other execute permission on POSIX (no-op on Windows).

    Release archives are frequently unpacked without their mode bits; without
    this a downloaded ``llama-server`` cannot be launched on Linux/macOS.
    """
    if is_windows():
        return
    try:
        mode = path.stat().st_mode
        path.chmod(mode | 0o111)
    except OSError:
        pass


def is_executable(path: Path) -> bool:
    """Return True when ``path`` is a file the current user may execute."""
    if not path.is_file():
        return False
    if is_windows():
        return True
    return os.access(path, os.X_OK)


def clear_quarantine(target: Path) -> None:
    """Strip the macOS ``com.apple.quarantine`` xattr from a downloaded tree.

    Gatekeeper refuses to run downloaded binaries that still carry the
    quarantine attribute. This is a best-effort call: failures are ignored
    because the user can also approve the binary manually.

    Walks the entire tree and clears the attribute on every file, because
    ``xattr -dr`` on the root alone does not always remove the attribute
    from nested files and symlinks.
    """
    if not is_macos() or not target.exists():
        return
    import subprocess

    try:
        for path in target.rglob("*"):
            if not path.is_file():
                continue
            subprocess.run(
                ["xattr", "-d", "com.apple.quarantine", str(path)],
                capture_output=True,
                timeout=30,
                check=False,
            )
    except (OSError, subprocess.SubprocessError):
        pass


def supports_symlinks() -> bool:
    """Return True when the OS can create symlinks without extra privileges."""
    return sys.platform != "win32"


__all__ = [
    "APP_NAME",
    "CONFIG_FILENAME",
    "LEGACY_ROOT",
    "app_config_dir",
    "app_data_dir",
    "arch_key",
    "clear_quarantine",
    "config_file",
    "default_root",
    "exe_name",
    "exe_suffix",
    "is_executable",
    "is_linux",
    "is_macos",
    "is_windows",
    "legacy_config_file",
    "make_executable",
    "platform_key",
    "supports_symlinks",
]
