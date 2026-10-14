"""
Micro-compaction: clear old tool results without calling an LLM.

OpenAI format: tool outputs live in messages with ``role: "tool"``.
Keeps the most recent N tool messages verbatim; older ones become a placeholder.
"""

from __future__ import annotations

import copy
from typing import Any, Optional, Sequence

from tokens import estimate_messages, estimate_message_tokens

CLEARED_MESSAGE = "[Old tool result cleared to free context space]"


def micro_compact(
    messages: Sequence[dict[str, Any]],
    keep_recent: int = 5,
    compactable_tool_names: Optional[Sequence[str]] = None,
) -> dict[str, Any]:
    """
    Clear old ``role:tool`` contents, keeping the last ``keep_recent``.

    If ``compactable_tool_names`` is set, only tool results whose matching
    ``tool_calls[].function.name`` is in that list are eligible.

    Returns:
        {
          "messages": list,
          "tool_results_cleared": int,
          "tokens_freed": int,  # estimated
        }
    """
    if keep_recent < 0:
        raise ValueError("keep_recent must be >= 0")

    # Map tool_call_id -> tool name from preceding assistant tool_calls
    id_to_name: dict[str, str] = {}
    for msg in messages:
        for call in msg.get("tool_calls") or []:
            if not isinstance(call, dict):
                continue
            cid = call.get("id")
            name = (call.get("function") or {}).get("name")
            if cid and name:
                id_to_name[str(cid)] = str(name)

    tool_indices: list[int] = []
    for i, msg in enumerate(messages):
        if msg.get("role") != "tool":
            continue
        if compactable_tool_names is not None:
            tid = msg.get("tool_call_id")
            name = id_to_name.get(str(tid) if tid is not None else "", "")
            if name not in compactable_tool_names:
                continue
        # Skip already-cleared placeholders
        content = msg.get("content")
        if content == CLEARED_MESSAGE:
            continue
        tool_indices.append(i)

    to_clear = tool_indices[: max(0, len(tool_indices) - keep_recent)]
    if not to_clear:
        return {
            "messages": [copy.deepcopy(m) for m in messages],
            "tool_results_cleared": 0,
            "tokens_freed": 0,
        }

    clear_set = set(to_clear)
    tokens_freed = 0
    out: list[dict[str, Any]] = []

    for i, msg in enumerate(messages):
        m = copy.deepcopy(msg)
        if i in clear_set:
            before = estimate_message_tokens(m)
            m["content"] = CLEARED_MESSAGE
            after = estimate_message_tokens(m)
            tokens_freed += max(0, before - after)
        out.append(m)

    return {
        "messages": out,
        "tool_results_cleared": len(to_clear),
        "tokens_freed": tokens_freed,
    }


def tokens_after_micro(
    messages: Sequence[dict[str, Any]],
    keep_recent: int = 5,
) -> tuple[list[dict[str, Any]], int, int]:
    """Helper: run micro_compact and return (messages, tokens_before, tokens_after)."""
    before = estimate_messages(messages)
    result = micro_compact(messages, keep_recent=keep_recent)
    after = estimate_messages(result["messages"])
    return result["messages"], before, after
