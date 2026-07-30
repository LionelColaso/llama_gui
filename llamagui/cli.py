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
from .backends.prebuilt import PrebuiltError
from .config import AppConfig
from .locking import LockAcquisitionError
from .orchestrator import Orchestrator
from .schemas import Envelope, ExitCode, contract_version


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
    else:
        if env.error:
            sys.stderr.write(f"error: {env.error}\n")
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


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]

    use_json = "--json" in argv

    parser = _Parser(prog="llamagui", description="llama-gui CLI")
    parser.add_argument("--root", help="Override the app root directory")
    sub = parser.add_subparsers(dest="action", required=True)

    describe_p = sub.add_parser("describe", help="Show available backends and actions")
    describe_p.add_argument("--json", action="store_true", help=argparse.SUPPRESS)

    status_p = sub.add_parser("status", help="Show current status")
    status_p.add_argument("--json", action="store_true", help=argparse.SUPPRESS)

    resolve_p = sub.add_parser("resolve", help="Resolve binary paths")
    resolve_p.add_argument("--json", action="store_true", help=argparse.SUPPRESS)

    install_p = sub.add_parser("install", help="Install backends")
    install_p.add_argument("backends", nargs="*", default=None)
    install_p.add_argument("--force", action="store_true")
    install_p.add_argument("--json", action="store_true", help=argparse.SUPPRESS)

    update_p = sub.add_parser("update", help="Update backends")
    update_p.add_argument("backends", nargs="*", default=None)
    update_p.add_argument("--force", action="store_true")
    update_p.add_argument("--json", action="store_true", help=argparse.SUPPRESS)

    use_p = sub.add_parser("use", help="Switch active backend")
    use_p.add_argument("--json", action="store_true", help=argparse.SUPPRESS)
    use_p.add_argument("backend")
    use_p.add_argument("--auto-install", action="store_true")

    stop_p = sub.add_parser("stop", help="Stop processes")
    stop_p.add_argument("--json", action="store_true", help=argparse.SUPPRESS)

    launch_p = sub.add_parser("launch", help="Launch llama-swap")
    launch_p.add_argument("--json", action="store_true", help=argparse.SUPPRESS)
    launch_p.add_argument("--verify", action="store_true", help="Wait for port")

    restart_p = sub.add_parser("restart", help="Stop then launch llama-swap")
    restart_p.add_argument("--json", action="store_true", help=argparse.SUPPRESS)
    restart_p.add_argument("--verify", action="store_true", help="Wait for port")

    build_p = sub.add_parser("build", help="Build backends from source")
    build_p.add_argument("backends", nargs="*", default=None)
    build_p.add_argument("--json", action="store_true", help=argparse.SUPPRESS)

    list_assets_p = sub.add_parser("list-assets", help="List available release assets")
    list_assets_p.add_argument("--json", action="store_true", help=argparse.SUPPRESS)

    gui_p = sub.add_parser("gui", help="Launch the GUI")
    gui_p.add_argument("--json", action="store_true", help=argparse.SUPPRESS)

    args = parser.parse_args(argv)

    # Launch the GUI directly (no envelope, no JSON)
    if args.action == "gui":
        from .gui.app import run as run_gui

        run_gui()
        return 0

    cfg = AppConfig.load()
    if args.root:
        cfg.root = args.root

    orch = Orchestrator(cfg)
    root_str = str(Path(cfg.root).resolve())

    try:
        start = time.monotonic()
        data: BaseModel | dict[str, Any] | list[Any] = {}
        if args.action == "describe":
            data = orch.describe()
        elif args.action == "status":
            data = orch.status()
        elif args.action == "resolve":
            data = orch.resolve()
        elif args.action == "build":
            data = orch.build(args.backends)
        elif args.action == "install":
            data = orch.install(args.backends, args.force)
        elif args.action == "update":
            data = orch.update(args.backends, args.force)
        elif args.action == "use":
            data = orch.use(args.backend, args.auto_install)
        elif args.action == "stop":
            data = orch.stop()
        elif args.action in ("launch", "restart"):
            pid = (
                orch.launch(verify=args.verify)
                if args.action == "launch"
                else orch.restart(verify=args.verify)
            )
            if args.verify and pid is None:
                env = build_env(
                    args.action,
                    False,
                    ExitCode.NOT_AVAILABLE,
                    error="llama-swap did not become ready on the configured port",
                )
                return emit(env, use_json)
            data = {"pid": pid}
        elif args.action == "list-assets":
            data = orch.list_assets()
        else:
            msg = f"Unknown action: {args.action}"
            env = build_env(args.action, False, ExitCode.BAD_ARGUMENT, error=msg)
            return emit(env, use_json)

        elapsed = int((time.monotonic() - start) * 1000)
        dur = build_env(args.action, True, ExitCode.SUCCESS, data=data, root=root_str)
        dur.duration_ms = elapsed
        return emit(dur, use_json)

    except LockAcquisitionError as e:
        env = build_env(args.action, False, e.exit_code, error=str(e))
        return emit(env, use_json)
    except ToolchainMissing as e:
        env = build_env(args.action, False, ExitCode.TOOLCHAIN_MISSING, error=str(e))
        return emit(env, use_json)
    except PrebuiltError as e:
        env = build_env(args.action, False, ExitCode.NETWORK_ERROR, error=str(e))
        return emit(env, use_json)
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as e:
        err = f"{type(e).__name__}: {e}"
        env = build_env(args.action, False, ExitCode.UNEXPECTED_ERROR, error=err)
        return emit(env, use_json)
