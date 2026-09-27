"""Tests for the developer scripts under ``scripts/``.

These are not shipped, but they are part of the project's tooling: a bug in
``stats.py`` once made it walk the entire virtualenv on every run, and nothing
noticed because no test ran it. The loaders here import each script by path so
the tests work regardless of whether ``scripts/`` is a package.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"


def _load(name: str) -> ModuleType:
    path = SCRIPTS / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"_script_{name}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# ─── stats.py ─────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def stats() -> ModuleType:
    return _load("stats")


def test_stats_counts_the_projects_own_sources(stats: ModuleType) -> None:
    files = list(stats._iter_source_files(ROOT))
    names = {p.name for p in files}
    assert "orchestrator.py" in names
    assert "__init__.py" in names


def test_stats_excludes_the_virtualenv(stats: ModuleType) -> None:
    """Regression: the walk must not descend into .venv.

    The old implementation used rglob('*') with an is_dir() branch that could
    not prevent the descent, so every run walked the whole virtualenv.
    """
    files = list(stats._iter_source_files(ROOT))
    assert files, "expected to find the project's own sources"
    assert not any(".venv" in p.parts for p in files), "walked into .venv"
    assert not any("__pycache__" in p.parts for p in files)


def test_stats_excludes_tool_caches(stats: ModuleType) -> None:
    files = list(stats._iter_source_files(ROOT))
    for cache in (".mypy_cache", ".ruff_cache", ".pytest_cache"):
        assert not any(cache in p.parts for p in files), f"walked into {cache}"


def test_stats_includes_stubs(stats: ModuleType) -> None:
    """SOURCE_EXTS covers .pyi as well as .py."""
    assert ".pyi" in stats.SOURCE_EXTS
    assert ".py" in stats.SOURCE_EXTS


def test_stats_ignores_non_source_files(stats: ModuleType) -> None:
    files = list(stats._iter_source_files(ROOT))
    assert not any(p.suffix in {".md", ".toml", ".yml"} for p in files)


def test_stats_main_runs_and_prints(
    stats: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    assert stats.main() == 0
    out = capsys.readouterr().out
    assert "Python files:" in out
    assert "Total LOC:" in out


# ─── mapping.py ───────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def mapping() -> ModuleType:
    return _load("mapping")


def test_mapping_generates_a_tree(mapping: ModuleType) -> None:
    assert mapping.main() == 0
    assert (ROOT / "mapping.md").is_file()
    text = (ROOT / "mapping.md").read_text(encoding="utf-8")
    assert text.startswith("# Project Structure Map")
    assert "llamagui" in text


def test_mapping_excludes_ignored_paths(mapping: ModuleType) -> None:
    """Ignored trees must not appear as entries in the tree itself.

    The header sentence legitimately names the excluded patterns, so only the
    tree lines are checked.
    """
    text = (ROOT / "mapping.md").read_text(encoding="utf-8")
    tree = [ln for ln in text.splitlines() if ("├──" in ln or "└──" in ln)]
    assert tree, "expected a tree in mapping.md"
    for excluded in (".venv", "__pycache__", ".mypy_cache", ".ruff_cache"):
        assert not any(ln.rstrip().endswith(f"{excluded}/") for ln in tree), (
            f"{excluded} should not be an entry in the generated tree"
        )


def test_mapping_respects_gitignore_entries() -> None:
    """The parser must read patterns rather than hardcoding them."""
    mapping = _load("mapping")
    patterns = mapping.parse_gitignore(ROOT)
    assert patterns, "expected patterns from .gitignore"
    names = {p for p, _, _ in patterns}
    assert ".venv" in names or "build" in names or "dist" in names


# ─── clean.py ─────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def clean() -> ModuleType:
    return _load("clean")


def test_clean_lists_what_it_would_remove(
    clean: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--dry-run must report without deleting."""
    monkeypatch.setattr(clean, "ROOT", tmp_path)
    (tmp_path / ".pytest_cache").mkdir()
    (tmp_path / ".coverage").write_text("x", encoding="utf-8")

    monkeypatch.setattr(sys, "argv", ["clean.py", "--dry-run"])
    assert clean.main() == 0
    assert (tmp_path / ".pytest_cache").is_dir(), "dry-run must not delete"
    assert (tmp_path / ".coverage").exists(), "dry-run must not delete"


def test_clean_removes_caches(
    clean: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(clean, "ROOT", tmp_path)
    cache = tmp_path / ".pytest_cache"
    cache.mkdir()
    coverage = tmp_path / "coverage.xml"
    coverage.write_text("<xml/>", encoding="utf-8")

    monkeypatch.setattr(sys, "argv", ["clean.py"])
    assert clean.main() == 0
    assert not cache.exists()
    assert not coverage.exists()
