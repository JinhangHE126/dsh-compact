"""Summary quality scoring: headings, gold facts, recent anchor."""

from __future__ import annotations

from bench.fixtures import GOLD, build_fixture
from bench.quality import HEADINGS, evaluate, format_report, score_summary
from engine import SUMMARY_MARKER


def _good_summary() -> str:
    return (
        "## Active Goal\n"
        f"- fix {GOLD['function']} in {GOLD['path']}\n"
        f"- constraint {GOLD['constraint']}\n"
        "## Previous Goals\n"
        "- none\n"
        "## Timeline\n"
        "1. opened the file\n"
        "## Current State\n"
        "- mid\n"
        "## Key Details\n"
        f"- {GOLD['path']} {GOLD['function']}\n"
        "## Errors and Fixes\n"
        f"- {GOLD['error']}: {GOLD['fix']}\n"
        "## Next Step\n"
        "- continue\n"
    )


def _tail() -> list[dict]:
    return [
        {"role": "system", "content": "You are a coding agent."},
        {
            "role": "user",
            "content": (
                f"{SUMMARY_MARKER}\n{_good_summary()}\n"
            ),
        },
        {"role": "assistant", "content": "已读取滚动摘要"},
        {"role": "user", "content": f"{GOLD['recent_anchor']} continue editing {GOLD['path']}"},
    ]


def test_complete_summary_scores_full_recall():
    score = score_summary(_good_summary(), _tail())
    assert score["headings_missing"] == []
    assert score["headings_repeated"] == []
    assert score["entity_recall"] == 1
    assert score["entity_hit_count"] == 5
    assert score["anchor_kept"] is True
    assert score["within_total_budget"] is True
    assert score["over_budget_sections"] == []


def test_missing_constraint_lowers_recall():
    text = _good_summary().replace(GOLD["constraint"], "some other rule")
    score = score_summary(text, _tail())
    assert score["entity_hits"]["constraint"] is False
    assert score["entity_hit_count"] == 4
    assert score["entity_hits"]["path"] is True


def test_missing_heading_and_repeat_and_budget():
    text = _good_summary().replace("## Next Step\n- continue\n", "")
    text += "\n## Active Goal\n- " + ("重" * 600) + ("字" * 3000)
    score = score_summary(text, _tail())
    assert "Next Step" in score["headings_missing"]
    assert "Active Goal" in score["headings_repeated"]
    assert "Active Goal" in score["over_budget_sections"]
    assert score["within_total_budget"] is False


def test_anchor_inside_summary_does_not_count():
    messages = [
        {
            "role": "user",
            "content": f"{SUMMARY_MARKER}\n{GOLD['recent_anchor']}\n",
        }
    ]
    score = score_summary(_good_summary(), messages)
    assert score["anchor_kept"] is False


def test_evaluate_with_injected_summarizer():
    def summarize(prompt: str) -> str:
        assert "待压缩对话" in prompt
        return _good_summary()

    fixture = build_fixture("chat", "over_trigger")
    report = evaluate(fixture["messages"], summarize)
    assert report["post"] < report["pre"]
    assert report["score"]["entity_recall"] == 1
    assert report["score"]["anchor_kept"] is True
    text = format_report(fixture["name"], report)
    assert "entity recall 5/5" in text
    assert HEADINGS[0] in text
