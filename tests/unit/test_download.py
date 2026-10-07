"""Auto-retry and pending-download behavior of the shared download engine."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from app import download as download_mod
from app.download import (
    DownloadCancelled,
    DownloadControl,
    discard_pending,
    get_download_control,
    pending_downloads,
    resumable_tasks,
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


def _recorder() -> tuple[list[tuple[str, int, int, str]], Callable[..., None]]:
    """A progress sink that records each tick in CLI line form."""
    emitted: list[tuple[str, int, int, str]] = []

    def record(
        component: str,
        done: int,
        total: int,
        phase: str,
        overall: float | None = None,
    ) -> None:
        emitted.append(_fmt(component, done, total, phase))

    return emitted, record


def test_transient_failure_retries_then_succeeds(tmp_path: Path) -> None:
    emitted, record = _recorder()

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


# ─── Pause / resume / cancel primitives ────────────────────────────


def test_download_control_lifecycle() -> None:
    control = DownloadControl()
    assert control.cancelled is False
    assert control.paused is False
    control.pause()
    assert control.paused is True
    control.resume()
    assert control.paused is False
    control.cancel()
    assert control.cancelled is True
    # Cancel also clears a pause, so a waiter never stays blocked.
    assert control.paused is False


def test_wait_while_paused_blocks_until_resumed() -> None:
    control = DownloadControl()
    control.pause()
    entered = threading.Event()
    released = threading.Event()

    def _waiter() -> None:
        entered.set()
        control.wait_while_paused(poll=0.05)
        released.set()

    t = threading.Thread(target=_waiter)
    t.start()
    assert entered.wait(timeout=5)
    assert not released.wait(timeout=0.3), "released while still paused"
    control.resume()
    assert released.wait(timeout=5)
    t.join(timeout=5)


def test_wait_while_paused_returns_when_cancelled() -> None:
    control = DownloadControl()
    control.pause()
    released = threading.Event()

    def _waiter() -> None:
        control.wait_while_paused(poll=0.05)
        released.set()

    t = threading.Thread(target=_waiter)
    t.start()
    time.sleep(0.1)
    control.cancel()
    assert released.wait(timeout=5)
    t.join(timeout=5)


def test_interruptible_sleep_runs_out() -> None:
    download_mod._interruptible_sleep(0.01, None, "https://example.com/x", poll=0.005)


def test_interruptible_sleep_aborts_on_cancel() -> None:
    control = DownloadControl()
    control.cancel()
    with pytest.raises(DownloadCancelled):
        download_mod._interruptible_sleep(
            10.0, control, "https://example.com/x", poll=0.005
        )


def test_interruptible_sleep_waits_out_a_pause() -> None:
    control = DownloadControl()
    control.pause()

    def _resumer() -> None:
        time.sleep(0.1)
        control.resume()

    t = threading.Thread(target=_resumer)
    t.start()
    download_mod._interruptible_sleep(0.4, control, "https://example.com/x", poll=0.05)
    t.join(timeout=5)


# ─── Helpers ───────────────────────────────────────────────────────


def test_read_meta_tolerates_garbage_and_directories(tmp_path: Path) -> None:
    garbage = tmp_path / "x.part.meta"
    garbage.write_text("not json", encoding="utf-8")
    assert download_mod._read_meta(garbage) is None
    folder = tmp_path / "y.part.meta"
    folder.mkdir()
    assert download_mod._read_meta(folder) is None


def test_overall_progress_is_clamped_and_unknown_without_total() -> None:
    assert download_mod._overall(5, 0, 0.0, 1.0) is None
    assert download_mod._overall(5, 10, 0.0, 1.0) == pytest.approx(0.5)
    assert download_mod._overall(0, 10, 0.2, 0.8) == pytest.approx(0.2)
    assert download_mod._overall(20, 10, 0.0, 1.0) == pytest.approx(1.0)


def test_part_path_with_and_without_a_suffix(tmp_path: Path) -> None:
    assert download_mod._part_path(tmp_path / "model") == tmp_path / "model.part"
    assert (
        download_mod._part_path(tmp_path / "model.gguf") == tmp_path / "model.gguf.part"
    )


def test_is_retryable_classifies_failures() -> None:
    assert download_mod._is_retryable(httpx.ConnectError("x")) is True
    request = _get("https://example.com/x")

    def _status(code: int) -> httpx.HTTPStatusError:
        return httpx.HTTPStatusError(
            f"status {code}",
            request=request,
            response=httpx.Response(code, request=request),
        )

    assert download_mod._is_retryable(_status(404)) is False
    assert download_mod._is_retryable(_status(429)) is True
    assert download_mod._is_retryable(_status(503)) is True
    # An HTTPError that is neither a transport nor a status error.
    assert download_mod._is_retryable(httpx.HTTPError("boom")) is False


# ─── Resume / headers / mid-stream control ─────────────────────────


def _matching_part(dest: Path, url: str, size: int, total: int) -> Path:
    """A ``.part`` with a URL-matching meta sidecar of ``size`` bytes."""
    part = download_mod._part_path(dest)
    part.write_bytes(b"x" * size)
    part.with_suffix(part.suffix + ".meta").write_text(
        json.dumps({"url": url, "total": total, "done": size}),
        encoding="utf-8",
    )
    return part


def test_resume_sends_a_range_header_and_parses_content_range(
    tmp_path: Path,
) -> None:
    dest = tmp_path / "out.bin"
    _matching_part(dest, "https://example.com/x", 6, 12)
    resp = _FakeResp(status_code=206, chunks=(b"world",))
    resp.headers["Content-Range"] = "bytes 6-11/12"

    with patch("app.download.httpx.stream", return_value=resp) as stream:
        out = stream_download("https://example.com/x", dest)

    assert out == dest
    assert dest.read_bytes() == b"xxxxxxworld"
    assert stream.call_args.kwargs["headers"]["Range"] == "bytes=6-"


def test_resume_total_from_content_length(tmp_path: Path) -> None:
    """A 206 without Content-Range: total = offset + Content-Length."""
    dest = tmp_path / "out.bin"
    _matching_part(dest, "https://example.com/x", 6, 0)
    resp = _FakeResp(status_code=206, chunks=(b"world",))
    resp.headers["Content-Length"] = "5"
    emitted, record = _recorder()

    with patch("app.download.httpx.stream", return_value=resp):
        stream_download("https://example.com/x", dest, emit=record)

    assert dest.read_bytes() == b"xxxxxxworld"
    assert emitted[0][2] == 11  # 6 resumed + 5 remaining


def test_server_ignoring_range_restarts_from_scratch(tmp_path: Path) -> None:
    dest = tmp_path / "out.bin"
    _matching_part(dest, "https://example.com/x", 6, 12)
    resp = _FakeResp(status_code=200, chunks=(b"fresh",))
    resp.headers["Content-Length"] = "5"

    with patch("app.download.httpx.stream", return_value=resp):
        stream_download("https://example.com/x", dest)

    assert dest.read_bytes() == b"fresh"


def test_stale_part_with_a_foreign_url_is_discarded(tmp_path: Path) -> None:
    dest = tmp_path / "out.bin"
    part = _matching_part(dest, "https://other.example.com/x", 6, 12)
    resp = _FakeResp(chunks=(b"fresh",))

    with patch("app.download.httpx.stream", return_value=resp):
        stream_download("https://example.com/x", dest)

    assert dest.read_bytes() == b"fresh"
    assert not part.exists()


def test_auth_token_is_sent_as_a_bearer_header(tmp_path: Path) -> None:
    resp = _FakeResp(chunks=(b"data",))
    with patch("app.download.httpx.stream", return_value=resp) as stream:
        stream_download(
            "https://example.com/x",
            tmp_path / "out.bin",
            auth_token="tok",
        )
    assert stream.call_args.kwargs["headers"]["Authorization"] == "Bearer tok"


class _CancellingResp(_FakeResp):
    """Cancels the control before yielding its (single) chunk."""

    def __init__(self, control: DownloadControl, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._control = control

    def iter_bytes(self, chunk_size: int = 65536) -> Iterator[bytes]:
        self._control.cancel()
        yield from self._chunks


def test_cancel_mid_stream_keeps_the_part_for_resume(tmp_path: Path) -> None:
    control = DownloadControl()
    resp = _CancellingResp(control, chunks=(b"aaa",))
    dest = tmp_path / "out.bin"

    with (
        patch("app.download.httpx.stream", return_value=resp),
        pytest.raises(DownloadCancelled),
    ):
        stream_download("https://example.com/x", dest, token=control)

    part = download_mod._part_path(dest)
    meta = part.with_suffix(part.suffix + ".meta")
    assert part.exists(), "the partial must survive a cancel"
    assert meta.exists()


def test_pause_mid_stream_resumes(tmp_path: Path) -> None:
    control = DownloadControl()
    control.pause()
    resp = _FakeResp(chunks=(b"hello",))
    dest = tmp_path / "out.bin"

    def _resumer() -> None:
        time.sleep(0.1)
        control.resume()

    t = threading.Thread(target=_resumer)
    t.start()
    with patch("app.download.httpx.stream", return_value=resp):
        out = stream_download("https://example.com/x", dest, token=control)
    t.join(timeout=5)

    assert out == dest
    assert dest.read_bytes() == b"hello"


def test_empty_chunks_are_skipped(tmp_path: Path) -> None:
    resp = _FakeResp(chunks=(b"a", b"", b"b"))
    dest = tmp_path / "out.bin"
    with patch("app.download.httpx.stream", return_value=resp):
        stream_download("https://example.com/x", dest)
    assert dest.read_bytes() == b"ab"


def test_failure_with_a_cancelled_control_raises_cancelled(
    tmp_path: Path,
) -> None:
    control = DownloadControl()
    control.cancel()
    with (
        patch("app.download.httpx.stream", return_value=_FailCtx()),
        pytest.raises(DownloadCancelled),
    ):
        stream_download("https://example.com/x", tmp_path / "out.bin", token=control)


# ─── Interrupted-download scanners ─────────────────────────────────


def test_resumable_tasks_lists_interrupted_downloads(tmp_path: Path) -> None:
    root = tmp_path / "downloads"
    root.mkdir()
    part = root / "llama-bin.zip.part"
    part.write_bytes(b"zip")
    part.with_suffix(part.suffix + ".meta").write_text(
        json.dumps(
            {"url": "https://example.com/llama-bin.zip", "total": 2000, "done": 500}
        ),
        encoding="utf-8",
    )
    # A directory named like a part file is not a download.
    (root / "broken.part").mkdir()
    # A part without a readable sidecar cannot be resumed.
    (root / "orphan.part").write_bytes(b"orphan")

    tasks = resumable_tasks(root)
    assert len(tasks) == 1
    assert tasks[0]["dest"] == str(root / "llama-bin.zip")
    assert tasks[0]["url"] == "https://example.com/llama-bin.zip"
    assert tasks[0]["total"] == 2000
    assert tasks[0]["done"] == 500
    # Roots that are not directories contribute nothing.
    assert resumable_tasks(tmp_path / "missing") == []


def test_resume_with_an_unparseable_content_range(tmp_path: Path) -> None:
    """A Content-Range without a digit total: keep the offset as total."""
    dest = tmp_path / "out.bin"
    _matching_part(dest, "https://example.com/x", 6, 0)
    resp = _FakeResp(status_code=206, chunks=(b"world",))
    resp.headers["Content-Range"] = "bytes 6-11/unknown"
    emitted, record = _recorder()

    with patch("app.download.httpx.stream", return_value=resp):
        stream_download("https://example.com/x", dest, emit=record)

    assert dest.read_bytes() == b"xxxxxxworld"
    assert emitted[0][2] == 6  # total unknown: the offset so far


def test_resume_without_size_headers_keeps_the_offset(
    tmp_path: Path,
) -> None:
    """A 206 with neither Content-Range nor Content-Length."""
    dest = tmp_path / "out.bin"
    _matching_part(dest, "https://example.com/x", 6, 0)
    resp = _FakeResp(status_code=206, chunks=(b"world",))
    emitted, record = _recorder()

    with patch("app.download.httpx.stream", return_value=resp):
        stream_download("https://example.com/x", dest, emit=record)

    assert emitted[0][2] == 6


def test_other_success_status_without_headers(tmp_path: Path) -> None:
    """A 2xx that is neither 200 nor 206 and carries no size headers."""
    resp = _FakeResp(status_code=204, chunks=(b"done",))
    dest = tmp_path / "out.bin"
    with patch("app.download.httpx.stream", return_value=resp):
        stream_download("https://example.com/x", dest)
    assert dest.read_bytes() == b"done"


def test_pending_downloads_ignores_missing_roots(tmp_path: Path) -> None:
    assert pending_downloads([("model", tmp_path / "missing")]) == []


def test_pending_downloads_skips_unusable_parts(tmp_path: Path) -> None:
    root = tmp_path / "models"
    root.mkdir()
    (root / "dir.part").mkdir()  # not a file
    (root / "orphan.part").write_bytes(b"x")  # no sidecar
    bad = root / "badjson.part"
    bad.write_bytes(b"x")
    bad.with_suffix(bad.suffix + ".meta").write_text("nope", encoding="utf-8")

    assert pending_downloads([("model", root)]) == []
