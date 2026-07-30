from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# These mirror the `just check` recipe exactly so the two are independent and
# run an identical set of checks (same tools, same scope, same config).
_RUFF_FMT = ["uv", "run", "ruff", "format", "--config", "pyproject.toml"]
_RUFF_CHECK = ["uv", "run", "ruff", "check", "--config", "pyproject.toml", "."]
_RUFF_FIX = ["uv", "run", "ruff", "check", "--fix", "--config", "pyproject.toml", "."]
_MYPY = ["uv", "run", "mypy", "--config", "pyproject.toml", "."]
_PYRIGHT = ["uv", "run", "pyright", "-p", "pyproject.toml", "."]
_JSCPD = ["npx", "--yes", "jscpd@latest", ".", "--config", ".jscpd.json"]
_PYTEST = ["uv", "run", "pytest", "tests/unit", "tests/gui", "-v", "--tb=short"]


def _run_step(
    name: str, cmd: list[str], failed: list[str], shell: bool = False
) -> None:
    print(f"\n==> {name}", file=sys.stderr)
    try:
        # `shell=True` lets the OS shell resolve extensionless launchers such as
        # `npx` (npx.cmd on Windows), matching how the `just` recipe runs it.
        result = subprocess.run(cmd, cwd=ROOT, check=False, shell=shell)
        if result.returncode != 0:
            raise RuntimeError(f"exit {result.returncode}")
        print("    OK", file=sys.stderr)
    except FileNotFoundError:
        print("    skipped (command not found)", file=sys.stderr)
    except RuntimeError as e:
        print(f"    FAILED: {e}", file=sys.stderr)
        failed.append(name)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the full check suite. Mirrors `just check` exactly and "
        "does not depend on `just` being installed."
    )
    parser.add_argument(
        "--fix",
        action="store_true",
        help="Auto-fix formatting issues (ruff format, no --check).",
    )
    args = parser.parse_args()

    failed: list[str] = []
    fmt = _RUFF_FMT + (["--check"] if not args.fix else [])

    _run_step("ruff format", fmt, failed)
    if args.fix:
        # Mirror `just fix`, which also auto-fixes lint issues (not just format).
        _run_step("ruff check --fix", _RUFF_FIX, failed)
    else:
        _run_step("ruff check", _RUFF_CHECK, failed)
    _run_step("mypy", _MYPY, failed)
    _run_step("pyright", _PYRIGHT, failed)
    # Run jscpd through the shell so `npx` resolves like the `just` recipe does.
    _run_step("jscpd", _JSCPD, failed, shell=True)

    _run_step("pytest (unit + gui)", _PYTEST, failed)

    print("\n========================================", file=sys.stderr)
    if not failed:
        print("All checks passed!", file=sys.stderr)
        return 0
    print(f"FAILED: {', '.join(failed)}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
