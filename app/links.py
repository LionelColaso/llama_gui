"""The ``managed/current`` backend link, per OS.

``managed/current`` points at the active backend directory. The mechanism
differs per platform and is the only place in the engine that creates links:

* POSIX — an atomically replaced symlink (``symlink`` to a temp name, then
  ``os.replace``).
* Windows — a symlink when Developer Mode allows it, otherwise a directory
  junction via ``mklink /J``, which needs no elevation.

Reading the link back is a pure read and lives in :mod:`app.state`
(:func:`~app.state.read_junction_target`). Deletion is **always** link-only:
:func:`remove_link` never touches the target's contents (invariant #3).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .paths import is_windows
from .schemas import EngineError, ExitCode


def link_current(link: Path, target: Path) -> None:
    """Point ``link`` at ``target`` using the best mechanism per OS.

    POSIX gets an atomically replaced symlink. Windows prefers a symlink (when
    Developer Mode is on) and falls back to a directory junction, which needs
    no elevation.
    """
    link.parent.mkdir(parents=True, exist_ok=True)

    if not is_windows():
        temp_link = link.with_name(link.name + ".new")
        remove_link(temp_link)
        try:
            os.symlink(str(target), str(temp_link), target_is_directory=True)
            os.replace(str(temp_link), str(link))
            return
        except (OSError, NotImplementedError, ValueError) as e:
            remove_link(temp_link)
            raise EngineError(
                ExitCode.UNEXPECTED_ERROR,
                f"Could not point 'current' at {target}: {e}",
            ) from e

    # Windows: replacing a directory link is not atomic, so drop it first.
    remove_link(link)
    try:
        os.symlink(str(target), str(link), target_is_directory=True)
        return
    except OSError, NotImplementedError, ValueError:
        remove_link(link)
    try:
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError) as e:
        raise EngineError(
            ExitCode.UNEXPECTED_ERROR,
            f"Could not point 'current' at {target}: {e}",
        ) from e


def remove_link(link: Path) -> None:
    """Delete a link without ever touching the directory it points at."""
    if link.is_symlink():
        link.unlink(missing_ok=True)
        return
    if not link.exists():
        return
    try:
        link.rmdir()  # junction / empty dir: removes the link, not the target
    except OSError:
        link.unlink(missing_ok=True)


__all__ = ["link_current", "remove_link"]
