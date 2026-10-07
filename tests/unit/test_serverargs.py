"""The llama-server option catalogue and its serialisation helpers.

``serverargs.py`` is the largest module in the project (2 100+ lines, 248
catalogue rows) and the GUI renders an editor from it, so a wrong row or a
broken helper shows up as an option the user can see but cannot set. These
tests cover the catalogue's own invariants, value validation per kind, and
the token serialisation the command line is built from.
"""

from __future__ import annotations

from collections import Counter, defaultdict

import pytest

from app.serverargs import (
    DEDICATED_FLAGS,
    SECTIONS,
    SERVER_ARGS,
    ArgKind,
    ServerArg,
    count,
    find_arg,
    options_to_cli,
    validate_options,
    validate_value,
)
from app.serverargs._helpers import _value_to_cli

_BY_FLAG = {a.flag: a for a in SERVER_ARGS}


def _arg(flag: str) -> ServerArg:
    found = _BY_FLAG.get(flag)
    assert found is not None, f"{flag} is not in the catalogue"
    return found


def _first(kind: ArgKind) -> ServerArg:
    for candidate in SERVER_ARGS:
        if candidate.kind is kind:
            return candidate
    raise AssertionError(f"no {kind.value} option in the catalogue")


# ─── catalogue invariants ─────────────────────────────────────────────────


def test_catalogue_is_not_empty() -> None:
    assert len(SERVER_ARGS) > 200, "the catalogue should cover llama-server"


def test_canonical_flags_are_unique() -> None:
    dupes = [f for f, n in Counter(a.flag for a in SERVER_ARGS).items() if n > 1]
    assert dupes == [], f"duplicate canonical flags: {dupes}"


def test_every_flag_uses_the_long_double_dash_form() -> None:
    """The canonical form is what gets emitted, so it must be --x."""
    bad = [a.flag for a in SERVER_ARGS if not a.flag.startswith("--")]
    assert bad == [], f"non-canonical flag spellings: {bad}"


def test_sections_come_from_the_declared_set() -> None:
    unknown = sorted({a.section for a in SERVER_ARGS} - set(SECTIONS))
    assert unknown == [], f"undeclared sections: {unknown}"


def test_every_section_is_populated() -> None:
    used = {a.section for a in SERVER_ARGS}
    assert used == set(SECTIONS), f"unused sections: {sorted(set(SECTIONS) - used)}"


def test_dedicated_flags_all_have_a_catalogue_row() -> None:
    missing = sorted(DEDICATED_FLAGS - set(_BY_FLAG))
    assert missing == [], f"dedicated flags without a row: {missing}"


def test_aliases_are_not_claimed_by_two_options() -> None:
    """A shared alias would make find_arg() ambiguous."""
    owners: dict[str, set[str]] = defaultdict(set)
    for arg in SERVER_ARGS:
        for alias in arg.aliases:
            owners[alias].add(arg.flag)
    clashes = {a: sorted(f) for a, f in owners.items() if len(f) > 1}
    assert not clashes, f"aliases claimed by more than one option: {clashes}"


def test_no_alias_shadows_another_option_flag() -> None:
    flags = set(_BY_FLAG)
    shadows = sorted(
        {
            alias
            for a in SERVER_ARGS
            for alias in a.aliases
            if alias in flags and alias != a.flag
        }
    )
    assert shadows == [], f"aliases that collide with a canonical flag: {shadows}"


def test_choice_options_declare_their_choices() -> None:
    empty = [a.flag for a in SERVER_ARGS if a.kind is ArgKind.CHOICE and not a.choices]
    assert empty == [], f"choice options with no values: {empty}"


def test_negation_is_only_set_on_booleans() -> None:
    """A non-boolean has no on/off state, so `negated` must be absent."""
    bad = [
        a.flag
        for a in SERVER_ARGS
        if a.negated is not None and a.kind is not ArgKind.BOOL
    ]
    assert bad == [], f"negated set on non-boolean options: {bad}"


def test_negated_form_is_itself_a_real_flag_spelling() -> None:
    """--jinja / --no-jinja: the negated form must start with --."""
    bad = [
        a.negated or ""
        for a in SERVER_ARGS
        if a.negated and not a.negated.startswith("--")
    ]
    assert bad == [], f"negated forms that are not --flags: {bad}"


def test_self_negating_flags_are_booleans() -> None:
    """A flag that already starts with --no- is its own 'off' form."""
    bad = [
        a.flag
        for a in SERVER_ARGS
        if a.flag.startswith("--no-") and a.kind is not ArgKind.BOOL
    ]
    assert bad == [], f"--no- flags that are not booleans: {bad}"


def test_volatile_options_are_never_dedicated() -> None:
    """--help and friends print and exit; they must not be launch options."""
    bad = [a.flag for a in SERVER_ARGS if a.volatile and a.flag in DEDICATED_FLAGS]
    assert bad == [], f"volatile options marked dedicated: {bad}"


def test_every_row_has_help_text() -> None:
    blank = [a.flag for a in SERVER_ARGS if not a.help.strip()]
    assert blank == [], f"rows without help text: {blank}"


# ─── find_arg ─────────────────────────────────────────────────────────────


def test_find_arg_resolves_canonical_flags() -> None:
    arg = find_arg("--ctx-size")
    assert arg is not None
    assert arg.flag == "--ctx-size"


def test_find_arg_resolves_aliases() -> None:
    arg = find_arg("-c")
    assert arg is not None
    assert arg.flag == "--ctx-size"


def test_find_arg_returns_none_for_unknown() -> None:
    assert find_arg("--not-a-real-flag") is None
    assert find_arg("") is None


# ─── validate_value ───────────────────────────────────────────────────────


def test_blank_value_stays_blank() -> None:
    """A blank value means "omit the flag", not "invalid"."""
    assert validate_value(_arg("--ctx-size"), "") == ""
    assert validate_value(_arg("--ctx-size"), "   ") == ""


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("on", "on"),
        ("ON", "on"),
        ("true", "on"),
        ("1", "on"),
        ("yes", "on"),
        ("enabled", "on"),
        ("off", "off"),
        ("False", "off"),
        ("0", "off"),
    ],
)
def test_boolean_values_normalise(raw: str, expected: str) -> None:
    assert validate_value(_arg("--jinja"), raw) == expected


def test_invalid_boolean_is_rejected() -> None:
    with pytest.raises(ValueError, match="on/off"):
        validate_value(_arg("--jinja"), "maybe")


def test_int_values_are_parsed() -> None:
    assert validate_value(_arg("--ctx-size"), "8192") == "8192"
    assert validate_value(_arg("--ctx-size"), " -1 ") == "-1"


def test_non_integer_for_an_int_option_is_rejected() -> None:
    with pytest.raises(ValueError):
        validate_value(_arg("--ctx-size"), "8k")


def test_float_values_are_parsed() -> None:
    arg = _first(ArgKind.FLOAT)
    assert validate_value(arg, "0.5") == "0.5"
    with pytest.raises(ValueError):
        validate_value(arg, "half")


def test_choice_must_be_one_of_the_declared_values() -> None:
    arg = next(a for a in SERVER_ARGS if a.kind is ArgKind.CHOICE and a.choices)
    assert validate_value(arg, arg.choices[0]) == arg.choices[0]
    with pytest.raises(ValueError):
        validate_value(arg, "definitely-not-a-choice")


def test_string_and_path_values_pass_through() -> None:
    assert (
        validate_value(_first(ArgKind.STRING), "anything at all") == "anything at all"
    )
    assert validate_value(_first(ArgKind.PATH), "/some/dir") == "/some/dir"


# ─── options_to_cli ───────────────────────────────────────────────────────


def test_options_to_cli_is_empty_for_an_empty_map() -> None:
    assert options_to_cli({}) == []


def test_options_to_cli_emits_a_bare_flag() -> None:
    assert options_to_cli({"--jinja": "on"}) == ["--jinja"]


def test_options_to_cli_emits_flag_and_value() -> None:
    assert options_to_cli({"--slot-save-path": "/tmp/slots"}) == [
        "--slot-save-path",
        "/tmp/slots",
    ]


def test_options_to_cli_skips_dedicated_flags() -> None:
    """host/port/ctx-size/n-gpu-layers come from the config, not the map."""
    tokens = options_to_cli(
        {"--host": "0.0.0.0", "--port": "9000", "--ctx-size": "4096", "--jinja": "on"}
    )
    assert tokens == ["--jinja"], "dedicated flags must not be emitted here"


def test_options_to_cli_ignores_blank_values() -> None:
    assert options_to_cli({"--jinja": "", "--cache-prompt": "   "}) == []


def test_options_to_cli_uses_negated_form_for_false() -> None:
    assert options_to_cli({"--jinja": "off"}) == ["--no-jinja"]


def test_options_to_cli_follows_catalogue_order() -> None:
    """Order is the catalogue's, not the caller's, so the command line is
    reproducible regardless of how the options dict was built."""
    options = {"--jinja": "on", "--metrics": "on", "--cache-prompt": "on"}
    expected = [
        a.flag
        for a in SERVER_ARGS
        if a.flag in {"--cache-prompt", "--metrics", "--jinja"}
    ]
    assert options_to_cli(options) == expected


def test_options_to_cli_omits_unknown_flags() -> None:
    assert options_to_cli({"--not-real": "x", "--jinja": "on"}) == ["--jinja"]


# ─── validate_options ─────────────────────────────────────────────────────


def test_validate_options_accepts_good_values() -> None:
    assert validate_options({"--jinja": "on", "--ctx-size": "4096"}) == {}


def test_validate_options_reports_unknown_flags() -> None:
    errors = validate_options({"--nope": "1"})
    assert "--nope" in errors
    assert "unknown option" in errors["--nope"]


def test_validate_options_reports_bad_values() -> None:
    assert "--jinja" in validate_options({"--jinja": "perhaps"})


def test_validate_options_reports_every_bad_entry() -> None:
    errors = validate_options({"--jinja": "perhaps", "--nope": "1"})
    assert set(errors) == {"--jinja", "--nope"}


# ─── count ────────────────────────────────────────────────────────────────


def test_count_sums_to_the_catalogue() -> None:
    assert sum(count().values()) == len(SERVER_ARGS)


def test_count_covers_every_section() -> None:
    assert set(count()) == set(SECTIONS)


def test_value_to_cli_rejects_an_invalid_boolean() -> None:
    arg = find_arg("--cache-list")
    assert arg is not None
    with pytest.raises(ValueError, match="invalid boolean"):
        _value_to_cli(arg, "maybe")
