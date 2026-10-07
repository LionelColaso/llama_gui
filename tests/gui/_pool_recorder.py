"""The WorkerPool stand-in shared by the GUI tests.

Every GUI test that triggers a worker action needs the same trick:
swap ``WorkerPool`` for a recorder so the test can assert on what
was started without a real thread pool. One implementation, patched
into whichever module under test looks it up.
"""

from __future__ import annotations

from typing import Any, ClassVar

import pytest


class PoolRecorder:
    """WorkerPool stand-in that records the workers it is asked to run."""

    started: ClassVar[list[Any]] = []

    @classmethod
    def instance(cls) -> Any:
        return cls

    @classmethod
    def start(cls, worker: Any) -> None:
        cls.started.append(worker)


def record_workers(monkeypatch: pytest.MonkeyPatch, *targets: str) -> list[Any]:
    """Patch ``WorkerPool`` in ``targets`` and return the shared log."""
    PoolRecorder.started = []
    for target in targets:
        monkeypatch.setattr(target, PoolRecorder)
    return PoolRecorder.started
