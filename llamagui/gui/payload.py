from __future__ import annotations

from typing import Any, cast


def as_payload(data: Any) -> dict[str, Any]:
    """Normalize a worker result (pydantic model or already a dict) to a dict.

    Every ``EngineWorker`` finish/error handler did this inline with a
    ``model_dump()`` + ``cast`` dance; sharing it removes the duplicated block
    that tripped the project's zero-duplication check.
    """
    raw = data.model_dump() if hasattr(data, "model_dump") else data
    return cast("dict[str, Any]", raw) if isinstance(raw, dict) else {}
