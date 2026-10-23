"""
Token estimation for OpenAI-format chat messages.

- Prefer API-reported usage when available.
- Fallback: CJK ≈ 1 tok/char, other ≈ 1 tok / 4 chars, then a safety buffer.
  (Plain latin ``chars/4`` alone under-counted long DSH sessions by ~2–3×.)
"""

from __future__ import annotations

import json
from typing import Any, Mapping, Optional, Sequence

# Latin / code: ~1 tok / 4 chars. CJK is counted separately (~1 tok / char).
CHARS_PER_TOKEN = 4.0
# Inflate so we rarely under-estimate relative to provider tokenizers / DSH UI.
SAFETY_BUFFER = 1.5


def _is_cjk_char(ch: str) -> bool:
    """True for CJK Unified Ideographs and common adjacent blocks."""
    o = ord(ch)
    return (
        0x4E00 <= o <= 0x9FFF  # CJK Unified
        or 0x3400 <= o <= 0x4DBF  # Extension A
        or 0xF900 <= o <= 0xFAFF  # Compatibility Ideographs
        or 0x3000 <= o <= 0x303F  # CJK punctuation
        or 0x3040 <= o <= 0x30FF  # Hiragana / Katakana
        or 0xAC00 <= o <= 0xD7AF  # Hangul syllables
    )


def estimate_tokens(text: str) -> int:
    """Estimate tokens for a string (CJK-aware)."""
    if not text:
        return 0
    cjk = 0
    other = 0
    for ch in text:
        if _is_cjk_char(ch):
            cjk += 1
        else:
            other += 1
    raw = cjk * 1.0 + other / CHARS_PER_TOKEN
    return max(1, int(raw * SAFETY_BUFFER))


def _block_text(block: Any) -> str:
    if isinstance(block, str):
        return block
    if not isinstance(block, dict):
        return str(block)
    btype = block.get("type")
    if btype == "text":
        return block.get("text") or ""
    if btype == "image_url":
        url = (block.get("image_url") or {}).get("url") or ""
        return url
    if btype == "input_audio":
        return str(block.get("input_audio") or "")
    return str(block)


def estimate_message_tokens(message: Mapping[str, Any]) -> int:
    """Estimate tokens for one OpenAI chat message."""
    total = 0
    content = message.get("content")
    if isinstance(content, str):
        total += estimate_tokens(content)
    elif isinstance(content, list):
        for block in content:
            total += estimate_tokens(_block_text(block))
    elif content is not None:
        total += estimate_tokens(str(content))

    # Reasoning models (e.g. company-code) put long chains here; DSH UI counts them.
    for key in ("reasoning_content", "reasoning"):
        reasoning = message.get(key)
        if isinstance(reasoning, str) and reasoning:
            total += estimate_tokens(reasoning)
        elif reasoning is not None and not isinstance(reasoning, str):
            total += estimate_tokens(str(reasoning))

    for call in message.get("tool_calls") or []:
        if not isinstance(call, dict):
            continue
        fn = call.get("function") or {}
        total += estimate_tokens(str(fn.get("name") or ""))
        total += estimate_tokens(str(fn.get("arguments") or ""))

    if message.get("name"):
        total += estimate_tokens(str(message["name"]))
    if message.get("tool_call_id"):
        total += 4

    # Per-message overhead (role framing)
    total += 4
    return total


def estimate_tools_tokens(tools: Any) -> int:
    """Estimate tokens for a chat-completions ``tools`` / function schema list."""
    if not tools:
        return 0
    try:
        blob = json.dumps(tools, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        blob = str(tools)
    return estimate_tokens(blob)


def estimate_messages(
    messages: Sequence[Mapping[str, Any]],
    usage: Optional[Mapping[str, Any]] = None,
) -> int:
    """
    Estimate conversation tokens.

    If usage contains prompt_tokens / input_tokens, that value wins.
    """
    if usage:
        for key in ("prompt_tokens", "input_tokens"):
            val = usage.get(key)
            if isinstance(val, (int, float)) and val > 0:
                return int(val)
    return sum(estimate_message_tokens(m) for m in messages)


def estimate_chat_request(
    messages: Sequence[Mapping[str, Any]],
    *,
    tools: Any = None,
    usage: Optional[Mapping[str, Any]] = None,
) -> int:
    """
    Estimate tokens for a full chat/completions request.

    When ``usage`` is present it is treated as the provider's full prompt
    count (messages + tools) and returned as-is. Otherwise messages and
    tool schemas are summed.
    """
    if usage:
        for key in ("prompt_tokens", "input_tokens"):
            val = usage.get(key)
            if isinstance(val, (int, float)) and val > 0:
                return int(val)
    return estimate_messages(messages) + estimate_tools_tokens(tools)


def group_messages_by_round(
    messages: Sequence[Mapping[str, Any]],
) -> list[list[dict[str, Any]]]:
    """
    Group messages into API rounds.

    - Leading ``system`` messages form the first group (alone) if present.
    - Each subsequent group starts at a ``user`` message and includes the
      following ``assistant`` / ``tool`` messages until the next ``user``.
    """
    if not messages:
        return []

    # Materialize as dicts for mutability-safe copies downstream
    msgs = [dict(m) for m in messages]
    groups: list[list[dict[str, Any]]] = []

    i = 0
    # Leading systems
    systems: list[dict[str, Any]] = []
    while i < len(msgs) and msgs[i].get("role") == "system":
        systems.append(msgs[i])
        i += 1
    if systems:
        groups.append(systems)

    current: list[dict[str, Any]] = []
    while i < len(msgs):
        msg = msgs[i]
        if msg.get("role") == "user":
            if current:
                groups.append(current)
            current = [msg]
        else:
            if not current:
                current = [msg]
            else:
                current.append(msg)
        i += 1

    if current:
        groups.append(current)
    return groups
