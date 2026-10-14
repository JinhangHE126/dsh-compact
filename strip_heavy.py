"""
Strip heavy multimodal / oversized tool payloads before summarization.

Free (no LLM). Operates on OpenAI chat message lists.
"""

from __future__ import annotations

import copy
import re
from typing import Any, Sequence

DEFAULT_TOOL_MAX_CHARS = 8_000
IMAGE_PLACEHOLDER = "[image removed for compaction]"
DATA_URI_RE = re.compile(
    r"data:image/[a-zA-Z0-9.+-]+;base64,[A-Za-z0-9+/=\s]+",
    re.IGNORECASE,
)


def _strip_data_uris(text: str) -> str:
    if "data:image" not in text.lower():
        return text
    return DATA_URI_RE.sub(IMAGE_PLACEHOLDER, text)


def _strip_content(content: Any) -> Any:
    if isinstance(content, str):
        return _strip_data_uris(content)

    if not isinstance(content, list):
        return content

    new_blocks: list[Any] = []
    for block in content:
        if not isinstance(block, dict):
            new_blocks.append(block)
            continue
        btype = block.get("type")
        if btype == "image_url":
            new_blocks.append({"type": "text", "text": IMAGE_PLACEHOLDER})
            continue
        if btype == "image":
            new_blocks.append({"type": "text", "text": IMAGE_PLACEHOLDER})
            continue
        if btype == "text":
            text = block.get("text") or ""
            new_blocks.append({**block, "text": _strip_data_uris(text)})
            continue
        # Pass through other block types unchanged
        new_blocks.append(block)
    return new_blocks


def strip_images(messages: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Replace image blocks and data:image base64 URIs with a text placeholder.
    Returns a deep-copied message list; originals are not mutated.
    """
    out: list[dict[str, Any]] = []
    for msg in messages:
        m = copy.deepcopy(msg)
        if "content" in m:
            m["content"] = _strip_content(m["content"])
        out.append(m)
    return out


def truncate_long_tool_outputs(
    messages: Sequence[dict[str, Any]],
    max_chars: int = DEFAULT_TOOL_MAX_CHARS,
) -> list[dict[str, Any]]:
    """
    Truncate oversized ``role:tool`` contents (and huge tool_call arguments).
    Keeps head + tail so errors at either end remain visible.
    """
    if max_chars < 64:
        raise ValueError("max_chars must be >= 64")

    out: list[dict[str, Any]] = []
    for msg in messages:
        m = copy.deepcopy(msg)
        role = m.get("role")

        if role == "tool":
            content = m.get("content")
            if isinstance(content, str) and len(content) > max_chars:
                m["content"] = _truncate(content, max_chars)
            elif isinstance(content, list):
                m["content"] = _truncate_blocks(content, max_chars)

        # Also shrink huge tool_call argument dumps on assistant turns
        if role == "assistant" and m.get("tool_calls"):
            for call in m["tool_calls"]:
                if not isinstance(call, dict):
                    continue
                fn = call.get("function")
                if not isinstance(fn, dict):
                    continue
                args = fn.get("arguments")
                if isinstance(args, str) and len(args) > max_chars:
                    fn["arguments"] = _truncate(args, max_chars)

        out.append(m)
    return out


def strip_heavy(
    messages: Sequence[dict[str, Any]],
    tool_max_chars: int = DEFAULT_TOOL_MAX_CHARS,
) -> list[dict[str, Any]]:
    """Convenience: strip images then truncate long tool outputs."""
    return truncate_long_tool_outputs(strip_images(messages), max_chars=tool_max_chars)


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    head = max_chars * 3 // 4
    tail = max_chars - head - 40
    if tail < 16:
        tail = 16
        head = max_chars - tail - 40
    return (
        text[:head]
        + f"\n...[truncated {len(text) - head - tail} chars]...\n"
        + text[-tail:]
    )


def _truncate_blocks(blocks: list[Any], max_chars: int) -> list[Any]:
    new_blocks: list[Any] = []
    for block in blocks:
        if isinstance(block, dict) and block.get("type") == "text":
            text = block.get("text") or ""
            if len(text) > max_chars:
                new_blocks.append({**block, "text": _truncate(text, max_chars)})
            else:
                new_blocks.append(block)
        else:
            new_blocks.append(block)
    return new_blocks
