from __future__ import annotations

import platform
import re
import shutil
import sys
import tarfile
import zipfile
from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path
from typing import IO, Any, Protocol, runtime_checkable

import httpx

# ─── Platform detection ───────────────────────────────────────────────────
_SYSTEM = platform.system().lower()  # "windows" | "linux" | "darwin"
_MACHINE = platform.machine().lower()  # "amd64" | "x86_64" | "arm64" | "aarch64"

if _SYSTEM == "windows":
    _platform_key = "win32"
elif _SYSTEM == "darwin":
    _platform_key = "darwin"
else:
    _platform_key = "linux"

_IS_ARM64 = _MACHINE in ("arm64", "aarch64")


def _backend_asset_pattern(backend: str) -> str:
    """Return the llama.cpp release asset pattern for the current platform/backend.

    Asset naming conventions (ggml-org/llama.cpp) verified against release b10213:
      Windows:  llama-<tag>-bin-win-<backend>-x64.zip
      Linux:    llama-<tag>-bin-ubuntu-<backend>-<arch>.tar.gz
      macOS:    llama-<tag>-bin-macos-<arch>.tar.gz
    Linux/macOS builds ship as .tar.gz; CUDA prebuilts are Windows-only.
    """
    if _platform_key == "win32":
        if backend == "vulkan":
            return r"llama-.*-bin-win-vulkan-x64\.zip"
        if backend == "cuda13":
            return r"llama-.*-bin-win-cuda-13[._]\d+-x64\.zip"
        if backend == "cuda12":
            return r"llama-.*-bin-win-cuda-12[._]\d+-x64\.zip"
    elif _platform_key == "linux":
        # No CUDA prebuilts are published for Linux in recent releases.
        if backend == "vulkan":
            arch = "arm64" if _IS_ARM64 else "x64"
            return rf"llama-.*-bin-ubuntu-vulkan-{arch}\.tar\.gz"
        # cuda12/cuda13 are not available on Linux → never match
    elif _platform_key == "darwin":
        # macOS ships Metal-enabled builds as .tar.gz (no separate vulkan/CUDA asset).
        if backend == "vulkan":
            arch = "arm64" if _IS_ARM64 else "x64"
            return rf"llama-.*-bin-macos-{arch}\.tar\.gz"
        # CUDA is not available on macOS
    return r"$^"  # never matches


BACKEND_GLOBS: dict[str, dict[str, Any]] = {
    "vulkan": {
        "pattern": _backend_asset_pattern("vulkan"),
        "needs_cudart": False,
    },
    "cuda13": {
        "pattern": _backend_asset_pattern("cuda13"),
        "needs_cudart": False,
    },
    "cuda12": {
        "pattern": _backend_asset_pattern("cuda12"),
        "needs_cudart": True,
        "cudart_pattern": r"cudart-.*\.(zip|tar\.gz)",
    },
}


# llama-swap release asset naming (mostlygeek/llama-swap), verified v245:
#   Windows: llama-swap_<ver>_windows_amd64.zip
#   Linux:   llama-swap_<ver>_linux_<amd64|arm64>.tar.gz
#   macOS:   llama-swap_<ver>_darwin_<amd64|arm64>.tar.gz
def _llama_swap_asset_pattern() -> str:
    if _platform_key == "win32":
        return r"^llama-swap_.*_windows_amd64\.zip$"
    swap_arch = "arm64" if _IS_ARM64 else "amd64"
    if _platform_key == "darwin":
        return rf"^llama-swap_.*_darwin_{swap_arch}\.tar\.gz$"
    return rf"^llama-swap_.*_linux_{swap_arch}\.tar\.gz$"


LLAMA_SWAP_PATTERN = _llama_swap_asset_pattern()

LLAMA_CPP_REPO = "ggml-org/llama.cpp"
LLAMA_SWAP_REPO = "mostlygeek/llama-swap"

# ─── GUI progress callback ────────────────────────────────────────────────
# The GUI worker thread sets this to a callable ``(done, total, phase)`` so
# that download/extract progress is forwarded to the progress bar.
ProgressCallback = Callable[[int, int, str], None]
_progress_callback: ProgressCallback | None = None


def set_progress_callback(cb: ProgressCallback | None) -> None:
    global _progress_callback
    _progress_callback = cb


class PrebuiltError(Exception):
    pass


def latest_release(repo: str, token: str | None = None) -> dict[str, Any]:
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    headers: dict[str, str] = {"Accept": "application/vnd.github.v3+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        resp = httpx.get(url, headers=headers, timeout=30.0)
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
                for chunk in resp.iter_bytes(chunk_size=8192):
                    f.write(chunk)
                    done += len(chunk)
                    if total > 0:
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
    name = url.rsplit("/", 1)[-1]
    cache_path = cache_dir / name
    if cache_path.exists() and cache_path.stat().st_size == size:
        return cache_path
    # Emit a 0/start progress line so the bar appears immediately, then let
    # download_file() tick bytes_done as chunks arrive.
    emit_progress(component, 0, size, "download")
    download_file(url, cache_path, token, component=component)
    if cache_path.stat().st_size != size:
        cache_path.unlink(missing_ok=True)
        raise PrebuiltError(f"Downloaded size mismatch for {name}")
    return cache_path


def _is_tar_gz(path: Path) -> bool:
    return path.name.endswith(".tar.gz") or path.suffix == ".gz"


def _normalize_parts(path: Path) -> Path:
    """Strip leading '.' components (e.g. './llama-...' in some tarballs)."""
    return Path(*[part for part in path.parts if part != "."])


def _common_top_dir(parts_list: list[tuple[str, ...]]) -> bool:
    """Return True if every member shares a single common top-level directory.

    Some release archives wrap everything in a versioned folder
    (e.g. ``llama-b10213/llama-server``) while others are flat
    (e.g. ``llama-server.exe`` directly at the root). We strip the wrapping
    folder only when *all* members live under exactly one shared directory.
    """
    if not parts_list:
        return False
    firsts = {parts[0] for parts in parts_list if parts}
    if len(firsts) != 1:
        return False
    return all(len(parts) > 1 for parts in parts_list)


@runtime_checkable
class _FileOpener(Protocol):
    def __call__(self) -> AbstractContextManager[IO[bytes]]: ...


def _extract_member_to_dest(
    member_path: Path,
    dest_dir: Path,
    strip_top: bool,
    open_src: _FileOpener,
) -> None:
    """Extract a single archive member to dest_dir, optionally stripping top-level.

    Args:
        member_path: Original path of the member in the archive.
        dest_dir: Destination directory.
        strip_top: If True, strip the first path component.
        open_src: Context manager that yields a readable file-like object.
    """
    relative = Path(*member_path.parts[1:]) if strip_top else member_path
    if not relative.parts or not relative.name:
        return
    target = dest_dir / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    with open_src() as src, target.open("wb") as dst:
        shutil.copyfileobj(src, dst)


def wipe_and_extract(archive_path: Path, dest_dir: Path) -> None:
    """Extract a .zip or .tar.gz archive into dest_dir.

    If every member lives under a single common top-level directory, that
    directory is stripped so the contents land directly in dest_dir. Flat
    archives (no wrapping folder) are extracted as-is.
    """
    if dest_dir.exists():
        shutil.rmtree(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    if not (_is_tar_gz(archive_path) or archive_path.suffix == ".zip"):
        raise PrebuiltError(f"Unsupported archive format: {archive_path}")

    # Collect file members and decide whether to strip a wrapping folder.
    if _is_tar_gz(archive_path):
        with tarfile.open(archive_path, "r:gz") as tf:
            file_members = [m for m in tf.getmembers() if m.isfile()]
            parts_list = [_normalize_parts(Path(m.name)).parts for m in file_members]
        strip_top = _common_top_dir(parts_list)

        with tarfile.open(archive_path, "r:gz") as tf:
            for member in file_members:
                member_path = _normalize_parts(Path(member.name))
                src_cm = tf.extractfile(member)
                if src_cm is not None:
                    _extract_member_to_dest(
                        member_path, dest_dir, strip_top, lambda s=src_cm: s
                    )
    else:
        with zipfile.ZipFile(archive_path, "r") as zf:
            infos = zf.infolist()
            file_infos = [i for i in infos if not i.is_dir()]
            parts_list = [_normalize_parts(Path(i.filename)).parts for i in file_infos]
        strip_top = _common_top_dir(parts_list)

        with zipfile.ZipFile(archive_path, "r") as zf:
            for info in file_infos:
                member_path = _normalize_parts(Path(info.filename))
                _extract_member_to_dest(
                    member_path, dest_dir, strip_top, lambda i=info: zf.open(i)
                )


def _extract_cudart(archive_path: Path, dest_dir: Path) -> None:
    """Extract CUDA runtime libraries (.dll/.so/.dylib) from an archive into dest_dir."""
    lib_suffixes = {".dll", ".so", ".dylib"}
    dest_dir.mkdir(parents=True, exist_ok=True)

    if _is_tar_gz(archive_path):
        with tarfile.open(archive_path, "r:gz") as tf:
            for member in tf.getmembers():
                if not member.isfile():
                    continue
                member_path = Path(member.name)
                if member_path.suffix.lower() not in lib_suffixes:
                    continue
                target = dest_dir / member_path.name
                src = tf.extractfile(member)
                if src is not None:
                    with src, target.open("wb") as dst:
                        shutil.copyfileobj(src, dst)
    elif archive_path.suffix == ".zip":
        with zipfile.ZipFile(archive_path, "r") as zf:
            for member_name in zf.namelist():
                member_path = Path(member_name)
                if member_path.suffix.lower() not in lib_suffixes:
                    continue
                target = dest_dir / member_path.name
                with zf.open(member_name) as src, target.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
    else:
        raise PrebuiltError(f"Unsupported archive format for cudart: {archive_path}")


def write_version_marker(
    dest_dir: Path, tag: str, source: str = "managed-prebuilt"
) -> None:
    marker = dest_dir / ".version"
    marker.write_text(f"{tag}\n{source}\n", encoding="utf-8")


BACKEND_DESCRIPTION: dict[str, dict[str, Any]] = {
    "vulkan": {
        "notes": "Default; best prefill on GTX 1650 (~189 t/s)",
        "needs_cudart": False,
    },
    "cuda13": {"notes": "CUDA 13.3 toolkit; arch 75", "needs_cudart": False},
    "cuda12": {
        "notes": "CUDA 12.4 binary with bundled cudart DLLs",
        "needs_cudart": True,
    },
}


def emit_progress(
    component: str, bytes_done: int, bytes_total: int, phase: str
) -> None:
    print(
        f"PROGRESS\t{component}\t{bytes_done}\t{bytes_total}\t{phase}", file=sys.stderr
    )
    # Forward to the GUI progress callback if one has been registered (by the
    # WorkerPool thread).  The callback receives (bytes_done, bytes_total,
    # phase) so that the progress bar can be updated in real time.
    if _progress_callback is not None:
        _progress_callback(bytes_done, bytes_total, phase)


def install_backend(
    backend: str,
    managed_root: Path,
    downloads_dir: Path,
    token: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    backend_info = BACKEND_GLOBS.get(backend)
    if not backend_info:
        raise PrebuiltError(f"Unknown backend: {backend}")

    release = latest_release(LLAMA_CPP_REPO, token)
    tag: str = release.get("tag_name", "unknown")
    assets: list[dict[str, Any]] = release.get("assets", [])

    if not force:
        marker = managed_root / backend / ".version"
        if marker.exists() and marker.read_text(encoding="utf-8").strip().startswith(
            tag
        ):
            return {
                "name": backend,
                "status": "skipped",
                "version": tag,
                "bytes": 0,
            }

    asset = match_asset(assets, backend_info["pattern"])
    if not asset:
        raise PrebuiltError(
            f"No asset matching '{backend_info['pattern']}' in release {tag}"
        )

    cache_path = cached_download(
        asset["url"], asset["size"], downloads_dir, token, component=backend
    )
    emit_progress(backend, asset["size"], asset["size"], "extract")
    wipe_and_extract(cache_path, managed_root / backend)

    if backend_info.get("needs_cudart"):
        cudart_asset = match_asset(assets, backend_info.get("cudart_pattern", r""))
        if cudart_asset:
            cudart_cache = cached_download(
                cudart_asset["url"],
                cudart_asset["size"],
                downloads_dir,
                token,
                component=f"{backend}-cudart",
            )
            emit_progress(
                f"{backend}-cudart",
                cudart_asset["size"],
                cudart_asset["size"],
                "extract",
            )
            _extract_cudart(cudart_cache, managed_root / backend)

    write_version_marker(managed_root / backend, tag)
    return {
        "name": backend,
        "status": "ok",
        "version": tag,
        "bytes": asset["size"],
    }


def install_llama_swap(
    managed_root: Path,
    downloads_dir: Path,
    token: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    release = latest_release(LLAMA_SWAP_REPO, token)
    tag: str = release.get("tag_name", "unknown")
    assets: list[dict[str, Any]] = release.get("assets", [])

    if not force:
        marker = managed_root / "llama-swap" / ".version"
        if marker.exists() and marker.read_text(encoding="utf-8").strip().startswith(
            tag
        ):
            return {"status": "skipped", "version": tag}

    asset = match_asset(assets, LLAMA_SWAP_PATTERN)
    if not asset:
        raise PrebuiltError(
            f"No asset matching '{LLAMA_SWAP_PATTERN}' in release {tag}"
        )

    cache_path = cached_download(
        asset["url"], asset["size"], downloads_dir, token, component="llama-swap"
    )
    emit_progress("llama-swap", asset["size"], asset["size"], "extract")
    wipe_and_extract(cache_path, managed_root / "llama-swap")
    write_version_marker(managed_root / "llama-swap", tag)
    return {"status": "ok", "version": tag, "bytes": asset["size"]}


def list_assets(repo: str = LLAMA_CPP_REPO, token: str | None = None) -> dict[str, Any]:
    release = latest_release(repo, token)
    return {
        "release": release.get("tag_name"),
        "assets": [
            {
                "name": a["name"],
                "size": a["size"],
                "flag": "vulkan" if "vulkan" in a["name"] else "cuda",
            }
            for a in release.get("assets", [])
        ],
    }
