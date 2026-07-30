from __future__ import annotations

import sys
from pathlib import Path

import pytest

from llamagui.config import AppConfig, PointedPaths
from llamagui.models import Source
from llamagui.resolver import (
    ResolvedBinary,
    resolve_both,
    resolve_llama_server,
)


@pytest.fixture
def stub_exe(tmp_path: Path) -> Path:
    """Create a stub exe that exits 0 and prints a version string."""
    if sys.platform == "win32":
        script = tmp_path / "stub_server.py"
        script.write_text(
            'import sys; sys.stdout.write("stub v1.0.0\\n"); sys.exit(0)\n',
            encoding="utf-8",
        )
        exe = tmp_path / "llama-server.bat"
        exe.write_text(f'@{sys.executable} "{script}" %*\n', encoding="utf-8")
        swap = tmp_path / "llama-swap.bat"
        swap.write_text(f'@{sys.executable} "{script}" %*\n', encoding="utf-8")
    else:
        exe = tmp_path / "llama-server"
        exe.write_text(
            "#!/usr/bin/env python3\nimport sys; sys.stdout.write('stub v1.0.0\\n'); sys.exit(0)\n"
        )
        exe.chmod(0o755)
        swap = tmp_path / "llama-swap"
        swap.write_text(
            "#!/usr/bin/env python3\nimport sys; sys.stdout.write('stub v1.0.0\\n'); sys.exit(0)\n"
        )
        swap.chmod(0o755)
    return exe


@pytest.fixture
def stub_exe_fail(tmp_path: Path) -> Path:
    """Create a stub exe that exits 1."""
    if sys.platform == "win32":
        script = tmp_path / "stub_fail.py"
        script.write_text(
            "import sys; sys.stderr.write('not found\\n'); sys.exit(1)\n",
            encoding="utf-8",
        )
        exe = tmp_path / "llama-server.bat"
        exe.write_text(f'@{sys.executable} "{script}" %*\n', encoding="utf-8")
    else:
        exe = tmp_path / "llama-server"
        exe.write_text(
            "#!/usr/bin/env python3\nimport sys; sys.stderr.write('not found\\n'); sys.exit(1)\n"
        )
        exe.chmod(0o755)
    return exe


def test_resolve_pointed_folder(stub_exe: Path) -> None:
    cfg = AppConfig(
        pointed=PointedPaths(folder=str(stub_exe.parent)),
        source_priority=["pointed"],
    )
    result = resolve_llama_server(cfg)
    assert result.source == Source.POINTED


def test_resolve_pointed_separate_paths(stub_exe: Path) -> None:
    cfg = AppConfig(
        pointed=PointedPaths(llama_server=str(stub_exe)),
        source_priority=["pointed"],
    )
    result = resolve_llama_server(cfg)
    assert result.source == Source.POINTED


def test_resolve_pointed_missing_path() -> None:
    cfg = AppConfig(
        pointed=PointedPaths(folder=r"C:\nonexistent"),
        source_priority=["pointed"],
    )
    result = resolve_llama_server(cfg)
    assert result.valid is False
    assert result.path is None


def test_resolve_system() -> None:
    cfg = AppConfig(source_priority=["system"])
    result = resolve_llama_server(cfg)
    if result.path:
        assert result.source == Source.SYSTEM
    else:
        assert result.valid is False


def test_resolve_both_returns_dict(stub_exe: Path) -> None:
    cfg = AppConfig(
        pointed=PointedPaths(folder=str(stub_exe.parent)),
        source_priority=["pointed"],
    )
    results = resolve_both(cfg)
    assert "llama_server" in results
    assert "llama_swap" in results


def test_resolve_priority_order() -> None:
    cfg = AppConfig(source_priority=["system", "pointed"])
    results = resolve_both(cfg)
    assert isinstance(results["llama_server"], ResolvedBinary)


def test_invalid_exe_reported_valid_false(stub_exe_fail: Path) -> None:
    cfg = AppConfig(
        pointed=PointedPaths(llama_server=str(stub_exe_fail)),
        source_priority=["pointed"],
    )
    result = resolve_llama_server(cfg)
    assert result.valid is False


def test_resolve_fast_path_skips_validation(stub_exe_fail: Path) -> None:
    """validate=False (status hot path) must not spawn --version subprocesses.

    Even a failing exe is reported valid-by-existence so the dashboard refresh
    never blocks on a subprocess (invariant: reads never spawn on the hot path).
    """
    cfg = AppConfig(
        pointed=PointedPaths(llama_server=str(stub_exe_fail)),
        source_priority=["pointed"],
    )
    result = resolve_llama_server(cfg, validate=False)
    assert result.valid is True
    assert result.path is not None
    assert result.source == Source.POINTED
