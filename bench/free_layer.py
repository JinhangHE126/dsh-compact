"""
Compression-ratio table for the fixture matrix. No network.

Stages, in proxy order:
  raw → strip_heavy → micro_compact → fake LLM compress (only if over trigger)

The fake summarizer returns a fixed short brief, so post_tokens measures the
cut (what stays verbatim) rather than model quality.

Usage:
  python -m bench.free_layer
  python -m bench.free_layer --csv
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from typing import Any, Optional

from bench.fixtures import GOLD, iter_fixtures
from engine import DEFAULT_TARGET, DEFAULT_TRIGGER, SUMMARY_MARKER, CompactEngine
from micro_compact import micro_compact
from strip_heavy import strip_heavy
from tokens import estimate_messages

FAKE_SUMMARY = (
    "## Active Goal\n- continue\n"
    "## Previous Goals\n- billing fix\n"
    "## Timeline\n1. saw the task\n"
    "## Current State\n- mid\n"
    "## Key Details\n- src/billing/invoice.py\n"
    "## Errors and Fixes\n- 无\n"
    "## Next Step\n- continue\n"
)


def fake_summarize(prompt: str) -> str:
    if not prompt:
        raise RuntimeError("empty summarize prompt")
    return FAKE_SUMMARY


def measure(fixture: dict[str, Any]) -> dict[str, Any]:
    """Run free layers and, when the production trigger fires, a fake compress."""
    messages = fixture["messages"]
    raw = estimate_messages(messages)
    stripped = strip_heavy(messages)
    after_strip = estimate_messages(stripped)
    micro = micro_compact(stripped, keep_recent=5)
    after_micro = estimate_messages(micro["messages"])

    engine = CompactEngine(summarize=fake_summarize)
    would = engine.should_compact(messages)
    row: dict[str, Any] = {
        "name": fixture["name"],
        "shape": fixture["shape"],
        "band": fixture["band"],
        "raw": raw,
        "strip": after_strip,
        "micro": after_micro,
        "tools_cleared": micro["tool_results_cleared"],
        "would_compact": would,
        "pre": None,
        "post": None,
        "keep_from": None,
        "wall_ms": None,
        "anchor_kept": None,
    }
    if not would:
        return row

    t0 = time.perf_counter()
    result = engine.compress(messages)
    wall_ms = (time.perf_counter() - t0) * 1000
    row["wall_ms"] = round(wall_ms, 2)
    if result is None:
        return row
    row["pre"] = result["pre_tokens"]
    row["post"] = result["post_tokens"]
    row["keep_from"] = result["keep_from"]
    blob = _flatten(result["messages"])
    row["anchor_kept"] = GOLD["recent_anchor"] in blob and SUMMARY_MARKER in blob
    return row


def _flatten(node: object) -> str:
    if isinstance(node, str):
        return node
    if isinstance(node, dict):
        return "\n".join(_flatten(v) for v in node.values())
    if isinstance(node, list):
        return "\n".join(_flatten(v) for v in node)
    return ""


def _ratio(num: Optional[int], den: Optional[int]) -> str:
    if num is None or den is None or den <= 0:
        return "-"
    return f"{num / den:.3f}"


def _cell(value: Optional[int]) -> str:
    if value is None:
        return "-"
    return str(value)


def rows_for_matrix() -> list[dict[str, Any]]:
    return [measure(fix) for fix in iter_fixtures()]


def format_table(rows: list[dict[str, Any]]) -> str:
    header = (
        f"{'name':<28} {'raw':>8} {'strip':>8} {'micro':>8} "
        f"{'free':>6} {'pre':>8} {'post':>8} {'llm':>6} {'end':>6} {'ms':>8}"
    )
    lines = [
        header,
        f"trigger={DEFAULT_TRIGGER} target={DEFAULT_TARGET} summarizer=fake",
    ]
    for row in rows:
        end_den = row["raw"]
        lines.append(
            f"{row['name']:<28} {row['raw']:>8} {row['strip']:>8} {row['micro']:>8} "
            f"{_ratio(row['micro'], row['raw']):>6} {_cell(row['pre']):>8} "
            f"{_cell(row['post']):>8} {_ratio(row['post'], row['pre']):>6} "
            f"{_ratio(row['post'], end_den):>6} {_cell(row['wall_ms']):>8}"
        )
    lines.append("free = micro/raw after strip+micro; llm = post/pre inside compress; end = post/raw")
    return "\n".join(lines)


def write_csv(rows: list[dict[str, Any]], out: Any) -> None:
    fields = [
        "name",
        "shape",
        "band",
        "raw",
        "strip",
        "micro",
        "tools_cleared",
        "would_compact",
        "pre",
        "post",
        "keep_from",
        "wall_ms",
        "anchor_kept",
        "free_ratio",
        "llm_ratio",
        "end_ratio",
    ]
    writer = csv.DictWriter(out, fieldnames=fields)
    writer.writeheader()
    for row in rows:
        extra = {
            "free_ratio": _ratio(row["micro"], row["raw"]),
            "llm_ratio": _ratio(row["post"], row["pre"]),
            "end_ratio": _ratio(row["post"], row["raw"]),
        }
        writer.writerow({**row, **extra})


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Free-layer and fake-compress ratios")
    parser.add_argument("--csv", action="store_true", help="write CSV to stdout")
    args = parser.parse_args(argv)
    rows = rows_for_matrix()
    if args.csv:
        write_csv(rows, sys.stdout)
    else:
        print(format_table(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
