from __future__ import annotations

import io
import os
import stat
import sys
import tarfile
import threading
import zipfile
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, ClassVar, Self
from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.backends.catalogue import get_backend
from app.backends.prebuilt import (
    PrebuiltError,
    PrebuiltUnavailable,
    _is_readable_archive,
    backend_asset_pattern,
    cached_download,
    clear_release_cache,
    download_file,
    emit_progress,
    failure_hint,
    get_progress_callback,
    install_backend,
    installed_backends,
    latest_release,
    latest_versions,
    list_assets,
    match_asset,
    set_progress_callback,
    wipe_and_extract,
    write_version_marker,
)
from app.paths import arch_key, platform_key

_PLATFORM = platform_key()
_ARCH = arch_key()
_IS_WINDOWS = _PLATFORM == "win32"


# ─── Asset naming (real release names, verified against b10331 / v248) ────


def _release_assets() -> list[dict[str, Any]]:
    """Assets named exactly as ggml-org/llama.cpp publishes them.

    Mirrors the real per-architecture coverage of a nightly (b11146): CUDA 13
    ships Windows arm64, Vulkan and CUDA 12 do not.
    """
    names = [
        "cudart-llama-bin-win-cuda-12.4-x64.zip",
        "cudart-llama-bin-win-cuda-13.3-arm64.zip",
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
        "llama-b10331-bin-win-cuda-13.3-arm64.zip",
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


def fake_release() -> dict[str, Any]:
    return {"tag_name": "b10331", "assets": _release_assets()}


@pytest.mark.parametrize(
    ("platform", "arch", "backend", "expected"),
    [
        ("win32", "x64", "vulkan", "llama-b10331-bin-win-vulkan-x64.zip"),
        ("win32", "x64", "cuda12", "llama-b10331-bin-win-cuda-12.4-x64.zip"),
        ("win32", "x64", "cuda13", "llama-b10331-bin-win-cuda-13.3-x64.zip"),
        ("win32", "arm64", "cpu", "llama-b10331-bin-win-cpu-arm64.zip"),
        ("win32", "arm64", "cuda13", "llama-b10331-bin-win-cuda-13.3-arm64.zip"),
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


@pytest.mark.parametrize("backend", ["vulkan", "cuda12"])
def test_no_native_windows_arm64_build_is_not_offered(backend: str) -> None:
    """llama.cpp ships no Windows arm64 Vulkan / CUDA 12 asset.

    The catalogue must report these unavailable on Windows-on-ARM rather than
    claiming a native prebuilt it cannot deliver. (CUDA 13 *does* have an arm64
    asset, so that row stays available — see the test above.)
    """
    entry = get_backend(backend)
    assert entry is not None
    pattern = entry.asset_pattern("win32", "arm64")
    assert pattern is not None
    assert match_asset(_release_assets(), pattern) is None


def _cudart_pattern_for(backend: str, arch: str) -> str:
    """The arch-resolved CUDA runtime pack regex for *backend*."""
    entry = get_backend(backend)
    assert entry is not None
    pattern = entry.cudart_asset_pattern(arch)
    assert pattern is not None
    return pattern


def test_cudart_pack_is_matched_per_cuda_major() -> None:
    """The 12.x binary must never pick up the CUDA 13 runtime (invariant #11)."""
    cuda12 = get_backend("cuda12")
    cuda13 = get_backend("cuda13")
    assert cuda12 is not None and cuda13 is not None
    assert cuda12.cudart_pattern is not None
    assert cuda13.cudart_pattern is not None

    pack12 = match_asset(_release_assets(), _cudart_pattern_for("cuda12", "x64"))
    pack13 = match_asset(_release_assets(), _cudart_pattern_for("cuda13", "x64"))
    assert pack12 is not None and pack12["name"].endswith("cuda-12.4-x64.zip")
    assert pack13 is not None and pack13["name"].endswith("cuda-13.3-x64.zip")


def test_cudart_pack_follows_the_running_arch() -> None:
    """The arm64 CUDA 13 pack must not be served to an x64 install (and vice versa)."""
    arm64 = match_asset(_release_assets(), _cudart_pattern_for("cuda13", "arm64"))
    assert arm64 is not None
    assert arm64["name"].endswith("cuda-13.3-arm64.zip")

    # CUDA 12 is x64-only upstream, so an arm64 install must find nothing.
    assert (
        match_asset(_release_assets(), _cudart_pattern_for("cuda12", "arm64")) is None
    )


def test_cudart_pack_never_matches_the_binary_archive() -> None:
    cuda12 = get_backend("cuda12")
    assert cuda12 is not None and cuda12.cudart_pattern is not None
    binary_pattern = cuda12.asset_pattern("win32", "x64")
    assert binary_pattern is not None
    binary = match_asset(_release_assets(), binary_pattern)
    assert binary is not None
    assert not binary["name"].startswith("cudart-")


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
    archive = tmp_path / "release.zip"
    _make_zip(archive, {"tool.exe": b"tool", "README.md": b"docs"})
    dest = tmp_path / "flat"
    wipe_and_extract(archive, dest)
    assert (dest / "tool.exe").exists()


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


@contextmanager
def _recording_progress() -> Generator[list[tuple[str, int, int]]]:
    """Record the ``(phase, done, total)`` ticks of the wrapped call."""
    events: list[tuple[str, int, int]] = []

    def _cb(done: int, total: int, phase: str, overall: float | None) -> None:
        events.append((phase, done, total))

    set_progress_callback(_cb)
    try:
        yield events
    finally:
        set_progress_callback(None)


def _no_extractfile(self: tarfile.TarFile, member: tarfile.TarInfo) -> None:
    """A member whose file object cannot be read (defensive skip)."""


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
    _make_zip(archive, {"llama-server": b"ELF"}, unix_mode=0o755)
    dest = tmp_path / "cpu"
    wipe_and_extract(archive, dest)
    if sys.platform != "win32":
        assert os.access(dest / "llama-server", os.X_OK)


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


def test_wipe_and_extract_emits_byte_progress(tmp_path: Path) -> None:
    # Regression: extraction must report real, moving byte progress (not a
    # frozen bar) and map it into the caller's overall window.
    import app.backends.prebuilt as pb

    archive = tmp_path / "release.zip"
    _make_zip(
        archive,
        {
            "a.dll": b"x" * 1000,
            "b.dll": b"y" * 2000,
            "c.dll": b"z" * 3000,
        },
    )
    dest = tmp_path / "vulkan"
    events: list[tuple[str, int, int, float]] = []

    def _cb(done: int, total: int, phase: str, overall: float | None) -> None:
        events.append((phase, done, total, overall if overall is not None else 0.0))

    pb.set_progress_callback(_cb)
    try:
        pb.wipe_and_extract(
            archive, dest, component="vulkan", overall_range=(0.5, 0.75)
        )
    finally:
        pb.set_progress_callback(None)

    assert (dest / "a.dll").read_bytes() == b"x" * 1000
    assert events, "expected extract progress events"
    assert all(e[0] == "extract" for e in events)
    assert all(e[2] == 6000 for e in events)  # total = 1000 + 2000 + 3000
    assert all(0.5 <= e[3] <= 0.75 for e in events)  # within the window
    assert events[-1][1] == 6000  # finished at the byte total
    assert events[-1][3] == 0.75  # reached the window's top


def _zip_bytes(entries: dict[str, bytes]) -> bytes:
    """A real zip archive in memory, so cache-hit checks exercise a real file."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


# ─── Download cache ───────────────────────────────────────────────────────


def test_cached_download_reuses_matching_size(tmp_path: Path) -> None:
    """A cache hit needs the right size *and* an archive that opens."""
    cache_dir = tmp_path / "downloads"
    cache_dir.mkdir()
    existing = cache_dir / "existing.zip"
    payload = _zip_bytes({"llama-server": b"cached content!"})
    existing.write_bytes(payload)

    downloaded: list[str] = []

    def _record(url: str, *args: Any, **kwargs: Any) -> None:
        downloaded.append(url)

    with patch("app.backends.prebuilt.download_file", side_effect=_record):
        result = cached_download(
            "https://example.com/existing.zip", len(payload), cache_dir
        )

    assert result == existing
    assert downloaded == [], "a valid cache hit must not re-download"


def test_cached_download_redownloads_a_corrupt_cache_hit(tmp_path: Path) -> None:
    """Regression: size alone accepted a corrupt file as a cache hit.

    A file of the right length that is not a readable archive was returned as
    a hit and only failed later, during extraction. It must now be discarded
    and fetched again. The corrupt file is deliberately the *same size* as
    the good one, since size is exactly what the old check trusted.
    """
    cache_dir = tmp_path / "downloads"
    cache_dir.mkdir()
    fresh = _zip_bytes({"llama-server": b"fresh"})

    corrupt = cache_dir / "asset.zip"
    corrupt.write_bytes(b"z" * len(fresh))  # right length, not a zip
    assert corrupt.stat().st_size == len(fresh)

    def fake_download(url: str, dest: Path, token: Any = None, **kw: Any) -> None:
        dest.write_bytes(fresh)

    with patch("app.backends.prebuilt.download_file", side_effect=fake_download):
        result = cached_download("https://example.com/asset.zip", len(fresh), cache_dir)

    assert result.read_bytes() == fresh
    assert zipfile.is_zipfile(result), "a corrupt cache hit must be re-downloaded"


def test_cached_download_redownloads_a_corrupt_tar_gz(tmp_path: Path) -> None:
    """Same for tar.gz, where a broken gzip stream raises EOFError.

    The cached file keeps the good archive's length so that only its
    *contents* differ -- otherwise the old size check would reject it anyway
    and the test would prove nothing.
    """
    cache_dir = tmp_path / "downloads"
    cache_dir.mkdir()
    good = tmp_path / "good.tar.gz"
    with tarfile.open(good, "w:gz") as tf:
        info = tarfile.TarInfo("llama-server")
        info.size = 5
        tf.addfile(info, io.BytesIO(b"hello"))
    full = good.read_bytes()

    # Same length as the good archive, but the gzip payload is destroyed.
    corrupt_bytes = bytearray(full)
    for i in range(len(full) // 2, len(full)):
        corrupt_bytes[i] ^= 0xFF
    corrupt = cache_dir / "asset.tar.gz"
    corrupt.write_bytes(bytes(corrupt_bytes))
    assert corrupt.stat().st_size == len(full)

    def fake_download(url: str, dest: Path, token: Any = None, **kw: Any) -> None:
        dest.write_bytes(full)

    with patch("app.backends.prebuilt.download_file", side_effect=fake_download):
        result = cached_download(
            "https://example.com/asset.tar.gz", len(full), cache_dir
        )

    assert result.read_bytes() == full


# ─── Install ──────────────────────────────────────────────────────────────


def test_cached_download_redownloads_on_size_mismatch(tmp_path: Path) -> None:
    cache_dir = tmp_path / "downloads"
    cache_dir.mkdir()
    (cache_dir / "asset.zip").write_bytes(b"truncated")

    def fake_download(url: str, dest: Path, token: Any = None, **kw: Any) -> None:
        dest.write_bytes(b"0123456789")

    with patch("app.backends.prebuilt.download_file", side_effect=fake_download):
        result = cached_download("https://example.com/asset.zip", 10, cache_dir)
    assert result.read_bytes() == b"0123456789"


# ─── Install ──────────────────────────────────────────────────────────────


@patch("app.backends.prebuilt.latest_release")
@patch("app.backends.prebuilt.cached_download")
@patch("app.backends.prebuilt.wipe_and_extract")
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


@patch("app.backends.prebuilt.latest_release")
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


@pytest.mark.skipif(
    not _IS_WINDOWS,
    reason=(
        "cuda12's prebuilt and cudart patterns are Windows-only, so "
        "elsewhere install_backend raises PrebuiltUnavailable before "
        "the mocked cudart download/extract path"
    ),
)
@patch("app.backends.prebuilt.latest_release")
@patch("app.backends.prebuilt.cached_download")
@patch("app.backends.prebuilt.wipe_and_extract")
@patch("app.backends.prebuilt._extract_cudart")
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


@pytest.mark.skipif(
    not _IS_WINDOWS,
    reason=(
        "cuda13's prebuilt and cudart patterns are Windows-only, so "
        "elsewhere install_backend raises PrebuiltUnavailable before "
        "the mocked opt-in cudart path"
    ),
)
@patch("app.backends.prebuilt.latest_release")
@patch("app.backends.prebuilt.cached_download")
@patch("app.backends.prebuilt.wipe_and_extract")
@patch("app.backends.prebuilt._extract_cudart")
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
        status_code: ClassVar[int] = 200  # full (non-range) 200 response
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
        patch("app.backends.prebuilt.httpx.stream", return_value=_Resp()),
        patch("app.backends.prebuilt.emit_progress", side_effect=_record),
    ):
        download_file("https://example.com/x", tmp_path / "out.bin")

    assert (tmp_path / "out.bin").read_bytes() == b"hello world"
    assert captured, "expected progress events even without Content-Length"
    # emit_progress is mocked, so it forwards its full (component, done, total,
    # phase, overall) tuple. total is unknown (0); overall is None without a
    # Content-Length; the final tick's done equals the full payload size.
    assert all(c[2] == 0 for c in captured)  # total is unknown (0)
    assert all(c[4] is None for c in captured)  # no overall without Content-Length
    assert captured[-1][1] == len(b"hello world")  # final done == full size


# ─── Release metadata caching ──────────────────────────────────────────────


class _FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict[str, Any]:
        return self._payload


@pytest.fixture(autouse=True)
def _clear_release_cache() -> Iterator[None]:
    clear_release_cache()
    yield
    clear_release_cache()


def test_release_is_cached_within_the_ttl() -> None:
    """One install run asks once per backend; it must not re-fetch each time."""
    calls: list[str] = []

    def _get(url: str, **kwargs: object) -> _FakeResponse:
        calls.append(url)
        return _FakeResponse({"tag_name": "b1"})

    with patch("app.backends.prebuilt.httpx.get", side_effect=_get):
        assert latest_release("o/r")["tag_name"] == "b1"
        assert latest_release("o/r")["tag_name"] == "b1"
        assert latest_release("o/r")["tag_name"] == "b1"

    assert len(calls) == 1, "release metadata was re-fetched inside the TTL"


def test_release_refetches_after_the_ttl() -> None:
    """Regression: the cache used to live for the whole process, so a
    long-running GUI could never observe a newly published release."""

    calls: list[str] = []

    def _get(url: str, **kwargs: object) -> _FakeResponse:
        calls.append(url)
        return _FakeResponse({"tag_name": f"b{len(calls)}"})

    with (
        patch("app.backends.prebuilt.httpx.get", side_effect=_get),
        patch("app.backends.prebuilt.RELEASE_CACHE_TTL", 0.0),
    ):
        assert latest_release("o/r")["tag_name"] == "b1"
        assert latest_release("o/r")["tag_name"] == "b2"

    assert len(calls) == 2, "an expired entry must be re-fetched"


def test_clear_release_cache_forces_a_refetch() -> None:
    calls: list[str] = []

    def _get(url: str, **kwargs: object) -> _FakeResponse:
        calls.append(url)
        return _FakeResponse({"tag_name": f"b{len(calls)}"})

    with patch("app.backends.prebuilt.httpx.get", side_effect=_get):
        assert latest_release("o/r")["tag_name"] == "b1"
        clear_release_cache()
        assert latest_release("o/r")["tag_name"] == "b2"

    assert len(calls) == 2


def test_cache_is_keyed_per_repo_and_token() -> None:
    """A different repo (or token) must not read another's cached entry."""
    calls: list[str] = []

    def _get(url: str, **kwargs: object) -> _FakeResponse:
        # .../repos/<owner>/<name>/releases/latest -> "<owner>/<name>"
        repo = url.split("/repos/", 1)[-1].split("/releases/", 1)[0]
        calls.append(repo)
        return _FakeResponse({"tag_name": repo})

    with patch("app.backends.prebuilt.httpx.get", side_effect=_get):
        assert latest_release("o/one")["tag_name"] == "o/one"
        assert latest_release("o/two")["tag_name"] == "o/two"
        assert latest_release("o/one", "tok")["tag_name"] == "o/one"
        # ...and each is still served from cache on a repeat.
        assert latest_release("o/one")["tag_name"] == "o/one"
        assert latest_release("o/two")["tag_name"] == "o/two"

    # three distinct keys: (o/one, None), (o/two, None), (o/one, "tok")
    assert calls == ["o/one", "o/two", "o/one"]


# ─── Stub-release resolution ──────────────────────────────────────────────


class _MarkerResponse(_FakeResponse):
    """A plain-text body (the marker asset), not a JSON payload."""

    def __init__(self, text: str) -> None:
        super().__init__({})
        self.text = text


def _stub_release(tag: str = "b11146") -> dict[str, Any]:
    """What ``/releases/latest`` actually returns for ggml-org/llama.cpp."""
    return {
        "tag_name": "v0.5.0",
        "assets": [
            {
                "name": "nightly-tag.txt",
                "size": 5,
                "browser_download_url": "https://example.com/nightly-tag.txt",
            }
        ],
        "_nightly": tag,
    }


def test_stub_release_resolves_to_the_nightly_with_real_assets() -> None:
    """Regression: ``/releases/latest`` is an asset-less stub, not the build.

    Taking it literally made every install fail with "No asset matching ... in
    release v0.5.0", because the binaries hang off the nightly tag instead.
    """
    calls: list[str] = []

    def _get(url: str, **kwargs: object) -> _FakeResponse:
        calls.append(url)
        if url.endswith("/releases/latest"):
            return _FakeResponse(_stub_release())
        if url.endswith("/releases/tags/b11146"):
            return _FakeResponse(fake_release())
        if "nightly-tag.txt" in url:
            return _MarkerResponse("b11146\n")
        raise AssertionError(f"unexpected URL: {url}")

    with patch("app.backends.prebuilt.httpx.get", side_effect=_get):
        release = latest_release("ggml-org/llama.cpp")

    assert release["tag_name"] == "b10331"
    assert (
        match_asset(release["assets"], r"llama-.*-bin-win-vulkan-x64\.zip") is not None
    )
    assert calls[0].endswith("/releases/latest")
    assert "nightly-tag.txt" in calls[1]
    assert calls[2].endswith("/releases/tags/b11146")


def test_stub_marker_download_follows_redirects() -> None:
    """The marker URL 302s to a CDN host, so redirects must be followed."""
    seen: list[bool] = []

    def _get(url: str, **kwargs: object) -> _FakeResponse:
        if url.endswith("/releases/latest"):
            return _FakeResponse(_stub_release())
        if url.endswith("/releases/tags/b11146"):
            return _FakeResponse(fake_release())
        seen.append(bool(kwargs.get("follow_redirects")))
        return _MarkerResponse("b11146")

    with patch("app.backends.prebuilt.httpx.get", side_effect=_get):
        latest_release("ggml-org/llama.cpp")

    assert seen == [True], "marker fetch must follow the CDN redirect"


def test_release_with_assets_is_used_directly() -> None:
    """No marker round-trip when the latest release already has builds."""
    calls: list[str] = []

    def _get(url: str, **kwargs: object) -> _FakeResponse:
        calls.append(url)
        return _FakeResponse(fake_release())

    with patch("app.backends.prebuilt.httpx.get", side_effect=_get):
        assert latest_release("ggml-org/llama.cpp")["tag_name"] == "b10331"

    assert len(calls) == 1, "a release with assets must cost exactly one request"


def test_unreadable_marker_falls_back_to_the_stub() -> None:
    """A broken marker must not raise: the caller reports 'no asset matching'."""

    def _get(url: str, **kwargs: object) -> _FakeResponse:
        if url.endswith("/releases/latest"):
            return _FakeResponse(_stub_release())
        if "nightly-tag.txt" in url:
            raise httpx.ConnectError("boom")
        raise AssertionError(f"unexpected URL: {url}")

    with patch("app.backends.prebuilt.httpx.get", side_effect=_get):
        assert latest_release("ggml-org/llama.cpp")["tag_name"] == "v0.5.0"


def test_empty_marker_falls_back_to_the_stub() -> None:
    """A blank marker names no tag, so there is nothing to resolve."""

    def _get(url: str, **kwargs: object) -> _FakeResponse:
        if url.endswith("/releases/latest"):
            return _FakeResponse(_stub_release())
        if "nightly-tag.txt" in url:
            return _MarkerResponse("   \n")
        raise AssertionError(f"unexpected URL: {url}")

    with patch("app.backends.prebuilt.httpx.get", side_effect=_get):
        assert latest_release("ggml-org/llama.cpp")["tag_name"] == "v0.5.0"


def test_update_drops_the_cached_release(tmp_path: Path) -> None:
    """``update`` is an explicit request for the newest release, so it must
    not resolve the metadata cached at startup."""
    from app.config import AppConfig
    from app.orchestrator import Orchestrator
    from app.schemas import EngineError

    calls: list[str] = []

    def _get(url: str, **kwargs: object) -> _FakeResponse:
        calls.append(url)
        return _FakeResponse({"tag_name": "b1", "assets": []})

    # Seed the cache, then confirm update() re-fetches rather than reusing it.
    # The empty asset list makes the run fail (no asset matches the backend),
    # which is fine: only the fetch behaviour is under test.
    with patch("app.backends.prebuilt.httpx.get", side_effect=_get):
        latest_release("ggml-org/llama.cpp")
        before = len(calls)
        with pytest.raises((EngineError, PrebuiltError)):
            Orchestrator(AppConfig(root=str(tmp_path))).update()
        assert len(calls) > before, "update() reused the stale cached release"


# ─── Progress callback is thread-scoped ────────────────────────────────────


def test_progress_callback_is_thread_local() -> None:
    """Regression: the callback was a process global, so two workers could
    clobber each other — the auto-update timer plus a model download."""
    seen: dict[str, str] = {}
    # Sized for the two workers only: the main thread must not join, or it would
    # consume a slot and deadlock the pair. The barrier alone is what guarantees
    # both callbacks are installed before either emits.
    ready = threading.Barrier(2, timeout=10)

    def _worker(name: str) -> None:
        def cb(done: int, total: int, phase: str, overall: float | None) -> None:
            seen[name] = name

        set_progress_callback(cb)
        ready.wait()  # both installed before either emits
        emit_progress(name, 1, 2, "download")
        set_progress_callback(None)

    threads = [threading.Thread(target=_worker, args=(n,)) for n in ("a", "b")]
    for t in threads:
        t.start()
    # The barrier is sized for the two workers only; the main thread must not
    # join it or it would consume a slot and deadlock the pair.
    for t in threads:
        t.join(timeout=10)

    assert seen == {"a": "a", "b": "b"}, "each worker must drive its own callback"
    assert get_progress_callback() is None


def test_progress_callback_defaults_to_none_on_a_fresh_thread() -> None:
    """A new worker thread must not inherit another worker's callback."""

    def _noop(done: int, total: int, phase: str, overall: float | None) -> None:
        return None

    set_progress_callback(_noop)
    try:
        result: list[bool] = []

        def _probe() -> None:
            result.append(get_progress_callback() is None)

        t = threading.Thread(target=_probe)
        t.start()
        t.join(timeout=10)
        assert result == [True], "a thread-local leaked into a new thread"
    finally:
        set_progress_callback(None)


# ─── _is_readable_archive ──────────────────────────────────────────────────


def test_is_readable_archive_accepts_a_valid_zip(tmp_path: Path) -> None:
    archive = tmp_path / "good.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("file.txt", "hello")
    assert _is_readable_archive(archive) is True


def test_is_readable_archive_rejects_a_corrupt_zip(tmp_path: Path) -> None:
    archive = tmp_path / "corrupt.zip"
    archive.write_bytes(b"this is not a zip")
    assert _is_readable_archive(archive) is False


def test_is_readable_archive_accepts_a_nonempty_tar_gz(tmp_path: Path) -> None:
    archive = tmp_path / "good.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        data = b"hello"
        info = tarfile.TarInfo(name="file.txt")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    assert _is_readable_archive(archive) is True


def test_is_readable_archive_rejects_an_empty_tar_gz(tmp_path: Path) -> None:
    archive = tmp_path / "empty.tar.gz"
    with tarfile.open(archive, "w:gz"):
        pass
    assert _is_readable_archive(archive) is False


def test_is_readable_archive_rejects_a_truncated_tar_gz(tmp_path: Path) -> None:
    archive = tmp_path / "truncated.tar.gz"
    archive.write_bytes(b"this is not a valid gzip stream")
    assert _is_readable_archive(archive) is False


# ─── Remaining branches ────────────────────────────────────────────


def test_is_readable_archive_survives_an_unopenable_path(
    tmp_path: Path,
) -> None:
    """A directory named like an archive must not crash the cache check."""
    folder = tmp_path / "archive.zip"
    folder.mkdir()
    assert _is_readable_archive(folder) is False


def test_get_release_maps_http_errors_to_prebuilt_error() -> None:
    def _get(url: str, **kwargs: object) -> _FakeResponse:
        raise httpx.ConnectError("boom")

    with (
        patch("app.backends.prebuilt.httpx.get", side_effect=_get),
        pytest.raises(PrebuiltError, match="GitHub API error"),
    ):
        latest_release("o/r")


def test_read_nightly_marker_scans_past_other_assets() -> None:
    """The marker may sit behind assets without a name."""
    import app.backends.prebuilt as pb

    release = {
        "assets": [
            {"browser_download_url": "https://example.com/x"},
            {
                "name": "nightly-tag.txt",
                "browser_download_url": "https://example.com/nightly-tag.txt",
            },
        ]
    }

    def _get(url: str, **kwargs: object) -> _MarkerResponse:
        return _MarkerResponse("b11146\n")

    with patch("app.backends.prebuilt.httpx.get", side_effect=_get):
        assert pb._read_nightly_marker(release, "o/r", None) == "b11146"


def test_nightly_without_build_assets_keeps_the_stub() -> None:
    """A nightly that carries only the marker names no build."""

    def _get(url: str, **kwargs: object) -> _FakeResponse:
        if url.endswith("/releases/latest"):
            return _FakeResponse(_stub_release())
        if url.endswith("/releases/tags/b11146"):
            return _FakeResponse(_stub_release())
        if "nightly-tag.txt" in url:
            return _MarkerResponse("b11146\n")
        raise AssertionError(f"unexpected URL: {url}")

    with patch("app.backends.prebuilt.httpx.get", side_effect=_get):
        release = latest_release("ggml-org/llama.cpp")

    assert release["tag_name"] == "v0.5.0"


def test_download_file_maps_cancellation(tmp_path: Path) -> None:
    from app.download import DownloadCancelled

    with (
        patch(
            "app.backends.prebuilt.stream_download",
            side_effect=DownloadCancelled("https://example.com/x"),
        ),
        pytest.raises(PrebuiltError, match="cancelled"),
    ):
        download_file("https://example.com/x", tmp_path / "x")


def test_download_file_maps_download_errors(tmp_path: Path) -> None:
    from app.download import DownloadError

    with (
        patch(
            "app.backends.prebuilt.stream_download",
            side_effect=DownloadError("boom"),
        ),
        pytest.raises(PrebuiltError, match="boom"),
    ):
        download_file("https://example.com/x", tmp_path / "x")


def test_cached_download_raises_on_size_mismatch(tmp_path: Path) -> None:
    cache_dir = tmp_path / "downloads"
    cache_dir.mkdir()

    def fake_download(url: str, dest: Path, token: Any = None, **kw: Any) -> None:
        dest.write_bytes(b"short")

    with (
        patch("app.backends.prebuilt.download_file", side_effect=fake_download),
        pytest.raises(PrebuiltError, match="size mismatch"),
    ):
        cached_download("https://example.com/asset.zip", 10, cache_dir)
    assert not (cache_dir / "asset.zip").exists()


def test_apply_mode_swallows_chmod_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.backends.prebuilt as pb

    target = tmp_path / "program"
    target.write_bytes(b"x")

    def _no_chmod(*_args: object, **_kwargs: object) -> None:
        raise OSError("read-only")

    monkeypatch.setattr(pb, "is_windows", lambda: False)
    monkeypatch.setattr(Path, "chmod", _no_chmod)
    pb._apply_mode(target, 0o755)  # must not raise


def _tar_with_symlink(path: Path) -> None:
    def build(tf: tarfile.TarFile) -> None:
        _add_tar_file(tf, "pkg/libllama.so.0.0.1", b"lib", 0o755)
        link = tarfile.TarInfo("pkg/libllama.so.0")
        link.type = tarfile.SYMTYPE
        link.linkname = "libllama.so.0.0.1"
        tf.addfile(link)

    _make_tar(path, build)


def test_write_symlink_replaces_an_existing_target(tmp_path: Path) -> None:
    """A stale file at the link path is removed before linking."""
    archive = tmp_path / "release.tar.gz"

    def build(tf: tarfile.TarFile) -> None:
        _add_tar_file(tf, "pkg/libllama.so.0.0.1", b"lib", 0o755)
        # A regular file at the link's own path: extracted first, it
        # must be replaced by the symlink that follows it.
        _add_tar_file(tf, "pkg/libllama.so.0", b"stale", 0o644)
        link = tarfile.TarInfo("pkg/libllama.so.0")
        link.type = tarfile.SYMTYPE
        link.linkname = "libllama.so.0.0.1"
        tf.addfile(link)

    _make_tar(archive, build)
    dest = tmp_path / "cpu"

    wipe_and_extract(archive, dest)
    assert (dest / "libllama.so.0").read_bytes() == b"lib"


def test_write_symlink_falls_back_to_a_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without symlink privilege the chain is rebuilt as a copy."""
    import app.backends.prebuilt as pb

    def _no_symlink(*_args: object, **_kwargs: object) -> None:
        raise OSError("no privilege")

    monkeypatch.setattr(Path, "symlink_to", _no_symlink)
    archive = tmp_path / "release.tar.gz"
    _tar_with_symlink(archive)
    dest = tmp_path / "cpu"

    pb.wipe_and_extract(archive, dest)
    assert (dest / "libllama.so.0").read_bytes() == b"lib"


def test_tar_extraction_reports_progress_and_special_members(
    tmp_path: Path,
) -> None:
    """Directory, root, hardlink and FIFO members all survive the walk."""
    archive = tmp_path / "release.tar.gz"

    def build(tf: tarfile.TarFile) -> None:
        _add_tar_file(tf, "pkg/llama-server", b"ELF", 0o755)
        sub = tarfile.TarInfo("pkg/sub")
        sub.type = tarfile.DIRTYPE
        tf.addfile(sub)
        _add_tar_file(tf, ".", b"root", 0o644)
        hard = tarfile.TarInfo("pkg/hard")
        hard.type = tarfile.LNKTYPE
        hard.linkname = "pkg/llama-server"
        tf.addfile(hard)
        fifo = tarfile.TarInfo("pkg/pipe")
        fifo.type = tarfile.FIFOTYPE
        tf.addfile(fifo)

    _make_tar(archive, build)
    dest = tmp_path / "cpu"

    with _recording_progress() as events:
        wipe_and_extract(archive, dest, component="cpu")

    assert (dest / "llama-server").read_bytes() == b"ELF"
    assert (dest / "hard").read_bytes() == b"ELF"  # hardlink copy
    assert (dest / "sub").is_dir()
    assert not (dest / "pipe").exists()
    # The root member (".") counts toward the total but is never
    # written, so the bar still finishes at the byte total.
    assert events[-1] == ("extract", 7, 7)


def test_zip_extraction_skips_root_entries(tmp_path: Path) -> None:
    archive = tmp_path / "release.zip"
    _make_zip(archive, {".": b"dot"})
    dest = tmp_path / "dest"
    wipe_and_extract(archive, dest)
    assert list(dest.iterdir()) == []


def test_zip_extraction_handles_directory_entries(tmp_path: Path) -> None:
    archive = tmp_path / "release.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("pkg/tool.exe", b"tool")
        zf.writestr(zipfile.ZipInfo("pkg/sub/"), b"")
    dest = tmp_path / "dest"
    wipe_and_extract(archive, dest)
    assert (dest / "tool.exe").read_bytes() == b"tool"
    assert (dest / "sub").is_dir()


def test_zip_extraction_recreates_stored_symlinks(tmp_path: Path) -> None:
    """A UNIX-created zip stores symlinks as mode bits plus a target name."""
    archive = tmp_path / "release.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("pkg/libllama.so.0.0.1", b"lib")
        info = zipfile.ZipInfo("pkg/libllama.so.0")
        info.create_system = 3
        info.external_attr = 0o120777 << 16
        zf.writestr(info, b"libllama.so.0.0.1")
    dest = tmp_path / "dest"
    wipe_and_extract(archive, dest)
    assert (dest / "libllama.so.0.0.1").read_bytes() == b"lib"
    assert (dest / "libllama.so.0").exists()


def test_mark_executables_runs_on_posix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.backends.prebuilt as pb

    monkeypatch.setattr(pb, "is_windows", lambda: False)
    archive = tmp_path / "release.zip"
    _make_zip(archive, {"llama-server": b"ELF", "lib.dll": b"dll"})
    dest = tmp_path / "cpu"

    pb.wipe_and_extract(archive, dest)
    assert (dest / "llama-server").exists()


def test_wipe_and_extract_replaces_a_symlinked_destination(
    tmp_path: Path,
) -> None:
    """A symlinked backend dir is replaced, not followed."""
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    dest = tmp_path / "vulkan"
    dest.symlink_to(elsewhere, target_is_directory=True)
    archive = tmp_path / "release.zip"
    _make_zip(archive, {"llama-server.exe": b"server"})

    wipe_and_extract(archive, dest)
    assert (dest / "llama-server.exe").read_bytes() == b"server"
    assert not (elsewhere / "llama-server.exe").exists()


def test_extract_cudart_drops_libraries_next_to_the_binary(
    tmp_path: Path,
) -> None:
    import app.backends.prebuilt as pb

    archive = tmp_path / "cudart.zip"
    _make_zip(
        archive,
        {
            "bin/cudart64_12.dll": b"dll",
            "bin/other.txt": b"text",
            "bin/nested/lib/llama.so": b"so",
        },
    )
    dest = tmp_path / "cuda12"

    with _recording_progress() as events:
        pb._extract_cudart(archive, dest, component="cuda12-cudart")

    assert (dest / "cudart64_12.dll").read_bytes() == b"dll"
    assert (dest / "llama.so").read_bytes() == b"so"
    assert not (dest / "other.txt").exists()
    assert ("extract", 5, 5) in events


def test_extract_cudart_from_a_tar_gz(tmp_path: Path) -> None:
    import app.backends.prebuilt as pb

    archive = tmp_path / "cudart.tar.gz"

    def build(tf: tarfile.TarFile) -> None:
        _add_tar_file(tf, "bin/cudart64_12.dll", b"dll", 0o644)

    _make_tar(archive, build)
    dest = tmp_path / "cuda12"

    pb._extract_cudart(archive, dest)
    assert (dest / "cudart64_12.dll").read_bytes() == b"dll"


def test_extract_cudart_rejects_other_formats(tmp_path: Path) -> None:
    import app.backends.prebuilt as pb

    bogus = tmp_path / "cudart.7z"
    bogus.write_bytes(b"nope")
    with pytest.raises(PrebuiltError, match="Unsupported archive format"):
        pb._extract_cudart(bogus, tmp_path / "dest")


@pytest.mark.skipif(
    not _IS_WINDOWS,
    reason="cuda12's prebuilt and cudart patterns are Windows-only",
)
@patch("app.backends.prebuilt.latest_release")
@patch("app.backends.prebuilt.cached_download")
@patch("app.backends.prebuilt.wipe_and_extract")
def test_install_backend_requires_its_runtime_pack(
    mock_wipe: MagicMock,
    mock_dl: MagicMock,
    mock_release: MagicMock,
    tmp_path: Path,
) -> None:
    """cuda12 cannot run without the cudart pack: a release without
    it is an error, not a silent install."""
    release = fake_release()
    release["assets"] = [
        a for a in release["assets"] if not a["name"].startswith("cudart-")
    ]
    mock_release.return_value = release

    with pytest.raises(PrebuiltError, match="CUDA runtime pack"):
        install_backend(
            "cuda12", tmp_path / "managed", tmp_path / "downloads", force=True
        )


def test_latest_versions_reports_the_tag() -> None:
    def _get(url: str, **kwargs: object) -> _FakeResponse:
        return _FakeResponse({"tag_name": "b10331"})

    with patch("app.backends.prebuilt.httpx.get", side_effect=_get):
        assert latest_versions() == {"llama_cpp": "b10331"}


def test_latest_versions_survives_an_api_failure() -> None:
    def _get(url: str, **kwargs: object) -> _FakeResponse:
        raise httpx.ConnectError("boom")

    with patch("app.backends.prebuilt.httpx.get", side_effect=_get):
        assert latest_versions() == {"llama_cpp": None}


def test_installed_backends_skips_non_backends(tmp_path: Path) -> None:
    managed = tmp_path / "managed"
    (managed / "vulkan").mkdir(parents=True)
    (managed / "vulkan" / ".version").write_text("b1\n", encoding="utf-8")
    (managed / "current").mkdir()  # the link, never a backend
    (managed / "notes.txt").write_text("not a dir", encoding="utf-8")
    (managed / "empty").mkdir()  # no .version marker

    assert installed_backends(managed) == {"vulkan": "b1"}


def test_installed_backends_without_a_root(tmp_path: Path) -> None:
    assert installed_backends(tmp_path / "missing") == {}


def test_failure_hint_per_exception_type() -> None:
    assert failure_hint(PrebuiltUnavailable("metal")) == (
        "Point at an existing binary instead (Settings, Paths group)."
    )
    assert failure_hint(PrebuiltError("boom")) == (
        "Check the network connection, or add a GitHub token in Settings."
    )
    assert failure_hint(ValueError("odd")) == "odd"


def test_write_version_marker_round_trips(tmp_path: Path) -> None:
    dest = tmp_path / "vulkan"
    write_version_marker(dest, "b10331", source="managed-prebuilt")
    assert (dest / ".version").read_text(encoding="utf-8") == (
        "b10331\nmanaged-prebuilt\n"
    )


# ─── Final branch sweep ────────────────────────────────────────


def test_is_readable_archive_survives_an_unopenable_zip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An OSError from zipfile.is_zipfile falls through to the tar check."""
    import app.backends.prebuilt as pb

    def _boom(_path: Any) -> bool:
        raise OSError("locked")

    monkeypatch.setattr(zipfile, "is_zipfile", _boom)
    candidate = tmp_path / "candidate.zip"
    candidate.write_bytes(b"not an archive at all")
    assert pb._is_readable_archive(candidate) is False


def test_write_symlink_fallback_without_a_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dangling symlink with no symlink privilege leaves nothing behind."""
    import app.backends.prebuilt as pb

    def _no_symlink(*_args: object, **_kwargs: object) -> None:
        raise OSError("no privilege")

    monkeypatch.setattr(Path, "symlink_to", _no_symlink)
    archive = tmp_path / "release.tar.gz"

    def build(tf: tarfile.TarFile) -> None:
        link = tarfile.TarInfo("pkg/libllama.so.0")
        link.type = tarfile.SYMTYPE
        link.linkname = "missing-target"
        tf.addfile(link)

    _make_tar(archive, build)
    dest = tmp_path / "cpu"

    pb.wipe_and_extract(archive, dest)
    assert not (dest / "libllama.so.0").exists()


def test_tar_hardlink_to_a_missing_source(tmp_path: Path) -> None:
    """A hardlink whose target never landed is skipped, not fatal."""
    archive = tmp_path / "release.tar.gz"

    def build(tf: tarfile.TarFile) -> None:
        hard = tarfile.TarInfo("pkg/hard")
        hard.type = tarfile.LNKTYPE
        hard.linkname = "nowhere"
        tf.addfile(hard)

    _make_tar(archive, build)
    dest = tmp_path / "cpu"
    wipe_and_extract(archive, dest)
    assert not (dest / "hard").exists()


def test_tar_extraction_skips_members_without_file_objects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """extractfile() returning None (defensive) skips the member."""
    import app.backends.prebuilt as pb

    monkeypatch.setattr(tarfile.TarFile, "extractfile", _no_extractfile)
    archive = tmp_path / "release.tar.gz"

    def build(tf: tarfile.TarFile) -> None:
        _add_tar_file(tf, "pkg/llama-server", b"ELF", 0o755)

    _make_tar(archive, build)
    dest = tmp_path / "cpu"

    pb.wipe_and_extract(archive, dest)
    assert not (dest / "llama-server").exists()


def test_mark_executables_walks_past_directories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The POSIX marker scans subdirectories and skips non-files."""
    import app.backends.prebuilt as pb

    monkeypatch.setattr(pb, "is_windows", lambda: False)
    archive = tmp_path / "release.zip"
    _make_zip(archive, {"one/tool.sh": b"#!/bin/sh", "two/other.txt": b"x"})
    dest = tmp_path / "cpu"

    pb.wipe_and_extract(archive, dest)
    assert (dest / "one").is_dir()
    assert (dest / "one" / "tool.sh").read_bytes() == b"#!/bin/sh"


def test_extract_cudart_from_a_tar_gz_reports_progress(
    tmp_path: Path,
) -> None:
    import app.backends.prebuilt as pb

    archive = tmp_path / "cudart.tar.gz"

    def build(tf: tarfile.TarFile) -> None:
        _add_tar_file(tf, "bin/cudart64_12.dll", b"dll", 0o644)
        _add_tar_file(tf, "bin/cublas64.dll", b"cublas", 0o644)

    _make_tar(archive, build)
    dest = tmp_path / "cuda12"

    with _recording_progress() as events:
        pb._extract_cudart(
            archive,
            dest,
            component="cuda12-cudart",
            overall_range=(0.95, 1.0),
        )

    assert (dest / "cudart64_12.dll").read_bytes() == b"dll"
    assert (dest / "cublas64.dll").read_bytes() == b"cublas"
    # add() per member, then the final completion tick.
    assert events == [("extract", 3, 9), ("extract", 9, 9), ("extract", 9, 9)]


def test_extract_cudart_zip_without_a_progress_callback(
    tmp_path: Path,
) -> None:
    """With no callback registered the cudart zip path runs unmeasured."""
    import app.backends.prebuilt as pb

    archive = tmp_path / "cudart.zip"
    _make_zip(archive, {"bin/cudart64_12.dll": b"dll"})
    dest = tmp_path / "cuda12"
    pb.set_progress_callback(None)

    pb._extract_cudart(archive, dest)
    assert (dest / "cudart64_12.dll").read_bytes() == b"dll"


def test_extract_cudart_tar_skips_unreadable_members(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.backends.prebuilt as pb

    monkeypatch.setattr(tarfile.TarFile, "extractfile", _no_extractfile)
    archive = tmp_path / "cudart.tar.gz"

    def build(tf: tarfile.TarFile) -> None:
        _add_tar_file(tf, "bin/cudart64_12.dll", b"dll", 0o644)

    _make_tar(archive, build)
    dest = tmp_path / "cuda12"
    pb.set_progress_callback(None)

    pb._extract_cudart(archive, dest)
    assert not (dest / "cudart64_12.dll").exists()


@pytest.mark.skipif(
    not _IS_WINDOWS,
    reason="cuda13's prebuilt and cudart patterns are Windows-only",
)
@patch("app.backends.prebuilt.latest_release")
@patch("app.backends.prebuilt.cached_download")
@patch("app.backends.prebuilt.wipe_and_extract")
def test_install_backend_without_an_optional_runtime_pack(
    mock_wipe: MagicMock,
    mock_dl: MagicMock,
    mock_release: MagicMock,
    tmp_path: Path,
) -> None:
    """cuda13 bundles its runtime only on request: a release without
    the pack still installs, because cuda13 does not need it."""
    release = fake_release()
    release["assets"] = [
        a
        for a in release["assets"]
        if not (a["name"].startswith("cudart-") and "cuda-13" in a["name"])
    ]
    mock_release.return_value = release

    result = install_backend(
        "cuda13",
        tmp_path / "managed",
        tmp_path / "downloads",
        force=True,
        bundle_cuda_runtime="always",
    )

    backend = get_backend("cuda13")
    assert backend is not None
    matched = match_asset(release["assets"], backend.asset_pattern() or "")
    assert matched is not None

    assert result == {
        "name": "cuda13",
        "status": "ok",
        "version": "b10331",
        "bytes": matched["size"],
    }
    assert (tmp_path / "managed" / "cuda13" / ".version").exists()


def test_list_assets_flags_every_asset_family() -> None:
    release = {
        "tag_name": "v0.6.0",
        "assets": [
            {"name": "cudart-12.4-win-x64.zip", "size": 10},
            {"name": "llama-bin-win-vulkan-x64.zip", "size": 11},
            {"name": "llama-bin-win-cuda-13.0-x64.zip", "size": 12},
            {"name": "llama-bin-linux-rocm-x64.tar.gz", "size": 13},
            {"name": "llama-bin-linux-sycl-x64.tar.gz", "size": 14},
            {"name": "llama-bin-win-openvino-x64.zip", "size": 15},
            {"name": "llama-bin-linux-cpu-x64.tar.gz", "size": 16},
            {"name": "llama-bin-macos-arm64.tar.gz", "size": 17},
            {"name": "llama-bin-win-cuda-12.4-x64.zip", "size": 18},
            {"name": "llama-bin-unknown-accel.zip", "size": 19},
        ],
    }

    def _get(url: str, **kwargs: object) -> _FakeResponse:
        return _FakeResponse(release)

    with patch("app.backends.prebuilt.httpx.get", side_effect=_get):
        payload = list_assets()

    assert payload["release"] == "v0.6.0"
    flags = {a["name"]: a["flag"] for a in payload["assets"]}
    assert flags["cudart-12.4-win-x64.zip"] == "cudart"
    assert flags["llama-bin-win-vulkan-x64.zip"] == "vulkan"
    assert flags["llama-bin-win-cuda-13.0-x64.zip"] == "cuda13"
    assert flags["llama-bin-linux-rocm-x64.tar.gz"] == "rocm"
    assert flags["llama-bin-linux-sycl-x64.tar.gz"] == "sycl"
    assert flags["llama-bin-win-openvino-x64.zip"] == "openvino"
    assert flags["llama-bin-linux-cpu-x64.tar.gz"] == "cpu"
    assert flags["llama-bin-macos-arm64.tar.gz"] == "metal"
    assert flags["llama-bin-win-cuda-12.4-x64.zip"] == "cuda12"
    assert flags["llama-bin-unknown-accel.zip"] == "other"
