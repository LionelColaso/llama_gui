from __future__ import annotations

from datetime import UTC, datetime

from llamagui.schemas import (
    Envelope,
    ExitCode,
    contract_version,
)


def test_contract_version_is_string() -> None:
    assert isinstance(contract_version, str)
    assert contract_version == "1"


def test_exit_code_values() -> None:
    assert int(ExitCode.SUCCESS) == 0
    assert int(ExitCode.UNEXPECTED_ERROR) == 1
    assert int(ExitCode.NOT_AVAILABLE) == 2
    assert int(ExitCode.NETWORK_ERROR) == 3
    assert int(ExitCode.LOCK_CONFLICT) == 4
    assert int(ExitCode.BAD_ARGUMENT) == 5
    assert int(ExitCode.CONTRACT_MISMATCH) == 6
    assert int(ExitCode.TOOLCHAIN_MISSING) == 7


def test_envelope_defaults() -> None:
    env = Envelope(
        contract_version=contract_version,
        ok=True,
        exit_code=0,
        action="status",
        root="D:\\test",
        timestamp=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        duration_ms=100,
        data={},
    )
    assert env.ok is True
    assert env.exit_code == 0
    assert env.error is None
    assert env.warnings == []


def test_envelope_json_roundtrip() -> None:
    env = Envelope(
        contract_version=contract_version,
        ok=False,
        exit_code=ExitCode.BAD_ARGUMENT,
        action="badcmd",
        root="D:\\test",
        timestamp="2026-07-31T12:00:00Z",
        duration_ms=0,
        data={},
        error="Unknown action: badcmd",
        log_tail=["line1"],
        warnings=["deprecated"],
    )
    json_str = env.model_dump_json()
    parsed = Envelope.model_validate_json(json_str)
    assert parsed.contract_version == contract_version
    assert parsed.exit_code == ExitCode.BAD_ARGUMENT
    assert parsed.error == "Unknown action: badcmd"


def test_envelope_data_status() -> None:
    from llamagui.schemas import BackendStatusData, StatusData

    sd = StatusData(
        backends={
            "vulkan": BackendStatusData(
                installed=True, version="b10189", source="managed-prebuilt"
            )
        },
        active="vulkan",
        junction_target="vulkan",
        config_present=True,
    )
    env = Envelope(
        contract_version=contract_version,
        ok=True,
        exit_code=0,
        action="status",
        root="D:\\test",
        timestamp="2026-07-31T12:00:00Z",
        duration_ms=50,
        data=sd.model_dump(),
    )
    assert env.data["active"] == "vulkan"
    assert env.data["backends"]["vulkan"]["installed"] is True
