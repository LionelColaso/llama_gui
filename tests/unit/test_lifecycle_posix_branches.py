"""The POSIX branch of lifecycle's pid helpers, exercised on any OS.

``_pid_exists`` / ``_terminate_pid`` / ``_spawn_kwargs`` are chosen at
import time from ``sys.platform``, so on Windows the POSIX definitions
can only be reached by importing the module again with the platform
flag flipped. The re-import runs the real module source (so coverage
attributes it to ``app/lifecycle.py``); the original module object is
restored afterwards, leaving the running app untouched. ``os.kill`` is
always faked here: on Windows a real ``os.kill`` terminates the target.
"""

from __future__ import annotations

import importlib
import os
import signal
import sys
from collections.abc import Iterator
from typing import Any

import pytest


@pytest.fixture
def posix_lifecycle(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    monkeypatch.setattr(sys, "platform", "linux")
    saved = sys.modules.pop("app.lifecycle", None)
    try:
        yield importlib.import_module("app.lifecycle")
    finally:
        if saved is not None:
            sys.modules["app.lifecycle"] = saved
        else:
            sys.modules.pop("app.lifecycle", None)


def test_posix_pid_exists_reports_every_kernel_answer(
    posix_lifecycle: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_kill(pid: int, sig: object) -> None:
        if pid == 4242:
            return
        if pid == 4343:
            raise PermissionError
        if pid == 4444:
            raise OSError("boom")
        raise ProcessLookupError

    monkeypatch.setattr(os, "kill", fake_kill)
    assert posix_lifecycle._pid_exists(4242) is True
    # PermissionError means the process exists but belongs to another user.
    assert posix_lifecycle._pid_exists(4343) is True
    assert posix_lifecycle._pid_exists(4444) is False
    assert posix_lifecycle._pid_exists(9999) is False


def test_posix_terminate_pid_signals_then_reports_failure(
    posix_lifecycle: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent: list[tuple[int, int]] = []

    def fake_kill(pid: int, sig: int) -> None:
        sent.append((pid, sig))
        if pid == 5555:
            raise OSError("boom")

    monkeypatch.setattr(os, "kill", fake_kill)
    # Windows' signal module has no SIGKILL; the POSIX helper
    # references it, so provide the standard value for the test.
    monkeypatch.setattr(signal, "SIGKILL", 9, raising=False)
    assert posix_lifecycle._terminate_pid(4242) is True
    assert sent[-1] == (4242, signal.SIGTERM)
    assert posix_lifecycle._terminate_pid(4242, force=True) is True
    assert sent[-1] == (4242, getattr(signal, "SIGKILL", 9))
    assert posix_lifecycle._terminate_pid(5555) is False


def test_posix_spawn_kwargs_detaches_via_new_session(
    posix_lifecycle: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(posix_lifecycle, "is_windows", lambda: False)
    assert posix_lifecycle._spawn_kwargs() == {"start_new_session": True}
