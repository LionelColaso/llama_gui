from __future__ import annotations

import os
from pathlib import Path

import pytest

from llamagui.backends.prebuilt import (
    install_backend,
    list_assets,
)

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
