from __future__ import annotations

import enum
import re
from dataclasses import dataclass
from typing import Any


class Source(enum.StrEnum):
    POINTED = "pointed"
    MANAGED_PREBUILT = "managed-prebuilt"
    MANAGED_BUILD = "managed-build"
    SYSTEM = "system"


BACKEND_TABLE: list[dict[str, Any]] = [
    {
        "name": "vulkan",
        "notes": "Default; best prefill on GTX 1650 (~189 t/s)",
        "needs_cudart": False,
    },
    {"name": "cuda13", "notes": "CUDA 13.3 toolkit; arch 75", "needs_cudart": False},
    {
        "name": "cuda12",
        "notes": "CUDA 12.4 binary with bundled cudart DLLs",
        "needs_cudart": True,
    },
]


@dataclass
class ProgressEvent:
    component: str
    bytes_done: int
    bytes_total: int
    phase: str


PROGRESS_RE = re.compile(r"^PROGRESS\t([\w-]+)\t(\d+)\t(\d+)\t(\w+)$")


def parse_progress_line(line: str) -> ProgressEvent | None:
    m = PROGRESS_RE.match(line.strip())
    if not m:
        return None
    return ProgressEvent(
        component=m.group(1),
        bytes_done=int(m.group(2)),
        bytes_total=int(m.group(3)),
        phase=m.group(4),
    )
