from __future__ import annotations

import pytest

from llamagui.cli import main


def test_describe_json() -> None:
    code = main(["describe", "--json"])
    assert code == 0


def test_status_json() -> None:
    code = main(["status", "--json"])
    assert code == 0


def test_resolve_json() -> None:
    code = main(["resolve", "--json"])
    assert code == 0


def test_stop_json(tmp_path: str) -> None:
    code = main(["--root", str(tmp_path), "stop", "--json"])
    assert code == 0


def test_bad_action_json() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["bogus", "--json"])
    assert exc.value.code == 5


def test_bad_action_human() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["bogus"])
    assert exc.value.code == 5


def test_use_empty_backend(tmp_path: str) -> None:
    code = main(["--root", str(tmp_path), "use", "vulkan", "--json"])
    assert code != 0


def test_use_auto_install_succeeds(tmp_path: str) -> None:
    code = main(["--root", str(tmp_path), "use", "vulkan", "--auto-install", "--json"])
    if code != 0:
        # This is a network-dependent test: llama.cpp's "latest" release can be
        # mid-publish (GPU assets like win-vulkan-x64.zip still uploading), so
        # the vulkan asset may be temporarily absent. Skip rather than fail.
        pytest.skip(
            f"vulkan asset unavailable in latest llama.cpp release (exit {code})"
        )
    assert code == 0
