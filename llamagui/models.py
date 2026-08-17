"""Domain types: the backend catalogue and the PROGRESS line protocol.

The backend catalogue is **data**, not code paths: adding a backend means
adding one :class:`Backend` row (invariant #10 in ``Agent.md``). Every consumer
— resolver, prebuilt downloader, CLI ``describe``, GUI — reads this table, so
Windows, Linux and macOS stay consistent with a single edit.
"""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass, field
from typing import Any, cast

from .paths import arch_key, platform_key


class Source(enum.StrEnum):
    MANAGED_PREBUILT = "managed-prebuilt"
    #: Legacy, read-only: label for ``.version`` markers written by older
    #: versions' from-source builds. Artifacts still resolve; never produced
    #: again (the build path was removed).
    MANAGED_BUILD = "managed-build"
    #: ``llama-server`` found on ``PATH`` (OS install), used when the
    #: "Use OS installed llama.cpp" toggle is on.
    SYSTEM = "system"


@dataclass(frozen=True)
class Backend:
    """One selectable compute backend.

    ``assets`` maps a platform key (``win32`` / ``linux`` / ``darwin``) to a
    regular expression matching the llama.cpp release asset for that platform.
    ``{arch}`` is substituted with ``x64`` or ``arm64``. A platform missing from
    the mapping has no official prebuilt and cannot be downloaded.
    """

    name: str
    notes: str
    assets: dict[str, str] = field(default_factory=lambda: cast("dict[str, str]", {}))
    #: Regex for the matching CUDA runtime pack (Windows only).
    cudart_pattern: str | None = None
    #: True when the backend cannot run without its CUDA runtime pack.
    needs_cudart: bool = False

    def asset_pattern(
        self, platform: str | None = None, arch: str | None = None
    ) -> str | None:
        """Return the release-asset regex for a platform, or None if unavailable."""
        pattern = self.assets.get(platform or platform_key())
        if pattern is None:
            return None
        return pattern.format(arch=arch or arch_key())

    def has_prebuilt(
        self, platform: str | None = None, arch: str | None = None
    ) -> bool:
        return self.asset_pattern(platform, arch) is not None


#: Windows CUDA assets are named ``...-cuda-12.4-x64.zip``; the minor version
#: moves between releases, so match the major version and any separator.
_WIN_CUDA12 = r"llama-.*-bin-win-cuda-12[.\-_]\d+-x64\.zip"
_WIN_CUDA13 = r"llama-.*-bin-win-cuda-13[.\-_]\d+-x64\.zip"
# Anchored on ``cudart-`` so the ~390 MB DLL pack never collides with the
# ~150 MB binary archive, and on the CUDA major version so cuda12 can never
# pick up the CUDA 13 runtime (invariant #1 / #11).
_WIN_CUDART12 = r"cudart-.*cuda-12[.\-_]\d+-x64\.zip"
_WIN_CUDART13 = r"cudart-.*cuda-13[.\-_]\d+-x64\.zip"


BACKENDS: tuple[Backend, ...] = (
    Backend(
        name="vulkan",
        notes="Vulkan GPU acceleration; broadest GPU support (default on Windows/Linux)",
        assets={
            "win32": r"llama-.*-bin-win-vulkan-x64\.zip",
            "linux": r"llama-.*-bin-ubuntu-vulkan-{arch}\.tar\.gz",
        },
    ),
    Backend(
        name="cuda13",
        notes="NVIDIA CUDA 13.x build; needs a CUDA 13 driver/toolkit",
        assets={"win32": _WIN_CUDA13},
        cudart_pattern=_WIN_CUDART13,
        needs_cudart=False,
    ),
    Backend(
        name="cuda12",
        notes="NVIDIA CUDA 12.x build; ships its own CUDA 12 runtime DLLs",
        assets={"win32": _WIN_CUDA12},
        cudart_pattern=_WIN_CUDART12,
        needs_cudart=True,
    ),
    Backend(
        name="cpu",
        notes="Portable CPU-only build; fallback where a GPU prebuilt is missing (Windows/Linux)",
        assets={
            "win32": r"llama-.*-bin-win-cpu-{arch}\.zip",
            "linux": r"llama-.*-bin-ubuntu-{arch}\.tar\.gz",
        },
    ),
    Backend(
        name="metal",
        notes="Apple Metal + Accelerate build (macOS only)",
        assets={"darwin": r"llama-.*-bin-macos-{arch}\.tar\.gz"},
    ),
)

BACKEND_BY_NAME: dict[str, Backend] = {b.name: b for b in BACKENDS}


def get_backend(name: str) -> Backend | None:
    return BACKEND_BY_NAME.get(name)


def backend_names() -> list[str]:
    """Every backend known to the app, regardless of platform."""
    return [b.name for b in BACKENDS]


def platform_backends(
    platform: str | None = None, arch: str | None = None
) -> list[Backend]:
    """Backends that have an official prebuilt for the platform."""
    plat = platform or platform_key()
    return [b for b in BACKENDS if b.has_prebuilt(plat, arch)]


def platform_backend_names(
    platform: str | None = None, arch: str | None = None
) -> list[str]:
    return [b.name for b in platform_backends(platform, arch)]


def platform_default_backend(
    platform: str | None = None, arch: str | None = None
) -> str:
    """Best default backend for a platform.

    Preference order is GPU-accelerated first, then CPU, and only backends with
    an official prebuilt for the running platform/arch are considered so a
    fresh install can always download something that works.
    """
    plat = platform or platform_key()
    preference = {
        "win32": ("vulkan", "cuda13", "cuda12", "cpu"),
        "linux": ("vulkan", "cpu"),
        "darwin": ("metal", "cpu"),
    }[plat]
    for name in preference:
        backend = BACKEND_BY_NAME[name]
        if backend.has_prebuilt(plat, arch):
            return name
    return "cpu"


def backend_availability(
    name: str, platform: str | None = None, arch: str | None = None
) -> dict[str, Any]:
    """Describe how (or whether) a backend can be obtained on a platform."""
    plat = platform or platform_key()
    backend = BACKEND_BY_NAME.get(name)
    if backend is None:
        return {
            "name": name,
            "prebuilt": False,
            "reason": f"Unknown backend '{name}'",
        }
    prebuilt = backend.has_prebuilt(plat, arch)
    reason = "" if prebuilt else f"No official {plat} prebuilt for '{name}'."
    return {
        "name": name,
        "prebuilt": prebuilt,
        "reason": reason,
    }


def backend_table(
    platform: str | None = None, arch: str | None = None
) -> list[dict[str, Any]]:
    """Serializable catalogue used by ``describe`` and the GUI."""
    plat = platform or platform_key()
    rows: list[dict[str, Any]] = []
    for backend in BACKENDS:
        availability = backend_availability(backend.name, plat, arch)
        rows.append(
            {
                "name": backend.name,
                "notes": backend.notes,
                "needs_cudart": backend.needs_cudart,
                "prebuilt_available": availability["prebuilt"],
                "unavailable_reason": availability["reason"],
            }
        )
    return rows


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


__all__ = [
    "BACKENDS",
    "BACKEND_BY_NAME",
    "PROGRESS_RE",
    "Backend",
    "ProgressEvent",
    "Source",
    "backend_availability",
    "backend_names",
    "backend_table",
    "get_backend",
    "parse_progress_line",
    "platform_backend_names",
    "platform_backends",
    "platform_default_backend",
]
