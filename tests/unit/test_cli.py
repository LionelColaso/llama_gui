from __future__ import annotations

import contextlib
import io
import json

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
    """A wrong action with --json must still emit the JSON envelope.

    Regression: the decision was read from ``sys.argv`` rather than the argv
    passed to ``main()``, so any caller not going through the process command
    line (tests, embedded use) got a human error instead of the contract's
    JSON. The exit code alone did not catch it, so the output is checked too.
    """
    out = io.StringIO()
    with (
        pytest.raises(SystemExit) as exc,
        contextlib.redirect_stdout(out),
    ):
        main(["bogus", "--json"])
    assert exc.value.code == 5
    envelope = json.loads(out.getvalue())
    assert envelope["ok"] is False
    assert envelope["exit_code"] == 5
    assert envelope["contract_version"] == "4"


def test_bad_action_human() -> None:
    """Without --json the error goes to stderr as plain text, and nothing
    is written to stdout, so a JSON consumer never sees a non-JSON line."""
    out, err = io.StringIO(), io.StringIO()
    with (
        pytest.raises(SystemExit) as exc,
        contextlib.redirect_stdout(out),
        contextlib.redirect_stderr(err),
    ):
        main(["bogus"])
    assert exc.value.code == 5
    assert out.getvalue() == ""
    assert "error:" in err.getvalue()


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
