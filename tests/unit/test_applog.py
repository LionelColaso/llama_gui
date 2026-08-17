from __future__ import annotations

import sys
from pathlib import Path
from types import TracebackType

import pytest
from loguru import logger

from llamagui.applog import configure_logging, install_excepthook


def test_configure_logging_writes_errors(tmp_path: Path) -> None:
    path = configure_logging(tmp_path / "logs")
    assert path is not None
    assert path.parent == tmp_path / "logs"

    logger.error("boom {} happened", "x")
    logger.complete()

    text = path.read_text(encoding="utf-8")
    assert "ERROR" in text
    assert "boom x happened" in text


def test_configure_logging_is_idempotent(tmp_path: Path) -> None:
    path = configure_logging(tmp_path / "logs")
    assert path is not None
    configure_logging(tmp_path / "logs")

    logger.error("only one line")
    logger.complete()

    text = path.read_text(encoding="utf-8")
    assert text.count("only one line") == 1


def test_configure_logging_unwritable_returns_none(tmp_path: Path) -> None:
    # A *file* where the log directory's parent should be makes mkdir fail.
    blocker = tmp_path / "blockfile"
    blocker.write_text("not a dir", encoding="utf-8")
    assert configure_logging(blocker / "logs") is None
    logger.complete()


def test_excepthook_logs_unhandled_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = configure_logging(tmp_path / "logs")
    assert path is not None

    def _noop_hook(
        exc_type: type[BaseException],
        exc: BaseException,
        tb: TracebackType | None,
    ) -> None:
        """Silence the default hook's stderr output for the duration of the test."""

    monkeypatch.setattr(sys, "excepthook", _noop_hook)
    install_excepthook()

    try:
        raise RuntimeError("kaboom")
    except RuntimeError:
        exc_type, exc, tb = sys.exc_info()
    assert exc_type is not None and exc is not None and tb is not None
    sys.excepthook(exc_type, exc, tb)
    logger.complete()

    text = path.read_text(encoding="utf-8")
    assert "unhandled exception" in text
    assert "RuntimeError: kaboom" in text


def test_emit_logs_failed_action(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = configure_logging(tmp_path / "logs")
    assert path is not None

    from llamagui.cli import build_env, emit
    from llamagui.schemas import ExitCode

    env = build_env(
        "install",
        False,
        int(ExitCode.NOT_AVAILABLE),
        error="no asset for this platform",
    )
    code = emit(env, use_json=True)
    logger.complete()

    assert code == int(ExitCode.NOT_AVAILABLE)
    assert "no asset" in capsys.readouterr().out  # envelope still on stdout
    text = path.read_text(encoding="utf-8")
    assert "failed" in text
    assert "no asset for this platform" in text
