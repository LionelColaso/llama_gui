from __future__ import annotations

import io
import os
import stat
import sys
import tarfile
import zipfile
from pathlib import Path
from typing import Any, ClassVar, Self
from unittest.mock import MagicMock, patch

import pytest

from llamagui.backends.prebuilt import (
    PrebuiltError,
    PrebuiltUnavailable,
    backend_asset_pattern,
    cached_download,
    download_file,
    install_backend,
    installed_backends,
    llama_swap_asset_pattern,
    match_asset,
    wipe_and_extract,
)
from llamagui.models import get_backend
from llamagui.paths import arch_key, platform_key

_PLATFORM = platform_key()
_ARCH = arch_key()
_IS_WINDOWS = _PLATFORM == "win32"


# ─── Asset naming (real release names, verified against b10331 / v248) ────


def _release_assets() -> list[dict[str, Any]]:
    """Assets named exactly as ggml-org/llama.cpp publishes them."""
    names = [
        "cudart-llama-bin-win-cuda-12.4-x64.zip",
        "cudart-llama-bin-win-cuda-13.3-x64.zip",
        "llama-b10331-bin-macos-arm64.tar.gz",
        "llama-b10331-bin-macos-x64.tar.gz",
        "llama-b10331-bin-ubuntu-arm64.tar.gz",
        "llama-b10331-bin-ubuntu-vulkan-arm64.tar.gz",
        "llama-b10331-bin-ubuntu-vulkan-x64.tar.gz",
        "llama-b10331-bin-ubuntu-x64.tar.gz",
        "llama-b10331-bin-win-cpu-arm64.zip",
        "llama-b10331-bin-win-cpu-x64.zip",
        "llama-b10331-bin-win-cuda-12.4-x64.zip",
        "llama-b10331-bin-win-cuda-13.3-x64.zip",
        "llama-b10331-bin-win-vulkan-x64.zip",
    ]
    return [
        {
            "name": name,
            "size": 1000 + index,
            "browser_download_url": f"https://example.com/{name}",
        }
        for index, name in enumerate(names)
    ]


def _swap_assets() -> list[dict[str, Any]]:
    names = [
        "llama-swap_248_checksums.txt",
        "llama-swap_248_darwin_amd64.tar.gz",
        "llama-swap_248_darwin_arm64.tar.gz",
        "llama-swap_248_linux_amd64.tar.gz",
        "llama-swap_248_linux_arm64.tar.gz",
        "llama-swap_248_windows_amd64.zip",
    ]
    return [
        {"name": n, "size": 10, "browser_download_url": f"https://example.com/{n}"}
        for n in names
    ]


def fake_release() -> dict[str, Any]:
    return {"tag_name": "b10331", "assets": _release_assets()}


@pytest.mark.parametrize(
    ("platform", "arch", "backend", "expected"),
    [
        ("win32", "x64", "vulkan", "llama-b10331-bin-win-vulkan-x64.zip"),
        ("win32", "x64", "cuda12", "llama-b10331-bin-win-cuda-12.4-x64.zip"),
        ("win32", "x64", "cuda13", "llama-b10331-bin-win-cuda-13.3-x64.zip"),
        ("win32", "arm64", "cpu", "llama-b10331-bin-win-cpu-arm64.zip"),
        ("linux", "x64", "vulkan", "llama-b10331-bin-ubuntu-vulkan-x64.tar.gz"),
        ("linux", "arm64", "vulkan", "llama-b10331-bin-ubuntu-vulkan-arm64.tar.gz"),
        ("linux", "x64", "cpu", "llama-b10331-bin-ubuntu-x64.tar.gz"),
        ("darwin", "arm64", "metal", "llama-b10331-bin-macos-arm64.tar.gz"),
        ("darwin", "x64", "metal", "llama-b10331-bin-macos-x64.tar.gz"),
    ],
)
def test_asset_selection_per_platform(
    platform: str, arch: str, backend: str, expected: str
) -> None:
    entry = get_backend(backend)
    assert entry is not None
    pattern = entry.asset_pattern(platform, arch)
    assert pattern is not None
    asset = match_asset(_release_assets(), pattern)
    assert asset is not None
    assert asset["name"] == expected


def test_cudart_pack_is_matched_per_cuda_major() -> None:
    """The 12.x binary must never pick up the CUDA 13 runtime (invariant #11)."""
    cuda12 = get_backend("cuda12")
    cuda13 = get_backend("cuda13")
    assert cuda12 is not None and cuda13 is not None
    assert cuda12.cudart_pattern is not None
    assert cuda13.cudart_pattern is not None

    pack12 = match_asset(_release_assets(), cuda12.cudart_pattern)
    pack13 = match_asset(_release_assets(), cuda13.cudart_pattern)
    assert pack12 is not None and pack12["name"].endswith("cuda-12.4-x64.zip")
    assert pack13 is not None and pack13["name"].endswith("cuda-13.3-x64.zip")


def test_cudart_pack_never_matches_the_binary_archive() -> None:
    cuda12 = get_backend("cuda12")
    assert cuda12 is not None and cuda12.cudart_pattern is not None
    binary_pattern = cuda12.asset_pattern("win32", "x64")
    assert binary_pattern is not None
    binary = match_asset(_release_assets(), binary_pattern)
    assert binary is not None
    assert not binary["name"].startswith("cudart-")


@pytest.mark.parametrize(
    ("platform", "arch", "expected"),
    [
        ("win32", "x64", "llama-swap_248_windows_amd64.zip"),
        ("linux", "x64", "llama-swap_248_linux_amd64.tar.gz"),
        ("linux", "arm64", "llama-swap_248_linux_arm64.tar.gz"),
        ("darwin", "arm64", "llama-swap_248_darwin_arm64.tar.gz"),
    ],
)
def test_llama_swap_asset_per_platform(platform: str, arch: str, expected: str) -> None:
    asset = match_asset(_swap_assets(), llama_swap_asset_pattern(platform, arch))
    assert asset is not None
    assert asset["name"] == expected


def test_unavailable_backend_has_no_pattern() -> None:
    unavailable = "metal" if _PLATFORM != "darwin" else "cuda12"
    assert backend_asset_pattern(unavailable) is None


def test_match_asset_no_match() -> None:
    assert match_asset(_release_assets(), r"nonexistent.*\.zip") is None


# ─── Archive extraction ───────────────────────────────────────────────────


def _make_zip(path: Path, entries: dict[str, bytes], unix_mode: int = 0) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in entries.items():
            info = zipfile.ZipInfo(name)
            if unix_mode:
                info.create_system = 3
                info.external_attr = unix_mode << 16
            zf.writestr(info, data)


def test_wipe_and_extract_strips_wrapping_folder(tmp_path: Path) -> None:
    archive = tmp_path / "release.zip"
    _make_zip(
        archive,
        {
            "llama-b10331/llama-server.exe": b"server",
            "llama-b10331/ggml.dll": b"lib",
        },
    )
    dest = tmp_path / "vulkan"
    wipe_and_extract(archive, dest)
    assert (dest / "llama-server.exe").read_bytes() == b"server"
    assert (dest / "ggml.dll").exists()


def test_wipe_and_extract_keeps_flat_archives_flat(tmp_path: Path) -> None:
    archive = tmp_path / "swap.zip"
    _make_zip(archive, {"llama-swap.exe": b"swap", "README.md": b"docs"})
    dest = tmp_path / "llama-swap"
    wipe_and_extract(archive, dest)
    assert (dest / "llama-swap.exe").exists()


def test_wipe_and_extract_removes_stale_files(tmp_path: Path) -> None:
    dest = tmp_path / "vulkan"
    dest.mkdir()
    (dest / "stale.dll").write_text("old")
    archive = tmp_path / "release.zip"
    _make_zip(archive, {"pkg/llama-server.exe": b"fresh"})

    wipe_and_extract(archive, dest)
    assert (dest / "llama-server.exe").exists()
    assert not (dest / "stale.dll").exists()


def test_wipe_and_extract_rejects_paths_outside_destination(tmp_path: Path) -> None:
    archive = tmp_path / "evil.zip"
    _make_zip(archive, {"../escape.txt": b"nope", "keep.txt": b"ok"})
    with pytest.raises(PrebuiltError, match="outside destination"):
        wipe_and_extract(archive, tmp_path / "dest")
    assert not (tmp_path / "escape.txt").exists()


def _make_tar(path: Path, build: Any) -> None:
    with tarfile.open(path, "w:gz") as tf:
        build(tf)


def _add_tar_file(tf: tarfile.TarFile, name: str, data: bytes, mode: int) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mode = mode
    tf.addfile(info, io.BytesIO(data))


def test_tar_extraction_preserves_executable_bit(tmp_path: Path) -> None:
    """A downloaded llama-server must be runnable on Linux/macOS."""
    archive = tmp_path / "release.tar.gz"

    def build(tf: tarfile.TarFile) -> None:
        _add_tar_file(tf, "llama-b10331/llama-server", b"ELF", 0o755)
        _add_tar_file(tf, "llama-b10331/LICENSE", b"text", 0o644)

    _make_tar(archive, build)
    dest = tmp_path / "cpu"
    wipe_and_extract(archive, dest)

    server = dest / "llama-server"
    assert server.exists()
    if sys.platform != "win32":
        # wipe_and_extract marks extension-less program files executable so
        # the downloaded llama-server is runnable on Linux/macOS.
        assert os.access(server, os.X_OK)
        assert stat.S_IMODE(server.stat().st_mode) & 0o111
        # LICENSE (also extension-less) is made executable by the same step;
        # assert only that it was extracted, not a specific mode.
        assert (dest / "LICENSE").exists()


def test_tar_extraction_keeps_shared_library_symlinks(tmp_path: Path) -> None:
    """libllama.so -> libllama.so.0 chains must survive extraction."""
    archive = tmp_path / "release.tar.gz"

    def build(tf: tarfile.TarFile) -> None:
        _add_tar_file(tf, "llama-b10331/libllama.so.0.0.1", b"lib", 0o755)
        link = tarfile.TarInfo("llama-b10331/libllama.so.0")
        link.type = tarfile.SYMTYPE
        link.linkname = "libllama.so.0.0.1"
        tf.addfile(link)
        _add_tar_file(tf, "llama-b10331/llama-server", b"ELF", 0o755)

    _make_tar(archive, build)
    dest = tmp_path / "cpu"
    wipe_and_extract(archive, dest)

    linked = dest / "libllama.so.0"
    assert linked.exists()  # resolves to the real library
    assert linked.read_bytes() == b"lib"


def test_zip_extraction_applies_unix_mode(tmp_path: Path) -> None:
    archive = tmp_path / "release.zip"
    _make_zip(archive, {"llama-swap": b"go-binary"}, unix_mode=0o755)
    dest = tmp_path / "llama-swap"
    wipe_and_extract(archive, dest)
    if sys.platform != "win32":
        assert os.access(dest / "llama-swap", os.X_OK)


def test_extensionless_files_get_executable_bit(tmp_path: Path) -> None:
    """Archives without mode bits still yield runnable programs on POSIX."""
    archive = tmp_path / "release.zip"
    _make_zip(archive, {"llama-server": b"ELF"})
    dest = tmp_path / "cpu"
    wipe_and_extract(archive, dest)
    if sys.platform != "win32":
        assert os.access(dest / "llama-server", os.X_OK)


def test_unsupported_archive_format(tmp_path: Path) -> None:
    bogus = tmp_path / "release.7z"
    bogus.write_bytes(b"not an archive")
    with pytest.raises(PrebuiltError, match="Unsupported archive"):
        wipe_and_extract(bogus, tmp_path / "dest")


# ─── Download cache ───────────────────────────────────────────────────────


def test_cached_download_reuses_matching_size(tmp_path: Path) -> None:
    cache_dir = tmp_path / "downloads"
    cache_dir.mkdir()
    existing = cache_dir / "existing.zip"
    existing.write_bytes(b"cached content!")
    result = cached_download("https://example.com/existing.zip", 15, cache_dir)
    assert result == existing


def test_cached_download_redownloads_on_size_mismatch(tmp_path: Path) -> None:
    cache_dir = tmp_path / "downloads"
    cache_dir.mkdir()
    (cache_dir / "asset.zip").write_bytes(b"truncated")

    def fake_download(url: str, dest: Path, token: Any = None, **kw: Any) -> None:
        dest.write_bytes(b"0123456789")

    with patch("llamagui.backends.prebuilt.download_file", side_effect=fake_download):
        result = cached_download("https://example.com/asset.zip", 10, cache_dir)
    assert result.read_bytes() == b"0123456789"


# ─── Install ──────────────────────────────────────────────────────────────


@patch("llamagui.backends.prebuilt.latest_release")
@patch("llamagui.backends.prebuilt.cached_download")
@patch("llamagui.backends.prebuilt.wipe_and_extract")
def test_install_backend_writes_marker(
    mock_wipe: MagicMock,
    mock_dl: MagicMock,
    mock_release: MagicMock,
    tmp_path: Path,
) -> None:
    mock_release.return_value = fake_release()
    mock_dl.return_value = tmp_path / "downloads" / "asset"
    backend = "metal" if _PLATFORM == "darwin" else "cpu"

    result = install_backend(
        backend,
        tmp_path / "managed",
        tmp_path / "downloads",
        force=True,
        bundle_cuda_runtime="never",
    )
    assert result["status"] == "ok"
    assert result["version"] == "b10331"
    assert installed_backends(tmp_path / "managed") == {backend: "b10331"}


@patch("llamagui.backends.prebuilt.latest_release")
def test_install_backend_is_idempotent(mock_release: MagicMock, tmp_path: Path) -> None:
    mock_release.return_value = fake_release()
    managed = tmp_path / "managed"
    backend = "metal" if _PLATFORM == "darwin" else "cpu"
    (managed / backend).mkdir(parents=True)
    (managed / backend / ".version").write_text(
        "b10331\nmanaged-prebuilt\n", encoding="utf-8"
    )

    result = install_backend(backend, managed, tmp_path / "downloads")
    assert result["status"] == "skipped"


def test_install_unknown_backend(tmp_path: Path) -> None:
    with pytest.raises(PrebuiltError, match="Unknown backend"):
        install_backend("nonexistent", tmp_path / "managed", tmp_path / "downloads")


def test_install_backend_unavailable_on_this_platform(tmp_path: Path) -> None:
    unavailable = "metal" if _PLATFORM != "darwin" else "cuda12"
    with pytest.raises(PrebuiltUnavailable):
        install_backend(unavailable, tmp_path / "managed", tmp_path / "downloads")


@pytest.mark.skipif(not _IS_WINDOWS, reason="cudart packs are Windows-only")
@patch("llamagui.backends.prebuilt.latest_release")
@patch("llamagui.backends.prebuilt.cached_download")
@patch("llamagui.backends.prebuilt.wipe_and_extract")
@patch("llamagui.backends.prebuilt._extract_cudart")
def test_cuda12_fetches_its_runtime_pack(
    mock_cudart: MagicMock,
    mock_wipe: MagicMock,
    mock_dl: MagicMock,
    mock_release: MagicMock,
    tmp_path: Path,
) -> None:
    mock_release.return_value = fake_release()
    mock_dl.return_value = tmp_path / "downloads" / "asset"

    install_backend("cuda12", tmp_path / "managed", tmp_path / "downloads", force=True)
    assert mock_cudart.called
    downloaded = [call.args[0] for call in mock_dl.call_args_list]
    assert any("cudart" in url for url in downloaded)


@pytest.mark.skipif(not _IS_WINDOWS, reason="cudart packs are Windows-only")
@patch("llamagui.backends.prebuilt.latest_release")
@patch("llamagui.backends.prebuilt.cached_download")
@patch("llamagui.backends.prebuilt.wipe_and_extract")
@patch("llamagui.backends.prebuilt._extract_cudart")
def test_cuda13_runtime_pack_is_opt_in(
    mock_cudart: MagicMock,
    mock_wipe: MagicMock,
    mock_dl: MagicMock,
    mock_release: MagicMock,
    tmp_path: Path,
) -> None:
    mock_release.return_value = fake_release()
    mock_dl.return_value = tmp_path / "downloads" / "asset"

    install_backend(
        "cuda13",
        tmp_path / "managed",
        tmp_path / "downloads",
        force=True,
        bundle_cuda_runtime="auto",
    )
    assert not mock_cudart.called

    install_backend(
        "cuda13",
        tmp_path / "managed",
        tmp_path / "downloads",
        force=True,
        bundle_cuda_runtime="always",
    )
    assert mock_cudart.called


def test_arch_key_is_known() -> None:
    assert _ARCH in ("x64", "arm64")


def test_download_emits_progress_without_content_length(tmp_path: Path) -> None:
    # Regression for E1: when the server omits Content-Length the progress bar
    # must still advance (total == 0 -> indeterminate) instead of freezing.
    captured: list[tuple[object, ...]] = []

    def _record(*args: object) -> None:
        captured.append(args)

    class _Resp:
        headers: ClassVar[dict[str, str]] = {}  # no content-length

        def raise_for_status(self) -> None:
            pass

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

        def iter_bytes(self, chunk_size: int = 65536) -> object:
            yield b"hello "
            yield b"world"

    with (
        patch("llamagui.backends.prebuilt.httpx.stream", return_value=_Resp()),
        patch("llamagui.backends.prebuilt.emit_progress", side_effect=_record),
    ):
        download_file("https://example.com/x", tmp_path / "out.bin")

    assert (tmp_path / "out.bin").read_bytes() == b"hello world"
    assert captured, "expected progress events even without Content-Length"
    assert all(c[2] == 0 for c in captured)  # total is unknown (0)
    assert captured[-1][1] == len(b"hello world")  # final done == full size
