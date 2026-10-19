"""
Score a real (or injected) summary for structure and fact retention.

Checks prompt.txt headings, the fixture's gold facts, the verbatim recent
anchor, and the 500 / 3000 character budget.

Usage:
  python -m bench.quality
  python -m bench.quality --shape chat --band over_trigger

Needs DASH_COMPACT_SUMMARIZE_BASE/KEY (or COMPANY_LLM_BASE/KEY). One
over-trigger chat fixture is several summarizer calls.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from typing import Any, Mapping, Optional, Sequence

from bench.fixtures import GOLD, build_fixture
from engine import (
    SUMMARY_MARKER,
    CompactEngine,
    make_http_summarizer,
)

HEADINGS = (
    "Active Goal",
    "Previous Goals",
    "Timeline",
    "Current State",
    "Key Details",
    "Errors and Fixes",
    "Next Step",
)

# Facts that must survive inside the summary. The recent anchor is checked
# on the verbatim tail instead.
SUMMARY_FACTS = ("path", "function", "constraint", "error", "fix")

SECTION_CHAR_BUDGET = 500
TOTAL_CHAR_BUDGET = 3000


def _content_str(msg: Mapping[str, Any]) -> str:
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text") or ""))
            else:
                parts.append(str(block))
        return "\n".join(parts)
    return "" if content is None else str(content)


def split_sections(summary: str) -> dict[str, list[str]]:
    """Map each heading to the body text of every time it appears."""
    found: dict[str, list[str]] = {name: [] for name in HEADINGS}
    current: Optional[str] = None
    buf: list[str] = []

    def flush() -> None:
        if current is not None:
            found[current].append("\n".join(buf).strip())

    for line in summary.splitlines():
        stripped = line.strip()
        matched = None
        for name in HEADINGS:
            if stripped == f"## {name}":
                matched = name
                break
        if matched is not None:
            flush()
            current = matched
            buf = []
            continue
        if current is not None:
            buf.append(line)
    flush()
    return found


def anchor_kept(messages: Sequence[Mapping[str, Any]], anchor: str) -> bool:
    for msg in messages:
        if msg.get("role") != "user":
            continue
        text = _content_str(msg)
        if SUMMARY_MARKER in text:
            continue
        if anchor in text:
            return True
    return False


def score_summary(
    summary: str,
    messages: Sequence[Mapping[str, Any]],
    gold: Mapping[str, str] = GOLD,
) -> dict[str, Any]:
    sections = split_sections(summary)
    missing = [name for name in HEADINGS if not sections[name]]
    repeats = [name for name in HEADINGS if len(sections[name]) > 1]
    section_chars = {
        name: max((len(body) for body in sections[name]), default=0) for name in HEADINGS
    }
    over_budget = [
        name for name, n in section_chars.items() if sections[name] and n > SECTION_CHAR_BUDGET
    ]
    hits = {key: gold[key] in summary for key in SUMMARY_FACTS}
    hit_count = sum(1 for ok in hits.values() if ok)
    return {
        "headings_missing": missing,
        "headings_repeated": repeats,
        "section_chars": section_chars,
        "over_budget_sections": over_budget,
        "total_chars": len(summary),
        "within_total_budget": len(summary) <= TOTAL_CHAR_BUDGET,
        "entity_hits": hits,
        "entity_hit_count": hit_count,
        "entity_total": len(SUMMARY_FACTS),
        "entity_recall": hit_count / len(SUMMARY_FACTS),
        "anchor_kept": anchor_kept(messages, gold["recent_anchor"]),
    }


def evaluate(
    messages: list[dict[str, Any]],
    summarize,
    gold: Mapping[str, str] = GOLD,
) -> dict[str, Any]:
    engine = CompactEngine(summarize=summarize)
    t0 = time.perf_counter()
    result = engine.compress(messages)
    wall_s = time.perf_counter() - t0
    if result is None:
        raise RuntimeError("compress returned nothing")
    score = score_summary(result["summary"], result["messages"], gold)
    return {
        "pre": result["pre_tokens"],
        "post": result["post_tokens"],
        "wall_s": round(wall_s, 2),
        "summary": result["summary"],
        "score": score,
    }


def credentials_ready() -> bool:
    base = os.environ.get("DASH_COMPACT_SUMMARIZE_BASE") or os.environ.get("COMPANY_LLM_BASE")
    key = os.environ.get("DASH_COMPACT_SUMMARIZE_KEY") or os.environ.get("COMPANY_LLM_KEY")
    return bool(base and key)


def format_report(name: str, report: dict[str, Any]) -> str:
    score = report["score"]
    lines = [
        f"fixture {name}",
        f"tokens {report['pre']} -> {report['post']}  wall_s={report['wall_s']}",
        (
            f"headings {len(HEADINGS) - len(score['headings_missing'])}/{len(HEADINGS)}"
            f"  missing={score['headings_missing'] or '-'}"
            f"  repeated={score['headings_repeated'] or '-'}"
        ),
        (
            f"entity recall {score['entity_hit_count']}/{score['entity_total']}"
            f"  ({score['entity_recall']:.2f})"
        ),
    ]
    for key, ok in score["entity_hits"].items():
        lines.append(f"  {key}: {'hit' if ok else 'MISS'}  {GOLD[key]}")
    lines.append(f"anchor_kept: {score['anchor_kept']}")
    lines.append(
        f"chars total={score['total_chars']} budget={TOTAL_CHAR_BUDGET}"
        f"  within={score['within_total_budget']}"
    )
    for name_h, n in score["section_chars"].items():
        flag = " OVER" if name_h in score["over_budget_sections"] else ""
        lines.append(f"  {name_h}: {n}{flag}")
    lines.append("")
    lines.append(report["summary"])
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Score summary quality on one fixture")
    parser.add_argument("--shape", default="chat", choices=("chat", "tools", "images"))
    parser.add_argument(
        "--band",
        default="over_trigger",
        choices=("short", "over_trigger", "near_hard_limit"),
    )
    args = parser.parse_args(argv)
    if not credentials_ready():
        print(
            "Missing summarizer credentials: set DASH_COMPACT_SUMMARIZE_BASE/KEY "
            "or COMPANY_LLM_BASE/KEY",
            file=sys.stderr,
        )
        return 2

    fixture = build_fixture(args.shape, args.band)
    report = evaluate(fixture["messages"], make_http_summarizer())
    print(format_report(fixture["name"], report))
    score = report["score"]
    if score["headings_missing"] or score["entity_recall"] < 1 or not score["anchor_kept"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
