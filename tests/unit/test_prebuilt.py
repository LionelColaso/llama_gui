from __future__ import annotations

import io
import os
import stat
import sys
import tarfile
import threading
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any, ClassVar, Self
from unittest.mock import MagicMock, patch

import pytest

from llamagui.backends.prebuilt import (
    PrebuiltError,
    PrebuiltUnavailable,
    _is_readable_archive,
    backend_asset_pattern,
    cached_download,
    clear_release_cache,
    download_file,
    emit_progress,
    get_progress_callback,
    install_backend,
    installed_backends,
    latest_release,
    match_asset,
    set_progress_callback,
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
    import llamagui.backends.prebuilt as pb

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

    with patch("llamagui.backends.prebuilt.download_file", side_effect=_record):
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

    with patch("llamagui.backends.prebuilt.download_file", side_effect=fake_download):
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

    with patch("llamagui.backends.prebuilt.download_file", side_effect=fake_download):
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
        patch("llamagui.backends.prebuilt.httpx.stream", return_value=_Resp()),
        patch("llamagui.backends.prebuilt.emit_progress", side_effect=_record),
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

    with patch("llamagui.backends.prebuilt.httpx.get", side_effect=_get):
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
        patch("llamagui.backends.prebuilt.httpx.get", side_effect=_get),
        patch("llamagui.backends.prebuilt.RELEASE_CACHE_TTL", 0.0),
    ):
        assert latest_release("o/r")["tag_name"] == "b1"
        assert latest_release("o/r")["tag_name"] == "b2"

    assert len(calls) == 2, "an expired entry must be re-fetched"


def test_clear_release_cache_forces_a_refetch() -> None:
    calls: list[str] = []

    def _get(url: str, **kwargs: object) -> _FakeResponse:
        calls.append(url)
        return _FakeResponse({"tag_name": f"b{len(calls)}"})

    with patch("llamagui.backends.prebuilt.httpx.get", side_effect=_get):
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

    with patch("llamagui.backends.prebuilt.httpx.get", side_effect=_get):
        assert latest_release("o/one")["tag_name"] == "o/one"
        assert latest_release("o/two")["tag_name"] == "o/two"
        assert latest_release("o/one", "tok")["tag_name"] == "o/one"
        # ...and each is still served from cache on a repeat.
        assert latest_release("o/one")["tag_name"] == "o/one"
        assert latest_release("o/two")["tag_name"] == "o/two"

    # three distinct keys: (o/one, None), (o/two, None), (o/one, "tok")
    assert calls == ["o/one", "o/two", "o/one"]


def test_update_drops_the_cached_release(tmp_path: Path) -> None:
    """``update`` is an explicit request for the newest release, so it must
    not resolve the metadata cached at startup."""
    from llamagui.config import AppConfig
    from llamagui.orchestrator import Orchestrator
    from llamagui.schemas import EngineError

    calls: list[str] = []

    def _get(url: str, **kwargs: object) -> _FakeResponse:
        calls.append(url)
        return _FakeResponse({"tag_name": "b1", "assets": []})

    # Seed the cache, then confirm update() re-fetches rather than reusing it.
    # The empty asset list makes the run fail (no asset matches the backend),
    # which is fine: only the fetch behaviour is under test.
    with patch("llamagui.backends.prebuilt.httpx.get", side_effect=_get):
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
