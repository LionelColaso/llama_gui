from __future__ import annotations

import os
import sys
from collections.abc import Generator
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Extensions that count as source code.
SOURCE_EXTS = {".py", ".pyi"}
IGNORE_DIRS = {
    ".git",
    ".venv",
    "build",
    "dist",
    "vendor",
    "__pycache__",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
}


def _iter_source_files(root: Path) -> Generator[Path, None, None]:
    """Yield the project's own ``.py``/``.pyi`` files, skipping vendor trees.

    This walks with os.walk and prunes in place. An earlier version used
    ``rglob('*')`` and tried to skip directories with an ``is_dir()`` branch,
    which does nothing: rglob has already descended into the virtualenv by the
    time the files are seen. So every run walked the whole of ``.venv`` (tens of
    thousands of files) just to count a few dozen source files.
    """
    for dirpath, dirnames, filenames in os.walk(root):
        # Prune in place so the excluded trees are never descended into.
        dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS]
        for name in filenames:
            if Path(name).suffix in SOURCE_EXTS:
                yield Path(dirpath) / name


def main() -> int:
    files = list(_iter_source_files(ROOT))
    loc = 0
    test_files = 0
    test_loc = 0

    for path in files:
        try:
            n = sum(1 for _ in path.open("r", encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            continue
        loc += n
        rel = path.relative_to(ROOT)
        if "tests" in rel.parts:
            test_files += 1
            test_loc += n

    print(f"Project: {ROOT}")
    print(f"Python files: {len(files)}")
    print(f"Total LOC: {loc}")
    print(f"Test files: {test_files}")
    print(f"Test LOC: {test_loc}")

    # Quick pytest count is optional — running it adds time, so only count files.
    return 0


if __name__ == "__main__":
    sys.exit(main())
