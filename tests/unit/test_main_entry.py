"""The ``python -m app`` entry point: GUI vs CLI dispatch."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from app.__main__ import main


@pytest.fixture(autouse=True)
def _no_real_logging(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the entry point's logging setup out of the real data root."""

    def _skip_configure(*_args: object, **_kwargs: object) -> None:
        pass

    monkeypatch.setattr("app.__main__.configure_logging", _skip_configure)
    monkeypatch.setattr("app.__main__.install_excepthook", lambda: None)
    monkeypatch.setattr("app.__main__.default_root", lambda: Path("/nonexistent"))


def test_no_arguments_launch_the_gui() -> None:
    with patch("app.__main__.logger"), patch("app.gui.bootstrap.run") as run_gui:
        main([])

    run_gui.assert_called_once_with()


def test_arguments_go_to_the_cli() -> None:
    with (
        patch("app.__main__.logger"),
        patch("app.cli.main", return_value=0) as cli_main,
        pytest.raises(SystemExit) as excinfo,
    ):
        main(["status", "--json"])

    cli_main.assert_called_once_with(["status", "--json"])
    assert excinfo.value.code == 0


def test_the_cli_exit_code_is_kept() -> None:
    with (
        patch("app.__main__.logger"),
        patch("app.cli.main", return_value=7),
        pytest.raises(SystemExit) as excinfo,
    ):
        main(["status"])

    assert excinfo.value.code == 7


def test_an_argparse_exit_drains_the_log_before_raising() -> None:
    """argparse exits directly; the queue must survive the process."""
    with (
        patch("app.__main__.logger") as log,
        patch("app.cli.main", side_effect=SystemExit(2)),
        pytest.raises(SystemExit) as excinfo,
    ):
        main(["--nope"])

    assert excinfo.value.code == 2
    log.complete.assert_called_once()


def test_a_missing_argv_defaults_to_the_command_line() -> None:
    with (
        patch("app.__main__.logger"),
        patch.object(sys, "argv", ["llamagui", "status"]),
        patch("app.cli.main", return_value=0) as cli_main,
        pytest.raises(SystemExit) as excinfo,
    ):
        main()

    cli_main.assert_called_once_with(["status"])
    assert excinfo.value.code == 0
