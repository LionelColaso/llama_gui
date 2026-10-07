"""Diff the ``serverargs`` catalogue against a real ``llama-server --help``.

The catalogue in :mod:`app.serverargs` is **data** transcribed from
``llama-server --help`` (see ``docs/reference/llama-server-help.txt``). llama.cpp
ships nightly builds whose flags move, so the snapshot can silently go stale: an
option the GUI renders may no longer exist, or a new one may be missing.

This script compares the two and reports the difference. It is read-only — it
never writes to the managed root or anywhere else.

Usage::

    # Against the committed snapshot (no binary needed; the default)
    uv run python scripts/check_server_args.py

    # Against an installed binary (the authoritative check)
    uv run python scripts/check_server_args.py --binary path/to/llama-server

    # Only fail on the serious direction (catalogue advertises a dead flag)
    uv run python scripts/check_server_args.py --strict

Exit codes: 0 = in sync (or only new-help flags found), 1 = out of sync.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT = ROOT / "docs" / "reference" / "llama-server-help.txt"

sys.path.insert(0, str(ROOT))

from app.serverargs import SERVER_ARGS

#: One entry of a ``--help`` flag column: a leading dash token plus everything
#: joined to it by ``-``, ``_`` or ``.`` (real flags include names like
#: ``--fim-qwen-1.5b-default``). The list form is what disambiguates a flag from
#: a value placeholder such as ``lo-hi``, which is never comma-joined.
_FLAG = re.compile(r"-{1,2}[A-Za-z][\w]*(?:[-_.][\w]+)*")

#: A section header such as ``----- common params -----``.
_SECTION = re.compile(r"^-{3,}")

#: A run of two or more spaces between two non-space characters. In
#: ``llama-server --help`` each option row is ``<flags><padding><description>``;
#: early runs are the ``-x,   --long`` column padding, and the run that reaches
#: the description column (see :data:`_DESC_COLUMN`) is the real boundary.
_GAP = re.compile(r"(?<=\S)\s{2,}(?=\S)")

#: Column where the description starts in the reference ``--help`` snapshot.
#: Rows whose flags overflow it have their description wrapped onto the next
#: line, so there is no in-line boundary to cut on and the whole row is scanned.
_DESC_COLUMN = 40

#: A ``{a,b,c}`` choice list: a whole word may appear between the two braces, so
#: its bare entries (``-cache``, ``-mtp``) must not be read as flags.
_CHOICE = re.compile(r"\{[^{}]*\}")

#: The leading comma-separated flag list of a row. Matching the *list* (rather
#: than scanning the whole column) is what keeps the trailing value placeholder
#: of a long row (``--fim-qwen-1.5b-default   use default ...``) from being
#: mistaken for a flag, and skips rows that are really wrapped description text.
_FLAG_LIST = re.compile(
    r"^(-{1,2}[A-Za-z][\w]*(?:[-_.][\w]+)*(?:\s*,\s*-{1,2}[\w.-]+)*)"
)


def parse_help_flags(text: str) -> set[str]:
    """Extract every flag token from ``llama-server --help`` output.

    Only the leading *flag column* of each option row is read, and only the
    leading comma-separated flag list within it. That keeps out the value
    placeholders (``N``, ``lo-hi``, ``{a,b,c}``) and the description prose, so
    neither ``-hi`` nor ``-mtp`` is mistaken for a flag.
    """
    flags: set[str] = set()
    for line in text.splitlines():
        if not line.startswith("-") or _SECTION.match(line):
            continue
        column = line
        for gap in _GAP.finditer(line):
            if gap.end() >= _DESC_COLUMN:
                column = line[: gap.start()]
                break
        # A long flag may push its value placeholder past the description
        # column, so the whole row is in play; guard the choice lists first.
        list_match = _FLAG_LIST.match(column)
        if list_match is None:
            continue
        head = _CHOICE.sub(" ", list_match.group(1))
        flags.update(_FLAG.findall(head))
    return flags


def catalogue_flags(include_deprecated: bool = True) -> set[str]:
    """Every flag the catalogue can emit: canonical flags, aliases, negations.

    A ``deprecated`` entry is a shim for an option upstream has *removed*: the
    binary keeps listing it, but only to say "use X instead". The snapshot here
    predates that removal, so the shim appears in neither set consistently.
    Excluding these entries (and only these) by default keeps a deliberate
    backwards-compat shim from being reported as drift in either direction.
    """
    flags: set[str] = set()
    for arg in SERVER_ARGS:
        if arg.deprecated and not include_deprecated:
            continue
        flags.add(arg.flag)
        flags.update(arg.aliases)
        if arg.negated:
            flags.add(arg.negated)
    return flags


def read_help(binary: Path | None) -> str:
    """``--help`` from ``binary``, or the committed snapshot when it is None."""
    if binary is None:
        if not SNAPSHOT.is_file():
            raise SystemExit(f"missing help snapshot: {SNAPSHOT}")
        return SNAPSHOT.read_text(encoding="utf-8")
    try:
        result = subprocess.run(
            [str(binary), "--help"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SystemExit(f"could not run {binary} --help: {exc}") from exc
    return result.stdout or result.stderr


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Diff the serverargs catalogue against llama-server --help.",
    )
    parser.add_argument(
        "--binary",
        type=Path,
        default=None,
        help="llama-server executable to query (default: the committed snapshot)",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Also exit non-zero when --help offers flags the catalogue lacks",
    )
    parser.add_argument(
        "--include-deprecated",
        action="store_true",
        help=(
            "Also diff the deprecated shim flags (they are absent from a "
            "current --help by design, so they are excluded by default)"
        ),
    )
    args = parser.parse_args()

    help_flags = parse_help_flags(read_help(args.binary))
    known = catalogue_flags(include_deprecated=args.include_deprecated)
    if not args.include_deprecated:
        # A deprecated shim is a flag upstream has removed. The binary still
        # lists it, but only to say "use X instead", so a snapshot taken before
        # that removal still shows it while a current --help does not. Drop them
        # from both sides so the diff reports only real drift.
        deprecated = catalogue_flags(include_deprecated=True) - known
        help_flags -= deprecated

    missing = sorted(help_flags - known)  # in --help, not in the catalogue
    dead = sorted(known - help_flags)  # in the catalogue, not in --help

    source = args.binary or SNAPSHOT
    print(f"source     : {source}")
    print(f"catalogue  : {len(known)} flags from {len(SERVER_ARGS)} options")
    print(f"--help     : {len(help_flags)} flags")

    if missing:
        label = "new in --help (not in the catalogue)"
        print(f"\n{label} ({len(missing)}):")
        for flag in missing:
            print(f"  {flag}")

    if dead:
        label = "in the catalogue but NOT in --help (dead or renamed)"
        print(f"\n{label} ({len(dead)}):")
        for flag in dead:
            print(f"  {flag}")

    if not missing and not dead:
        print("\nOK: the catalogue matches --help exactly.")
        return 0

    print(
        "\nTo resync: edit SERVER_ARGS in app/serverargs.py, refresh "
        "docs/reference/llama-server-help.txt, and re-run this script."
    )
    if dead or (args.strict and missing):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
