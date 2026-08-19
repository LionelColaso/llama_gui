"""Auto-retry and pending-download behavior of the shared download engine."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from llamagui.download import discard_pending, pending_downloads, stream_download


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
    with patch(
        "llamagui.download.httpx.stream", side_effect=[_FailCtx(), ok]
    ) as stream:
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
        patch("llamagui.download.httpx.stream", return_value=_FakeResp(404)) as stream,
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
            "llamagui.download.httpx.stream",
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
