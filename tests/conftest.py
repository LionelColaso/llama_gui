from __future__ import annotations

import socket
import subprocess
import sys
import threading
from collections.abc import Generator
from pathlib import Path

import pytest
from loguru import logger

from app.applog import configure_logging


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep every test off the developer's real config and managed root.

    Without this, any test that reaches ``AppConfig.load()`` (or the CLI,
    which does so implicitly) reads and can write the user's actual
    ``%APPDATA%/llamagui/config.json`` and their real managed root. A test run
    could then depend on, or corrupt, real state.

    ``LLAMAGUI_CONFIG_DIR`` is the documented override for the settings file.
    The data dir is redirected by setting the platform's own base-directory
    variables (``LOCALAPPDATA`` on Windows, ``XDG_DATA_HOME`` elsewhere), and
    ``LEGACY_ROOT`` is repointed because ``default_root()`` prefers an existing
    ``~/.llamagui`` over the platform data dir. A stray ``LLAMAGUI_CONFIG_DIR``
    inherited from the developer's own shell is cleared first so this fixture
    is what decides, not the environment.
    """
    monkeypatch.delenv("LLAMAGUI_CONFIG_DIR", raising=False)
    monkeypatch.setenv("LLAMAGUI_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("LLAMAGUI_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setattr("app.paths.LEGACY_ROOT", tmp_path / "no-legacy-root")


def _drain_loguru(timeout: float = 10.0) -> None:
    """Drain loguru's enqueue queue, bounded.

    ``logger.complete()`` blocks on an unbounded
    ``multiprocessing.Event`` until the enqueue worker
    confirms the queue is empty. A worker stalled by a
    loaded CI runner would wedge this teardown — and with
    it the whole suite — forever (the Windows runner hung
    exactly this way). Draining on a daemon thread bounds
    the wait: a stalled worker then costs at most one
    test, and the next ``configure_logging`` (whose
    ``logger.remove()`` stops the handler and joins the
    worker) replaces it regardless.
    """
    drain = threading.Thread(target=logger.complete, daemon=True)
    drain.start()
    drain.join(timeout)


@pytest.fixture(autouse=True)
def _app_log(tmp_path: Path) -> Generator[None]:
    """Route loguru to a per-test temp file.

    Without this, loguru's default sink writes to stderr (breaking tests that
    assert on captured stderr), and the enqueued worker-thread sink would
    flush into unrelated tests. ``_drain_loguru`` drains the queue on
    teardown so each test's log is self-contained.
    """
    configure_logging(tmp_path / "logs")
    yield
    _drain_loguru()


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
def port_server(ephemeral_port: int) -> Generator[int]:
    stop = threading.Event()
    t = threading.Thread(target=_serve_port, args=(ephemeral_port, stop), daemon=True)
    t.start()
    yield ephemeral_port
    stop.set()
    t.join(timeout=2)
