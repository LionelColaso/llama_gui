"""The justfile-independent runner for the project's check suite.

This mirrors the ``just check`` recipe step for step: same tools, same scope,
same config. ``just`` is not always available (and is not on CI), so this
script has to stand on its own -- which is why the two must not drift.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

_CONFIG = ["--config", "pyproject.toml", "."]

#: The check suite, in the order ``just check`` runs it: ruff format, ruff
#: check, mypy, pyright, jscpd, actionlint, pytest. Keep in sync with the
#: ``check`` recipe in the justfile.
_STEPS: tuple[tuple[str, list[str], bool], ...] = (
    ("ruff format", ["uv", "run", "ruff", "format", "--check", *_CONFIG], False),
    ("ruff check", ["uv", "run", "ruff", "check", *_CONFIG], False),
    ("mypy", ["uv", "run", "mypy", *_CONFIG], False),
    ("pyright", ["uv", "run", "pyright", "-p", "pyproject.toml", "."], False),
    ("jscpd", ["npx", "--yes", "jscpd@latest", ".", "--config", ".jscpd.json"], True),
    ("actionlint", ["actionlint"], True),
    (
        "pytest (all)",
        [
            "uv",
            "run",
            "pytest",
            "-v",
            "--tb=short",
            "--doctest-modules",
            "--no-qt-log",
            "-s",
            "-ra",
            "tests/unit",
            "tests/gui",
            "tests/integration",
        ],
        False,
    ),
)

#: Optional developer tools whose absence must not fail the run.
_OPTIONAL = frozenset({"jscpd", "actionlint"})

#: The steps --fix replaces, in place.
_FIXABLE = frozenset({"ruff format", "ruff check"})


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
        if name in _OPTIONAL:
            print("    skipped (command not found)", file=sys.stderr)
        else:
            print("    FAILED: command not found", file=sys.stderr)
            failed.append(name)
    except RuntimeError as e:
        print(f"    FAILED: {e}", file=sys.stderr)
        failed.append(name)


def _steps(*, fix: bool) -> list[tuple[str, list[str], bool]]:
    """The steps to run, in order. ``fix`` mirrors the ``just fix`` recipe."""
    if not fix:
        return list(_STEPS)
    return [
        ("ruff format", ["uv", "run", "ruff", "format", *_CONFIG], False),
        (
            "ruff check --fix",
            ["uv", "run", "ruff", "check", "--fix", *_CONFIG],
            False,
        ),
        *(s for s in _STEPS if s[0] not in _FIXABLE),
    ]


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run the full check suite. Mirrors `just check` exactly and does "
            "not depend on `just` being installed."
        ),
    )
    parser.add_argument(
        "--fix",
        action="store_true",
        help="Auto-fix formatting and lint issues (mirrors `just fix`).",
    )
    args = parser.parse_args()

    failed: list[str] = []
    for name, cmd, shell in _steps(fix=args.fix):
        _run_step(name, cmd, failed, shell=shell)

    print("\n========================================", file=sys.stderr)
    if not failed:
        print("All checks passed!", file=sys.stderr)
        return 0
    print(f"FAILED: {', '.join(failed)}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
