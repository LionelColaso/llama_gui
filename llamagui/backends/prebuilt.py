"""Managed-prebuilt source: download official GitHub release binaries.

Works on Windows, Linux and macOS: the asset for the running platform comes
from the backend catalogue in :mod:`llamagui.models`, and archives are unpacked
in a way that survives POSIX packaging conventions (executable bits, symlinked
``libllama.so`` chains) as well as Windows zips.
"""

from __future__ import annotations

import functools
import re
import shutil
import stat
import sys
import tarfile
import zipfile
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

import httpx

from ..models import Backend, backend_availability, get_backend
from ..paths import (
    arch_key,
    clear_quarantine,
    is_windows,
    make_executable,
    platform_key,
)

LLAMA_CPP_REPO = "ggml-org/llama.cpp"
LLAMA_SWAP_REPO = "mostlygeek/llama-swap"


class PrebuiltError(Exception):
    """A prebuilt download/extract step failed (network, asset, archive)."""


class PrebuiltUnavailable(PrebuiltError):
    """No official prebuilt exists for this backend on this platform."""


# ─── Asset patterns ───────────────────────────────────────────────────────


def backend_asset_pattern(backend: str) -> str | None:
    """Return the llama.cpp asset regex for ``backend`` on this platform."""
    entry = get_backend(backend)
    return entry.asset_pattern() if entry else None


def llama_swap_asset_pattern(
    platform: str | None = None, arch: str | None = None
) -> str:
    """Return the llama-swap release asset regex for a platform.

    Naming (mostlygeek/llama-swap, verified v248)::

        llama-swap_<ver>_windows_amd64.zip
        llama-swap_<ver>_linux_<amd64|arm64>.tar.gz
        llama-swap_<ver>_darwin_<amd64|arm64>.tar.gz
    """
    plat = platform or platform_key()
    goarch = "arm64" if (arch or arch_key()) == "arm64" else "amd64"
    if plat == "win32":
        return rf"^llama-swap_.*_windows_{goarch}\.zip$"
    if plat == "darwin":
        return rf"^llama-swap_.*_darwin_{goarch}\.tar\.gz$"
    return rf"^llama-swap_.*_linux_{goarch}\.tar\.gz$"


LLAMA_SWAP_PATTERN = llama_swap_asset_pattern()


def _cudart_pattern(entry: Backend, mode: str) -> str | None:
    """Return the CUDA runtime pack regex to fetch, honouring the user's mode.

    ``auto``   – only when the backend cannot run without it (CUDA 12).
    ``always`` – whenever the backend has a runtime pack (self-contained CUDA).
    ``never``  – rely on the CUDA toolkit installed on the machine.
    """
    if entry.cudart_pattern is None or mode == "never":
        return None
    if mode == "always" or entry.needs_cudart:
        return entry.cudart_pattern
    return None


# ─── GUI progress callback ────────────────────────────────────────────────

ProgressCallback = Callable[[int, int, str], None]
_progress_callback: ProgressCallback | None = None


def set_progress_callback(cb: ProgressCallback | None) -> None:
    global _progress_callback
    _progress_callback = cb


def emit_progress(
    component: str, bytes_done: int, bytes_total: int, phase: str
) -> None:
    print(
        f"PROGRESS\t{component}\t{bytes_done}\t{bytes_total}\t{phase}", file=sys.stderr
    )
    if _progress_callback is not None:
        _progress_callback(bytes_done, bytes_total, phase)


# ─── GitHub API ───────────────────────────────────────────────────────────


@functools.lru_cache(maxsize=32)
def latest_release(repo: str, token: str | None = None) -> dict[str, Any]:
    """Return the latest GitHub release for ``repo``.

    Memoized per ``(repo, token)`` for the process lifetime: a single
    install/update run can request the same release several times (once per
    backend plus llama-swap), and the GitHub API rate-limits unauthenticated
    callers, so caching avoids redundant network round-trips.
    """
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    headers: dict[str, str] = {"Accept": "application/vnd.github.v3+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        resp = httpx.get(url, headers=headers, timeout=30.0, follow_redirects=True)
        resp.raise_for_status()
        return dict(resp.json())
    except httpx.HTTPError as e:
        raise PrebuiltError(f"GitHub API error for {repo}: {e}") from e


def match_asset(assets: list[dict[str, Any]], pattern: str) -> dict[str, Any] | None:
    compiled = re.compile(pattern)
    for asset in assets:
        name = asset.get("name", "")
        if compiled.search(name):
            return {
                "name": name,
                "size": asset.get("size", 0),
                "url": asset["browser_download_url"],
            }
    return None


def download_file(
    url: str,
    dest: Path,
    token: str | None = None,
    *,
    component: str = "download",
) -> None:
    headers: dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        with httpx.stream(
            "GET", url, headers=headers, timeout=120.0, follow_redirects=True
        ) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("content-length", "0") or 0)
            dest.parent.mkdir(parents=True, exist_ok=True)
            done = 0
            with dest.open("wb") as f:
                for chunk in resp.iter_bytes(chunk_size=65536):
                    f.write(chunk)
                    done += len(chunk)
                    # Always emit so the GUI shows movement; when the server
                    # omits Content-Length (total == 0) the bar renders
                    # indeterminate rather than frozen.
                    emit_progress(component, done, total, "download")
    except httpx.HTTPError as e:
        raise PrebuiltError(f"Download failed: {e}") from e


def cached_download(
    url: str,
    size: int,
    cache_dir: Path,
    token: str | None = None,
    *,
    component: str = "download",
) -> Path:
    """Download ``url`` unless a cached file of exactly ``size`` bytes exists."""
    name = url.rsplit("/", 1)[-1]
    cache_path = cache_dir / name
    if cache_path.exists() and cache_path.stat().st_size == size:
        emit_progress(component, size, size, "cache hit")
        return cache_path
    emit_progress(component, 0, size, "download")
    download_file(url, cache_path, token, component=component)
    if size and cache_path.stat().st_size != size:
        cache_path.unlink(missing_ok=True)
        raise PrebuiltError(f"Downloaded size mismatch for {name}")
    return cache_path


# ─── Archive extraction ───────────────────────────────────────────────────


def _is_tar_gz(path: Path) -> bool:
    return path.name.endswith((".tar.gz", ".tgz"))


def _normalize(name: str) -> Path:
    """Normalize an archive member name (strip ``./`` and Windows slashes)."""
    parts = [p for p in Path(name.replace("\\", "/")).parts if p not in (".", "")]
    return Path(*parts) if parts else Path()


def _common_top_dir(parts_list: Iterable[tuple[str, ...]]) -> bool:
    """True when every member lives under one shared top-level directory."""
    materialized = [p for p in parts_list if p]
    if not materialized:
        return False
    firsts = {parts[0] for parts in materialized}
    if len(firsts) != 1:
        return False
    return all(len(parts) > 1 for parts in materialized)


def _strip(relative: Path, strip_top: bool) -> Path:
    return Path(*relative.parts[1:]) if strip_top else relative


def _safe_target(dest_dir: Path, relative: Path) -> Path:
    """Resolve an archive member inside ``dest_dir`` (blocks zip-slip)."""
    root = dest_dir.resolve()
    target = (root / relative).resolve()
    if target != root and root not in target.parents:
        raise PrebuiltError(f"Refusing to extract outside destination: {relative}")
    return target


def _apply_mode(target: Path, mode: int) -> None:
    """Apply POSIX permission bits recorded in the archive (no-op on Windows)."""
    if is_windows() or not mode:
        return
    try:
        target.chmod(stat.S_IMODE(mode))
    except OSError:
        pass


def _write_symlink(target: Path, link_to: str) -> None:
    """Recreate an archive symlink; fall back to copying when unsupported.

    Linux llama.cpp tarballs ship ``libllama.so -> libllama.so.0`` chains; a
    dropped symlink leaves ``llama-server`` unable to load its own libraries.
    """
    if target.exists() or target.is_symlink():
        target.unlink()
    try:
        target.symlink_to(link_to)
        return
    except (OSError, NotImplementedError):
        pass
    source = (target.parent / link_to).resolve()
    if source.is_file():
        shutil.copy2(source, target)


def _extract_tar(archive_path: Path, dest_dir: Path) -> None:
    with tarfile.open(archive_path, "r:gz") as tf:
        members = tf.getmembers()
        strip_top = _common_top_dir(
            _normalize(m.name).parts for m in members if not m.isdir()
        )
        # Regular files first so symlink targets already exist.
        for member in sorted(members, key=lambda m: m.issym() or m.islnk()):
            relative = _strip(_normalize(member.name), strip_top)
            if not relative.parts:
                continue
            target = _safe_target(dest_dir, relative)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            if member.issym():
                _write_symlink(target, member.linkname)
                continue
            if member.islnk():
                source = _safe_target(
                    dest_dir, _strip(_normalize(member.linkname), strip_top)
                )
                if source.is_file():
                    shutil.copy2(source, target)
                continue
            if not member.isfile():
                continue
            src = tf.extractfile(member)
            if src is None:
                continue
            with src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)
            _apply_mode(target, member.mode)


def _zip_member_mode(info: zipfile.ZipInfo) -> int:
    """Return the UNIX mode stored in a zip entry (0 when absent)."""
    if info.create_system == 3:  # 3 == UNIX
        return info.external_attr >> 16
    return 0


def _extract_zip(archive_path: Path, dest_dir: Path) -> None:
    with zipfile.ZipFile(archive_path, "r") as zf:
        infos = zf.infolist()
        strip_top = _common_top_dir(
            _normalize(i.filename).parts for i in infos if not i.is_dir()
        )
        for info in infos:
            relative = _strip(_normalize(info.filename), strip_top)
            if not relative.parts:
                continue
            target = _safe_target(dest_dir, relative)
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            mode = _zip_member_mode(info)
            if stat.S_ISLNK(mode):
                _write_symlink(target, zf.read(info).decode("utf-8"))
                continue
            with zf.open(info) as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)
            _apply_mode(target, mode)


def _mark_executables(dest_dir: Path) -> None:
    """Ensure unpacked program files are runnable on POSIX.

    Some archives carry no mode bits at all (zips created on Windows). Any
    extension-less regular file is a program on POSIX, so give it +x.
    """
    if is_windows():
        return
    for path in dest_dir.rglob("*"):
        if path.is_symlink() or not path.is_file():
            continue
        if path.suffix in ("", ".sh") or ".so" in path.suffixes:
            make_executable(path)


def wipe_and_extract(archive_path: Path, dest_dir: Path) -> None:
    """Replace ``dest_dir`` with the contents of ``archive_path``.

    The directory is wiped first so stale DLLs/SOs from a previous release can
    never be picked up (invariant #4). A single wrapping top-level folder in
    the archive is stripped; flat archives are extracted as-is.
    """
    if dest_dir.exists() or dest_dir.is_symlink():
        if dest_dir.is_symlink():
            dest_dir.unlink()
        else:
            shutil.rmtree(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    if _is_tar_gz(archive_path):
        _extract_tar(archive_path, dest_dir)
    elif archive_path.suffix.lower() == ".zip":
        _extract_zip(archive_path, dest_dir)
    else:
        raise PrebuiltError(f"Unsupported archive format: {archive_path}")

    _mark_executables(dest_dir)
    clear_quarantine(dest_dir)


def _extract_cudart(archive_path: Path, dest_dir: Path) -> None:
    """Drop the CUDA runtime shared libraries next to the backend binaries."""
    lib_suffixes = {".dll", ".so", ".dylib"}
    dest_dir.mkdir(parents=True, exist_ok=True)

    def _wanted(name: str) -> bool:
        member = _normalize(name)
        return bool(member.parts) and member.suffix.lower() in lib_suffixes

    if _is_tar_gz(archive_path):
        with tarfile.open(archive_path, "r:gz") as tf:
            for member in tf.getmembers():
                if not member.isfile() or not _wanted(member.name):
                    continue
                target = _safe_target(dest_dir, Path(_normalize(member.name).name))
                src = tf.extractfile(member)
                if src is not None:
                    with src, target.open("wb") as dst:
                        shutil.copyfileobj(src, dst)
                    _apply_mode(target, member.mode)
    elif archive_path.suffix.lower() == ".zip":
        with zipfile.ZipFile(archive_path, "r") as zf:
            for name in zf.namelist():
                if not _wanted(name):
                    continue
                target = _safe_target(dest_dir, Path(_normalize(name).name))
                with zf.open(name) as src, target.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
                _apply_mode(target, _zip_member_mode(zf.getinfo(name)))
    else:
        raise PrebuiltError(f"Unsupported archive format for cudart: {archive_path}")


def write_version_marker(
    dest_dir: Path, tag: str, source: str = "managed-prebuilt"
) -> None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    (dest_dir / ".version").write_text(f"{tag}\n{source}\n", encoding="utf-8")


def read_version_marker(dest_dir: Path) -> str | None:
    marker = dest_dir / ".version"
    if not marker.exists():
        return None
    return marker.read_text(encoding="utf-8").strip().split("\n", 1)[0].strip()


# ─── Install ──────────────────────────────────────────────────────────────


def install_backend(
    backend: str,
    managed_root: Path,
    downloads_dir: Path,
    token: str | None = None,
    force: bool = False,
    *,
    bundle_cuda_runtime: str = "auto",
) -> dict[str, Any]:
    """Install (or refresh) one backend from the latest llama.cpp release."""
    entry = get_backend(backend)
    if entry is None:
        raise PrebuiltError(f"Unknown backend: {backend}")

    pattern = entry.asset_pattern()
    if pattern is None:
        raise PrebuiltUnavailable(backend_availability(backend)["reason"])

    release = latest_release(LLAMA_CPP_REPO, token)
    tag: str = release.get("tag_name", "unknown")
    assets: list[dict[str, Any]] = release.get("assets", [])

    if not force and read_version_marker(managed_root / backend) == tag:
        return {"name": backend, "status": "skipped", "version": tag, "bytes": 0}

    asset = match_asset(assets, pattern)
    if not asset:
        raise PrebuiltError(f"No asset matching '{pattern}' in release {tag}")

    cache_path = cached_download(
        asset["url"], asset["size"], downloads_dir, token, component=backend
    )
    emit_progress(backend, asset["size"], asset["size"], "extract")
    wipe_and_extract(cache_path, managed_root / backend)

    cudart = _cudart_pattern(entry, bundle_cuda_runtime)
    if cudart:
        cudart_asset = match_asset(assets, cudart)
        if cudart_asset:
            component = f"{backend}-cudart"
            cudart_cache = cached_download(
                cudart_asset["url"],
                cudart_asset["size"],
                downloads_dir,
                token,
                component=component,
            )
            emit_progress(
                component, cudart_asset["size"], cudart_asset["size"], "extract"
            )
            _extract_cudart(cudart_cache, managed_root / backend)
        elif entry.needs_cudart:
            raise PrebuiltError(
                f"Release {tag} has no CUDA runtime pack matching '{cudart}'; "
                f"'{backend}' cannot run without it."
            )

    write_version_marker(managed_root / backend, tag)
    return {"name": backend, "status": "ok", "version": tag, "bytes": asset["size"]}


def install_llama_swap(
    managed_root: Path,
    downloads_dir: Path,
    token: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Install (or refresh) llama-swap from its latest GitHub release."""
    release = latest_release(LLAMA_SWAP_REPO, token)
    tag: str = release.get("tag_name", "unknown")
    assets: list[dict[str, Any]] = release.get("assets", [])

    if not force and read_version_marker(managed_root / "llama-swap") == tag:
        return {"name": "llama-swap", "status": "skipped", "version": tag, "bytes": 0}

    pattern = llama_swap_asset_pattern()
    asset = match_asset(assets, pattern)
    if not asset:
        raise PrebuiltError(f"No asset matching '{pattern}' in release {tag}")

    cache_path = cached_download(
        asset["url"], asset["size"], downloads_dir, token, component="llama-swap"
    )
    emit_progress("llama-swap", asset["size"], asset["size"], "extract")
    wipe_and_extract(cache_path, managed_root / "llama-swap")
    write_version_marker(managed_root / "llama-swap", tag)
    return {
        "name": "llama-swap",
        "status": "ok",
        "version": tag,
        "bytes": asset["size"],
    }


def _asset_flag(name: str) -> str:
    """Best-effort label describing what an asset contains."""
    lowered = name.lower()
    if lowered.startswith("cudart"):
        return "cudart"
    for backend in ("vulkan", "cuda-13", "cuda-12", "rocm", "sycl", "openvino", "cpu"):
        if backend in lowered:
            return backend.replace("-", "")
    if "macos" in lowered:
        return "metal"
    return "other"


def list_assets(repo: str = LLAMA_CPP_REPO, token: str | None = None) -> dict[str, Any]:
    release = latest_release(repo, token)
    return {
        "release": release.get("tag_name"),
        "assets": [
            {
                "name": a["name"],
                "size": a.get("size", 0),
                "flag": _asset_flag(a["name"]),
            }
            for a in release.get("assets", [])
        ],
    }


def latest_versions(token: str | None = None) -> dict[str, str | None]:
    """Return the newest published tags for both upstream projects."""
    versions: dict[str, str | None] = {"llama_cpp": None, "llama_swap": None}
    for key, repo in (("llama_cpp", LLAMA_CPP_REPO), ("llama_swap", LLAMA_SWAP_REPO)):
        try:
            versions[key] = latest_release(repo, token).get("tag_name")
        except PrebuiltError:
            versions[key] = None
    return versions


def installed_backends(managed_root: Path) -> dict[str, str]:
    """Map of installed backend name → version tag (from ``.version`` files)."""
    found: dict[str, str] = {}
    if not managed_root.is_dir():
        return found
    for child in managed_root.iterdir():
        if not child.is_dir() or child.name == "current":
            continue
        tag = read_version_marker(child)
        if tag:
            found[child.name] = tag
    return found


def failure_hint(exc: Exception) -> str:
    """Human-readable next step for a failed prebuilt operation."""
    if isinstance(exc, PrebuiltUnavailable):
        return "Build from source or point at an existing binary instead."
    if isinstance(exc, PrebuiltError):
        return "Check the network connection, or add a GitHub token in Settings."
    return str(exc)


__all__ = [
    "LLAMA_CPP_REPO",
    "LLAMA_SWAP_PATTERN",
    "LLAMA_SWAP_REPO",
    "PrebuiltError",
    "PrebuiltUnavailable",
    "backend_asset_pattern",
    "cached_download",
    "download_file",
    "emit_progress",
    "failure_hint",
    "install_backend",
    "install_llama_swap",
    "installed_backends",
    "latest_release",
    "latest_versions",
    "list_assets",
    "llama_swap_asset_pattern",
    "match_asset",
    "read_version_marker",
    "set_progress_callback",
    "wipe_and_extract",
    "write_version_marker",
]
