"""Resumable, pausable, cancellable download engine.

A single streaming downloader shared by backend (prebuilt) and model (``.gguf``)
downloads. It is built for huge files (multi-GB llama.cpp release archives and
models) and survives flaky networks and app restarts:

* **Resume** — each download writes a ``<name>.part`` temp file and, if the
  server supports HTTP ``Range`` (it does for GitHub releases and Hugging
  Face), continues from the bytes already on disk. A crash, cancel, or app
  restart leaves the ``.part`` behind; the next run resumes instead of
  restarting. A sidecar ``.part.meta`` records the source URL and how many
  bytes are done so the resume can be validated and offered to the user.
* **Pause / Resume / Cancel** — driven by a :class:`DownloadControl` the GUI
  owns. The streaming loop checks it between chunks: pause blocks until
  resumed, cancel stops cleanly (keeping the ``.part`` for later resume).
* **Auto-retry with backoff** — a transient failure (connection reset, a
  read/connect timeout, or a 429/5xx from the server) does not kill the
  download: it is retried with exponential backoff, resuming via ``Range``
  from the bytes already on disk, and only raises after the retries are
  exhausted (or the failure is permanent, e.g. a 404).
* **Progress** — every chunk is reported through the existing
  ``emit_progress`` channel (``done``/``total``/``phase``/``overall``), so the
  GUI progress bar works unchanged; between attempts a ``retrying`` tick is
  emitted so the bar shows the recovery instead of freezing or failing.

The active control is held in a module-global set by the worker before an
action runs (mirroring ``set_progress_callback``), so the engine reads it
without threading it through every ``install`` / ``download_model`` signature.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, cast

import httpx

#: Default emit target; imported lazily to avoid a circular import with
#: ``backends.prebuilt`` (which in turn imports this module).
ProgressEmitter = Callable[[str, int, int, str, float | None], None]


class DownloadCancelled(Exception):
    """The user cancelled the download; ``.part`` is kept for later resume."""

    def __init__(self, url: str) -> None:
        super().__init__(f"Download cancelled: {url}")
        self.url = url


class DownloadError(Exception):
    """A download failed for a non-cancellation reason (network/HTTP)."""


class DownloadControl:
    """Thread-safe pause / resume / cancel handle for one download.

    The GUI creates one, hands it to the worker (which publishes it via
    :func:`set_download_control`), and drives it from Pause/Resume/Cancel
    buttons. The engine's streaming loop polls :meth:`wait_while_paused`
    between chunks.
    """

    def __init__(self) -> None:
        self._cancelled = False
        self._paused = False
        self._cond = threading.Condition()

    def cancel(self) -> None:
        with self._cond:
            self._cancelled = True
            self._paused = False
            self._cond.notify_all()

    def pause(self) -> None:
        with self._cond:
            self._paused = True

    def resume(self) -> None:
        with self._cond:
            self._paused = False
            self._cond.notify_all()

    @property
    def cancelled(self) -> bool:
        with self._cond:
            return self._cancelled

    @property
    def paused(self) -> bool:
        with self._cond:
            return self._paused

    def wait_while_paused(self, poll: float = 0.25) -> None:
        """Block until resumed or cancelled. No-op when not paused."""
        with self._cond:
            while self._paused and not self._cancelled:
                self._cond.wait(timeout=poll)


# ─── Active control (mirrors the progress-callback global) ───────────────────

_current_control: DownloadControl | None = None


def set_download_control(control: DownloadControl | None) -> None:
    global _current_control
    _current_control = control


def get_download_control() -> DownloadControl | None:
    return _current_control


# ─── Meta sidecar (survives restarts so a download can be offered to resume) ──


def _meta_path(part: Path) -> Path:
    return part.with_suffix(part.suffix + ".meta")


def _write_meta(meta: Path, url: str, total: int, done: int) -> None:
    tmp = meta.with_suffix(meta.suffix + ".tmp")
    tmp.write_text(
        json.dumps({"url": url, "total": total, "done": done}), encoding="utf-8"
    )
    tmp.replace(meta)


def _read_meta(meta: Path) -> dict[str, Any] | None:
    try:
        return cast("dict[str, Any]", json.loads(meta.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return None


def _overall(done: int, total: int, lo: float, hi: float) -> float | None:
    if not total:
        return None
    return max(lo, min(lo + (done / total) * (hi - lo), hi))


def _part_path(dest: Path) -> Path:
    """The ``.part`` temp file sitting next to the final destination."""
    if dest.suffix:
        return dest.with_suffix(dest.suffix + ".part")
    return dest.with_name(dest.name + ".part")


# ─── Retry policy ──────────────────────────────────────────────────────────

#: HTTP status codes that are safe to retry (the resource still exists; the
#: server is busy or did not finish). Permanent codes (404, 403, 401, …) are
#: surfaced immediately so the user fixes the URL instead of waiting out retries.
_RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})


def _is_retryable(exc: httpx.HTTPError) -> bool:
    """True when ``exc`` is a transient failure worth retrying."""
    if isinstance(exc, httpx.TransportError):
        return True  # connect/read/write timeouts, resets, protocol errors
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _RETRYABLE_STATUS
    return False


def _interruptible_sleep(
    seconds: float,
    control: DownloadControl | None,
    url: str,
    poll: float = 0.25,
) -> None:
    """Sleep for ``seconds`` while staying responsive to pause and cancel.

    Pause blocks the backoff until the user resumes; cancel aborts the retry
    cleanly (the ``.part`` stays behind for a later resume).
    """
    deadline = time.monotonic() + seconds
    while True:
        if control is not None:
            if control.cancelled:
                raise DownloadCancelled(url)
            control.wait_while_paused(poll)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(poll, remaining))


def stream_download(
    url: str,
    dest: Path,
    *,
    token: DownloadControl | None = None,
    component: str = "download",
    overall_range: tuple[float, float] = (0.0, 1.0),
    chunk_size: int = 1024 * 1024,
    auth_token: str | None = None,
    timeout: float = 120.0,
    emit: ProgressEmitter | None = None,
    retries: int = 3,
    retry_backoff: float = 1.0,
) -> Path:
    """Stream ``url`` into ``dest``, resuming/pausing/cancelling as needed.

    Writes ``<dest>.part`` and renames it into place only on success. On cancel
    (or any interrupt) the ``.part`` and its ``.part.meta`` are kept so the
    next run resumes. Transient failures (connect/read/write timeouts, resets,
    429/5xx) are retried with exponential backoff, resuming from the bytes
    already on disk via ``Range``. Returns the final ``dest`` path.
    """
    from .backends.prebuilt import emit_progress  # lazy: avoid import cycle

    emit = emit or emit_progress
    lo, hi = overall_range
    dest = Path(dest)
    part = _part_path(dest)
    meta = _meta_path(part)
    control = token or get_download_control()

    # Resume offset from an existing, URL-matching partial download.
    offset = 0
    if part.exists():
        info = _read_meta(meta) if meta.exists() else None
        if info and info.get("url") == url:
            offset = part.stat().st_size
        else:
            # Stale/unknown partial — start fresh so we never append garbage.
            part.unlink(missing_ok=True)
            meta.unlink(missing_ok=True)

    total = 0
    attempt = 0
    while True:
        attempt += 1
        headers: dict[str, str] = {}
        if auth_token:
            headers["Authorization"] = f"Bearer {auth_token}"
        if offset:
            headers["Range"] = f"bytes={offset}-"

        try:
            with httpx.stream(
                "GET", url, headers=headers, timeout=timeout, follow_redirects=True
            ) as resp:
                if offset and resp.status_code == 200:
                    # Server ignored Range: restart from scratch.
                    offset = 0
                resp.raise_for_status()

                total = offset
                content_range = resp.headers.get("Content-Range")
                if content_range:
                    last = content_range.rsplit("/", 1)[-1]
                    if last.isdigit():
                        total = int(last)
                elif resp.status_code == 200 and offset == 0:
                    length = resp.headers.get("Content-Length")
                    if length and length.isdigit():
                        total = int(length)
                elif total == offset and resp.status_code == 206:
                    length = resp.headers.get("Content-Length")
                    if length and length.isdigit():
                        total = offset + int(length)

                part.parent.mkdir(parents=True, exist_ok=True)
                _write_meta(meta, url, total, offset)
                emit(
                    component,
                    offset,
                    total,
                    "download",
                    _overall(offset, total, lo, hi),
                )

                done = offset
                mode = "ab" if offset else "wb"
                with part.open(mode, buffering=chunk_size) as handle:
                    for chunk in resp.iter_bytes(chunk_size):
                        if control is not None:
                            if control.cancelled:
                                raise DownloadCancelled(url)
                            control.wait_while_paused()
                        if not chunk:
                            continue
                        handle.write(chunk)
                        done += len(chunk)
                        _write_meta(meta, url, total, done)
                        emit(
                            component,
                            done,
                            total,
                            "download",
                            _overall(done, total, lo, hi),
                        )
                part.replace(dest)
                meta.unlink(missing_ok=True)
                return dest
        except DownloadCancelled:
            # Keep the partial + meta so the user can resume after restart.
            raise
        except httpx.HTTPError as e:  # network / HTTP failure: keep .part for resume
            if control is not None and control.cancelled:
                raise DownloadCancelled(url)
            if not _is_retryable(e):
                # Permanent failure (404, auth, …) surfaces immediately so the
                # user fixes the URL instead of waiting out retries.
                raise DownloadError(f"Download failed: {e}") from e
            if attempt >= retries:
                raise DownloadError(
                    f"Download failed after {retries} attempts: {e}"
                ) from e
            # Recompute the resume point from the bytes actually on disk.
            offset = part.stat().st_size if part.exists() else 0
            delay = retry_backoff * (2 ** (attempt - 1))
            emit(component, offset, total, "retrying", _overall(offset, total, lo, hi))
            _interruptible_sleep(delay, control, url)


def resumable_tasks(*roots: Path) -> list[dict[str, Any]]:
    """Find interrupted downloads (``*.part``) under ``roots`` for resume.

    Each entry has ``dest``, ``url``, ``total`` and ``done`` so the GUI can
    show "Resume download of <name>? (done / total)".
    """
    tasks: list[dict[str, Any]] = []
    for root in roots:
        if not root.is_dir():
            continue
        for part in root.rglob("*.part"):
            if not part.is_file():
                continue
            meta = _meta_path(part)
            info = _read_meta(meta)
            if not info:
                continue
            dest = part.with_suffix("")  # strip trailing ".part"
            tasks.append(
                {
                    "dest": str(dest),
                    "url": info.get("url", ""),
                    "total": int(info.get("total", 0) or 0),
                    "done": int(info.get("done", 0) or 0),
                }
            )
    return tasks


def pending_downloads(groups: Iterable[tuple[str, Path]]) -> list[dict[str, Any]]:
    """Find every interrupted download across several roots, with a kind label.

    Each group is a ``(kind, root)`` pair — e.g. ``("model", models_dir)`` and
    ``("backend", downloads_dir)`` — so a single screen can offer to resume
    both model ``.gguf`` files and half-downloaded backend archives.
    """
    tasks: list[dict[str, Any]] = []
    for kind, root in groups:
        if not root.is_dir():
            continue
        for part in root.rglob("*.part"):
            if not part.is_file():
                continue
            meta = _meta_path(part)
            info = _read_meta(meta)
            if not info:
                continue
            total = int(info.get("total", 0) or 0)
            done = int(info.get("done", 0) or 0)
            dest = part.with_suffix("")  # strip trailing ".part"
            tasks.append(
                {
                    "id": str(dest),
                    "kind": kind,
                    "name": dest.name,
                    "url": str(info.get("url", "")),
                    "dest": str(dest),
                    "part": str(part),
                    "total": total,
                    "done": done,
                    "percent": round(done * 100 / total) if total else 0,
                }
            )
    tasks.sort(key=lambda t: (t["name"].lower(), t["dest"]))
    return tasks


def discard_pending(dest: Path) -> bool:
    """Delete a pending download's ``.part`` and its metadata sidecar.

    Returns ``True`` when anything was removed, so callers can tell a "gone
    already" discard from a real cleanup.
    """
    part = _part_path(dest)
    meta = _meta_path(part)
    removed = False
    if part.exists():
        part.unlink()
        removed = True
    if meta.exists():
        meta.unlink()
        removed = True
    return removed
