"""Lifecycle launch/stop semantics on POSIX: new session, SIGTERM then SIGKILL."""

from __future__ import annotations

import contextlib
import signal
import sys
from pathlib import Path

import pytest

from llamagui import lifecycle

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX session/kill semantics"
)


def test_launch_uses_new_session(tmp_path: Path) -> None:
    import os
    import subprocess

    proc = subprocess.Popen(
        [sys.executable, "-c", "import os,time; time.sleep(30)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        # start_new_session makes the child its own session leader.
        assert os.getsid(proc.pid) == proc.pid  # type: ignore[attr-defined]
    finally:
        os.killpg(proc.pid, signal.SIGKILL)  # type: ignore[attr-defined]
        proc.wait(timeout=5)


def test_terminate_then_kill_via_process_group(tmp_path: Path) -> None:
    import os
    import subprocess

    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    sid = proc.pid
    try:
        os.killpg(sid, signal.SIGTERM)  # type: ignore[attr-defined]
        # Give it a grace period, then guarantee death like lifecycle does.
        try:
            proc.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            os.killpg(sid, signal.SIGKILL)  # type: ignore[attr-defined]
            proc.wait(timeout=5)
        assert proc.returncode is not None
        assert not lifecycle._pid_exists(proc.pid)
    finally:
        if proc.poll() is None:
            with contextlib.suppress(Exception):
                os.killpg(sid, signal.SIGKILL)  # type: ignore[attr-defined]


def test_stop_processes_only_kills_recorded_pids(tmp_path: Path) -> None:
    import subprocess

    survivor = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    target = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    from llamagui.lifecycle import _write_pids

    try:
        _write_pids(tmp_path, {"llama_swap": target.pid, "servers": {}})
        result = lifecycle.stop_processes(tmp_path)
        assert target.pid in result["stopped_pids"]
        # The survivor (not recorded) is left untouched.
        assert survivor.poll() is None
    finally:
        for p in (survivor, target):
            if p.poll() is None:
                p.kill()
