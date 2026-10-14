"""
Load chat messages from DSH / OpenAI-style session files.

Supported shapes:
  - JSON object with ``messages`` (chat.completions request body)
  - JSON array of message objects
  - JSONL: one message object per line, or one wrapper object per line
  - JSON object with nested ``conversation`` / ``transcript`` / ``items`` lists
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Union

PathLike = Union[str, Path]

_WRAPPER_KEYS = (
    "messages",
    "conversation",
    "transcript",
    "items",
    "history",
    "chat",
)


def _is_message(obj: Any) -> bool:
    return isinstance(obj, dict) and "role" in obj


def _extract_from_obj(obj: Any) -> list[dict[str, Any]] | None:
    if isinstance(obj, list):
        if not obj:
            return []
        if all(_is_message(x) for x in obj):
            return [dict(x) for x in obj]
        # list of wrappers
        out: list[dict[str, Any]] = []
        for x in obj:
            if _is_message(x):
                out.append(dict(x))
            elif isinstance(x, dict):
                # common: {"type":"message","message":{...}} or {"message":{role,content}}
                inner = x.get("message") or x.get("data")
                if _is_message(inner):
                    out.append(dict(inner))
        return out or None

    if not isinstance(obj, dict):
        return None

    if _is_message(obj) and "messages" not in obj:
        return [dict(obj)]

    for key in _WRAPPER_KEYS:
        if key in obj:
            found = _extract_from_obj(obj[key])
            if found is not None:
                return found

    # Some exports nest under "request" / "body"
    for key in ("request", "body", "payload"):
        if key in obj and isinstance(obj[key], (dict, list)):
            found = _extract_from_obj(obj[key])
            if found is not None:
                return found

    return None


def load_messages(path: PathLike) -> list[dict[str, Any]]:
    """
    Load messages from a file path.

    Raises:
        FileNotFoundError, ValueError on unsupported / empty content.
    """
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(p)

    raw = p.read_text(encoding="utf-8-sig").strip()
    if not raw:
        raise ValueError(f"Empty file: {p}")

    # Prefer whole-file JSON
    try:
        obj = json.loads(raw)
        msgs = _extract_from_obj(obj)
        if msgs is not None:
            if not msgs:
                raise ValueError(f"No messages in {p}")
            return msgs
    except json.JSONDecodeError:
        pass

    # JSONL fallback
    messages: list[dict[str, Any]] = []
    for line_no, line in enumerate(raw.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSONL at {p}:{line_no}: {e}") from e
        if _is_message(obj):
            messages.append(dict(obj))
            continue
        found = _extract_from_obj(obj)
        if found:
            messages.extend(found)
            continue
        raise ValueError(f"Unrecognized JSONL record at {p}:{line_no}")

    if not messages:
        raise ValueError(f"No messages parsed from {p}")
    return messages


def peek_format(path: PathLike) -> str:
    """Return a short label for diagnostics."""
    p = Path(path)
    raw = p.read_text(encoding="utf-8-sig").strip()
    try:
        obj = json.loads(raw)
        if isinstance(obj, dict) and "messages" in obj:
            return "openai_request_json"
        if isinstance(obj, list):
            return "messages_array_json"
        return "json_object"
    except json.JSONDecodeError:
        return "jsonl"
