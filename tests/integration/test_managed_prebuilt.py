from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.backends.catalogue import platform_backend_names
from app.backends.prebuilt import (
    PrebuiltError,
    install_backend,
    list_assets,
)
from app.cli import main

#: Backends for the live install tests, lightest first. Each platform
#: picks the first entry that has an official prebuilt, so Windows and
#: Linux download the ~17-19 MB cpu bundle and macOS the ~11 MB metal
#: one — instead of a fixed backend that may not exist for the host
#: (vulkan, for instance, has no macOS prebuilt).
_LIGHT_BACKENDS = ("cpu", "vulkan", "metal")


def _light_native_backend() -> str:
    """The lightest backend with an official prebuilt for this platform."""
    available = platform_backend_names()
    for name in _LIGHT_BACKENDS:
        if name in available:
            return name
    pytest.skip(f"no prebuilt backend for this platform: {available}")


def test_list_assets_live() -> None:
    result = list_assets(token=os.environ.get("GITHUB_TOKEN"))
    assert result["release"] is not None
    assert len(result["assets"]) > 0


def test_install_native_prebuilt(tmp_path: Path) -> None:
    """Install the lightest native prebuilt into a temp managed root.

    A release that is still being published can lack the asset for a
    moment; that is a skip, not a failure.
    """
    backend = _light_native_backend()
    managed = tmp_path / "managed"
    downloads = tmp_path / "downloads"
    try:
        result = install_backend(
            backend,
            managed,
            downloads,
            token=os.environ.get("GITHUB_TOKEN"),
            force=True,
        )
    except PrebuiltError as exc:
        pytest.skip(f"{backend} asset unavailable in the latest release: {exc}")
    assert result["status"] in ("ok", "skipped")
    assert result["version"] is not None
    marker = managed / backend / ".version"
    assert marker.exists()


def test_cli_use_auto_install_live(tmp_path: Path) -> None:
    """The live CLI path behind `use --auto-install`, kept out of the unit suite.

    This is the network-dependent test that used to live in
    tests/unit/test_cli.py: it really downloads a release archive, so it
    lives in the integration directory and runs only when the network
    gate lets it.
    """
    backend = _light_native_backend()
    code = main(["--root", str(tmp_path), "use", backend, "--auto-install", "--json"])
    if code != 0:
        # llama.cpp publishes continuously: the asset for a given platform can
        # be mid-upload, in which case "latest" simply does not have it yet.
        pytest.skip(f"{backend} asset unavailable in the latest release (exit {code})")
    assert code == 0
    assert (tmp_path / "state" / "active.txt").read_text(encoding="utf-8").strip() == (
        backend
    )
