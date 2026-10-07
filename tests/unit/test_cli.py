from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from pydantic import BaseModel

from app.backends.prebuilt import PrebuiltError, PrebuiltUnavailable
from app.cli import (
    _FLAG_TOKEN_PREFIX,
    _decode_flag_positional,
    _encode_flag_positional,
    build_env,
    emit,
    main,
)
from app.orchestrator import Orchestrator
from app.schemas import ExitCode


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


# ─── emit: the human-readable output path ────────────────────


class _Tiny(BaseModel):
    x: int = 1


def _emit_human(env: Any) -> tuple[int, str, str]:
    """Run ``emit`` in human mode, capturing both streams."""
    out, err = io.StringIO(), io.StringIO()
    with (
        contextlib.redirect_stdout(out),
        contextlib.redirect_stderr(err),
    ):
        code = emit(env, use_json=False)
    return code, out.getvalue(), err.getvalue()


def test_emit_human_success_prints_data_and_warnings(
    tmp_path: Path,
) -> None:
    env = build_env("status", True, ExitCode.SUCCESS, data={"x": 1}, warnings=["w1"])
    code, out, err = _emit_human(env)
    assert code == 0
    assert json.loads(out) == {"x": 1}
    assert "warning: w1" in err


def test_emit_human_error_skips_stdout(tmp_path: Path) -> None:
    env = build_env("status", False, ExitCode.UNEXPECTED_ERROR, error="boom")
    code, out, err = _emit_human(env)
    assert code == 1
    assert out == ""
    assert "error: boom" in err


def test_emit_human_dumps_a_model_payload(tmp_path: Path) -> None:
    env = build_env("status", True, ExitCode.SUCCESS, data=_Tiny())
    code, out, _err = _emit_human(env)
    assert code == 0
    assert json.loads(out) == {"x": 1}


# ─── dispatch: every action reaches its orchestrator method ──


@pytest.mark.parametrize(
    ("argv", "method"),
    [
        (["config"], "config"),
        (["bootstrap"], "bootstrap"),
        (["install"], "install"),
        (["update"], "update"),
        (["list-models"], "list_models"),
        (["download-model", "https://x/m.gguf"], "download_model"),
        (["set-model", "m.gguf"], "set_active_model"),
        (["remove-model", "m.gguf"], "remove_model"),
        (["list-assets"], "list_assets"),
        (["pending-downloads"], "pending_downloads"),
        (["discard-download", "x.part"], "discard_download"),
        (["server-args"], "describe_server_args"),
        (["set-arg", "--ctx-size", "16384"], "set_server_arg"),
        (["clear-args"], "clear_server_args"),
    ],
)
def test_dispatch_every_action(tmp_path: Path, argv: list[str], method: str) -> None:
    with (
        patch.object(Orchestrator, method, autospec=True) as mock,
        contextlib.redirect_stdout(io.StringIO()),
    ):
        mock.return_value = {"ok": True}
        code = main(["--root", str(tmp_path), *argv, "--json"])
    assert code == 0
    assert mock.called, f"{method} was not dispatched"


def test_main_defaults_to_the_process_argv(tmp_path: Path) -> None:
    with (
        patch.object(
            sys,
            "argv",
            ["llamagui", "--root", str(tmp_path), "status", "--json"],
        ),
        contextlib.redirect_stdout(io.StringIO()),
    ):
        code = main()
    assert code == 0


def test_gui_action_launches_the_desktop_app(tmp_path: Path) -> None:
    ran: list[bool] = []
    with patch("app.gui.bootstrap.run", side_effect=lambda: ran.append(True)):
        code = main(["--root", str(tmp_path), "gui"])
    assert code == 0
    assert ran == [True]


# ─── launch / restart ────────────────────────────────────────


def test_launch_verify_reports_the_pid(tmp_path: Path) -> None:
    out = io.StringIO()
    with (
        patch.object(Orchestrator, "launch", return_value=4242),
        contextlib.redirect_stdout(out),
    ):
        code = main(["--root", str(tmp_path), "launch", "--verify", "--json"])
    assert code == 0
    assert json.loads(out.getvalue())["data"] == {"pid": 4242}


def test_restart_verify_reports_the_pid(tmp_path: Path) -> None:
    out = io.StringIO()
    with (
        patch.object(Orchestrator, "restart", return_value=4242),
        contextlib.redirect_stdout(out),
    ):
        code = main(["--root", str(tmp_path), "restart", "--verify", "--json"])
    assert code == 0
    assert json.loads(out.getvalue())["data"] == {"pid": 4242}


def test_launch_verify_failure_includes_the_log_tail(tmp_path: Path) -> None:
    out = io.StringIO()
    with (
        patch.object(Orchestrator, "launch", return_value=None),
        patch.object(Orchestrator, "log_tail", return_value=["line 1"]),
        contextlib.redirect_stdout(out),
    ):
        code = main(["--root", str(tmp_path), "launch", "--verify", "--json"])
    assert code == 2
    envelope = json.loads(out.getvalue())
    assert "did not start listening" in envelope["error"]
    assert envelope["log_tail"] == ["line 1"]


# ─── prebuilt failure mapping ────────────────────────────────


def test_prebuilt_unavailable_exits_not_available(tmp_path: Path) -> None:
    env = _envelope_for(PrebuiltUnavailable("metal"), tmp_path)
    assert env["exit_code"] == 2


def test_prebuilt_error_exits_network(tmp_path: Path) -> None:
    env = _envelope_for(PrebuiltError("boom"), tmp_path)
    assert env["exit_code"] == 3


def test_dispatch_rejects_an_unknown_action(tmp_path: Path) -> None:
    """The fall-through guard: an unhandled action name is a bad argument."""
    from app.cli import _dispatch
    from app.config import AppConfig
    from app.schemas import EngineError

    orch = Orchestrator(AppConfig(root=str(tmp_path)))
    args = argparse.Namespace(action="bogus")
    with pytest.raises(EngineError):
        _dispatch(orch, args)
