"""Free-layer ratio table: strip, micro, and a fake summarizer."""

from __future__ import annotations

from bench.fixtures import GOLD, build_fixture
from bench.free_layer import FAKE_SUMMARY, measure, rows_for_matrix
from engine import DEFAULT_TRIGGER


def test_short_sessions_skip_llm():
    for shape in ("chat", "tools", "images"):
        row = measure(build_fixture(shape, "short"))
        assert row["raw"] < DEFAULT_TRIGGER
        assert row["would_compact"] is False
        assert row["pre"] is None
        assert row["post"] is None


def test_chat_text_is_unchanged_by_free_layers():
    row = measure(build_fixture("chat", "over_trigger"))
    assert row["strip"] == row["raw"]
    assert row["micro"] == row["raw"]
    assert row["tools_cleared"] == 0
    assert row["would_compact"] is True
    assert row["post"] < row["pre"]
    assert row["anchor_kept"] is True


def test_large_images_drop_on_strip():
    row = measure(build_fixture("images", "over_trigger"))
    assert row["strip"] < row["raw"] * 0.5
    assert row["would_compact"] is True


def test_large_tools_drop_on_micro_after_strip():
    row = measure(build_fixture("tools", "near_hard_limit"))
    assert row["strip"] < row["raw"]
    assert row["micro"] < row["strip"]
    assert row["tools_cleared"] >= 1
    assert row["would_compact"] is True
    assert row["anchor_kept"] is True


def test_fake_summary_is_what_the_engine_asked_for():
    assert "Active Goal" in FAKE_SUMMARY
    assert "src/billing/invoice.py" in FAKE_SUMMARY


def test_matrix_rows_cover_nine_cells_and_repeat():
    first = rows_for_matrix()
    second = rows_for_matrix()
    assert len(first) == 9
    keys = ("name", "raw", "strip", "micro", "pre", "post", "would_compact")
    assert [{k: r[k] for k in keys} for r in first] == [
        {k: r[k] for k in keys} for r in second
    ]
    assert GOLD["recent_anchor"]
