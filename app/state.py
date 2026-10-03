"""Pure-Python reads over the managed root (contract v4, §5.2).

Every function here is a **read**: a file read, a link read, or a
non-blocking socket connect. None of them spawn a subprocess, so the dashboard
can poll them on the GUI thread without a cold-start cost.

Process *control* (launch / verify / stop, pid bookkeeping) lives in
:mod:`app.lifecycle`; anything that mutates the managed root goes through the
mutation lock in :mod:`app.orchestrator`.
"""

from __future__ import annotations

import os
import socket
import struct
from pathlib import Path


def read_active_backend(root: Path) -> str | None:
    active_path = root / "state" / "active.txt"
    if not active_path.exists():
        return None
    return active_path.read_text(encoding="utf-8").strip() or None


def read_component_version(root: Path, name: str) -> tuple[str, str | None] | None:
    """Read ``managed/<name>/.version`` as ``(tag, source_label)``."""
    version_file = root / "managed" / name / ".version"
    if not version_file.exists():
        return None
    text = version_file.read_text(encoding="utf-8").strip()
    parts = text.split("\n", 1)
    tag = parts[0].strip()
    source_str = parts[1].strip() if len(parts) > 1 else ""
    return (tag, source_str)


def read_link_target(path: Path) -> str | None:
    """Resolve a symlink or Windows junction, tolerating a broken link."""
    if not path.is_symlink() and not path.exists():
        return None
    try:
        return os.readlink(str(path))
    except (OSError, NotImplementedError, ValueError):
        pass
    return _read_reparse_point(path)


def read_junction_target(root: Path) -> str | None:
    """Target of the ``managed/current`` link, or None when not set."""
    return read_link_target(root / "managed" / "current")


def _read_reparse_point(path: Path) -> str | None:
    """Read a Windows junction target from the raw reparse-point bytes."""
    try:
        handle = os.open(str(path), os.O_RDONLY)
        try:
            data = os.read(handle, 1024)
            if len(data) < 20:
                return None
            tag = struct.unpack_from("I", data, 0)[0]
            if tag != 0xA000000C:
                return None
            name_len = struct.unpack_from("H", data, 12)[0]
            raw = data[20 : 20 + name_len]
            return raw.decode("utf-16-le").rstrip("\x00")
        finally:
            os.close(handle)
    except (OSError, struct.error, UnicodeDecodeError):
        return None


def check_port(host: str, port: int, timeout: float = 0.2) -> bool:
    """True when something accepts a TCP connection on ``host:port``."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


__all__ = [
    "check_port",
    "read_active_backend",
    "read_component_version",
    "read_junction_target",
    "read_link_target",
]
