"""Moving existing data when a path setting changes.

The Settings page lets the user point the managed root and/or the models
directory somewhere else. Changing where things *live* does not move them, so
this module offers that move: it walks the source tree, puts every file into the
destination and only then drops what is left of the source.

Safety rules that shape the code:

* **Nothing is overwritten.** The destination must be missing or empty, so a
  mis-typed path can never destroy an existing model library or backend tree.
* **The source outlives a failure.** Files are renamed (same filesystem) or
  copied (different one) one at a time, and the source tree is only removed
  after every file arrived.
* **Links are never followed.** ``managed/current`` is a symlink/junction with an
  absolute target; descending into it would copy the active backend twice.
* **The source root is never deleted.** A filtered move (models: ``*.gguf``)
  only prunes the directories it emptied, leaving anything else — an
  interrupted ``.part`` download, say — exactly where the user put it.

:func:`relocate` moves a tree and :func:`copy` duplicates it, for the user who
wants the old location kept as a backup; both share :func:`transfer`, so they
cannot drift apart on what "safe" means.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable, Iterator
from contextlib import suppress
from pathlib import Path
from typing import Literal

from .schemas import EngineError, ExitCode

#: ``emit(done_bytes, total_bytes, phase, overall)`` — the GUI progress slot.
Emit = Callable[[int, int, str, float | None], None]

#: How a tree reaches its new location: ``"move"`` drops the old copy once every
#: file arrived, ``"copy"`` keeps it as a backup.
Transfer = Literal["move", "copy"]


def is_link(path: Path) -> bool:
    """True for a symlink or a Windows junction (a directory that is not a real one)."""
    if path.is_symlink():
        return True
    isjunction = getattr(os.path, "isjunction", None)
    return bool(isjunction and isjunction(path))


def iter_files(root: Path, suffixes: tuple[str, ...] | None = None) -> Iterator[Path]:
    """Every regular file under ``root``, in a stable order, links excluded."""
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if not is_link(Path(dirpath, d)))
        for name in sorted(filenames):
            path = Path(dirpath, name)
            if is_link(path):
                continue
            if suffixes and path.suffix.lower() not in suffixes:
                continue
            yield path


def scan(root: Path, suffixes: tuple[str, ...] | None = None) -> tuple[int, int]:
    """``(files, bytes)`` that a transfer of ``root`` would carry."""
    if not root.is_dir():
        return (0, 0)
    files = 0
    total = 0
    for path in iter_files(root, suffixes):
        try:
            total += path.stat().st_size
        except OSError:
            continue
        files += 1
    return (files, total)


def is_empty_dir(path: Path) -> bool:
    """True when ``path`` is missing or holds no entries at all."""
    if not path.exists():
        return True
    return not any(path.iterdir())


def contains(parent: Path, child: Path) -> bool:
    """True when ``child`` is ``parent`` or lives under it.

    Used to refuse a move whose destination sits inside its own source, which
    would copy a tree into itself.
    """
    return parent == child or parent in child.parents


def relocate(
    source: Path,
    destination: Path,
    *,
    suffixes: tuple[str, ...] | None = None,
    emit: Emit | None = None,
) -> tuple[int, int]:
    """Move every file under ``source`` into ``destination``.

    ``suffixes`` restricts the transfer (models move ``.gguf`` only). Returns
    ``(files, bytes)``. Raises :class:`EngineError` when the destination already
    holds files, which is the one case where a transfer could destroy data.
    """
    return transfer(source, destination, suffixes, emit, remove_source=True)


def copy(
    source: Path,
    destination: Path,
    *,
    suffixes: tuple[str, ...] | None = None,
    emit: Emit | None = None,
) -> tuple[int, int]:
    """Copy every file under ``source`` into ``destination``, keeping the source.

    The mirror image of :func:`relocate`, for the user who wants the old location
    kept as a backup. The destination guarantee is the same.
    """
    return transfer(source, destination, suffixes, emit, remove_source=False)


def transfer(
    source: Path,
    destination: Path,
    suffixes: tuple[str, ...] | None = None,
    emit: Emit | None = None,
    *,
    remove_source: bool,
) -> tuple[int, int]:
    files, total = scan(source, suffixes=suffixes)
    if files == 0:
        return (0, 0)

    if not is_empty_dir(destination):
        raise EngineError(
            ExitCode.BAD_ARGUMENT,
            f"{destination} already contains files. Move or remove them first, "
            "or choose another location.",
        )

    phase = "move" if remove_source else "copy"

    # A whole tree that lands on the same filesystem is one atomic rename.
    if (
        remove_source
        and suffixes is None
        and not destination.exists()
        and _same_device(source, destination.parent)
    ):
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(source, destination)
        _report(emit, total, total, phase, 1.0)
        return (files, total)

    destination.mkdir(parents=True, exist_ok=True)
    done = 0
    for path in iter_files(source, suffixes):
        size = path.stat().st_size
        target = destination / path.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        if remove_source and _same_device(path, target.parent):
            os.replace(path, target)
        else:
            shutil.copy2(path, target)
        done += size
        _report(emit, done, total, phase, done / total if total else 1.0)

    if not remove_source:
        return (files, total)
    if suffixes is None:
        shutil.rmtree(source)
    else:
        _prune_empty(source)
    return (files, total)


def _same_device(left: Path, right: Path) -> bool:
    """True when both paths sit on one filesystem, so a rename is possible."""
    try:
        return os.stat(left).st_dev == os.stat(right).st_dev
    except OSError:
        return False


def _prune_empty(root: Path) -> None:
    """Delete the sub-directories a filtered move emptied, keeping ``root``.

    Whatever the user kept (an interrupted ``.part`` download, a notes file) must
    survive, so the directory they pointed at is never removed.
    """
    for dirpath, dirnames, filenames in os.walk(root, topdown=False):
        here = Path(dirpath)
        if here == root or dirnames or filenames:
            continue
        with suppress(OSError):
            here.rmdir()


def _report(
    emit: Emit | None, done: int, total: int, phase: str, overall: float | None
) -> None:
    if emit is not None:
        emit(done, total, phase, overall)


__all__ = [
    "Emit",
    "Transfer",
    "contains",
    "copy",
    "is_empty_dir",
    "is_link",
    "iter_files",
    "relocate",
    "scan",
    "transfer",
]
