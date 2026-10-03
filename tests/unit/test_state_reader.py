from __future__ import annotations

from pathlib import Path

from llamagui.lifecycle import (
    check_port,
    read_active_backend,
    read_component_version,
    read_junction_target,
)


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
