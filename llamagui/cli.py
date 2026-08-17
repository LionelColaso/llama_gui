"""Machine interface: ``python -m llamagui <action> [--json]``.

With ``--json`` stdout carries exactly one envelope (§9.1) and nothing else;
progress and human logs always go to stderr. The exit code is the contract
status (§9.2), so scripts and the GUI can react without parsing text.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import time
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NoReturn

from loguru import logger
from pydantic import BaseModel

from .backends.prebuilt import PrebuiltError, PrebuiltUnavailable
from .config import AppConfig
from .orchestrator import Orchestrator
from .schemas import EngineError, Envelope, ExitCode, contract_version


def build_env(
    action: str,
    ok: bool,
    exit_code: int,
    data: Any = None,
    error: str | None = None,
    log_tail: list[str] | None = None,
    warnings: list[str] | None = None,
    root: str = "",
) -> Envelope:
    return Envelope(
        contract_version=contract_version,
        ok=ok,
        exit_code=exit_code,
        action=action,
        root=root,
        timestamp=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        duration_ms=0,
        data=data,
        error=error,
        log_tail=log_tail,
        warnings=warnings or [],
    )


def emit(env: Envelope, use_json: bool) -> int:
    if env.error:
        logger.error(
            "cli action '{}' failed (exit {}): {}", env.action, env.exit_code, env.error
        )
    elif env.warnings:
        logger.warning(
            "cli action '{}' warnings: {}", env.action, "; ".join(env.warnings)
        )
    else:
        logger.debug("cli action '{}' ok (exit {})", env.action, env.exit_code)
    if use_json:
        sys.stdout.write(env.model_dump_json(indent=2) + "\n")
        return env.exit_code
    if env.error:
        sys.stderr.write(f"error: {env.error}\n")
    for warning in env.warnings:
        sys.stderr.write(f"warning: {warning}\n")
    data: Any = env.data
    if isinstance(data, BaseModel):
        data = data.model_dump()
    if data:
        sys.stdout.write(json.dumps(data, indent=2, default=str) + "\n")
    return env.exit_code


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        logger.error("cli argument error: {}", message)
        use_json = "--json" in sys.argv
        env = build_env("", False, ExitCode.BAD_ARGUMENT, error=message)
        if use_json:
            sys.stdout.write(env.model_dump_json(indent=2) + "\n")
        else:
            sys.stderr.write(f"error: {message}\n")
        sys.exit(ExitCode.BAD_ARGUMENT)


def _build_parser() -> _Parser:
    parser = _Parser(prog="llamagui", description="llama-gui engine CLI")
    parser.add_argument("--root", help="Override the managed root directory")
    parser.add_argument("--json", action="store_true", help="Emit one JSON envelope")
    sub = parser.add_subparsers(dest="action", required=True)

    def add(name: str, help_text: str) -> argparse.ArgumentParser:
        child = sub.add_parser(name, help=help_text)
        # Accept --json before or after the sub-command.
        child.add_argument("--json", action="store_true", help=argparse.SUPPRESS)
        return child

    add("describe", "Show backends, actions and platform defaults")
    add("status", "Show current status (fast, no subprocesses)")
    add("resolve", "Resolve and validate the binaries that would be used")
    add("config", "Show the saved settings and where they live")

    bootstrap_p = add("bootstrap", "Download whatever is missing, then activate it")
    bootstrap_p.add_argument("--backend", default=None)
    bootstrap_p.add_argument("--force", action="store_true")

    for name, help_text in (
        ("install", "Install backends from the latest release"),
        ("update", "Update backends to the latest release"),
    ):
        child = add(name, help_text)
        child.add_argument("backends", nargs="*", default=None)
        child.add_argument("--force", action="store_true")

    use_p = add("use", "Switch the active backend")
    use_p.add_argument("backend")
    use_p.add_argument("--auto-install", action="store_true")

    add("stop", "Stop everything this app launched")

    for name, help_text in (
        ("launch", "Launch llama-server with the active model"),
        ("restart", "Stop, then launch llama-server"),
    ):
        child = add(name, help_text)
        child.add_argument("--verify", action="store_true", help="Wait for the port")

    add("list-models", "List the .gguf models in the models directory")

    dl_p = add("download-model", "Download a .gguf model by URL")
    dl_p.add_argument("url")

    set_p = add("set-model", "Set the active model (by file name)")
    set_p.add_argument("name")

    rm_p = add("remove-model", "Remove a model from the models directory")
    rm_p.add_argument("name")

    add("list-assets", "List the assets of the latest release")

    server_args_p = add(
        "server-args", "List every llama-server option with its current value"
    )
    server_args_p.add_argument("--flag", default=None, help="Only one option")

    set_arg_p = add(
        "set-arg", "Set one llama-server option (empty value resets it to default)"
    )
    set_arg_p.add_argument("flag", help="Flag or alias, e.g. --top-k or -t")
    set_arg_p.add_argument("value", nargs="?", default="")

    add("clear-args", "Reset every llama-server option to its default")

    add("gui", "Launch the desktop app")
    return parser


def _dispatch(orch: Orchestrator, args: argparse.Namespace) -> Any:
    action: str = args.action
    if action == "describe":
        return orch.describe()
    if action == "status":
        return orch.status()
    if action == "resolve":
        return orch.resolve()
    if action == "config":
        return orch.config()
    if action == "bootstrap":
        return orch.bootstrap(args.backend, args.force)
    if action == "install":
        return orch.install(args.backends or None, args.force)
    if action == "update":
        return orch.update(args.backends or None, args.force)
    if action == "use":
        return orch.use(args.backend, args.auto_install)
    if action == "list-models":
        return orch.list_models()
    if action == "download-model":
        return orch.download_model(args.url)
    if action == "set-model":
        return orch.set_active_model(args.name)
    if action == "remove-model":
        return orch.remove_model(args.name)
    if action == "stop":
        return orch.stop()
    if action == "list-assets":
        return orch.list_assets()
    if action == "server-args":
        return orch.describe_server_args(args.flag)
    if action == "set-arg":
        return orch.set_server_arg(args.flag, args.value)
    if action == "clear-args":
        return orch.clear_server_args()
    raise EngineError(ExitCode.BAD_ARGUMENT, f"Unknown action: {action}")


def _force_utf8_stdio() -> None:
    """Make the JSON envelope byte-safe on any console code page.

    A piped stdout on Windows defaults to the ANSI code page (cp1252); writing
    the envelope (arbitrary UTF-8 in model names and errors) to it would crash
    with ``UnicodeEncodeError``. The contract is UTF-8 regardless of locale.
    """
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            with suppress(ValueError, OSError):
                stream.reconfigure(encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]

    _force_utf8_stdio()
    use_json = "--json" in argv
    args = _build_parser().parse_args(argv)

    if args.action == "gui":
        from .gui.app import run as run_gui

        run_gui()
        return 0

    cfg = AppConfig.load()
    if args.root:
        cfg.root = args.root
    orch = Orchestrator(cfg)
    root_str = str(Path(cfg.root).expanduser())
    warnings = list(cfg.load_warnings)

    start = time.monotonic()
    try:
        if args.action in ("launch", "restart"):
            data = _run_launch(orch, args)
        else:
            data = _dispatch(orch, args)
    except EngineError as e:
        env = build_env(
            args.action,
            False,
            int(e.exit_code),
            error=str(e),
            log_tail=e.log_tail,
            warnings=warnings,
            root=root_str,
        )
        return emit(env, use_json)
    except PrebuiltUnavailable as e:
        env = build_env(
            args.action,
            False,
            ExitCode.NOT_AVAILABLE,
            error=str(e),
            warnings=warnings,
            root=root_str,
        )
        return emit(env, use_json)
    except PrebuiltError as e:
        env = build_env(
            args.action,
            False,
            ExitCode.NETWORK_ERROR,
            error=str(e),
            warnings=warnings,
            root=root_str,
        )
        return emit(env, use_json)
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as e:
        env = build_env(
            args.action,
            False,
            ExitCode.UNEXPECTED_ERROR,
            error=f"{type(e).__name__}: {e}",
            warnings=warnings,
            root=root_str,
        )
        return emit(env, use_json)

    env = build_env(
        args.action,
        True,
        ExitCode.SUCCESS,
        data=data,
        warnings=warnings,
        root=root_str,
    )
    env.duration_ms = int((time.monotonic() - start) * 1000)
    return emit(env, use_json)


def _run_launch(orch: Orchestrator, args: argparse.Namespace) -> dict[str, Any]:
    pid = (
        orch.launch(verify=args.verify)
        if args.action == "launch"
        else orch.restart(verify=args.verify)
    )
    if args.verify and pid is None:
        raise EngineError(
            ExitCode.NOT_AVAILABLE,
            "llama-server did not start listening on the configured port",
            orch.log_tail(20),
        )
    return {"pid": pid}
