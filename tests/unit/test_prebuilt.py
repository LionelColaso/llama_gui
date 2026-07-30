from __future__ import annotations

import platform as _platform
import zipfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from llamagui.backends.prebuilt import (
    BACKEND_GLOBS,
    LLAMA_SWAP_PATTERN,
    cached_download,
    install_backend,
    match_asset,
    wipe_and_extract,
)

_SYSTEM = _platform.system().lower()
_MACHINE = _platform.machine().lower()
_IS_ARM64 = _MACHINE in ("arm64", "aarch64")
_IS_WINDOWS = _SYSTEM == "windows"
_IS_MACOS = _SYSTEM == "darwin"
_IS_LINUX = _SYSTEM == "linux"


def _platform_asset_name(base: str, ext: str) -> str:
    """Return a platform-appropriate asset name for the test suite.

    Matches the naming conventions used by ``_backend_asset_pattern`` in
    prebuilt.py so that ``match_asset`` exercises the real pattern on every OS.
    """
    if _SYSTEM == "windows":
        return f"llama-b10189-bin-win-{base}-x64.{ext}"
    if _SYSTEM == "darwin":
        arch = "arm64" if _IS_ARM64 else "x64"
        return f"llama-b10189-bin-macos-{arch}.{ext}"
    # Linux
    arch = "arm64" if _IS_ARM64 else "x64"
    return f"llama-b10189-bin-ubuntu-{base}-{arch}.{ext}"


def _cudart_asset_name() -> str:
    """Return a cudart asset name for the current platform."""
    return (
        "cudart-12.4-runtime-win64.zip"
        if _SYSTEM == "windows"
        else "cudart-12.4-runtime-linux64.tar.gz"
    )


def _llama_swap_asset_name() -> str:
    """Return a llama-swap asset name for the current platform."""
    if _SYSTEM == "windows":
        return "llama-swap_v0.8.0_windows_amd64.zip"
    if _SYSTEM == "darwin":
        arch = "arm64" if _IS_ARM64 else "amd64"
        return f"llama-swap_v0.8.0_darwin_{arch}.tar.gz"
    return "llama-swap_v0.8.0_linux_amd64.tar.gz"


def fake_assets() -> list[dict[str, Any]]:
    return [
        {
            "name": _platform_asset_name(
                "vulkan", "zip" if _SYSTEM == "windows" else "tar.gz"
            ),
            "size": 100000,
            "browser_download_url": "https://example.com/vulkan.zip",
        },
        {
            "name": _platform_asset_name(
                "cuda-12.4", "zip" if _SYSTEM == "windows" else "tar.gz"
            ),
            "size": 200000,
            "browser_download_url": "https://example.com/cuda12.zip",
        },
        {
            "name": _platform_asset_name(
                "cuda-13.3", "zip" if _SYSTEM == "windows" else "tar.gz"
            ),
            "size": 250000,
            "browser_download_url": "https://example.com/cuda13.zip",
        },
        {
            "name": _cudart_asset_name(),
            "size": 50000,
            "browser_download_url": "https://example.com/cudart.zip",
        },
    ]


def fake_release() -> dict[str, Any]:
    return {
        "tag_name": "b10189",
        "assets": fake_assets(),
    }


def test_match_asset_vulkan() -> None:
    asset = match_asset(fake_assets(), BACKEND_GLOBS["vulkan"]["pattern"])
    assert asset is not None
    assert "vulkan" in asset["name"]


@pytest.mark.skipif(not _IS_WINDOWS, reason="CUDA prebuilt assets are Windows-only")
def test_match_asset_cuda12() -> None:
    asset = match_asset(fake_assets(), BACKEND_GLOBS["cuda12"]["pattern"])
    assert asset is not None
    assert "cuda-12" in asset["name"]


@pytest.mark.skipif(not _IS_WINDOWS, reason="CUDA prebuilt assets are Windows-only")
def test_match_asset_cuda13() -> None:
    asset = match_asset(fake_assets(), BACKEND_GLOBS["cuda13"]["pattern"])
    assert asset is not None
    assert "cuda-13" in asset["name"]


def test_match_asset_llama_swap() -> None:
    assets = [
        {
            "name": _llama_swap_asset_name(),
            "size": 50000,
            "browser_download_url": "https://example.com/swap.zip",
        },
    ]
    asset = match_asset(assets, LLAMA_SWAP_PATTERN)
    assert asset is not None
    assert "llama-swap" in asset["name"]


def test_match_asset_no_match() -> None:
    asset = match_asset(fake_assets(), r"nonexistent.*\.zip")
    assert asset is None


def test_wipe_and_extract(tmp_path: Path) -> None:
    zip_path = tmp_path / "test.zip"
    extract_dir = tmp_path / "extracted"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("llama-b10189/bin/llama-server.exe", b"binary content")
        zf.writestr("llama-b10189/bin/llama-cli.exe", b"cli content")

    wipe_and_extract(zip_path, extract_dir)
    assert (extract_dir / "bin" / "llama-server.exe").exists()
    assert (extract_dir / "bin" / "llama-cli.exe").exists()


def test_wipe_and_extract_replaces(tmp_path: Path) -> None:
    zip_path = tmp_path / "test.zip"
    extract_dir = tmp_path / "extracted"
    extract_dir.mkdir(parents=True)
    (extract_dir / "stale.txt").write_text("stale")

    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("pkg/llama-server.exe", b"fresh binary")

    wipe_and_extract(zip_path, extract_dir)
    assert (extract_dir / "llama-server.exe").exists()
    assert not (extract_dir / "stale.txt").exists()


def test_cached_download(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cache_dir = tmp_path / "downloads"
    cache_dir.mkdir()
    existing = cache_dir / "existing.zip"
    existing.write_bytes(b"cached content!")
    result = cached_download("https://example.com/existing.zip", 15, cache_dir)
    assert result == existing


@patch("llamagui.backends.prebuilt.latest_release")
@patch("llamagui.backends.prebuilt.cached_download")
@patch("llamagui.backends.prebuilt.wipe_and_extract")
def test_install_backend(
    mock_wipe: MagicMock,
    mock_dl: MagicMock,
    mock_release: MagicMock,
    tmp_path: Path,
) -> None:
    mock_release.return_value = fake_release()
    mock_dl.return_value = tmp_path / "downloads" / "vulkan.zip"
    (tmp_path / "managed" / "vulkan").mkdir(parents=True)

    result = install_backend(
        "vulkan",
        tmp_path / "managed",
        tmp_path / "downloads",
        force=True,
    )
    assert result["status"] == "ok"
    assert result["version"] == "b10189"


@patch("llamagui.backends.prebuilt.latest_release")
def test_install_backend_skipped_on_marker(
    mock_release: MagicMock,
    tmp_path: Path,
) -> None:
    mock_release.return_value = fake_release()
    managed = tmp_path / "managed"
    (managed / "vulkan").mkdir(parents=True)
    (managed / "vulkan" / ".version").write_text(
        "b10189\nmanaged-prebuilt\n", encoding="utf-8"
    )

    result = install_backend("vulkan", managed, tmp_path / "downloads")
    assert result["status"] == "skipped"


def test_install_unknown_backend(tmp_path: Path) -> None:
    from llamagui.backends.prebuilt import PrebuiltError

    with pytest.raises(PrebuiltError, match="Unknown backend"):
        install_backend("nonexistent", tmp_path / "managed", tmp_path / "downloads")
