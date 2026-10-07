"""Network gate for the integration suite.

Every test here hits the real GitHub API. Rather than being excluded
from the default run (and rotting untested), the suite probes the API
first: when it is unreachable each test skips with a message, so the
default run is safe offline and still exercises the live API whenever
a connection is available.
"""

from __future__ import annotations

import httpx
import pytest

#: The endpoint the suite depends on, and how long to wait for it.
PROBE_URL = "https://api.github.com"
PROBE_TIMEOUT_SECONDS = 5.0


def check_network(url: str = PROBE_URL, timeout: float = PROBE_TIMEOUT_SECONDS) -> None:
    """Raise :class:`httpx.HTTPError` when the endpoint is unreachable.

    Connection errors *and* error status codes (rate limits, outages)
    both count as unreachable, so the gate skips instead of failing.
    """
    response = httpx.head(url, timeout=timeout)
    response.raise_for_status()


def skip_if_offline() -> None:
    """Skip the calling test when the GitHub API is unreachable."""
    try:
        check_network()
    except httpx.HTTPError as exc:
        pytest.skip(
            f"no network access to {PROBE_URL} ({exc}) — integration tests skipped"
        )


@pytest.fixture(scope="session", autouse=True)
def _network_gate() -> None:
    """Skip every integration test when the GitHub API is unreachable."""
    skip_if_offline()
