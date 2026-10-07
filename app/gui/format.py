"""Small formatting helpers shared by the GUI.

Anything that turns a stored number into something a person reads belongs here
rather than being copied into each dialog: two copies of a size formatter is
one copy too many, and the two drifted before.
"""

from __future__ import annotations


def human_bytes(size: float) -> str:
    """A byte count as a short human string, e.g. ``942.3 MB``.

    Binary units, one decimal for anything above a kilobyte and none for plain
    bytes: ``"512 B"`` reads better than ``"512.0 B"``.
    """
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"  # pragma: no cover - loop always returns


__all__ = ["human_bytes"]
