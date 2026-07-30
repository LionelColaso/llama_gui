from __future__ import annotations

import socket
import subprocess
import sys
import threading
from collections.abc import Generator
from pathlib import Path

import pytest


@pytest.fixture
def fake_root(tmp_path: Path) -> Path:
    root = tmp_path / "llamagui"
    (root / "state").mkdir(parents=True)
    return root


@pytest.fixture
def fake_root_with_junction(fake_root: Path) -> Path:
    target = fake_root / "managed" / "vulkan"
    target.mkdir(parents=True)
    (target / ".version").write_text("b10189\nmanaged-prebuilt\n", encoding="utf-8")
    (fake_root / "state" / "active.txt").write_text("vulkan\n", encoding="utf-8")

    current = fake_root / "managed" / "current"
    if sys.platform == "win32":
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(current), str(target)],
            check=True,
            capture_output=True,
        )
    else:
        current.symlink_to(target, target_is_directory=True)

    return fake_root


@pytest.fixture
def ephemeral_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port: int = s.getsockname()[1]
        return port


def _serve_port(port: int, stop_event: threading.Event) -> None:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", port))
    s.listen(1)
    s.settimeout(0.5)
    while not stop_event.is_set():
        try:
            s.accept()
        except TimeoutError:
            continue
    s.close()


@pytest.fixture
def port_server(ephemeral_port: int) -> Generator[int, None, None]:
    stop = threading.Event()
    t = threading.Thread(target=_serve_port, args=(ephemeral_port, stop), daemon=True)
    t.start()
    yield ephemeral_port
    stop.set()
    t.join(timeout=2)
