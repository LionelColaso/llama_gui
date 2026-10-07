from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from app.links import link_current, remove_link
from app.schemas import EngineError, ExitCode


def _raise_oserror(*_args: object, **_kwargs: object) -> None:
    raise OSError("simulated failure")


# ─── link_current ───────────────────────────────────────────────────


def test_link_current_posix_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.links.is_windows", lambda: False)
    target = tmp_path / "vulkan"
    target.mkdir()
    link = tmp_path / "current"
    link_current(link, target)
    assert link.is_symlink()
    got = os.readlink(link)
    # Windows stores the target verbatim, in \\?\ extended form.
    assert got == str(target) or got == "\\\\?\\" + str(target)


def test_link_current_posix_symlink_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed symlink leaves no temp link behind and raises EngineError."""
    monkeypatch.setattr("app.links.is_windows", lambda: False)
    monkeypatch.setattr(os, "symlink", _raise_oserror)
    with pytest.raises(EngineError) as excinfo:
        link_current(tmp_path / "current", tmp_path / "vulkan")
    assert excinfo.value.exit_code == ExitCode.UNEXPECTED_ERROR
    assert not (tmp_path / "current.new").exists()


def test_link_current_windows_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.links.is_windows", lambda: True)
    made: list[tuple[str, str]] = []

    def _symlink(src: str, dst: str, **_kwargs: object) -> None:
        made.append((src, dst))

    monkeypatch.setattr(os, "symlink", _symlink)
    target = tmp_path / "vulkan"
    target.mkdir()
    link = tmp_path / "current"
    link_current(link, target)
    assert made == [(str(target), str(link))]


def test_link_current_windows_falls_back_to_junction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without symlink privilege, ``mklink /J`` is attempted."""
    monkeypatch.setattr("app.links.is_windows", lambda: True)
    monkeypatch.setattr(os, "symlink", _raise_oserror)
    ran: list[list[str]] = []

    def _run(cmd: list[str], **_kwargs: object) -> None:
        ran.append(list(cmd))

    monkeypatch.setattr("app.links.subprocess.run", _run)
    target = tmp_path / "vulkan"
    target.mkdir()
    link_current(tmp_path / "current", target)
    assert ran == [
        ["cmd", "/c", "mklink", "/J", str(tmp_path / "current"), str(target)]
    ]


def test_link_current_windows_junction_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.links.is_windows", lambda: True)
    monkeypatch.setattr(os, "symlink", _raise_oserror)

    def _fail_run(cmd: list[str], **_kwargs: object) -> None:
        raise subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr("app.links.subprocess.run", _fail_run)
    with pytest.raises(EngineError) as excinfo:
        link_current(tmp_path / "current", tmp_path / "vulkan")
    assert excinfo.value.exit_code == ExitCode.UNEXPECTED_ERROR


# ─── remove_link ────────────────────────────────────────────────────


def test_remove_link_symlink(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    link = tmp_path / "current"
    unlinked: list[Path] = []

    def _always_symlink(self: Path) -> bool:
        return True

    def _unlink(self: Path, **_kwargs: object) -> None:
        unlinked.append(self)

    monkeypatch.setattr(Path, "is_symlink", _always_symlink)
    monkeypatch.setattr(Path, "unlink", _unlink)
    remove_link(link)
    assert unlinked == [link]


def test_remove_link_missing_is_a_noop(tmp_path: Path) -> None:
    remove_link(tmp_path / "does-not-exist")


def test_remove_link_junction_uses_rmdir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A junction is a directory: rmdir removes the link, not the target."""
    link = tmp_path / "current"
    link.mkdir()
    removed: list[Path] = []

    def _rmdir(self: Path, **_kwargs: object) -> None:
        removed.append(self)

    monkeypatch.setattr(Path, "rmdir", _rmdir)
    remove_link(link)
    assert removed == [link]


def test_remove_link_junction_rmdir_failure_falls_back_to_unlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    link = tmp_path / "current"
    link.mkdir()
    monkeypatch.setattr(Path, "rmdir", _raise_oserror)
    unlinked: list[Path] = []

    def _unlink(self: Path, **_kwargs: object) -> None:
        unlinked.append(self)

    monkeypatch.setattr(Path, "unlink", _unlink)
    remove_link(link)
    assert unlinked == [link]
