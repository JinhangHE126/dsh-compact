"""
Emergency offline CLI: session file → brief.md for pasting into a new DSH session.

Usage:
  python compact.py path/to/session.json -o brief.md
  python compact.py path/to/session.jsonl --recent-rounds 5
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Any, Optional, Sequence

from engine import (
    CompactEngine,
    build_summarize_prompt,
    make_http_summarizer,
    messages_to_text,
)
from micro_compact import micro_compact
from session_reader import load_messages, peek_format
from strip_heavy import strip_heavy
from tokens import estimate_messages, group_messages_by_round

log = logging.getLogger("dsh-compact.cli")

DEFAULT_RECENT_ROUNDS = 4


def _content_preview(msg: dict[str, Any], limit: int = 4000) -> str:
    content = msg.get("content")
    if content is None and msg.get("tool_calls"):
        parts = []
        for call in msg["tool_calls"]:
            if not isinstance(call, dict):
                continue
            fn = call.get("function") or {}
            args = str(fn.get("arguments") or "")
            if len(args) > 500:
                args = args[:400] + "...[truncated]"
            parts.append(f"[tool_call {fn.get('name')}({args})]")
        text = "\n".join(parts)
    elif isinstance(content, str):
        text = content
    elif isinstance(content, list):
        bits = []
        for b in content:
            if isinstance(b, dict) and b.get("type") == "text":
                bits.append(b.get("text") or "")
            elif isinstance(b, dict) and b.get("type") in ("image_url", "image"):
                bits.append("[image]")
            else:
                bits.append(str(b)[:200])
        text = "\n".join(bits)
    else:
        text = str(content)
    if len(text) > limit:
        text = text[: limit - 20] + "\n...[truncated]...\n"
    return text


def format_recent_verbatim(messages: Sequence[dict[str, Any]]) -> str:
    lines = ["## 近期原文（请原样理解后续上下文）", ""]
    for msg in messages:
        role = msg.get("role", "?")
        lines.append(f"### {role}")
        lines.append(_content_preview(dict(msg)))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def split_old_and_recent(
    messages: Sequence[dict[str, Any]],
    recent_rounds: int = DEFAULT_RECENT_ROUNDS,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """
    Returns (leading_system, old_messages, recent_messages).
    ``recent_rounds`` counts user-started rounds (system group excluded).
    """
    groups = group_messages_by_round(list(messages))
    if not groups:
        return [], [], []

    leading: list[dict[str, Any]] = []
    rest = groups
    if groups[0] and all(m.get("role") == "system" for m in groups[0]):
        leading = list(groups[0])
        rest = groups[1:]

    if recent_rounds <= 0 or len(rest) <= recent_rounds:
        old: list[dict[str, Any]] = []
        recent = [m for g in rest for m in g]
        return leading, old, recent

    old_groups = rest[:-recent_rounds]
    recent_groups = rest[-recent_rounds:]
    old = [m for g in old_groups for m in g]
    recent = [m for g in recent_groups for m in g]
    return leading, old, recent


def build_brief(
    messages: Sequence[dict[str, Any]],
    *,
    summarize,
    recent_rounds: int = DEFAULT_RECENT_ROUNDS,
    do_micro: bool = True,
    micro_keep: int = 5,
) -> str:
    """
    Build a paste-ready brief.md body (summary + recent verbatim).
    Always calls the summarizer when there is older content (or whole chat if short).
    """
    msgs = strip_heavy(list(messages))
    if do_micro:
        msgs = micro_compact(msgs, keep_recent=micro_keep)["messages"]

    leading, old, recent = split_old_and_recent(msgs, recent_rounds=recent_rounds)

    if old:
        to_sum = old
        existing = ""
    else:
        # Short session: summarize everything except we still append recent
        to_sum = leading + recent
        recent_for_append = recent
        conv = messages_to_text(to_sum)
        prompt = build_summarize_prompt(conv, existing)
        summary = summarize(prompt)
        parts = [
            "# 会话交接 Brief",
            "",
            "> 粘贴到新 DSH 会话，并说明：按上述 brief 继续。",
            "",
            summary.strip(),
            "",
            format_recent_verbatim(recent_for_append),
        ]
        return "\n".join(parts)

    # Use engine chunking for large old sections
    engine = CompactEngine(
        trigger_tokens=1,
        target_tokens=max(1_000, estimate_messages(recent) or 1_000),
        summarize=summarize,
        micro_keep_recent=micro_keep,
    )
    # Force summarize via private chunked helper on `old` only
    summary = engine._summarize_chunked(old, existing_summary="")

    parts = [
        "# 会话交接 Brief",
        "",
        "> 粘贴到新 DSH 会话，并说明：按上述 brief 继续。",
        "",
        summary.strip(),
        "",
        format_recent_verbatim(recent),
    ]
    if leading:
        # Keep system intent visible briefly
        sys_text = _content_preview(leading[0], limit=800)
        parts.insert(4, "## 原 System（节选）\n\n" + sys_text + "\n")
    return "\n".join(parts)


def write_brief(
    session_path: Path,
    output_path: Path,
    *,
    recent_rounds: int = DEFAULT_RECENT_ROUNDS,
    summarize=None,
) -> Path:
    messages = load_messages(session_path)
    log.info(
        "loaded %s messages from %s (%s)",
        len(messages),
        session_path,
        peek_format(session_path),
    )
    if summarize is None:
        summarize = make_http_summarizer()
    text = build_brief(messages, summarize=summarize, recent_rounds=recent_rounds)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(text, encoding="utf-8")
    log.info("wrote %s (%s chars)", output_path, len(text))
    return output_path


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Offline DSH session → brief.md (emergency handoff)"
    )
    parser.add_argument("session", type=Path, help="Path to session .json / .jsonl")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("brief.md"),
        help="Output markdown path (default: brief.md)",
    )
    parser.add_argument(
        "--recent-rounds",
        type=int,
        default=DEFAULT_RECENT_ROUNDS,
        help=f"Verbatim recent user-rounds to append (default {DEFAULT_RECENT_ROUNDS})",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="DEBUG logging",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )

    if not os.environ.get("COMPANY_LLM_KEY") or not os.environ.get("COMPANY_LLM_BASE"):
        log.error("Set COMPANY_LLM_KEY and COMPANY_LLM_BASE (and optionally COMPANY_LLM_MODEL)")
        return 2

    try:
        write_brief(
            args.session,
            args.output,
            recent_rounds=args.recent_rounds,
        )
    except Exception as e:
        log.error("%s", e)
        if args.verbose:
            log.exception("detail")
        return 1
    print(f"OK → {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
