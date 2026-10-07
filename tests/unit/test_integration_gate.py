"""The integration suite's network gate (``tests/integration/conftest.py``).

The gate is what lets the default run include live-API tests: when the
GitHub API is unreachable every integration test must skip (not fail),
and when it is reachable the probe must pass through. The probe is
exercised against stubbed ``httpx.head`` responses so these tests stay
offline.
"""

from __future__ import annotations

from pathlib import Path
from types import ModuleType

import httpx
import pytest

from .script_loader import load_script

ROOT = Path(__file__).resolve().parents[2]
GATE_DIR = ROOT / "tests" / "integration"


@pytest.fixture(scope="module")
def gate() -> ModuleType:
    return load_script("conftest", root=GATE_DIR)


def _response(status: int) -> httpx.Response:
    return httpx.Response(status, request=httpx.Request("HEAD", "https://x"))


def test_probe_passes_when_the_api_is_reachable(
    gate: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple[str, float]] = []

    def fake_head(url: str, timeout: float) -> httpx.Response:
        seen.append((url, timeout))
        return _response(200)

    monkeypatch.setattr(httpx, "head", fake_head)
    gate.check_network()
    assert seen == [(gate.PROBE_URL, gate.PROBE_TIMEOUT_SECONDS)]


def test_probe_raises_when_the_api_is_unreachable(
    gate: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_head(url: str, timeout: float) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    monkeypatch.setattr(httpx, "head", fake_head)
    with pytest.raises(httpx.HTTPError):
        gate.check_network()


def test_probe_raises_on_http_error_status(
    gate: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rate limits (403/429) and outages (5xx) count as unreachable."""

    def fake_head(url: str, timeout: float) -> httpx.Response:
        return _response(503)

    monkeypatch.setattr(httpx, "head", fake_head)
    with pytest.raises(httpx.HTTPError):
        gate.check_network()


def test_gate_skips_every_test_when_offline(
    gate: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_head(url: str, timeout: float) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    monkeypatch.setattr(httpx, "head", fake_head)
    skipped: list[str] = []

    def record_skip(msg: str) -> None:
        skipped.append(msg)

    monkeypatch.setattr(gate.pytest, "skip", record_skip)
    gate.skip_if_offline()
    assert len(skipped) == 1
    assert gate.PROBE_URL in skipped[0]
    assert "integration tests skipped" in skipped[0]
