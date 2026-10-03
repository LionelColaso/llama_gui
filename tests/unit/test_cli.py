from __future__ import annotations

import argparse
import contextlib
import io
import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from app.cli import (
    _FLAG_TOKEN_PREFIX,
    _decode_flag_positional,
    _encode_flag_positional,
    main,
)


class TestFlagPositionalArgv:
    """``set-arg <flag>`` takes a token starting with a dash (regression).

    argparse read it as an option of the ``set-arg`` sub-parser and rejected it,
    so the documented ``set-arg <flag> [value]`` form failed for every real flag.
    """

    def test_the_flag_is_hidden_from_argparse_then_restored(self) -> None:
        encoded = _encode_flag_positional(["set-arg", "--ctx-size", "16384", "--json"])
        assert encoded == [
            "set-arg",
            f"{_FLAG_TOKEN_PREFIX}--ctx-size",
            "16384",
            "--json",
        ], "only the flag is encoded; trailing options must still parse"

    def test_a_short_alias_is_encoded_too(self) -> None:
        assert _encode_flag_positional(["set-arg", "-t", "40"])[1] == (
            f"{_FLAG_TOKEN_PREFIX}-t"
        )

    def test_a_plain_positional_is_untouched(self) -> None:
        argv = ["set-arg", "not-a-flag", "value"]
        assert _encode_flag_positional(argv) == argv

    def test_other_actions_are_untouched(self) -> None:
        argv = ["server-args", "--flag", "--ctx-size"]
        assert _encode_flag_positional(argv) == argv

    def test_decode_restores_the_flag(self) -> None:
        args = argparse.Namespace(flag=f"{_FLAG_TOKEN_PREFIX}--ctx-size")
        _decode_flag_positional(args)
        assert args.flag == "--ctx-size"

    def test_decode_leaves_an_untouched_flag_alone(self) -> None:
        args = argparse.Namespace(flag="--ctx-size")
        _decode_flag_positional(args)
        assert args.flag == "--ctx-size"

    def test_set_arg_with_a_dash_flag_end_to_end(self, tmp_path: Path) -> None:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(
                ["--root", str(tmp_path), "set-arg", "--flash-attn", "on", "--json"]
            )
        assert code == 0
        envelope = json.loads(out.getvalue())
        assert envelope["data"]["args"][0]["flag"] == "--flash-attn"
        assert envelope["data"]["scope"] == "global"

    def test_set_arg_scoped_to_a_model_end_to_end(self, tmp_path: Path) -> None:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(
                [
                    "--root",
                    str(tmp_path),
                    "set-arg",
                    "--ctx-size",
                    "16384",
                    "--model",
                    "big.gguf",
                    "--json",
                ]
            )
        assert code == 0
        data = json.loads(out.getvalue())["data"]
        assert data["scope"] == "model"
        assert data["model"] == "big.gguf"
        assert data["args"][0]["value"] == "16384"


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


def test_use_auto_install_obtains_the_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``use --auto-install`` must fetch, extract and activate, offline.

    Regression: this used to hit the real GitHub releases API and pull a
    ~150 MB vulkan archive, so the unit suite was network-dependent and slow,
    and it skipped itself whenever llama.cpp happened to be mid-publish. The
    network is stubbed here; the live download is covered by the ``integration``
    suite instead.
    """
    from app.backends.catalogue import platform_backend_names

    backend = next(iter(platform_backend_names()), "vulkan")
    assets = [{"name": f"{backend}-asset.zip", "browser_download_url": "https://x/y"}]

    calls: list[str] = []

    def fake_release(repo: str, token: str | None = None) -> dict[str, object]:
        return {"tag_name": "b1", "assets": assets}

    def fake_obtain(self: object, name: str, force: bool = False) -> object:
        calls.append(name)
        target = Path(str(self.root)) / "managed" / name  # type: ignore[attr-defined]
        target.mkdir(parents=True, exist_ok=True)
        (target / "llama-server").write_text("", encoding="utf-8")
        return None

    monkeypatch.setattr("app.backends.prebuilt.latest_release", fake_release)
    monkeypatch.setattr("app.orchestrator.Orchestrator._obtain_backend", fake_obtain)

    code = main(["--root", str(tmp_path), "use", backend, "--auto-install", "--json"])

    assert code == 0, f"use --auto-install failed for {backend}"
    assert calls == [backend], "the backend was not obtained"
    assert (tmp_path / "state" / "active.txt").read_text(encoding="utf-8").strip() == (
        backend
    )


# ─── exit codes for user-facing filesystem errors ─────────────────────────


def _envelope_for(exc: Exception, tmp_path: Path) -> dict[str, Any]:
    """Run one CLI action that raises ``exc``, and return its JSON envelope."""
    out = io.StringIO()
    with (
        patch("app.cli.Orchestrator.status", side_effect=exc),
        contextlib.redirect_stdout(out),
    ):
        code = main(["--root", str(tmp_path), "status", "--json"])
    assert code != 0
    envelope: dict[str, Any] = json.loads(out.getvalue())
    return envelope


def test_missing_file_exits_not_available(tmp_path: Path) -> None:
    """A missing model/file is a setup problem (2), not an engine fault (1)."""
    env = _envelope_for(FileNotFoundError(2, "No such file", "/x/m.gguf"), tmp_path)
    assert env["exit_code"] == 2
    assert env["ok"] is False


def test_permission_error_exits_not_available(tmp_path: Path) -> None:
    env = _envelope_for(PermissionError(13, "denied", "/ro/models"), tmp_path)
    assert env["exit_code"] == 2


def test_permission_error_suggests_a_fix(tmp_path: Path) -> None:
    """The message must tell the user what to do, not name the exception type."""
    env = _envelope_for(PermissionError(13, "denied", "/ro/models"), tmp_path)
    message = str(env["error"])
    assert "Permission denied" in message
    assert "write access" in message
    assert "PermissionError" not in message


def test_not_a_directory_exits_not_available(tmp_path: Path) -> None:
    env = _envelope_for(NotADirectoryError(20, "not a dir", "/x/f"), tmp_path)
    assert env["exit_code"] == 2
    assert "Not a directory" in str(env["error"])


def test_other_os_errors_stay_unexpected(tmp_path: Path) -> None:
    """A generic OSError is still a fault: the mapping must stay narrow."""
    env = _envelope_for(OSError(5, "I/O error", "/dev/x"), tmp_path)
    assert env["exit_code"] == 1


def test_runtime_errors_stay_unexpected(tmp_path: Path) -> None:
    env = _envelope_for(RuntimeError("boom"), tmp_path)
    assert env["exit_code"] == 1
