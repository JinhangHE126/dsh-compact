"""
Token estimation for OpenAI-format chat messages.

- Prefer API-reported usage when available.
- Fallback: ~1 token per 4 chars + 33% safety buffer (contextkit-style).
"""

from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

# ~1 tok / 4 chars, then inflate by 33% so we under-estimate rarely.
CHARS_PER_TOKEN = 4.0
SAFETY_BUFFER = 1.33


def estimate_tokens(text: str) -> int:
    """Estimate tokens for a string."""
    if not text:
        return 0
    return max(1, int(len(text) / CHARS_PER_TOKEN * SAFETY_BUFFER))


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
