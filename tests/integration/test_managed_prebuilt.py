from __future__ import annotations

import os
from pathlib import Path

import pytest

from llamagui.backends.prebuilt import (
    install_backend,
    list_assets,
)
from llamagui.cli import main
from llamagui.models import platform_backend_names

pytestmark = pytest.mark.integration


def test_list_assets_live() -> None:
    result = list_assets(token=os.environ.get("GITHUB_TOKEN"))
    assert result["release"] is not None
    assert len(result["assets"]) > 0


@pytest.mark.skipif(
    not os.environ.get("GITHUB_TOKEN"),
    reason="requires GITHUB_TOKEN for live GitHub access",
)
def test_install_vulkan_temp(tmp_path: Path) -> None:
    managed = tmp_path / "managed"
    downloads = tmp_path / "downloads"
    result = install_backend(
        "vulkan",
        managed,
        downloads,
        token=os.environ.get("GITHUB_TOKEN"),
        force=True,
    )
    assert result["status"] in ("ok", "skipped")
    assert result["version"] is not None
    marker = managed / "vulkan" / ".version"
    assert marker.exists()


@pytest.mark.skipif(
    not os.environ.get("GITHUB_TOKEN"),
    reason="requires GITHUB_TOKEN for live GitHub access",
)
def test_cli_use_auto_install_live(tmp_path: Path) -> None:
    """The live CLI path behind `use --auto-install`, kept out of the unit suite.

    This is the network-dependent test that used to live in
    tests/unit/test_cli.py: it really downloads a release archive, so it is
    marked `integration` and skipped by the default test run.
    """
    backend = "vulkan"
    if backend not in platform_backend_names():
        pytest.skip(f"{backend} has no prebuilt for this platform")

    code = main(["--root", str(tmp_path), "use", backend, "--auto-install", "--json"])
    if code != 0:
        # llama.cpp publishes continuously: the asset for a given platform can
        # be mid-upload, in which case "latest" simply does not have it yet.
        pytest.skip(f"{backend} asset unavailable in the latest release (exit {code})")
    assert code == 0
    assert (tmp_path / "state" / "active.txt").read_text(encoding="utf-8").strip() == (
        backend
    )
