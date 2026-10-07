from __future__ import annotations

import os
import struct
from pathlib import Path

import pytest

from app.state import (
    _read_reparse_point,
    check_port,
    read_active_backend,
    read_component_version,
    read_junction_target,
    read_link_target,
)

_JUNCTION_TAG = 0xA000000C


def _junction_bytes(target: str) -> bytes:
    """Craft ``IO_REPARSE_MOUNT_POINT`` bytes pointing at ``target``."""
    name = target.encode("utf-16-le")
    buf = bytearray(16 + len(name))
    struct.pack_into("I", buf, 0, _JUNCTION_TAG)
    struct.pack_into("H", buf, 8, 0)  # SubstituteNameOffset
    struct.pack_into("H", buf, 10, len(name))  # SubstituteNameLength
    buf[16:] = name
    return bytes(buf)


def _fake_open(*_a: object, **_k: object) -> int:
    return 42


def _fake_close(*_a: object, **_k: object) -> None:
    return None


def test_read_active_backend_none(fake_root: Path) -> None:
    assert read_active_backend(fake_root) is None


def test_read_active_backend_present(fake_root: Path) -> None:
    state = fake_root / "state"
    state.mkdir(parents=True, exist_ok=True)
    (state / "active.txt").write_text("vulkan\n", encoding="utf-8")
    assert read_active_backend(fake_root) == "vulkan"


def test_read_active_backend_empty_file(fake_root: Path) -> None:
    state = fake_root / "state"
    state.mkdir(parents=True, exist_ok=True)
    (state / "active.txt").write_text("", encoding="utf-8")
    assert read_active_backend(fake_root) is None


def test_read_component_version_none(fake_root: Path) -> None:
    assert read_component_version(fake_root, "vulkan") is None


def test_read_component_version_present(fake_root: Path) -> None:
    managed = fake_root / "managed" / "vulkan"
    managed.mkdir(parents=True)
    (managed / ".version").write_text("b10189\nmanaged-prebuilt\n", encoding="utf-8")
    cv = read_component_version(fake_root, "vulkan")
    assert cv is not None
    tag, source = cv
    assert tag == "b10189"
    assert source == "managed-prebuilt"


def test_read_junction_target_none(fake_root: Path) -> None:
    assert read_junction_target(fake_root) is None


def test_read_junction_target_present(fake_root_with_junction: Path) -> None:
    target = read_junction_target(fake_root_with_junction)
    assert target is not None
    assert "vulkan" in target


def test_check_port_open(port_server: int) -> None:
    assert check_port("127.0.0.1", port_server, timeout=1.0) is True


def test_check_port_closed(ephemeral_port: int) -> None:
    assert check_port("127.0.0.1", ephemeral_port, timeout=0.1) is False


# ─── read_link_target / _read_reparse_point ─────────────────


def test_read_link_target_missing_path(tmp_path: Path) -> None:
    assert read_link_target(tmp_path / "nope") is None


def test_read_link_target_reads_a_symlink(tmp_path: Path) -> None:
    target = tmp_path / "vulkan"
    target.mkdir()
    link = tmp_path / "current"
    link.symlink_to(target, target_is_directory=True)
    got = read_link_target(link)
    assert got == os.readlink(str(link))
    assert got is not None and "vulkan" in got


def test_read_link_target_reads_a_broken_symlink(tmp_path: Path) -> None:
    link = tmp_path / "current"
    link.symlink_to(tmp_path / "gone", target_is_directory=True)
    got = read_link_target(link)
    assert got == os.readlink(str(link))
    assert got is not None and "gone" in got


def test_read_link_target_falls_back_when_readlink_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """readlink raising OSError drops into the raw reparse reader."""
    link = tmp_path / "current"
    link.write_bytes(b"plain file, not a reparse point at all")

    def _no_readlink(_path: str) -> str:
        raise OSError("readlink failed")

    monkeypatch.setattr(os, "readlink", _no_readlink)
    assert read_link_target(link) is None


def test_read_reparse_point_decodes_a_junction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    link = tmp_path / "current"
    link.write_bytes(b"\x00" * 64)

    def _read_junction(*_a: object, **_k: object) -> bytes:
        return _junction_bytes(r"C:\backends\vulkan")

    monkeypatch.setattr(os, "open", _fake_open)
    monkeypatch.setattr(os, "read", _read_junction)
    monkeypatch.setattr(os, "close", _fake_close)
    assert _read_reparse_point(link) == r"C:\backends\vulkan"


def test_read_reparse_point_short_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    link = tmp_path / "current"
    link.write_bytes(b"\x00" * 64)

    def _read_short(*_a: object, **_k: object) -> bytes:
        return b"\x00" * 10

    monkeypatch.setattr(os, "open", _fake_open)
    monkeypatch.setattr(os, "read", _read_short)
    monkeypatch.setattr(os, "close", _fake_close)
    assert _read_reparse_point(link) is None


def test_read_reparse_point_wrong_tag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    link = tmp_path / "current"
    link.write_bytes(b"\x00" * 64)
    data = bytearray(_junction_bytes(r"C:\backends\vulkan"))
    struct.pack_into("I", data, 0, 0x9000000B)  # some other reparse tag

    def _read_wrong_tag(*_a: object, **_k: object) -> bytes:
        return bytes(data)

    monkeypatch.setattr(os, "open", _fake_open)
    monkeypatch.setattr(os, "read", _read_wrong_tag)
    monkeypatch.setattr(os, "close", _fake_close)
    assert _read_reparse_point(link) is None


def test_read_reparse_point_open_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    link = tmp_path / "current"
    link.write_bytes(b"\x00" * 64)

    def _no_open(*_a: object, **_k: object) -> int:
        raise OSError("permission denied")

    monkeypatch.setattr(os, "open", _no_open)
    assert _read_reparse_point(link) is None


def test_read_reparse_point_undecodable_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An odd-length name is not valid utf-16-le."""
    link = tmp_path / "current"
    link.write_bytes(b"\x00" * 64)
    data = bytearray(_junction_bytes(r"C:\backends\vulkan"))
    data[10] = 5  # SubstituteNameLength: odd -> decode error

    def _read_odd(*_a: object, **_k: object) -> bytes:
        return bytes(data)

    monkeypatch.setattr(os, "open", _fake_open)
    monkeypatch.setattr(os, "read", _read_odd)
    monkeypatch.setattr(os, "close", _fake_close)
    assert _read_reparse_point(link) is None
