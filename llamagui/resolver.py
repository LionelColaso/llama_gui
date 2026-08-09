"""Binary resolver: decide which ``llama-server`` / ``llama-swap`` to run.

Four sources, in the user's configured priority order (default
``pointed → managed → system``):

``pointed``
    Paths the user set explicitly — a folder holding both binaries, or a
    separate path per binary. Works whether or not anything is installed.
``managed``
    The app's own ``<root>/managed`` tree, populated by a prebuilt download or
    a from-source build; the active backend is the ``managed/current`` link.
``system``
    Whatever is on ``PATH``.

Everything here is platform-agnostic: executable naming, the executable bit and
link resolution are handled per OS so the same config works on Windows, Linux
and macOS.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import NamedTuple

from .config import AppConfig, PointedPaths
from .lifecycle import read_component_version, read_link_target
from .models import Source
from .paths import exe_suffix, is_executable, is_windows

#: Sub-directories searched (one level) when a pointed folder holds a full
#: llama.cpp layout rather than the binaries directly.
_NESTED_DIRS = ("bin", "build/bin", "build/bin/Release", "Release")

BINARY_NAMES = ("llama-server", "llama-swap")


class ResolvedBinary(NamedTuple):
    path: str | None
    source: Source | None
    version: str | None
    valid: bool
    error: str | None = None


# ─── Discovery ────────────────────────────────────────────────────────────


def _candidate_names(name: str) -> list[str]:
    """Executable file names to try for ``name`` on this platform."""
    if is_windows():
        return [f"{name}.exe", f"{name}.bat", f"{name}.cmd", name]
    return [name, f"{name}{exe_suffix()}"]


def find_exe_in_folder(folder: Path, name: str) -> Path | None:
    """Locate ``name`` inside ``folder`` (or a well-known sub-directory).

    An executable match always wins; a match that exists but lacks the execute
    bit is returned as a last resort so validation can explain the problem
    instead of reporting a misleading "not found".
    """
    if not folder.is_dir():
        return None
    fallback: Path | None = None
    search_dirs = [folder, *(folder / nested for nested in _NESTED_DIRS)]
    for directory in search_dirs:
        if not directory.is_dir():
            continue
        for candidate_name in _candidate_names(name):
            candidate = directory / candidate_name
            if not candidate.is_file():
                continue
            if is_executable(candidate):
                return candidate
            fallback = fallback or candidate
    return fallback


def _resolve_path_setting(raw: str, name: str) -> Path | None:
    """Interpret a user-supplied path that may be a file or a folder."""
    candidate = Path(raw).expanduser()
    if candidate.is_dir():
        return find_exe_in_folder(candidate, name)
    if candidate.is_file():
        return candidate
    # Tolerate a Windows path written without the .exe suffix.
    with_suffix = candidate.with_name(candidate.name + exe_suffix())
    if exe_suffix() and with_suffix.is_file():
        return with_suffix
    return None


# ─── Validation ───────────────────────────────────────────────────────────


def _run_probe(
    path: str | Path, flag: str, timeout: float
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(path), flag],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def validate_binary(
    path: str | Path, timeout: float = 8.0
) -> tuple[bool, str | None, str | None]:
    """Run the binary to prove it works. Returns ``(valid, version, error)``.

    ``--version`` is preferred; some builds only answer ``--help``. A binary
    that fails both is reported invalid *with* its stderr (missing CUDA/Vulkan
    libraries show up here) rather than being silently used.
    """
    exe = Path(path)
    if not exe.is_file():
        return False, None, f"Not a file: {exe}"
    if not is_executable(exe):
        return False, None, f"Not executable (missing +x): {exe}"
    try:
        result = _run_probe(exe, "--version", timeout)
        if result.returncode == 0:
            version = result.stdout.strip() or result.stderr.strip() or None
            return True, _first_line(version), None
        help_result = _run_probe(exe, "--help", timeout)
        if help_result.returncode == 0:
            return True, _first_line(result.stderr.strip()) or None, None
        error = (
            help_result.stderr.strip()
            or result.stderr.strip()
            or f"exit code {help_result.returncode}"
        )
        return False, None, error
    except (subprocess.TimeoutExpired, OSError) as e:
        return False, None, str(e)


def _first_line(text: str | None) -> str | None:
    if not text:
        return None
    return text.splitlines()[0].strip() or None


def _describe(path: Path, source: Source, validate: bool) -> ResolvedBinary:
    """Build a :class:`ResolvedBinary`, optionally probing the executable.

    With ``validate=False`` (the dashboard's hot path) existence is enough: the
    3-second status refresh must never spawn a subprocess.
    """
    if not validate:
        return ResolvedBinary(str(path), source, None, True, None)
    valid, version, error = validate_binary(path)
    return ResolvedBinary(str(path), source, version, valid, error)


# ─── Sources ──────────────────────────────────────────────────────────────


def _resolve_pointed(
    pointed: PointedPaths, exe_name: str, validate: bool
) -> ResolvedBinary | None:
    explicit = {
        "llama-server": pointed.llama_server,
        "llama-swap": pointed.llama_swap,
    }.get(exe_name)
    if explicit:
        found = _resolve_path_setting(explicit, exe_name)
        if found:
            return _describe(found, Source.POINTED, validate)
    if pointed.folder:
        found = _resolve_path_setting(pointed.folder, exe_name)
        if found:
            return _describe(found, Source.POINTED, validate)
    return None


def _managed_source(root: Path, name: str) -> Source:
    """Distinguish a downloaded install from a locally built one."""
    marker = read_component_version(root, name)
    if marker is not None and marker[1] == Source.MANAGED_BUILD.value:
        return Source.MANAGED_BUILD
    return Source.MANAGED_PREBUILT


def _resolve_managed(
    root: Path,
    cfg: AppConfig,
    exe_name: str,
    validate: bool,
    use_current_link: bool,
) -> ResolvedBinary | None:
    managed = root / "managed"
    if use_current_link:
        target = current_backend_dir(root)
        if target is not None:
            found = find_exe_in_folder(target, exe_name)
            if found:
                return _describe(found, _managed_source(root, target.name), validate)
    directory = managed / (
        "llama-swap" if exe_name == "llama-swap" else cfg.default_backend
    )
    found = find_exe_in_folder(directory, exe_name)
    if found:
        return _describe(found, _managed_source(root, directory.name), validate)
    return None


def current_backend_dir(root: Path) -> Path | None:
    """Directory the ``managed/current`` link points at (None when unset/broken)."""
    link = root / "managed" / "current"
    if not link.exists() and not link.is_symlink():
        return None
    target = read_link_target(link)
    if target:
        resolved = Path(target)
        if not resolved.is_absolute():
            resolved = (link.parent / resolved).resolve()
        if resolved.is_dir():
            return resolved
    # A plain directory (some filesystems disallow links) is valid too.
    return link if link.is_dir() else None


def _resolve_system(exe_name: str, validate: bool) -> ResolvedBinary | None:
    found = shutil.which(exe_name)
    if found:
        return _describe(Path(found), Source.SYSTEM, validate)
    return None


def _resolve_one(
    cfg: AppConfig,
    exe_name: str,
    use_current_link: bool,
    validate: bool = True,
) -> ResolvedBinary:
    root = cfg.root_path
    for source_name in cfg.source_priority:
        result: ResolvedBinary | None = None
        if source_name == "pointed":
            result = _resolve_pointed(cfg.pointed, exe_name, validate)
        elif source_name == "managed":
            result = _resolve_managed(root, cfg, exe_name, validate, use_current_link)
        elif source_name == "system":
            result = _resolve_system(exe_name, validate)
        if result:
            return result
    return ResolvedBinary(None, None, None, False, f"{exe_name} not found")


def resolve_llama_server(cfg: AppConfig, validate: bool = True) -> ResolvedBinary:
    return _resolve_one(cfg, "llama-server", use_current_link=True, validate=validate)


def resolve_llama_swap(cfg: AppConfig, validate: bool = True) -> ResolvedBinary:
    return _resolve_one(cfg, "llama-swap", use_current_link=False, validate=validate)


def resolve_both(cfg: AppConfig, validate: bool = True) -> dict[str, ResolvedBinary]:
    return {
        "llama_server": resolve_llama_server(cfg, validate=validate),
        "llama_swap": resolve_llama_swap(cfg, validate=validate),
    }


def anything_resolved(cfg: AppConfig) -> bool:
    """True when both binaries can be located (used to decide on first-run setup)."""
    resolved = resolve_both(cfg, validate=False)
    return all(r.path for r in resolved.values())


__all__ = [
    "BINARY_NAMES",
    "ResolvedBinary",
    "anything_resolved",
    "current_backend_dir",
    "find_exe_in_folder",
    "resolve_both",
    "resolve_llama_server",
    "resolve_llama_swap",
    "validate_binary",
]
