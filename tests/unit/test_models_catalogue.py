"""Cross-platform backend catalogue + availability logic.

The catalogue encodes the portability contract: the same backend names map to
different download assets per OS/arch, and some backends are simply unavailable
on a given platform (no official prebuilt asset).
"""

from __future__ import annotations

import sys

import pytest

from llamagui.models import (
    BACKEND_BY_NAME,
    BACKENDS,
    backend_availability,
    backend_table,
    platform_default_backend,
)


def test_all_backend_names_present() -> None:
    names = {b.name for b in BACKENDS}
    assert names == {"vulkan", "cuda13", "cuda12", "cpu", "metal"}


def test_backend_table_lists_every_backend() -> None:
    table = backend_table()
    listed = {row["name"] for row in table}
    assert listed == {"vulkan", "cuda13", "cuda12", "cpu", "metal"}
    for row in table:
        assert "prebuilt_available" in row
        assert "unavailable_reason" in row


def test_asset_patterns_differ_per_platform() -> None:
    backend = BACKEND_BY_NAME["vulkan"]
    win = backend.asset_pattern("win32")
    linux = backend.asset_pattern("linux")
    assert win is not None and linux is not None
    assert win != linux
    assert "vulkan" in win
    assert win.endswith(".zip")
    assert "tar" in linux and "gz" in linux


def test_asset_pattern_is_valid_regex() -> None:
    import re

    pattern = BACKEND_BY_NAME["cuda12"].asset_pattern("win32")
    assert pattern is not None
    compiled = re.compile(pattern)
    assert compiled.match("llama-12345-bin-win-cuda-12.4-x64.zip")


def test_cuda_runtime_patterns_are_pinned_to_major_version() -> None:
    cuda12 = BACKEND_BY_NAME["cuda12"]
    cuda13 = BACKEND_BY_NAME["cuda13"]
    assert cuda12.cudart_pattern is not None
    assert cuda13.cudart_pattern is not None
    assert "12" in cuda12.cudart_pattern
    assert "13" in cuda13.cudart_pattern
    assert "13" not in cuda12.cudart_pattern
    assert "12" not in cuda13.cudart_pattern


def test_platform_default_is_supported_here() -> None:
    default = platform_default_backend()
    assert default in {b.name for b in BACKENDS}
    assert default in BACKEND_BY_NAME


@pytest.mark.skipif(sys.platform != "darwin", reason="metal only meaningful on macOS")
def test_metal_is_available_on_darwin() -> None:
    availability = backend_availability("metal", platform="darwin", arch="arm64")
    assert availability["prebuilt"]


def test_availability_reports_reason_when_unavailable() -> None:
    # metal has no Windows prebuilt.
    availability = backend_availability("metal", platform="win32", arch="x64")
    assert availability["prebuilt"] is False
    assert availability["reason"]


def test_cpu_backend_available_where_prebuilt_exists() -> None:
    for platform in ("win32", "linux"):
        availability = backend_availability("cpu", platform=platform, arch="x64")
        assert availability["prebuilt"]
    # macOS ships a single universal (Metal) prebuilt; there is no separate
    # cpu asset, so cpu is not an installable backend there.
    availability = backend_availability("cpu", platform="darwin", arch="arm64")
    assert availability["prebuilt"] is False
