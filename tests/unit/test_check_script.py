"""``scripts/check.py`` must stay in step with the ``just check`` recipe.

The script exists precisely because ``just`` is not always available, and both
claim to run the same suite. When they drift, a developer running one of them
gets a weaker gate than the other and the difference is invisible. These
tests pin the two together.
"""

from __future__ import annotations

import re
from pathlib import Path
from types import ModuleType

import pytest

from .script_loader import load_script

ROOT = Path(__file__).resolve().parents[2]
JUSTFILE = ROOT / "justfile"

#: The dependency line of the justfile's ``check`` recipe.
_CHECK_LINE = re.compile(r"^check:\s*(.+)$", re.MULTILINE)


def _justfile() -> str:
    return JUSTFILE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def check_mod() -> ModuleType:
    return load_script("check")


def _step_names(mod: ModuleType) -> list[str]:
    steps: list[tuple[str, list[str], bool]] = mod._STEPS
    return [name for name, _, _ in steps]


#: ``check.py``'s step labels mapped to the justfile recipe that runs the same
#: tool. The justfile names recipes (``ruff-format-check``) while the script
#: names what it does (``ruff format``), and it groups mypy+pyright under one
#: ``typecheck`` recipe -- hence the alias below. Parity is asserted through
#: this map rather than by string-matching two different vocabularies.
_RECIPE_FOR_STEP = {
    "ruff format": "ruff-format-check",
    "ruff check": "ruffcheck",
    "mypy": "typecheck",
    "pyright": "typecheck",
    "jscpd": "jscpd",
    "actionlint": "actionlint",
    "pytest (unit + gui)": "test-verbose",
}

#: Recipes the ``check`` recipe pulls in that are only a wrapper for another
#: step. They must not appear as an extra entry of their own.
_ALIAS_RECIPES = {"typecheck"}


def _recipe_chain(recipe_steps: list[str]) -> list[str]:
    """Expand wrapper recipes so both sides are compared as a flat list."""
    out: list[str] = []
    for step in recipe_steps:
        if step in _ALIAS_RECIPES:
            out.extend(("mypy", "pyright"))
        else:
            out.append(step)
    return out


def test_check_recipe_exists() -> None:
    assert _CHECK_LINE.search(_justfile()), (
        "the justfile no longer has a `check:` recipe"
    )


def test_every_step_maps_to_a_known_recipe(check_mod: ModuleType) -> None:
    assert set(_step_names(check_mod)) == set(_RECIPE_FOR_STEP), (
        "a check.py step has no justfile counterpart (or vice versa)"
    )


def test_check_recipe_runs_actionlint(check_mod: ModuleType) -> None:
    """Regression: check.py omitted actionlint while the justfile ran it."""
    assert "actionlint" in _justfile(), "the justfile no longer runs actionlint"
    assert "actionlint" in _step_names(check_mod), (
        "scripts/check.py must also run actionlint"
    )


def test_check_steps_match_the_justfile_recipe_order(check_mod: ModuleType) -> None:
    """The steps, in order, must mirror the `check:` recipe's dependencies."""
    match = _CHECK_LINE.search(_justfile())
    assert match is not None
    recipe_steps = _recipe_chain(match.group(1).split())
    # Map each script step to the recipe(s) it stands for, expanding the
    # typecheck wrapper the same way the justfile side was expanded.
    script_steps: list[str] = []
    for name in _step_names(check_mod):
        recipe = _RECIPE_FOR_STEP[name]
        if recipe in _ALIAS_RECIPES:
            script_steps.append(name)
        else:
            script_steps.append(recipe)
    assert script_steps == recipe_steps, (
        f"just check runs {recipe_steps} but check.py runs {script_steps}"
    )


def test_pytest_step_matches_the_justfile(check_mod: ModuleType) -> None:
    """`just check` uses `pytest -m "not integration"`; check.py must too."""
    steps: list[tuple[str, list[str], bool]] = check_mod._STEPS
    pytest_cmd = next(cmd for name, cmd, _ in steps if name.startswith("pytest"))
    assert "-m" in pytest_cmd
    assert "not integration" in pytest_cmd


def test_fix_mode_mirrors_just_fix(check_mod: ModuleType) -> None:
    """`--fix` must reformat AND auto-fix lint, as `just fix` does."""
    steps: list[tuple[str, list[str], bool]] = check_mod._steps(fix=True)
    names = [name for name, _, _ in steps]
    assert "ruff format" in names
    assert "ruff check --fix" in names
    # ...and nothing is left in check-only form.
    assert "ruff check" not in names
    # The remaining (non-fixable) steps are preserved, in order.
    plain = _step_names(check_mod)
    fixable = {"ruff format", "ruff check"}
    assert [n for n in names if n in plain and n not in fixable] == [
        n for n in plain if n not in fixable
    ]


def test_optional_tools_do_not_fail_the_run(check_mod: ModuleType) -> None:
    """npx/actionlint may be absent; that must not fail the suite."""
    optional: frozenset[str] = check_mod._OPTIONAL
    assert "jscpd" in optional
    assert "actionlint" in optional
    assert "mypy" not in optional
