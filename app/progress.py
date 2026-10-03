"""The ``PROGRESS`` stderr line protocol (contract v4, §11).

In CLI mode the engine reports download progress as a stable 4-field line on
stderr::

    PROGRESS\t<component>\t<done>\t<total>\t<phase>

This module owns the *reading* side of that wire format, so an external tool (or
a test) can turn a captured stderr line back into structured data without
knowing the field order. The GUI never parses it — it receives
``(done, total, phase, overall)`` over a Qt signal instead (§6.14).
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class ProgressEvent:
    component: str
    bytes_done: int
    bytes_total: int
    phase: str


PROGRESS_RE = re.compile(r"^PROGRESS\t([\w-]+)\t(\d+)\t(\d+)\t(\w+)$")


def parse_progress_line(line: str) -> ProgressEvent | None:
    """Parse one ``PROGRESS`` line, or return None when it is not one."""
    m = PROGRESS_RE.match(line.strip())
    if not m:
        return None
    return ProgressEvent(
        component=m.group(1),
        bytes_done=int(m.group(2)),
        bytes_total=int(m.group(3)),
        phase=m.group(4),
    )


__all__ = ["PROGRESS_RE", "ProgressEvent", "parse_progress_line"]
