from __future__ import annotations

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
    for path in root.rglob("*"):
        if path.is_dir():
            if path.name in IGNORE_DIRS:
                continue
            continue
        if path.suffix in SOURCE_EXTS:
            yield path


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
