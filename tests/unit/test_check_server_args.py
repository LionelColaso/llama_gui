"""Parser and diff tests for ``scripts/check_server_args.py``.

The parser has to survive the shapes ``llama-server --help`` actually emits:
alignment padding, comma-joined flag lists, value placeholders (``N``,
``lo-hi``), ``{a,b,c}`` choice lists, section headers, wrapped rows, and flags
containing dots. A regression here would either hide real drift or bury it in
false positives, so the tricky rows are pinned explicitly.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_server_args.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_server_args", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def mod() -> ModuleType:
    return _load()


def test_parses_short_and_long_forms(mod: ModuleType) -> None:
    text = "-h,    --help, --usage                  print usage and exit\n"
    assert mod.parse_help_flags(text) == {"-h", "--help", "--usage"}


def test_parses_negated_forms(mod: ModuleType) -> None:
    text = "--perf, --no-perf                       whether to enable timings\n"
    assert mod.parse_help_flags(text) == {"--perf", "--no-perf"}


def test_ignores_section_headers(mod: ModuleType) -> None:
    text = "----- common params -----\n"
    assert mod.parse_help_flags(text) == set()


def test_ignores_value_placeholder_hyphen(mod: ModuleType) -> None:
    # ``lo-hi`` is a value, not a ``-hi`` flag.
    text = "-Cr,   --cpu-range lo-hi                range of CPUs for affinity\n"
    assert mod.parse_help_flags(text) == {"-Cr", "--cpu-range"}


def test_ignores_choice_list_entries(mod: ModuleType) -> None:
    # Bare entries inside {a,b,c} must not be read as flags.
    text = "--spec-type none,draft-simple,draft-mtp    speculative method\n"
    assert mod.parse_help_flags(text) == {"--spec-type"}


def test_keeps_dotted_flag_name(mod: ModuleType) -> None:
    text = "--fim-qwen-1.5b-default                 use default Qwen 1.5B model\n"
    assert mod.parse_help_flags(text) == {"--fim-qwen-1.5b-default"}


def test_keeps_flags_on_overflowing_row(mod: ModuleType) -> None:
    # A row whose flag list runs past the description column (description
    # wrapped to the next line) still yields every flag.
    text = (
        "-kvu,  --kv-unified, -no-kvu, --no-kv-unified\n"
        "                                        use single unified KV buffer\n"
    )
    assert mod.parse_help_flags(text) == {
        "-kvu",
        "--kv-unified",
        "-no-kvu",
        "--no-kv-unified",
    }


def test_snapshot_is_in_sync_with_catalogue(mod: ModuleType) -> None:
    """The committed snapshot and the catalogue must not drift.

    This is the regression guard the script exists for: if llama.cpp renames or
    removes a flag, this fails and points at what needs updating.
    """
    if not mod.SNAPSHOT.is_file():
        pytest.skip(f"no help snapshot at {mod.SNAPSHOT}")
    help_flags = mod.parse_help_flags(mod.SNAPSHOT.read_text(encoding="utf-8"))
    known = mod.catalogue_flags(include_deprecated=False)
    deprecated = mod.catalogue_flags(include_deprecated=True) - known
    assert help_flags - deprecated - known == set(), "flags in --help not in catalogue"
    assert known - help_flags == set(), "catalogue flags missing from --help"


def test_deprecated_shims_are_excluded_by_default(mod: ModuleType) -> None:
    """Deprecated entries are removed upstream, so they are not drift."""
    live = mod.catalogue_flags(include_deprecated=False)
    every = mod.catalogue_flags(include_deprecated=True)
    assert every - live, "expected at least one deprecated shim in the catalogue"
