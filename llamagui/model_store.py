"""Model store: list, download and remove .gguf models in the models dir.

Models are plain files in a user-configurable directory (default
``<root>/models``). There is no index and no lock: listing is a directory scan,
downloading streams into a ``.part`` temp file that is renamed into place only
when complete, so a crash or a cancelled download never leaves a truncated
model that the server would try to load.

Downloads are resumable: an existing ``.part`` file is continued with an HTTP
``Range`` request. Progress is forwarded through the same
:func:`llamagui.backends.prebuilt.emit_progress` channel as backend downloads,
so the GUI progress bar works for both.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

import httpx

from .backends.prebuilt import emit_progress
from .schemas import DownloadData, ModelInfo

#: Files that count as models. Lower-cased suffix match.
_MODEL_SUFFIXES = (".gguf",)


class ModelDownloadError(Exception):
    """A model download/management step failed (network, HTTP, bad name)."""


def _is_model_file(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in _MODEL_SUFFIXES


def _rel_model_name(path: Path, models_dir: Path) -> str:
    """Model identity: the path relative to the models dir, forward slashes.

    Nested directories keep their structure (``empero-ai/…/model.gguf``); the
    name is what the GUI table, ``set-model``, ``remove-model`` and the launch
    argument all use, so a model library organised in folders works as-is.
    """
    return path.relative_to(models_dir).as_posix()


def _skip_hidden(path: Path, models_dir: Path) -> bool:
    """Skip files under a dot-directory (caches, temp dirs)."""
    try:
        rel = path.relative_to(models_dir)
    except ValueError:
        return False
    return any(part.startswith(".") for part in rel.parts[:-1])


def list_models(models_dir: Path) -> list[ModelInfo]:
    """All models under ``models_dir`` (recursively), sorted by name.

    Missing dir -> empty list. Hidden directories are skipped.
    """
    if not models_dir.is_dir():
        return []
    infos: list[ModelInfo] = []
    for path in models_dir.rglob("*.gguf"):
        if not _is_model_file(path) or _skip_hidden(path, models_dir):
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        infos.append(
            ModelInfo(
                name=_rel_model_name(path, models_dir),
                size_bytes=stat.st_size,
                modified=datetime.fromtimestamp(stat.st_mtime, tz=UTC).strftime(
                    "%Y-%m-%d %H:%M"
                ),
            )
        )
    infos.sort(key=lambda m: m.name.lower())
    return infos


def model_name_from_url(url: str) -> str:
    """Derive the file name for a download URL.

    Hugging Face URLs (``.../resolve/main/model.gguf``) end in the asset name;
    a trailing query string (``?download=true``) is stripped. When the URL has
    no usable file name (e.g. an HF repo root) a generic name is generated.
    """
    clean = url.split("?", 1)[0].split("#", 1)[0]
    segment = clean.rstrip("/").rsplit("/", 1)[-1]
    if segment and "." in segment and not segment.startswith("."):
        return segment
    # No asset name in the URL: derive a short, stable, readable name.
    digest = re.sub(r"[^0-9a-f]", "", url.lower())
    suffix = digest[:12] if len(digest) >= 12 else digest or "model"
    return f"model-{suffix}.gguf"


def download_model(url: str, models_dir: Path, timeout: float = 30.0) -> DownloadData:
    """Stream ``url`` into ``models_dir`` with resume + progress.

    Raises :class:`ModelDownloadError` on HTTP or network failure (the partial
    file is kept so a retry can resume).
    """
    models_dir.mkdir(parents=True, exist_ok=True)
    name = model_name_from_url(url)
    final_path = models_dir / name
    part_path = models_dir / f"{name}.part"

    # Resume offset from a previous interrupted download.
    offset = part_path.stat().st_size if part_path.exists() else 0
    headers: dict[str, str] = {}
    if offset:
        headers["Range"] = f"bytes={offset}-"

    mode = "ab" if offset else "wb"
    try:
        done, size_total = _stream_download(
            url, part_path, headers, offset, mode, timeout
        )
    except httpx.HTTPError as e:
        raise ModelDownloadError(f"Download failed: {e}") from e

    if size_total and done != size_total:
        raise ModelDownloadError(
            f"Download size mismatch for {name}: got {done} of {size_total} bytes"
        )

    part_path.replace(final_path)
    return DownloadData(name=name, path=str(final_path), size_bytes=done)


def _stream_download(
    url: str,
    part_path: Path,
    headers: dict[str, str],
    offset: int,
    mode: str,
    timeout: float,
) -> tuple[int, int]:
    """Stream the URL body into ``part_path``; returns ``(done, size_total)``.

    Lets :func:`download_model` keep its httpx concerns in one place and map
    every ``httpx.HTTPError`` (connect, read, status) to ``ModelDownloadError``.
    """
    with httpx.stream(
        "GET", url, headers=headers, timeout=timeout, follow_redirects=True
    ) as response:
        if offset and response.status_code == 200:
            # Server ignored the Range header: start from scratch.
            offset = 0
            mode = "wb"
        response.raise_for_status()

        size_total = offset
        content_range = response.headers.get("Content-Range")
        if content_range:
            # "bytes 100-199/200" -> total is the last field.
            with_last = content_range.rsplit("/", 1)[-1]
            if with_last.isdigit():
                size_total = int(with_last)
        elif response.status_code == 200:
            length = response.headers.get("Content-Length")
            if length and length.isdigit():
                size_total = int(length)
        elif size_total == offset:
            length = response.headers.get("Content-Length")
            if length and length.isdigit():
                size_total = offset + int(length)

        emit_progress("model", offset, size_total, "download")
        done = offset
        with part_path.open(mode, buffering=1024 * 1024) as handle:
            for chunk in response.iter_bytes(1024 * 1024):
                if not chunk:
                    continue
                handle.write(chunk)
                done += len(chunk)
                emit_progress("model", done, size_total, "download")
    return done, size_total


def remove_model(models_dir: Path, name: str) -> None:
    """Delete one model file. Raises ``FileNotFoundError`` when missing.

    ``name`` may be a nested relative path (``folder/model.gguf``). Anything
    that escapes ``models_dir`` (absolute paths, ``../``, traversal) is
    rejected by design.
    """
    base = models_dir.resolve()
    candidate = (models_dir / name).resolve()
    if not candidate.is_relative_to(base):
        raise ModelDownloadError(f"Invalid model name: {name}")
    if not _is_model_file(candidate):
        raise FileNotFoundError(f"No such model: {name}")
    candidate.unlink()


__all__ = [
    "ModelDownloadError",
    "download_model",
    "list_models",
    "model_name_from_url",
    "remove_model",
]
