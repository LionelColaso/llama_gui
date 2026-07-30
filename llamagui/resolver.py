from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

from .config import AppConfig, PointedPaths
from .lifecycle import _read_reparse_point, read_component_version
from .models import Source


class ResolvedBinary(NamedTuple):
    path: str | None
    source: Source | None
    version: str | None
    valid: bool
    error: str | None = None


def _validate_exe(
    path: str | Path, timeout: float = 5.0
) -> tuple[bool, str | None, str | None]:
    """Return (valid, version, error).

    ``version`` is populated from stdout/stderr when the executable accepts
    ``--version``.  ``error`` captures stderr on failure so callers can report
    richer diagnostics (e.g. missing DLLs, bad manifest).
    """
    try:
        result = subprocess.run(
            [str(path), "--version"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if result.returncode == 0:
            version = result.stdout.strip() or result.stderr.strip() or None
            return True, version, None
        result = subprocess.run(
            [str(path), "--help"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if result.returncode == 0:
            return True, None, None
        error = result.stderr.strip() or "exit code " + str(result.returncode)
        return False, None, error
    except (subprocess.TimeoutExpired, OSError) as e:
        return False, None, str(e)


def _resolve_pointed(
    p: PointedPaths, folder: Path, exe_name: str, validate: bool
) -> ResolvedBinary | None:
    """Resolve from user-pointed paths (folder or direct exe path)."""
    if p.folder:
        result = _find_exe_in_folder(folder, exe_name)
        if result:
            return _case(result, Source.POINTED, validate)
    exe_attr = {
        "llama-server": p.llama_server,
        "llama-swap": p.llama_swap,
    }.get(exe_name)
    if exe_attr:
        candidate = Path(exe_attr)
        if candidate.exists():
            return _case(candidate, Source.POINTED, validate)
    return None


def _resolve_managed(
    root: Path,
    cfg: AppConfig,
    exe_name: str,
    validate: bool,
    use_junction: bool = True,
) -> ResolvedBinary | None:
    """Resolve from managed installations.

    Args:
        root: App root directory.
        cfg: App configuration (needed for default_backend).
        exe_name: Executable name (e.g. "llama-server", "llama-swap").
        validate: Whether to validate the executable.
        use_junction: If True, follow the 'current' junction first.
    """
    if use_junction:
        current_link = root / "managed" / "current"
        if current_link.exists():
            target = _resolve_junction(current_link)
            if target:
                result = _find_exe_in_folder(Path(target), exe_name)
                if result:
                    source = _marker_source(root, Path(target).name)
                    return _case(result, source, validate)
    managed_dir = (
        root
        / "managed"
        / (exe_name if exe_name == "llama-swap" else cfg.default_backend)
    )
    result = _find_exe_in_folder(managed_dir, exe_name)
    if result:
        source = _marker_source(root, managed_dir.name)
        return _case(result, source, validate)
    return None


def _resolve_one(
    cfg: AppConfig,
    exe_name: str,
    use_junction: bool,
    validate: bool = True,
) -> ResolvedBinary:
    """Walk the configured source priority for a single executable.

    Shared by the per-exe resolvers: the only differences are the executable
    name and whether the managed 'current' junction is followed (llama-swap is
    resolved directly, the server follows the junction).
    """
    root = Path(cfg.root)

    for source_name in cfg.source_priority:
        if source_name == "pointed":
            result = _resolve_pointed(
                cfg.pointed, Path(cfg.pointed.folder or "."), exe_name, validate
            )
            if result:
                return result

        elif source_name == "managed":
            result = _resolve_managed(
                root, cfg, exe_name, validate, use_junction=use_junction
            )
            if result:
                return result

        elif source_name == "system":
            which = _shutil_which(exe_name)
            if which:
                return _case(Path(which), Source.SYSTEM, validate)

    return ResolvedBinary(None, None, None, False, None)


def resolve_llama_server(cfg: AppConfig, validate: bool = True) -> ResolvedBinary:
    return _resolve_one(cfg, "llama-server", use_junction=True, validate=validate)


def resolve_llama_swap(cfg: AppConfig, validate: bool = True) -> ResolvedBinary:
    return _resolve_one(cfg, "llama-swap", use_junction=False, validate=validate)


def _find_exe_in_folder(folder: Path, name: str) -> Path | None:
    candidates = [
        folder / f"{name}.exe",
        folder / f"{name}.bat",
        folder / f"{name}.cmd",
    ]
    for c in candidates:
        if c.exists():
            return c
    if sys.platform != "win32":
        no_ext = folder / name
        if no_ext.exists():
            return no_ext
    return None


def _shutil_which(name: str) -> str | None:
    import shutil

    result = shutil.which(name)
    return result


def _case(path: Path, source: Source, validate: bool) -> ResolvedBinary:
    """Build a ResolvedBinary for a found executable.

    When ``validate`` is False (fast status path), skip the ``--version``
    subprocess so reads never block on the GUI hot path; existence of the
    executable is treated as valid.
    """
    if not validate:
        return ResolvedBinary(str(path), source, None, True, None)
    valid, ver, err = _validate_exe(path)
    return ResolvedBinary(str(path), source, ver, valid, err)


def _marker_source(root: Path, name: str) -> Source:
    """Read the .version marker's source line to distinguish managed-build from managed-prebuilt."""
    cv = read_component_version(root, name)
    if cv is not None and cv[1] == "managed-build":
        return Source.MANAGED_BUILD
    return Source.MANAGED_PREBUILT


def _resolve_junction(path: Path) -> str | None:
    """Return the target of a symlink/junction, or None."""
    try:
        return os.readlink(str(path))
    except (OSError, NotImplementedError):
        pass
    return _read_reparse_point(path)


def resolve_both(cfg: AppConfig, validate: bool = True) -> dict[str, ResolvedBinary]:
    return {
        "llama_server": resolve_llama_server(cfg, validate=validate),
        "llama_swap": resolve_llama_swap(cfg, validate=validate),
    }
