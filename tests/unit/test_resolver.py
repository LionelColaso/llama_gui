from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from llamagui.config import AppConfig
from llamagui.models import Source
from llamagui.resolver import resolve_llama_server


def _place(stub: Path, directory: Path) -> Path:
    """Copy the stub executable into ``directory`` keeping its executable bit."""
    target = directory / stub.name
    target.write_text(stub.read_text(encoding="utf-8"), encoding="utf-8")
    if sys.platform != "win32":
        target.chmod(0o755)
    return target


def _managed_backend(root: Path, stub: Path, backend: str = "cpu") -> Path:
    """Lay a backend out in the backend location (``<root>/managed/<backend>``)."""
    directory = root / "managed" / backend
    directory.mkdir(parents=True, exist_ok=True)
    return _place(stub, directory)


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
    else:
        exe = tmp_path / "llama-server"
        exe.write_text(
            "#!/usr/bin/env python3\nimport sys; sys.stdout.write('stub v1.0.0\\n'); sys.exit(0)\n"
        )
        exe.chmod(0o755)
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


def test_resolve_managed_default(stub_exe: Path, tmp_path: Path) -> None:
    """Toggle off: the backend location is the only source."""
    root = tmp_path / "root"
    _managed_backend(root, stub_exe)
    cfg = AppConfig(root=str(root), default_backend="cpu")
    result = resolve_llama_server(cfg)
    assert result.source is Source.MANAGED_PREBUILT
    assert result.path is not None
    assert result.valid is True


def test_resolve_managed_build_marker(stub_exe: Path, tmp_path: Path) -> None:
    """Legacy from-source artifacts still resolve, labeled managed-build."""
    root = tmp_path / "root"
    directory = _managed_backend(root, stub_exe).parent
    (directory / ".version").write_text("b12345\nmanaged-build\n", encoding="utf-8")
    cfg = AppConfig(root=str(root), default_backend="cpu")
    result = resolve_llama_server(cfg)
    assert result.source is Source.MANAGED_BUILD


def test_resolve_os_toggle_prefers_path(stub_exe: Path, tmp_path: Path) -> None:
    """Toggle on: a PATH install wins even when a backend is downloaded."""
    root = tmp_path / "root"
    _managed_backend(root, stub_exe)
    cfg = AppConfig(root=str(root), default_backend="cpu", use_os_llama_server=True)
    with patch("llamagui.resolver.shutil.which", return_value=str(stub_exe)):
        result = resolve_llama_server(cfg)
    assert result.source is Source.SYSTEM


def test_resolve_os_toggle_falls_back_to_managed(
    stub_exe: Path, tmp_path: Path
) -> None:
    """Toggle on but nothing on PATH: the downloaded backend is used."""
    root = tmp_path / "root"
    _managed_backend(root, stub_exe)
    cfg = AppConfig(root=str(root), default_backend="cpu", use_os_llama_server=True)
    with patch("llamagui.resolver.shutil.which", return_value=None):
        result = resolve_llama_server(cfg)
    assert result.source is Source.MANAGED_PREBUILT


def test_resolve_toggle_off_ignores_path(stub_exe: Path, tmp_path: Path) -> None:
    """Toggle off: even a PATH install is not used."""
    root = tmp_path / "root"
    (root / "managed").mkdir(parents=True)
    cfg = AppConfig(root=str(root), default_backend="cpu")
    with patch("llamagui.resolver.shutil.which", return_value=str(stub_exe)):
        result = resolve_llama_server(cfg)
    assert result.path is None
    assert result.valid is False
    assert "backend location" in (result.error or "")


def test_resolve_os_toggle_nothing_available(tmp_path: Path) -> None:
    """Toggle on, empty PATH, empty backend location: a clear not-found."""
    root = tmp_path / "root"
    (root / "managed").mkdir(parents=True)
    cfg = AppConfig(root=str(root), default_backend="cpu", use_os_llama_server=True)
    with patch("llamagui.resolver.shutil.which", return_value=None):
        result = resolve_llama_server(cfg)
    assert result.path is None
    assert result.valid is False
    assert "PATH" in (result.error or "")


def test_invalid_exe_reported_valid_false(stub_exe_fail: Path, tmp_path: Path) -> None:
    cfg = AppConfig(root=str(tmp_path / "root"), use_os_llama_server=True)
    with patch("llamagui.resolver.shutil.which", return_value=str(stub_exe_fail)):
        result = resolve_llama_server(cfg)
    assert result.valid is False


def test_resolve_fast_path_skips_validation(
    stub_exe_fail: Path, tmp_path: Path
) -> None:
    """validate=False (status hot path) must not spawn --version subprocesses.

    Even a failing exe is reported valid-by-existence so the dashboard refresh
    never blocks on a subprocess (invariant: reads never spawn on the hot path).
    """
    cfg = AppConfig(root=str(tmp_path / "root"), use_os_llama_server=True)
    with patch("llamagui.resolver.shutil.which", return_value=str(stub_exe_fail)):
        result = resolve_llama_server(cfg, validate=False)
    assert result.valid is True
    assert result.path is not None
    assert result.source is Source.SYSTEM
