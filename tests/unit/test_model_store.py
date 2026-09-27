"""Model store: listing, URL name derivation, removal, and the listing cache."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from llamagui import model_store
from llamagui.model_store import (
    ModelDownloadError,
    list_models,
    model_name_from_url,
    remove_model,
)

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


# ─── list_models ──────────────────────────────────────────────────────────


def test_list_models_of_a_missing_dir_is_empty(tmp_path: Path) -> None:
    assert list_models(tmp_path / "nope") == []


def test_list_models_finds_top_level_files(tmp_path: Path) -> None:
    (tmp_path / "a.gguf").write_bytes(b"x" * 4)
    (tmp_path / "b.gguf").write_bytes(b"y" * 8)
    names = [m.name for m in list_models(tmp_path)]
    assert names == ["a.gguf", "b.gguf"]


def test_list_models_descends_into_subdirectories(tmp_path: Path) -> None:
    nested = tmp_path / "org" / "repo"
    nested.mkdir(parents=True)
    (nested / "model.gguf").write_bytes(b"z" * 4)
    assert [m.name for m in list_models(tmp_path)] == ["org/repo/model.gguf"]


def test_list_models_ignores_non_gguf_files(tmp_path: Path) -> None:
    (tmp_path / "a.gguf").write_bytes(b"x")
    (tmp_path / "notes.txt").write_text("hi", encoding="utf-8")
    (tmp_path / "a.gguf.part").write_bytes(b"x")
    assert [m.name for m in list_models(tmp_path)] == ["a.gguf"]


def test_list_models_skips_hidden_directories(tmp_path: Path) -> None:
    """Caches and temp dirs under .something are not part of the library."""
    hidden = tmp_path / ".cache" / "blobs"
    hidden.mkdir(parents=True)
    (hidden / "junk.gguf").write_bytes(b"x")
    (tmp_path / "real.gguf").write_bytes(b"x")
    assert [m.name for m in list_models(tmp_path)] == ["real.gguf"]


def test_list_models_sorts_case_insensitively(tmp_path: Path) -> None:
    for name in ("Zebra.gguf", "apple.gguf", "Mango.gguf"):
        (tmp_path / name).write_bytes(b"x")
    names = [m.name for m in list_models(tmp_path)]
    assert names == sorted(names, key=str.lower)
    assert names[0] == "apple.gguf"


def test_list_models_reports_size_and_mtime(tmp_path: Path) -> None:
    target = tmp_path / "a.gguf"
    target.write_bytes(b"x" * 1234)
    info = list_models(tmp_path)[0]
    assert info.size_bytes == 1234
    assert info.modified, "a model should carry a human-readable timestamp"


# ─── model_name_from_url ──────────────────────────────────────────────────


def test_name_from_a_huggingface_resolve_url() -> None:
    url = "https://huggingface.co/org/repo/resolve/main/model.gguf"
    assert model_name_from_url(url) == "model.gguf"


def test_name_strips_the_query_string() -> None:
    url = "https://huggingface.co/o/r/resolve/main/m.gguf?download=true"
    assert model_name_from_url(url) == "m.gguf"


def test_name_strips_the_fragment() -> None:
    assert model_name_from_url("https://x/y/m.gguf#section") == "m.gguf"


def test_name_keeps_a_dotted_asset_name() -> None:
    url = "https://huggingface.co/o/r/resolve/main/q4_k_m-1.5b.gguf"
    assert model_name_from_url(url) == "q4_k_m-1.5b.gguf"


def test_name_falls_back_when_the_url_has_no_asset() -> None:
    """A repo root or API URL has no file name; synthesise a stable one."""
    for url in ("https://huggingface.co/api/models/llama", "https://example.com/repo"):
        name = model_name_from_url(url)
        assert name.endswith(".gguf")
        assert name.startswith("model-"), name


def test_fallback_name_is_stable_and_hex_only() -> None:
    first = model_name_from_url("https://example.com/repo")
    assert first == model_name_from_url("https://example.com/repo")
    digest = first.removeprefix("model-").removesuffix(".gguf")
    assert digest, "the fallback should carry a short digest"
    assert all(c in "0123456789abcdef" for c in digest)


def test_name_never_escapes_the_models_directory() -> None:
    """The name becomes a filename, so a traversal attempt must not survive."""
    for url in (
        "https://x/..%2F..%2Fetc%2Fpasswd",
        "https://x/../../etc/passwd",
    ):
        name = model_name_from_url(url)
        assert "/" not in name, name
        assert ".." not in name, name


# ─── remove_model ─────────────────────────────────────────────────────────


def test_remove_model_deletes_the_file(tmp_path: Path) -> None:
    target = tmp_path / "a.gguf"
    target.write_bytes(b"x")
    remove_model(tmp_path, "a.gguf")
    assert not target.exists()


def test_remove_model_accepts_a_nested_name(tmp_path: Path) -> None:
    nested = tmp_path / "org" / "m.gguf"
    nested.parent.mkdir(parents=True)
    nested.write_bytes(b"x")
    remove_model(tmp_path, "org/m.gguf")
    assert not nested.exists()


def test_remove_model_raises_for_a_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        remove_model(tmp_path, "absent.gguf")


def test_remove_model_rejects_path_traversal(tmp_path: Path) -> None:
    """Invariant: a name may never reach outside the models directory."""
    outside = tmp_path / "outside.gguf"
    outside.write_bytes(b"secret")
    models = tmp_path / "models"
    models.mkdir()

    for name in ("../outside.gguf", "org/../../outside.gguf"):
        with pytest.raises(ModelDownloadError):
            remove_model(models, name)

    assert outside.exists(), "a traversal attempt must not delete anything"


def test_remove_model_rejects_an_absolute_path_outside(tmp_path: Path) -> None:
    """An absolute path is resolved as-is, so one outside the models dir must
    be refused. (An absolute path *inside* it is legitimate and works.)"""
    models = tmp_path / "models"
    models.mkdir()
    outside = tmp_path / "outside.gguf"
    outside.write_bytes(b"secret")

    with pytest.raises(ModelDownloadError):
        remove_model(models, str(outside))

    assert outside.exists(), "an absolute path must not escape the models dir"


def test_remove_model_allows_an_absolute_path_inside(tmp_path: Path) -> None:
    """An absolute path that resolves inside the models dir is fine."""
    target = tmp_path / "a.gguf"
    target.write_bytes(b"x")
    remove_model(tmp_path, str(target))
    assert not target.exists()


def test_remove_model_rejects_a_non_gguf_file(tmp_path: Path) -> None:
    other = tmp_path / "notes.txt"
    other.write_text("keep me", encoding="utf-8")
    with pytest.raises(FileNotFoundError):
        remove_model(tmp_path, "notes.txt")
    assert other.exists(), "only .gguf files are managed by the model store"
