"""Model store: listing, URL name derivation, removal, and the listing cache."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from llamagui import model_store

# ─── cached model listing ──────────────────────────────────────────────────


def test_list_models_cached_reuses_the_scan(tmp_path: Path) -> None:
    """A dashboard poll must not rebuild ModelInfo rows every few seconds."""
    (tmp_path / "a.gguf").write_bytes(b"x" * 10)
    model_store.clear_model_cache()

    with patch.object(model_store, "list_models", wraps=model_store.list_models) as spy:
        model_store.list_models_cached(tmp_path)
        model_store.list_models_cached(tmp_path)
        model_store.list_models_cached(tmp_path)

    assert spy.call_count == 1, "an unchanged tree should be listed once"


def test_cached_listing_notices_a_new_model(tmp_path: Path) -> None:
    model_store.clear_model_cache()
    assert model_store.list_models_cached(tmp_path) == []

    (tmp_path / "new.gguf").write_bytes(b"y" * 4)
    assert [m.name for m in model_store.list_models_cached(tmp_path)] == ["new.gguf"]


def test_cached_listing_notices_a_deleted_model(tmp_path: Path) -> None:
    (tmp_path / "a.gguf").write_bytes(b"x" * 4)
    (tmp_path / "b.gguf").write_bytes(b"y" * 4)
    model_store.clear_model_cache()
    assert len(model_store.list_models_cached(tmp_path)) == 2

    (tmp_path / "a.gguf").unlink()
    assert [m.name for m in model_store.list_models_cached(tmp_path)] == ["b.gguf"]


def test_cached_listing_notices_a_resized_model(tmp_path: Path) -> None:
    """A completed download grows the file; the size must not go stale."""
    target = tmp_path / "a.gguf"
    target.write_bytes(b"x" * 4)
    model_store.clear_model_cache()
    assert model_store.list_models_cached(tmp_path)[0].size_bytes == 4

    target.write_bytes(b"x" * 4096)
    assert model_store.list_models_cached(tmp_path)[0].size_bytes == 4096


def test_cached_listing_is_not_mutable_by_the_caller(tmp_path: Path) -> None:
    """A caller mutating the returned list must not corrupt the cache."""
    (tmp_path / "a.gguf").write_bytes(b"x" * 4)
    model_store.clear_model_cache()

    first = model_store.list_models_cached(tmp_path)
    first.clear()
    second = model_store.list_models_cached(tmp_path)
    assert [m.name for m in second] == ["a.gguf"]


def test_cached_listing_of_a_missing_dir_is_empty(tmp_path: Path) -> None:
    assert model_store.list_models_cached(tmp_path / "nope") == []
