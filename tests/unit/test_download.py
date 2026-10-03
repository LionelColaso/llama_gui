"""Auto-retry and pending-download behavior of the shared download engine."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from app import download as download_mod
from app.download import (
    DownloadControl,
    discard_pending,
    get_download_control,
    pending_downloads,
    set_download_control,
    stream_download,
)


def _get(url: str) -> httpx.Request:
    return httpx.Request("GET", url)


def _fmt(
    component: str, done: int, total: int, phase: str
) -> tuple[str, int, int, str]:
    return (component, done, total, phase)


class _FailCtx:
    """A ``httpx.stream`` context whose enter raises a transient network error."""

    def __init__(self, exc: Exception | None = None) -> None:
        self._exc = exc or httpx.ConnectError("connection refused")

    def __enter__(self) -> object:
        raise self._exc

    def __exit__(self, *exc: object) -> None:
        return None


class _FakeResp:
    """A minimal successful httpx streaming response."""

    def __init__(self, status_code: int = 200, chunks: tuple[bytes, ...] = ()) -> None:
        self.status_code = status_code
        self.headers: dict[str, str] = {}
        self._chunks = chunks

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            req = _get("https://example.com/x")
            raise httpx.HTTPStatusError(
                f"status {self.status_code}",
                request=req,
                response=httpx.Response(self.status_code, request=req),
            )

    def __enter__(self) -> object:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def iter_bytes(self, chunk_size: int = 65536) -> Iterator[bytes]:
        return iter(self._chunks)


def test_meta_sidecar_is_not_written_per_chunk(tmp_path: Path) -> None:
    """Regression: the .part.meta sidecar was rewritten on every chunk.

    A temp-file write plus a replace per chunk is thousands of small
    synchronous writes for a multi-GB model, all on the download hot path.
    It is a resume *hint*, not a progress log, so it is throttled.
    """
    writes: list[tuple[str, int]] = []
    real_write_meta = download_mod._write_meta

    def _spy(meta: Path, url: str, total: int, done: int) -> None:
        writes.append((url, done))
        real_write_meta(meta, url, total, done)

    chunks = tuple(b"x" * 1024 for _ in range(500))
    resp = _FakeResp(chunks=chunks)
    with (
        patch.object(download_mod, "_write_meta", _spy),
        patch("httpx.stream", return_value=resp),
    ):
        stream_download("https://example.com/big.bin", tmp_path / "out.bin")

    assert len(writes) < len(chunks), (
        f"sidecar written {len(writes)}x for {len(chunks)} chunks"
    )
    # The URL must still be recorded, so a later run can offer to resume.
    assert writes, "the sidecar must still be written at least once"
    assert writes[0][0] == "https://example.com/big.bin"
    # ...and the final write records the complete byte count.
    assert writes[-1][1] == 500 * 1024


def test_transient_failure_retries_then_succeeds(tmp_path: Path) -> None:
    emitted: list[tuple[str, int, int, str]] = []

    def record(
        component: str,
        done: int,
        total: int,
        phase: str,
        overall: float | None = None,
    ) -> None:
        emitted.append(_fmt(component, done, total, phase))

    ok = _FakeResp(chunks=(b"hello ", b"world"))
    with patch("app.download.httpx.stream", side_effect=[_FailCtx(), ok]) as stream:
        out = stream_download(
            "https://example.com/x",
            tmp_path / "out.bin",
            emit=record,
            retry_backoff=0.0,
        )

    assert out == tmp_path / "out.bin"
    assert (tmp_path / "out.bin").read_bytes() == b"hello world"
    assert stream.call_count == 2
    # A "retrying" tick was emitted between attempts so the bar recovers instead
    # of freezing or failing outright.
    assert any(phase == "retrying" for _, _, _, phase in emitted)


def test_permanent_status_fails_without_retrying(tmp_path: Path) -> None:
    with (
        patch("app.download.httpx.stream", return_value=_FakeResp(404)) as stream,
        pytest.raises(Exception, match="404"),
    ):
        stream_download(
            "https://example.com/x", tmp_path / "out.bin", retry_backoff=0.0
        )
    # A 404 is permanent — exactly one attempt, no backoff, no retry.
    assert stream.call_count == 1


def test_retries_exhausted_raise(tmp_path: Path) -> None:

    with (
        patch(
            "app.download.httpx.stream",
            side_effect=[_FailCtx(), _FailCtx(), _FailCtx()],
        ),
        pytest.raises(Exception, match="after 3 attempts"),
    ):
        stream_download(
            "https://example.com/x", tmp_path / "out.bin", retry_backoff=0.0
        )


def test_pending_downloads_scans_roots_and_kinds(tmp_path: Path) -> None:
    models = tmp_path / "models"
    downloads = tmp_path / "downloads"
    models.mkdir()
    downloads.mkdir()

    # Half-downloaded model.
    dest = models / "model.gguf"
    part = dest.with_suffix(dest.suffix + ".part")  # model.gguf.part
    part.write_bytes(b"partial")
    part.with_suffix(part.suffix + ".meta").write_text(
        json.dumps(
            {"url": "https://example.com/model.gguf", "total": 1000, "done": 300}
        ),
        encoding="utf-8",
    )
    # Half-downloaded backend archive.
    arch = downloads / "llama-bin.zip.part"
    arch.write_bytes(b"zip")
    arch.with_suffix(arch.suffix + ".meta").write_text(
        json.dumps(
            {"url": "https://example.com/llama-bin.zip", "total": 2000, "done": 0}
        ),
        encoding="utf-8",
    )

    tasks = pending_downloads([("model", models), ("backend", downloads)])
    by_name = {t["name"]: t for t in tasks}
    assert set(by_name) == {"model.gguf", "llama-bin.zip"}
    assert by_name["model.gguf"]["kind"] == "model"
    assert by_name["model.gguf"]["total"] == 1000
    assert by_name["model.gguf"]["done"] == 300
    assert by_name["model.gguf"]["percent"] == 30
    assert by_name["llama-bin.zip"]["kind"] == "backend"


def test_discard_pending_removes_part_and_meta(tmp_path: Path) -> None:
    dest = tmp_path / "model.gguf"
    part = dest.with_suffix(dest.suffix + ".part")
    meta = part.with_suffix(part.suffix + ".meta")
    part.write_bytes(b"partial")
    meta.write_text("{}", encoding="utf-8")

    assert discard_pending(dest) is True
    assert not part.exists()
    assert not meta.exists()
    # A second discard is a no-op (nothing left to remove).
    assert discard_pending(dest) is False


# ─── Active control is thread-scoped ───────────────────────────────────────


def test_download_control_is_thread_local() -> None:
    """Regression: the control was a process global, so two workers could
    clobber each other and Pause/Cancel would stop working mid-download."""
    a = DownloadControl()
    b = DownloadControl()
    # Sized for the two workers only; the main thread must not join it.
    ready = threading.Barrier(2, timeout=10)
    seen: dict[str, DownloadControl | None] = {}

    def _worker(name: str, control: DownloadControl) -> None:
        set_download_control(control)
        ready.wait()  # both installed before either reads
        seen[name] = get_download_control()
        set_download_control(None)

    threads = [
        threading.Thread(target=_worker, args=("a", a)),
        threading.Thread(target=_worker, args=("b", b)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert seen == {"a": a, "b": b}, "each worker must keep its own control"
    assert get_download_control() is None


def test_download_control_does_not_leak_across_threads() -> None:
    set_download_control(DownloadControl())
    try:
        result: list[bool] = []

        def _probe() -> None:
            result.append(get_download_control() is None)

        t = threading.Thread(target=_probe)
        t.start()
        t.join(timeout=10)
        assert result == [True], "a thread-local leaked into a new thread"
    finally:
        set_download_control(None)
