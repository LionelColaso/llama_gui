"""Model store: list, download and remove .gguf models in the models dir.

Models are plain files in a user-configurable directory (default
``<root>/models``). There is no index and no lock: listing is a directory scan,
downloading streams into a ``.part`` temp file that is renamed into place only
when complete, so a crash or a cancelled download never leaves a truncated
model that the server would try to load.

Downloads are resumable: an existing ``.part`` file is continued with an HTTP
``Range`` request. Progress is forwarded through the same
:func:`app.backends.prebuilt.emit_progress` channel as backend downloads,
so the GUI progress bar works for both.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .backends.prebuilt import emit_progress
from .download import DownloadCancelled, DownloadError, stream_download
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


def _dir_fingerprint(models_dir: Path) -> tuple[Any, ...]:
    """A cheap signature of the tree: names, sizes and mtimes of .gguf files.

    Enough to detect a model being added, removed, renamed, completed or
    deleted, which is what a cached listing must react to. Deliberately much
    cheaper than a full ``stat`` per file when nothing has changed, because
    this is what the dashboard's poll calls.
    """
    entries: list[tuple[str, int, float]] = []
    try:
        for path in models_dir.rglob("*.gguf"):
            if _skip_hidden(path, models_dir):
                continue
            try:
                st = path.stat()
            except OSError:
                continue
            entries.append((_rel_model_name(path, models_dir), st.st_size, st.st_mtime))
    except OSError:
        return ()
    return tuple(sorted(entries))


#: Cached listings keyed by models dir. A dashboard poll calls this every few
#: seconds; re-listing a large .gguf library each time hammers the disk, so the
#: result is reused until the tree actually changes.
_MODEL_LISTING_CACHE: dict[Path, tuple[tuple[Any, ...], list[ModelInfo]]] = {}


def clear_model_cache() -> None:
    """Drop the cached model listings (used by mutations that change the tree)."""
    _MODEL_LISTING_CACHE.clear()


def list_models_cached(models_dir: Path) -> list[ModelInfo]:
    """:func:`list_models`, memoised until the directory contents change.

    The fingerprint still walks the tree, so this is not free, but it avoids
    the per-file ``stat`` + ``ModelInfo`` construction and the sort on every
    poll, and it returns immediately for an unchanged library.
    """
    if not models_dir.is_dir():
        return []
    key = models_dir
    fingerprint = _dir_fingerprint(models_dir)
    cached = _MODEL_LISTING_CACHE.get(key)
    if cached is not None and cached[0] == fingerprint:
        return list(cached[1])
    models = list_models(models_dir)
    _MODEL_LISTING_CACHE[key] = (fingerprint, models)
    return list(models)


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
    try:
        stream_download(
            url, final_path, component="model", timeout=timeout, emit=emit_progress
        )
    except DownloadCancelled as e:
        raise ModelDownloadError(f"Download cancelled: {url}") from e
    except DownloadError as e:
        raise ModelDownloadError(str(e)) from e
    return DownloadData(
        name=name, path=str(final_path), size_bytes=final_path.stat().st_size
    )


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
    "clear_model_cache",
    "download_model",
    "list_models",
    "list_models_cached",
    "model_name_from_url",
    "remove_model",
]
