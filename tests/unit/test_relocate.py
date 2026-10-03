"""The relocation primitives: scanning a tree and moving it without data loss."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.relocate import contains, copy, is_empty_dir, is_link, relocate, scan
from app.schemas import EngineError


def _write(path: Path, size: int = 4) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    return path


def _tree(tmp_path: Path) -> Path:
    """A private directory, since ``tmp_path`` also holds the test's log sink."""
    root = tmp_path / "tree"
    root.mkdir()
    return root


class _Ticker:
    """Collect the progress ticks a move reports."""

    def __init__(self) -> None:
        self.ticks: list[tuple[int, int, str, float | None]] = []

    def __call__(
        self, done: int, total: int, phase: str, overall: float | None
    ) -> None:
        self.ticks.append((done, total, phase, overall))


# ─── scan ──────────────────────────────────────────────────────────────────


def test_scan_counts_files_and_bytes(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    _write(root / "a.bin", 10)
    _write(root / "sub" / "b.bin", 5)

    assert scan(root) == (2, 15)


def test_scan_filters_by_suffix(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    _write(root / "model.gguf", 8)
    _write(root / "notes.txt", 100)
    _write(root / "model.gguf.part", 3)

    assert scan(root, suffixes=(".gguf",)) == (1, 8)


def test_scan_of_a_missing_directory_is_empty(tmp_path: Path) -> None:
    assert scan(tmp_path / "absent") == (0, 0)


def test_scan_counts_a_dotfile_as_a_file(tmp_path: Path) -> None:
    """.version markers have no suffix and must still travel with a backend."""
    root = _tree(tmp_path)
    _write(root / ".version", 6)

    assert scan(root) == (1, 6)


def test_scan_never_descends_into_a_directory_link(tmp_path: Path) -> None:
    """``managed/current`` points at a backend: counting it twice would lie."""
    backend = _write(tmp_path / "managed" / "vulkan" / "llama-server.exe", 40)
    link = tmp_path / "managed" / "current"
    try:
        link.symlink_to(backend.parent, target_is_directory=True)
    except (OSError, NotImplementedError):  # pragma: no cover - Windows w/o Dev Mode
        pytest.skip("directory symlinks unavailable")

    assert is_link(link)
    assert scan(tmp_path / "managed") == (1, 40)


# ─── relocate ──────────────────────────────────────────────────────────────


def test_relocate_moves_a_whole_tree_and_drops_the_source(tmp_path: Path) -> None:
    source = tmp_path / "old"
    _write(source / "a.bin", 3)
    _write(source / "sub" / "b.bin", 4)
    destination = tmp_path / "new" / "tree"

    assert relocate(source, destination) == (2, 7)

    assert (destination / "a.bin").read_bytes() == b"xxx"
    assert (destination / "sub" / "b.bin").read_bytes() == b"xxxx"
    assert not source.exists()


def test_relocate_keeps_the_source_directory_for_a_filtered_move(
    tmp_path: Path,
) -> None:
    """The user's models directory survives, with whatever else was in it."""
    source = tmp_path / "models"
    _write(source / "a.gguf", 5)
    _write(source / "a.gguf.part", 2)
    destination = tmp_path / "new-models"

    assert relocate(source, destination, suffixes=(".gguf",)) == (1, 5)

    assert (destination / "a.gguf").read_bytes() == b"xxxxx"
    assert source.is_dir()
    assert (source / "a.gguf.part").is_file()


def test_relocate_prunes_only_the_directories_it_emptied(tmp_path: Path) -> None:
    source = tmp_path / "models"
    _write(source / "nested" / "deep" / "a.gguf", 1)
    _write(source / "nested" / "keep.txt", 1)

    relocate(source, tmp_path / "new", suffixes=(".gguf",))

    assert not (source / "nested" / "deep").exists()
    assert (source / "nested" / "keep.txt").is_file()


def test_relocate_refuses_a_destination_that_has_files(tmp_path: Path) -> None:
    """The one case where a move could destroy data, so it must be refused."""
    source = tmp_path / "old"
    _write(source / "a.bin", 1)
    destination = tmp_path / "new"
    _write(destination / "existing.bin", 99)

    with pytest.raises(EngineError, match="already contains files"):
        relocate(source, destination)

    # Both sides are untouched.
    assert (source / "a.bin").is_file()
    assert (destination / "existing.bin").read_bytes() == b"x" * 99


def test_relocate_accepts_an_empty_destination(tmp_path: Path) -> None:
    source = tmp_path / "old"
    _write(source / "a.bin", 1)
    destination = tmp_path / "new"
    destination.mkdir()

    relocate(source, destination)

    assert (destination / "a.bin").is_file()


def test_relocate_of_an_empty_source_does_nothing(tmp_path: Path) -> None:
    source = tmp_path / "old"
    source.mkdir()

    assert relocate(source, tmp_path / "new") == (0, 0)

    assert not (tmp_path / "new").exists()


def test_relocate_reports_progress_per_file(tmp_path: Path) -> None:
    """A filtered move always copies file by file, so it reports per file."""
    source = _tree(tmp_path) / "models"
    _write(source / "a.gguf", 10)
    _write(source / "b.gguf", 10)

    ticks = _Ticker()
    relocate(source, tmp_path / "new", suffixes=(".gguf",), emit=ticks)

    assert [tick[0] for tick in ticks.ticks] == [10, 20]
    assert all(tick[1] == 20 and tick[2] == "move" for tick in ticks.ticks)
    assert ticks.ticks[-1][3] == 1.0


def test_relocate_reports_one_completed_tick_for_the_rename_fast_path(
    tmp_path: Path,
) -> None:
    """A whole tree on the same filesystem is one rename, not a byte-by-byte copy."""
    source = _tree(tmp_path) / "old"
    _write(source / "a.bin", 10)

    ticks = _Ticker()
    relocate(source, tmp_path / "new", emit=ticks)

    assert ticks.ticks == [(10, 10, "move", 1.0)]


def test_relocate_skips_a_linked_directory_when_copying(tmp_path: Path) -> None:
    """A per-file move (different device) must not copy the link's target too."""
    source = tmp_path / "old"
    _write(source / "vulkan" / "llama-server.exe", 40)
    link = source / "current"
    try:
        link.symlink_to(source / "vulkan", target_is_directory=True)
    except (OSError, NotImplementedError):  # pragma: no cover - Windows w/o Dev Mode
        pytest.skip("directory symlinks unavailable")

    relocate(source, tmp_path / "new", suffixes=(".exe",))

    assert scan(tmp_path / "new", suffixes=(".exe",)) == (1, 40)


# ─── copy ──────────────────────────────────────────────────────────────────


def test_copy_leaves_the_source_untouched(tmp_path: Path) -> None:
    source = _tree(tmp_path) / "old"
    _write(source / "a.bin", 3)
    _write(source / "sub" / "b.bin", 4)

    assert copy(source, tmp_path / "new") == (2, 7)

    assert (tmp_path / "new" / "a.bin").read_bytes() == b"xxx"
    assert (source / "a.bin").read_bytes() == b"xxx"
    assert (source / "sub" / "b.bin").is_file()


def test_copy_reports_the_copy_phase(tmp_path: Path) -> None:
    source = _tree(tmp_path) / "old"
    _write(source / "a.bin", 5)

    ticks = _Ticker()
    copy(source, tmp_path / "new", emit=ticks)

    assert ticks.ticks == [(5, 5, "copy", 1.0)]


def test_copy_refuses_a_destination_that_has_files(tmp_path: Path) -> None:
    source = _tree(tmp_path) / "old"
    _write(source / "a.bin", 1)
    destination = tmp_path / "new"
    _write(destination / "existing.bin", 2)

    with pytest.raises(EngineError, match="already contains files"):
        copy(source, destination)

    assert (source / "a.bin").is_file()


def test_copy_of_an_empty_source_does_nothing(tmp_path: Path) -> None:
    source = _tree(tmp_path) / "old"
    source.mkdir()

    assert copy(source, tmp_path / "new") == (0, 0)

    assert not (tmp_path / "new").exists()


def test_copy_never_follows_a_directory_link(tmp_path: Path) -> None:
    source = _tree(tmp_path) / "managed"
    _write(source / "vulkan" / "llama-server.exe", 40)
    try:
        (source / "current").symlink_to(source / "vulkan", target_is_directory=True)
    except (OSError, NotImplementedError):  # pragma: no cover - Windows w/o Dev Mode
        pytest.skip("directory symlinks unavailable")

    copy(source, tmp_path / "new")

    assert scan(tmp_path / "new") == (1, 40)
    assert not (tmp_path / "new" / "current").exists()


# ─── helpers ───────────────────────────────────────────────────────────────


def test_contains_covers_the_directory_itself_and_its_children(
    tmp_path: Path,
) -> None:
    assert contains(tmp_path / "a", tmp_path / "a")
    assert contains(tmp_path / "a", tmp_path / "a" / "b" / "c")
    assert not contains(tmp_path / "a", tmp_path / "ab")
    assert not contains(tmp_path / "a", tmp_path)


def test_is_empty_dir_treats_a_missing_path_as_empty(tmp_path: Path) -> None:
    assert is_empty_dir(tmp_path / "absent")
    (tmp_path / "there").mkdir()
    assert is_empty_dir(tmp_path / "there")
    _write(tmp_path / "there" / "f")
    assert not is_empty_dir(tmp_path / "there")
