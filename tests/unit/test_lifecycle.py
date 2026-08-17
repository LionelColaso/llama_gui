from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from llamagui.lifecycle import (
    _pid_exists,
    _read_pids,
    _write_pids,
    build_llama_server_args,
    launch_llama_server,
    read_log_tail,
    stop_processes,
    verify_launch,
    wait_for_port,
)


def test_pid_exists_current() -> None:
    assert _pid_exists(os.getpid()) is True


def test_pid_exists_nonexistent() -> None:
    assert _pid_exists(999999999) is False


def test_read_pids_empty(fake_root: Path) -> None:
    pids = _read_pids(fake_root)
    assert pids["llama_server"] is None
    assert pids["servers"] == {}


def test_read_pids_present(fake_root: Path) -> None:
    data = {"llama_server": 1234, "servers": {"main": 5678}}
    _write_pids(fake_root, data)
    loaded = _read_pids(fake_root)
    assert loaded == data


def test_wait_for_port_open(port_server: int) -> None:
    assert wait_for_port("127.0.0.1", port_server, timeout=2.0) is True


def test_wait_for_port_closed() -> None:
    assert wait_for_port("127.0.0.1", 1, timeout=0.5) is False


def test_verify_launch_fails_for_nonexistent_pid() -> None:
    assert verify_launch(999999999, "127.0.0.1", 1, timeout=0.5) is False


def test_build_llama_server_args() -> None:
    args = build_llama_server_args(
        "llama-server.exe",
        r"C:\models\m.gguf",
        host="127.0.0.1",
        port=8080,
        ctx_size=4096,
        n_gpu_layers=999,
        extra_args="--threads 8",
    )
    assert args[0] == "llama-server.exe"
    assert args[1:3] == ["-m", r"C:\models\m.gguf"]
    assert args[args.index("--host") + 1] == "127.0.0.1"
    assert args[args.index("--port") + 1] == "8080"
    assert args[args.index("-c") + 1] == "4096"
    assert args[args.index("-ngl") + 1] == "999"
    assert args[-2:] == ["--threads", "8"]


def test_build_llama_server_args_auto_ctx() -> None:
    """ctx_size <= 0 ("auto") omits -c so llama.cpp uses the model default."""
    args = build_llama_server_args(
        "llama-server.exe",
        r"C:\models\m.gguf",
        ctx_size=-1,
    )
    assert "-c" not in args
    assert args[args.index("-ngl") + 1] == "999"


def test_launch_llama_server_with_nonexistent_exe(fake_root: Path) -> None:
    from llamagui.lifecycle import LifecycleError

    with pytest.raises(LifecycleError):
        launch_llama_server(
            build_llama_server_args(
                r"C:\nonexistent\llama-server-nonexistent.exe",
                str(fake_root / "models" / "m.gguf"),
            ),
            host="127.0.0.1",
            port=8080,
            root=fake_root,
        )


def test_read_log_tail_nonexistent(fake_root: Path) -> None:
    assert read_log_tail(fake_root) == []


def test_read_log_tail_falls_back_to_out_log(fake_root: Path) -> None:
    # Regression guard: read_log_tail must fall back to .out.log when no .err.log
    # exists (it used a truthy Path object, so the fallback never ran).
    state = fake_root / "state"
    (state / "llama-server.out.log").write_text(
        "line one\nline two\n", encoding="utf-8"
    )
    tail = read_log_tail(fake_root, name="llama-server", lines=10)
    assert tail == ["line one", "line two"]


def test_read_log_tail_prefers_err_over_out(fake_root: Path) -> None:
    state = fake_root / "state"
    (state / "llama-server.out.log").write_text("from stdout\n", encoding="utf-8")
    (state / "llama-server.err.log").write_text("from stderr\n", encoding="utf-8")
    tail = read_log_tail(fake_root, name="llama-server")
    assert tail == ["from stderr"]


def test_stop_processes_clean(fake_root: Path) -> None:
    result = stop_processes(fake_root)
    assert result["stopped_pids"] == []


def test_process_exited_after_kill(fake_root: Path) -> None:
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _write_pids(fake_root, {"llama_server": proc.pid, "servers": {}})
        result = stop_processes(fake_root)
        assert proc.pid in result["stopped_pids"]
        proc.wait(timeout=5)
        assert proc.returncode is not None
    finally:
        if proc.poll() is None:
            proc.kill()


def test_pidfile_cleared_after_stop(fake_root: Path) -> None:
    _write_pids(fake_root, {"llama_server": 9999, "servers": {}})
    stop_processes(fake_root)
    pids = _read_pids(fake_root)
    assert pids["llama_server"] is None
