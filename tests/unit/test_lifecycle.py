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
    running_pids,
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


def _write_raw_pids(fake_root: Path, text: str) -> None:
    path = fake_root / "state" / "pids.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_read_pids_rejects_non_object_json(fake_root: Path) -> None:
    """A hand-edited pids.json holding a list must not crash the engine.

    Regression: json.loads happily returns a list, the old code passed it
    through, and running_pids()/stop_processes() then died with
    ``AttributeError: 'list' object has no attribute 'get'``.
    """
    _write_raw_pids(fake_root, "[1, 2]")
    assert _read_pids(fake_root) == {"llama_server": None, "servers": {}}


@pytest.mark.parametrize(
    "payload",
    ['"a string"', "42", "null", "[1, 2]", "true"],
)
def test_read_pids_survives_any_non_object(fake_root: Path, payload: str) -> None:
    _write_raw_pids(fake_root, payload)
    pids = _read_pids(fake_root)
    assert pids["llama_server"] is None
    assert pids["servers"] == {}


def test_read_pids_survives_truncated_json(fake_root: Path) -> None:
    _write_raw_pids(fake_root, '{"llama_server": 42')
    assert _read_pids(fake_root) == {"llama_server": None, "servers": {}}


def test_read_pids_normalises_servers_type(fake_root: Path) -> None:
    """``servers`` must be a dict; a list would break the ``.values()`` call."""
    _write_raw_pids(fake_root, '{"llama_server": 5, "servers": [1, 2]}')
    pids = _read_pids(fake_root)
    assert pids["llama_server"] == 5
    assert pids["servers"] == {}


def test_running_pids_tolerates_malformed_file(fake_root: Path) -> None:
    _write_raw_pids(fake_root, "[1, 2]")
    assert running_pids(fake_root) == []


def test_stop_processes_tolerates_malformed_file(fake_root: Path) -> None:
    _write_raw_pids(fake_root, "[1, 2]")
    result = stop_processes(fake_root)
    assert result["stopped_pids"] == []


def test_status_tolerates_malformed_pids_file(tmp_path: Path) -> None:
    """The dashboard read path must survive a damaged pid file."""
    from llamagui.config import AppConfig
    from llamagui.orchestrator import Orchestrator

    root = tmp_path / "llamagui"
    (root / "state").mkdir(parents=True)
    (root / "state" / "pids.json").write_text("[1, 2]", encoding="utf-8")
    status = Orchestrator(AppConfig(root=str(root))).status()
    assert status.server.pids == []


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
