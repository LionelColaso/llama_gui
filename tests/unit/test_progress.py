from __future__ import annotations

from llamagui.models import parse_progress_line


def test_parse_valid_line() -> None:
    ev = parse_progress_line("PROGRESS\tvulkan\t5000\t10000\tdownload")
    assert ev is not None
    assert ev.component == "vulkan"
    assert ev.bytes_done == 5000
    assert ev.bytes_total == 10000
    assert ev.phase == "download"


def test_parse_invalid_line() -> None:
    assert parse_progress_line("something else") is None
    assert parse_progress_line("PROGRESS bad") is None


def test_parse_missing_fields() -> None:
    assert parse_progress_line("PROGRESS\tvulkan\t100\t") is None


def test_parse_edge_cases() -> None:
    ev = parse_progress_line("PROGRESS\tllama-swap\t0\t0\textract")
    assert ev is not None
    assert ev.component == "llama-swap"
    assert ev.bytes_done == 0
    assert ev.bytes_total == 0
