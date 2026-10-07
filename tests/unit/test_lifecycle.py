from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

import pytest

from app.config import AppConfig
from app.lifecycle import (
    _TERM_GRACE_SECONDS,
    _pid_exists,
    _read_pids,
    _terminate_pid,
    _write_pids,
    build_llama_server_args,
    dedicated_value_int,
    dedicated_value_text,
    launch_llama_server,
    launch_settings,
    model_server_options,
    read_log_tail,
    running_pids,
    stop_processes,
    uses_global_server_config,
    verify_launch,
    wait_for_port,
)


def test_pid_exists_current() -> None:
    assert _pid_exists(os.getpid()) is True


def test_pid_exists_nonexistent() -> None:
    assert _pid_exists(999999999) is False


def test_terminate_pid_returns_false_for_unknown_pid() -> None:
    assert _terminate_pid(999999999) is False


def test_model_server_options_returns_the_own_entry() -> None:
    cfg = AppConfig(
        root=str(Path("C:/tmp/root")),
        server_options={"--flash-attn": "on"},
        model_server_options={"big.gguf": {"--jinja": "on"}},
    )
    assert model_server_options(cfg, "big.gguf") == {"--jinja": "on"}
    assert model_server_options(cfg, "small.gguf") == {"--flash-attn": "on"}


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
    from app.config import AppConfig
    from app.orchestrator import Orchestrator

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


def test_verify_launch_succeeds_when_pid_alive_and_port_up(
    port_server: int,
) -> None:
    assert verify_launch(os.getpid(), "127.0.0.1", port_server, timeout=2.0) is True


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
    from app.lifecycle import LifecycleError

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


class _FakePopen:
    """Stands in for ``subprocess.Popen``: a pid, no real process."""

    pid = 4321


def _fake_popen(cmd: object, **kwargs: object) -> _FakePopen:
    return _FakePopen()


def test_launch_with_root_records_the_pid(
    fake_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(subprocess, "Popen", _fake_popen)
    pid = launch_llama_server(
        ["llama-server", "-m", "m.gguf"],
        host="127.0.0.1",
        port=8080,
        root=fake_root,
    )
    assert pid == 4321
    assert _read_pids(fake_root) == {"llama_server": 4321, "servers": {}}


def test_launch_with_verify_returns_none_when_port_never_comes_up(
    fake_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app import lifecycle

    monkeypatch.setattr(subprocess, "Popen", _fake_popen)

    def _never_up(_pid: int, _host: str, _port: int) -> bool:
        return False

    monkeypatch.setattr(lifecycle, "verify_launch", _never_up)
    assert (
        launch_llama_server(
            ["llama-server"], "127.0.0.1", 8080, root=fake_root, verify=True
        )
        is None
    )


def test_launch_without_root_captures_logs_in_a_temp_dir(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CLI mode (no managed root) still captures the server's logs."""
    from app import lifecycle

    monkeypatch.setattr(subprocess, "Popen", _fake_popen)
    try:
        assert launch_llama_server(["llama-server"], "127.0.0.1", 8080) == 4321
        log_dir = getattr(lifecycle._SERVER_LOG_DIR, "dir", None)
        assert isinstance(log_dir, Path)
        assert (log_dir / "llama-server.out.log").exists()
        assert (log_dir / "llama-server.err.log").exists()
    finally:
        log_dir = getattr(lifecycle._SERVER_LOG_DIR, "dir", None)
        if log_dir is not None:
            delattr(lifecycle._SERVER_LOG_DIR, "dir")
            shutil.rmtree(log_dir, ignore_errors=True)


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


def test_read_log_tail_accepts_an_explicit_state_dir(
    fake_root: Path,
) -> None:
    state = fake_root / "state"
    (state / "llama-server.out.log").write_text("explicit\n", encoding="utf-8")
    tail = read_log_tail(None, state_dir=state, name="llama-server")
    assert tail == ["explicit"]


def test_read_log_tail_reads_the_captured_cli_logs(
    tmp_path: Path,
) -> None:
    """Logs captured in the temp dir (root=None) are readable afterwards."""
    from app import lifecycle

    lifecycle._SERVER_LOG_DIR.dir = tmp_path
    try:
        (tmp_path / "llama-server.err.log").write_text("cli boom\n", encoding="utf-8")
        assert read_log_tail(None) == ["cli boom"]
    finally:
        delattr(lifecycle._SERVER_LOG_DIR, "dir")


def test_read_log_tail_without_any_state_returns_nothing() -> None:
    from app import lifecycle

    if hasattr(lifecycle._SERVER_LOG_DIR, "dir"):
        delattr(lifecycle._SERVER_LOG_DIR, "dir")
    assert read_log_tail(None) == []


def test_read_log_tail_survives_an_unreadable_log(fake_root: Path) -> None:
    state = fake_root / "state"
    # A directory where the log file should be makes read_text
    # raise OSError; the tail must come back empty, not crash.
    (state / "llama-server.err.log").mkdir()
    assert read_log_tail(fake_root) == []


def test_stop_processes_clean(fake_root: Path) -> None:
    result = stop_processes(fake_root)
    assert result["stopped_pids"] == []


def test_stop_processes_reports_an_unknown_port_holder(
    fake_root: Path, port_server: int
) -> None:
    """A live port with no surviving pid belongs to someone else.

    It must be reported, never killed (invariant #8).
    """
    _write_pids(fake_root, {"llama_server": 999999999, "servers": {}})
    result = stop_processes(fake_root, port=port_server)
    assert result["stopped_pids"] == []
    assert result["still_listening"] is True
    assert result["port_free"] is False
    assert result["unknown_holder"] is True


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


# ─── stop grace period bounds how long a shutdown can block ────────────────


def test_stop_with_short_grace_is_fast(fake_root: Path) -> None:
    """A GUI close must not wait out the full 5s grace on the event loop.

    The child ignores SIGTERM, so _stop_pid must escalate to a hard kill as
    soon as the (short) grace elapses rather than sleeping the default.
    """
    import time

    from app.lifecycle import _TERM_GRACE_SECONDS

    proc = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import signal,time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\ntime.sleep(30)",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _write_pids(fake_root, {"llama_server": proc.pid, "servers": {}})
        started = time.monotonic()
        result = stop_processes(fake_root, grace=0.2)
        elapsed = time.monotonic() - started

        assert proc.pid in result["stopped_pids"]
        assert elapsed < _TERM_GRACE_SECONDS, (
            f"stop took {elapsed:.2f}s, so the grace was not honoured"
        )
        assert elapsed < 3.0
    finally:
        if proc.poll() is None:
            proc.kill()


class _GraceRecorder:
    """Stands in for ``_stop_pid`` and records the grace it was handed."""

    def __init__(self) -> None:
        self.graces: list[float] = []

    def __call__(self, pid: int, grace: float = _TERM_GRACE_SECONDS) -> bool:
        self.graces.append(grace)
        return False


def _stop_grace_recorder() -> AbstractContextManager[_GraceRecorder]:
    """Patch ``_stop_pid`` so the grace it receives can be inspected."""
    from app import lifecycle

    recorder = _GraceRecorder()
    return patch.object(lifecycle, "_stop_pid", recorder)


def test_stop_processes_passes_the_grace_through(fake_root: Path) -> None:
    """A caller-supplied grace must reach every _stop_pid call."""
    _write_pids(fake_root, {"llama_server": 4242, "servers": {}})
    with _stop_grace_recorder() as recorder:
        stop_processes(fake_root, grace=0.25)
    assert recorder.graces == [0.25]


def test_stop_processes_keeps_the_default_grace(fake_root: Path) -> None:
    """The engine default must stay at the documented 5s for CLI use."""
    _write_pids(fake_root, {"llama_server": 4242, "servers": {}})
    with _stop_grace_recorder() as recorder:
        stop_processes(fake_root)
    assert recorder.graces == [_TERM_GRACE_SECONDS]


class _TerminateRecorder:
    """Stands in for ``_terminate_pid`` and records the force flag."""

    def __init__(self) -> None:
        self.forces: list[bool] = []

    def __call__(self, pid: int, force: bool = False) -> bool:
        self.forces.append(force)
        return True


def test_stop_pid_with_zero_grace_force_kills(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A zero grace skips the wait loop and escalates to the hard kill."""
    from app import lifecycle

    alive: list[bool] = [True, False]

    def fake_pid_exists(pid: int) -> bool:
        return alive.pop(0) if alive else False

    monkeypatch.setattr(lifecycle, "_pid_exists", fake_pid_exists)

    def _reap_noop(_pid: int) -> None:
        return None

    monkeypatch.setattr(lifecycle, "_reap", _reap_noop)
    recorder = _TerminateRecorder()
    monkeypatch.setattr(lifecycle, "_terminate_pid", recorder)
    assert lifecycle._stop_pid(4242, grace=0) is True
    assert recorder.forces == [False, True]


def test_stop_pid_force_kills_after_the_grace_elapses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A process that survives the whole grace period is force-killed."""
    from app import lifecycle

    ticks: list[float] = [100.0, 160.0]

    def fake_monotonic() -> float:
        return ticks.pop(0) if ticks else 160.0

    # The clock jumps past the deadline, so the wait loop never
    # runs: liveness is probed once up front and once at the end.
    alive: list[bool] = [True, False]

    def fake_pid_exists(pid: int) -> bool:
        return alive.pop(0) if alive else False

    monkeypatch.setattr(time, "monotonic", fake_monotonic)

    def _sleep_noop(_seconds: float) -> None:
        return None

    def _reap_noop(_pid: int) -> None:
        return None

    monkeypatch.setattr(time, "sleep", _sleep_noop)
    monkeypatch.setattr(lifecycle, "_pid_exists", fake_pid_exists)
    monkeypatch.setattr(lifecycle, "_reap", _reap_noop)
    recorder = _TerminateRecorder()
    monkeypatch.setattr(lifecycle, "_terminate_pid", recorder)
    assert lifecycle._stop_pid(4242, grace=0.2) is True
    assert recorder.forces == [False, True]


def test_reap_runs_waitpid_on_posix(monkeypatch: pytest.MonkeyPatch) -> None:
    from app import lifecycle

    reaped: list[tuple[int, int]] = []

    def fake_waitpid(pid: int, flags: int) -> tuple[int, int]:
        reaped.append((pid, flags))
        return (pid, 0)

    monkeypatch.setattr(lifecycle, "is_windows", lambda: False)
    monkeypatch.setattr(os, "waitpid", fake_waitpid, raising=False)
    monkeypatch.setattr(os, "WNOHANG", 1, raising=False)
    lifecycle._reap(4242)
    assert reaped == [(4242, 1)]


class TestLaunchSettings:
    """Global defaults for a model with no config of its own; its own otherwise."""

    @staticmethod
    def _cfg(**kwargs: object) -> AppConfig:
        return AppConfig(root=str(Path("C:/tmp/root")), **kwargs)  # type: ignore[arg-type]

    def test_no_model_uses_the_global_defaults(self) -> None:
        cfg = self._cfg(ctx_size=4096, server_options={"--flash-attn": "on"})
        settings = launch_settings(cfg, None)
        assert settings.ctx_size == 4096
        assert settings.options["--flash-attn"] == "on"

    def test_a_model_with_no_config_follows_the_globals(self) -> None:
        cfg = self._cfg(ctx_size=4096, server_options={"--flash-attn": "on"})
        settings = launch_settings(cfg, "big.gguf")
        assert settings.ctx_size == 4096
        assert settings.options["--flash-attn"] == "on"
        assert uses_global_server_config(cfg, "big.gguf") is True

    def test_an_own_config_wins_over_the_globals(self) -> None:
        cfg = self._cfg(
            ctx_size=4096,
            server_options={"--flash-attn": "on"},
            model_server_options={"big.gguf": {"--ctx-size": "16384"}},
        )
        settings = launch_settings(cfg, "big.gguf")
        assert settings.ctx_size == 16384
        assert uses_global_server_config(cfg, "big.gguf") is False

    def test_an_own_config_ignores_the_globals_entirely(self) -> None:
        """Its own values alone decide; a flag it omits is the binary's default."""
        cfg = self._cfg(
            ctx_size=4096,
            server_options={"--flash-attn": "on"},
            model_server_options={"big.gguf": {"--jinja": "on"}},
        )
        settings = launch_settings(cfg, "big.gguf")
        assert settings.ctx_size == -1, "no ctx of its own means 'let the model decide'"
        assert settings.options == {"--jinja": "on"}
        assert launch_settings(cfg, "small.gguf").options["--flash-attn"] == "on"

    def test_an_empty_own_config_is_still_an_own_config(self) -> None:
        """``{}`` means 'my own settings, all default' — not 'inherit'."""
        cfg = self._cfg(
            ctx_size=4096,
            server_options={"--flash-attn": "on"},
            model_server_options={"big.gguf": {}},
        )
        settings = launch_settings(cfg, "big.gguf")
        assert settings.options == {}
        assert settings.ctx_size == -1
        assert uses_global_server_config(cfg, "big.gguf") is False

    def test_an_own_config_can_turn_a_global_flag_off(self) -> None:
        """The model scope holds a value, not an on/off: "off" must win."""
        cfg = self._cfg(
            server_options={"--flash-attn": "on"},
            model_server_options={"big.gguf": {"--flash-attn": "off"}},
        )
        assert launch_settings(cfg, "big.gguf").options["--flash-attn"] == "off"
        # Another model still gets the global default.
        assert model_server_options(cfg, "small.gguf")["--flash-attn"] == "on"

    def test_dedicated_flags_are_never_emitted_twice(self) -> None:
        """-c comes from the dedicated field, so it must appear exactly once."""
        cfg = self._cfg(
            model_server_options={"big.gguf": {"--ctx-size": "16384"}},
        )
        settings = launch_settings(cfg, "big.gguf")
        cmd = build_llama_server_args(
            "llama-server",
            "big.gguf",
            ctx_size=settings.ctx_size,
            n_gpu_layers=settings.n_gpu_layers,
            server_options=settings.options,
        )
        assert cmd.count("-c") == 1
        assert cmd[cmd.index("-c") + 1] == "16384"

    def test_a_bad_own_value_falls_back_to_the_default(
        self,
    ) -> None:
        cfg = self._cfg(
            model_server_options={"big.gguf": {"--ctx-size": "huge"}},
        )
        assert launch_settings(cfg, "big.gguf").ctx_size == -1


class TestDedicatedValueText:
    @pytest.mark.parametrize(
        ("flag", "value", "text", "parsed"),
        [
            ("--ctx-size", 8192, "8192", 8192),
            ("--ctx-size", -1, "", -1),
            ("--n-gpu-layers", 33, "33", 33),
            # "all" and -1 both mean "as many as fit"; the editor says "all".
            ("--n-gpu-layers", 999, "all", -1),
            ("--n-gpu-layers", -1, "", -1),
        ],
    )
    def test_text_and_int_agree(
        self, flag: str, value: int, text: str, parsed: int
    ) -> None:
        assert dedicated_value_text(flag, value) == text
        assert dedicated_value_int(flag, text, -99) == parsed

    def test_auto_words_mean_auto(self) -> None:
        assert dedicated_value_int("--ctx-size", "auto", 4096) == -1
        assert dedicated_value_int("--n-gpu-layers", "all", 999) == -1

    def test_other_flags_pass_through(self) -> None:
        assert dedicated_value_text("--bogus", 5) == "5"


def test_launch_treats_raw_fd_handles_as_unclosable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Log handles may be raw file descriptors: nothing to close."""

    class _FakeProc:
        pid = 4242

    def _fake_popen(cmd: list[str], **kwargs: object) -> _FakeProc:
        return _FakeProc()

    monkeypatch.setattr("app.lifecycle.subprocess.Popen", _fake_popen)

    real_open = Path.open

    def _fd_open(self: Path, *args: Any, **kwargs: Any) -> object:
        if self.name.startswith("llama-server."):
            return 1  # a raw descriptor, not a file object
        return cast(object, real_open(self, *args, **kwargs))

    monkeypatch.setattr(Path, "open", _fd_open)

    pid = launch_llama_server(["fake-server"], "127.0.0.1", 8080, root=tmp_path)
    assert pid == 4242
