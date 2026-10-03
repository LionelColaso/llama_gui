"""Import helper for the developer scripts under ``scripts/``.

``scripts/`` is not an importable package, so tests load the modules by file
path. Kept in one place so several test modules do not each carry their own
copy of the loader (which jscpd rightly flags as duplication).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"


def load_script(name: str) -> ModuleType:
    """Import ``scripts/<name>.py`` as a standalone module and return it."""
    path = SCRIPTS / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"_script_{name}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module
