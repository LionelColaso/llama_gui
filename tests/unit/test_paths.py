"""Platform path selection and OS helpers (Windows / Linux / macOS)."""

from __future__ import annotations

import os
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


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
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
