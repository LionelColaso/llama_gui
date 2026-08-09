"""Machine interface: ``python -m llamagui <action> [--json]``.

With ``--json`` stdout carries exactly one envelope (§9.1) and nothing else;
progress and human logs always go to stderr. The exit code is the contract
status (§9.2), so scripts and the GUI can react without parsing text.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NoReturn

from pydantic import BaseModel

from .backends.build import ToolchainMissing
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
        child.add_argument(
            "--source",
            choices=("prebuilt", "build"),
            default=None,
            help="Override the configured install source",
        )

    build_p = add("build", "Build backends from the vendored submodules")
    build_p.add_argument("backends", nargs="*", default=None)

    use_p = add("use", "Switch the active backend")
    use_p.add_argument("backend")
    use_p.add_argument("--auto-install", action="store_true")

    add("stop", "Stop everything this app launched")

    for name, help_text in (
        ("launch", "Launch llama-swap"),
        ("restart", "Stop, then launch llama-swap"),
    ):
        child = add(name, help_text)
        child.add_argument("--verify", action="store_true", help="Wait for the port")

    add("list-assets", "List the assets of the latest release")
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
        return orch.install(args.backends or None, args.force, args.source)
    if action == "update":
        return orch.update(args.backends or None, args.force, args.source)
    if action == "build":
        return orch.build(args.backends or None)
    if action == "use":
        return orch.use(args.backend, args.auto_install)
    if action == "stop":
        return orch.stop()
    if action == "list-assets":
        return orch.list_assets()
    raise EngineError(ExitCode.BAD_ARGUMENT, f"Unknown action: {action}")


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]

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
    except ToolchainMissing as e:
        env = build_env(
            args.action,
            False,
            ExitCode.TOOLCHAIN_MISSING,
            error=str(e),
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
            "llama-swap did not start listening on the configured port",
            orch.log_tail(20),
        )
    return {"pid": pid}
