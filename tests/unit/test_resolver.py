from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from app import resolver as resolver_mod
from app.backends.catalogue import Source
from app.config import AppConfig
from app.resolver import (
    _first_line,
    anything_resolved,
    current_backend_dir,
    find_exe_in_folder,
    resolve_llama_server,
    validate_binary,
)


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
    with patch("app.resolver.shutil.which", return_value=str(stub_exe)):
        result = resolve_llama_server(cfg)
    assert result.source is Source.SYSTEM


def test_resolve_os_toggle_falls_back_to_managed(
    stub_exe: Path, tmp_path: Path
) -> None:
    """Toggle on but nothing on PATH: the downloaded backend is used."""
    root = tmp_path / "root"
    _managed_backend(root, stub_exe)
    cfg = AppConfig(root=str(root), default_backend="cpu", use_os_llama_server=True)
    with patch("app.resolver.shutil.which", return_value=None):
        result = resolve_llama_server(cfg)
    assert result.source is Source.MANAGED_PREBUILT


def test_resolve_toggle_off_ignores_path(stub_exe: Path, tmp_path: Path) -> None:
    """Toggle off: even a PATH install is not used."""
    root = tmp_path / "root"
    (root / "managed").mkdir(parents=True)
    cfg = AppConfig(root=str(root), default_backend="cpu")
    with patch("app.resolver.shutil.which", return_value=str(stub_exe)):
        result = resolve_llama_server(cfg)
    assert result.path is None
    assert result.valid is False
    assert "backend location" in (result.error or "")


def test_resolve_os_toggle_nothing_available(tmp_path: Path) -> None:
    """Toggle on, empty PATH, empty backend location: a clear not-found."""
    root = tmp_path / "root"
    (root / "managed").mkdir(parents=True)
    cfg = AppConfig(root=str(root), default_backend="cpu", use_os_llama_server=True)
    with patch("app.resolver.shutil.which", return_value=None):
        result = resolve_llama_server(cfg)
    assert result.path is None
    assert result.valid is False
    assert "PATH" in (result.error or "")


def test_invalid_exe_reported_valid_false(stub_exe_fail: Path, tmp_path: Path) -> None:
    cfg = AppConfig(root=str(tmp_path / "root"), use_os_llama_server=True)
    with patch("app.resolver.shutil.which", return_value=str(stub_exe_fail)):
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
    with patch("app.resolver.shutil.which", return_value=str(stub_exe_fail)):
        result = resolve_llama_server(cfg, validate=False)
    assert result.valid is True
    assert result.path is not None
    assert result.source is Source.SYSTEM


# ─── Discovery details ───────────────────────────────────────────


def test_candidate_names_on_posix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(resolver_mod, "is_windows", lambda: False)
    names = resolver_mod._candidate_names("llama-server")
    assert names[0] == "llama-server"
    assert len(names) == 2


def _never_executable(p: Path) -> bool:
    """A filesystem where nothing has the execute bit."""
    return False


def test_find_exe_in_folder_yields_a_non_executable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A match without the execute bit is a last-resort fallback."""
    monkeypatch.setattr(resolver_mod, "is_executable", _never_executable)
    folder = tmp_path / "bin"
    folder.mkdir()
    candidate = folder / "llama-server"
    candidate.write_bytes(b"ELF")
    assert find_exe_in_folder(folder, "llama-server") == candidate


# ─── Validation details ──────────────────────────────────────────


def test_validate_binary_rejects_a_missing_file(tmp_path: Path) -> None:
    valid, version, error = validate_binary(tmp_path / "missing")
    assert valid is False
    assert version is None
    assert "Not a file" in (error or "")


def test_validate_binary_rejects_a_non_executable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(resolver_mod, "is_executable", _never_executable)
    exe = tmp_path / "llama-server"
    exe.write_bytes(b"ELF")
    valid, version, error = validate_binary(exe)
    assert valid is False
    assert version is None
    assert "Not executable" in (error or "")


@pytest.fixture
def stub_help_exe(tmp_path: Path) -> Path:
    """A stub that fails ``--version`` but answers ``--help``."""
    body = (
        "import sys\n"
        'if "--version" in sys.argv:\n'
        '    sys.stderr.write("no version\\n"); sys.exit(1)\n'
        'sys.stderr.write("stub help v2.0\\n"); sys.exit(0)\n'
    )
    if sys.platform == "win32":
        script = tmp_path / "stub_help.py"
        script.write_text(body, encoding="utf-8")
        exe = tmp_path / "llama-server.bat"
        exe.write_text(f'@{sys.executable} "{script}" %*\n', encoding="utf-8")
    else:
        exe = tmp_path / "llama-server"
        exe.write_text("#!/usr/bin/env python3\n" + body)
        exe.chmod(0o755)
    return exe


def test_validate_binary_falls_back_to_help(stub_help_exe: Path) -> None:
    valid, version, error = validate_binary(stub_help_exe)
    assert valid is True
    assert version == "stub help v2.0"
    assert error is None


def test_validate_binary_maps_probe_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    exe = tmp_path / "llama-server"
    exe.write_bytes(b"ELF")
    if sys.platform != "win32":
        # POSIX refuses to probe a file without the execute bit,
        # so without this the "not executable" branch would
        # short-circuit before the stubbed probe ever runs.
        exe.chmod(0o755)

    def _timeout(path: str, flag: str, timeout: float) -> object:
        raise subprocess.TimeoutExpired(cmd=str(path), timeout=timeout)

    monkeypatch.setattr(resolver_mod, "_run_probe", _timeout)
    valid, version, error = validate_binary(exe, timeout=0.1)
    assert valid is False
    assert version is None
    assert error is not None

    def _os_error(path: str, flag: str, timeout: float) -> object:
        raise OSError("spawn failed")

    monkeypatch.setattr(resolver_mod, "_run_probe", _os_error)
    valid, version, error = validate_binary(exe)
    assert valid is False
    assert "spawn failed" in (error or "")


def test_first_line_trims_and_treats_blank_as_none() -> None:
    assert _first_line(None) is None
    assert _first_line("") is None
    assert _first_line("   \n  ") is None
    assert _first_line("v1.0.0\nmore") == "v1.0.0"


# ─── The managed/current link ────────────────────────────────────


def test_current_symlink_wins_over_the_default_backend(
    stub_exe: Path, tmp_path: Path
) -> None:
    root = tmp_path / "root"
    vulkan = _managed_backend(root, stub_exe, "vulkan").parent
    current = root / "managed" / "current"
    current.symlink_to(vulkan, target_is_directory=True)
    cfg = AppConfig(root=str(root), default_backend="cpu")

    result = resolve_llama_server(cfg)

    assert result.path is not None
    assert "vulkan" in (result.path or "")


def test_current_symlink_with_a_relative_target(stub_exe: Path, tmp_path: Path) -> None:
    root = tmp_path / "root"
    _managed_backend(root, stub_exe, "vulkan")
    current = root / "managed" / "current"
    current.symlink_to("vulkan", target_is_directory=True)

    assert current_backend_dir(root) == (root / "managed" / "vulkan").resolve()


def test_current_as_a_plain_directory(stub_exe: Path, tmp_path: Path) -> None:
    """Filesystems that disallow links keep a real directory."""
    root = tmp_path / "root"
    _managed_backend(root, stub_exe, "cpu")
    current = root / "managed" / "current"
    current.mkdir()
    _place(stub_exe, current)
    cfg = AppConfig(root=str(root), default_backend="vulkan")

    result = resolve_llama_server(cfg)

    assert result.path is not None
    assert "current" in (result.path or "")


def test_current_as_a_plain_file_falls_back_to_the_default(
    stub_exe: Path, tmp_path: Path
) -> None:
    root = tmp_path / "root"
    _managed_backend(root, stub_exe, "cpu")
    (root / "managed" / "current").write_bytes(b"not a directory")
    cfg = AppConfig(root=str(root), default_backend="cpu")

    result = resolve_llama_server(cfg)

    assert result.path is not None
    assert "cpu" in (result.path or "")


def test_current_backend_dir_without_a_link(tmp_path: Path) -> None:
    root = tmp_path / "root"
    (root / "managed").mkdir(parents=True)
    assert current_backend_dir(root) is None


def test_current_dangling_symlink(tmp_path: Path) -> None:
    """A link to a directory that never arrived is treated as unset."""
    root = tmp_path / "root"
    (root / "managed").mkdir(parents=True)
    current = root / "managed" / "current"
    current.symlink_to(root / "managed" / "nowhere", target_is_directory=True)
    assert current_backend_dir(root) is None


def test_current_link_to_a_backend_without_the_exe(
    stub_exe: Path, tmp_path: Path
) -> None:
    """The link target holds no binary: fall back to the default."""
    root = tmp_path / "root"
    _managed_backend(root, stub_exe, "cpu")
    empty = root / "managed" / "empty"
    empty.mkdir()
    current = root / "managed" / "current"
    current.symlink_to(empty, target_is_directory=True)
    cfg = AppConfig(root=str(root), default_backend="cpu")

    result = resolve_llama_server(cfg)

    assert result.path is not None
    assert "cpu" in (result.path or "")


def test_resolve_managed_without_the_current_link(
    stub_exe: Path, tmp_path: Path
) -> None:
    """use_current_link=False skips the link entirely."""
    root = tmp_path / "root"
    _managed_backend(root, stub_exe, "cpu")
    cfg = AppConfig(root=str(root), default_backend="cpu")

    result = resolver_mod._resolve_managed(root, cfg, "llama-server", True, False)

    assert result is not None
    assert result.path is not None
    assert "cpu" in result.path


# ─── First-run decision ──────────────────────────────────────────


def test_anything_resolved_reflects_the_backend_location(
    stub_exe: Path, tmp_path: Path
) -> None:
    root = tmp_path / "root"
    _managed_backend(root, stub_exe)
    cfg = AppConfig(root=str(root), default_backend="cpu")
    assert anything_resolved(cfg) is True

    empty = AppConfig(root=str(tmp_path / "empty"), default_backend="cpu")
    assert anything_resolved(empty) is False
