"""Platform path selection and OS helpers (Windows / Linux / macOS)."""

from __future__ import annotations

import os
import platform
import subprocess
import sys
from pathlib import Path

import pytest

from app import paths


def test_platform_key_is_one_of_three() -> None:
    assert paths.platform_key() in ("win32", "linux", "darwin")


def test_arch_key_is_normalized() -> None:
    assert paths.arch_key() in ("x64", "arm64")


def test_exe_suffix_matches_platform() -> None:
    expected = ".exe" if sys.platform == "win32" else ""
    assert paths.exe_suffix() == expected
    assert paths.exe_name("llama-server") == f"llama-server{expected}"


@pytest.mark.parametrize(
    ("platform", "env", "expected_parts"),
    [
        ("win32", {"APPDATA": "C:\\Users\\t\\AppData\\Roaming"}, ("Roaming",)),
        ("linux", {"XDG_CONFIG_HOME": "/home/t/.config"}, (".config",)),
        ("darwin", {}, ("Library", "Preferences")),
    ],
)
def test_config_dir_follows_platform_convention(
    monkeypatch: pytest.MonkeyPatch,
    platform: str,
    env: dict[str, str],
    expected_parts: tuple[str, ...],
) -> None:
    monkeypatch.setattr(paths, "platform_key", lambda: platform)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    result = paths.app_config_dir()
    assert result.name == paths.APP_NAME
    for part in expected_parts:
        assert part in result.parts


def test_data_dir_is_separate_from_config_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths, "platform_key", lambda: "linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", "/home/t/.config")
    monkeypatch.setenv("XDG_DATA_HOME", "/home/t/.local/share")
    assert paths.app_config_dir() != paths.app_data_dir()


def test_relative_env_vars_are_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    """A relative XDG value is invalid per spec and must not be trusted."""
    monkeypatch.setattr(paths, "platform_key", lambda: "linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", "relative/path")
    assert paths.app_config_dir().is_absolute()


def test_config_file_can_be_overridden(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("LLAMAGUI_CONFIG_DIR", str(tmp_path))
    assert paths.config_file() == tmp_path / "config.json"


def test_existing_legacy_root_is_preferred(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An existing ~/.llamagui install keeps working after the path change."""
    legacy = tmp_path / ".llamagui"
    legacy.mkdir()
    monkeypatch.setattr(paths, "LEGACY_ROOT", legacy)
    assert paths.default_root() == legacy


def test_default_root_without_legacy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(paths, "LEGACY_ROOT", tmp_path / "missing")
    assert paths.default_root() == paths.app_data_dir()


@pytest.mark.skipif(
    sys.platform == "win32",
    reason=(
        "POSIX permission bits: Windows has no execute bit "
        "(executability is the .exe extension), so "
        "make_executable's chmod path only exists on Linux/macOS"
    ),
)
def test_make_executable_sets_x_bit(tmp_path: Path) -> None:
    target = tmp_path / "llama-server"
    target.write_bytes(b"ELF")
    target.chmod(0o644)
    assert not os.access(target, os.X_OK)

    paths.make_executable(target)
    assert os.access(target, os.X_OK)
    assert paths.is_executable(target)


def test_is_executable_rejects_directories(tmp_path: Path) -> None:
    assert paths.is_executable(tmp_path) is False


def test_clear_quarantine_is_safe_on_missing_path(tmp_path: Path) -> None:
    paths.clear_quarantine(tmp_path / "does-not-exist")


# ─── clear_quarantine uses one recursive xattr call ────────────────────────


def _macos_tree(tmp_path: Path, files: int) -> Path:
    """A release-like tree: nested dirs and many files."""
    root = tmp_path / "backend"
    for d in range(3):
        sub = root / "build0" / f"bin{d}"
        sub.mkdir(parents=True, exist_ok=True)
        for i in range(files):
            (sub / f"file{i}.dylib").write_bytes(b"x" * 8)
    return root


class _XattrResult:
    def __init__(self, code: int) -> None:
        self.returncode = code


def _record_xattr(
    monkeypatch: pytest.MonkeyPatch, *, recursive_fails: bool = False
) -> list[list[str]]:
    """Patch subprocess.run, recording every xattr invocation.

    ``recursive_fails`` makes the ``-dr`` form report failure so the per-file
    fallback can be exercised.
    """
    calls: list[list[str]] = []

    def _run(cmd: list[str], **kwargs: object) -> _XattrResult:
        calls.append(cmd)
        code = 1 if (recursive_fails and "-dr" in cmd) else 0
        return _XattrResult(code)

    monkeypatch.setattr("subprocess.run", _run)
    return calls


def test_clear_quarantine_uses_a_single_recursive_call(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Regression: it spawned one xattr per file, so a real release (thousands
    of files) took minutes and could be interrupted half-quarantined."""
    monkeypatch.setattr(paths, "is_macos", lambda: True)
    calls = _record_xattr(monkeypatch)
    root = _macos_tree(tmp_path, files=50)

    paths.clear_quarantine(root)

    assert len(calls) == 1, f"expected one recursive xattr call, got {len(calls)}"
    assert calls[0][:2] == ["xattr", "-dr"]
    assert calls[0][-1] == str(root)


def test_clear_quarantine_falls_back_per_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """If the recursive form fails, still try per file rather than giving up."""
    monkeypatch.setattr(paths, "is_macos", lambda: True)
    calls = _record_xattr(monkeypatch, recursive_fails=True)
    root = _macos_tree(tmp_path, files=2)

    paths.clear_quarantine(root)

    assert calls[0][:2] == ["xattr", "-dr"]
    assert any(c[:2] == ["xattr", "-d"] for c in calls[1:])


def test_clear_quarantine_is_a_noop_off_macos(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(paths, "is_macos", lambda: False)
    calls: list[str] = []

    def _spy(*args: str) -> bool:
        calls.extend(args)
        return True

    monkeypatch.setattr(paths, "_xattr", _spy)

    paths.clear_quarantine(tmp_path)
    assert calls == []


# ─── Platform predicates, resolved through the real functions ──


def test_platform_key_maps_every_system(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    assert paths.platform_key() == "win32"
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    assert paths.platform_key() == "darwin"
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    assert paths.platform_key() == "linux"
    # Anything else (BSDs included) is treated as Linux.
    monkeypatch.setattr(platform, "system", lambda: "FreeBSD")
    assert paths.platform_key() == "linux"


def test_is_linux_is_the_fallthrough(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths, "platform_key", lambda: "linux")
    assert paths.is_linux() is True


def test_arch_key_accepts_both_arm_spellings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for machine in ("arm64", "aarch64", "ARM64"):
        monkeypatch.setattr(platform, "machine", lambda m=machine: m)
        assert paths.arch_key() == "arm64"


def test_env_dir_ignores_unset_and_relative(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LLAMAGUI_TEST_VAR", raising=False)
    assert paths._env_dir("LLAMAGUI_TEST_VAR") is None
    monkeypatch.setenv("LLAMAGUI_TEST_VAR", "")
    assert paths._env_dir("LLAMAGUI_TEST_VAR") is None
    monkeypatch.setenv("LLAMAGUI_TEST_VAR", "relative/path")
    assert paths._env_dir("LLAMAGUI_TEST_VAR") is None


def test_data_dir_on_macos(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths, "platform_key", lambda: "darwin")
    monkeypatch.delenv("LLAMAGUI_DATA_DIR", raising=False)
    expected = Path.home() / "Library" / "Application Support" / paths.APP_NAME
    assert paths.app_data_dir() == expected


def test_data_dir_macos_env_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(paths, "platform_key", lambda: "darwin")
    monkeypatch.setenv("LLAMAGUI_DATA_DIR", str(tmp_path))
    assert paths.app_data_dir() == tmp_path


def test_config_file_defaults_to_the_platform_config_dir(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LLAMAGUI_CONFIG_DIR", raising=False)
    assert paths.config_file() == paths.app_config_dir() / "config.json"


def test_make_executable_chmods_and_swallows_missing_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The POSIX chmod path (skipped as a real test on Windows)."""
    monkeypatch.setattr(paths, "is_windows", lambda: False)
    target = tmp_path / "llama-server"
    target.write_bytes(b"ELF")

    paths.make_executable(target)  # must not raise
    # A missing file is an OSError the caller never sees.
    paths.make_executable(tmp_path / "missing")


def test_is_executable_uses_access_on_posix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(paths, "is_windows", lambda: False)
    target = tmp_path / "tool"
    target.write_bytes(b"x")
    assert paths.is_executable(target) == os.access(target, os.X_OK)


def test_xattr_reports_an_unusable_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _os_error(cmd: list[str], **kwargs: object) -> _XattrResult:
        raise OSError("no xattr on PATH")

    monkeypatch.setattr("subprocess.run", _os_error)
    assert paths._xattr("-dr", "com.apple.quarantine", "/x") is False

    def _timeout(cmd: list[str], **kwargs: object) -> _XattrResult:
        raise subprocess.TimeoutExpired(cmd="xattr", timeout=1)

    monkeypatch.setattr("subprocess.run", _timeout)
    assert paths._xattr("-d", "com.apple.quarantine", "/x") is False

    def _fails(cmd: list[str], **kwargs: object) -> _XattrResult:
        return _XattrResult(1)

    monkeypatch.setattr("subprocess.run", _fails)
    assert paths._xattr("-d", "com.apple.quarantine", "/x") is False


def test_clear_quarantine_swallows_a_broken_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _no_quarantine(*_args: str) -> bool:
        return False

    monkeypatch.setattr(paths, "is_macos", lambda: True)
    monkeypatch.setattr(paths, "_xattr", _no_quarantine)

    def _broken(self: Path, pattern: str) -> list[Path]:
        raise OSError("volume vanished")

    monkeypatch.setattr(Path, "rglob", _broken)
    paths.clear_quarantine(tmp_path)  # must not raise


@pytest.mark.parametrize(
    ("sys_platform", "expected"),
    [("win32", False), ("linux", True), ("darwin", True)],
)
def test_supports_symlinks_follows_the_platform(
    monkeypatch: pytest.MonkeyPatch, sys_platform: str, expected: bool
) -> None:
    monkeypatch.setattr(sys, "platform", sys_platform)
    assert paths.supports_symlinks() == expected


def test_legacy_config_file_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(paths, "LEGACY_ROOT", tmp_path)
    assert paths.legacy_config_file() == tmp_path / "config.json"


def test_make_executable_early_returns_on_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(paths, "is_windows", lambda: True)
    target = tmp_path / "llama-server.exe"
    target.write_bytes(b"exe")
    paths.make_executable(target)  # returns before any chmod
    assert target.exists()


def test_is_executable_on_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(paths, "is_windows", lambda: True)
    target = tmp_path / "tool.exe"
    target.write_bytes(b"exe")
    assert paths.is_executable(target) is True
