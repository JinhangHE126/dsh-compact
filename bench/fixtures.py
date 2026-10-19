"""
Deterministic chat fixtures for the benchmark matrix.

Nine cells: three size bands × three session shapes.

Bands (estimated tokens, same estimator as the proxy):
  short            well under DASH_COMPACT_TRIGGER (180k)
  over_trigger     above the trigger, clear of the 240k hard limit
  near_hard_limit  just under HARD_LIMIT_TOKENS (sync compress)

Shapes:
  chat     user/assistant text only
  tools    assistant tool_calls + role:tool results
  images   image_url data-URIs (and a data-URI inside text)

Every fixture plants the same gold facts in the old prefix and a
RECENT_ANCHOR on the tail, so later quality checks can reuse them.
"""

from __future__ import annotations

import copy
from functools import lru_cache
from typing import Any

from engine import DEFAULT_TRIGGER
from proxy import HARD_LIMIT_TOKENS
from tokens import estimate_messages

SHAPES = ("chat", "tools", "images")
BANDS = ("short", "over_trigger", "near_hard_limit")

# Inclusive token windows. Gaps keep the three bands from blending together.
BAND_RANGES: dict[str, tuple[int, int]] = {
    "short": (6_000, 15_000),
    "over_trigger": (DEFAULT_TRIGGER + 5_000, 205_000),
    "near_hard_limit": (HARD_LIMIT_TOKENS - 12_000, HARD_LIMIT_TOKENS - 1),
}

GOLD = {
    "path": "src/billing/invoice.py",
    "function": "recalc_tax",
    "constraint": "DO_NOT_REWRITE_AUTH",
    "error": "TaxOverflowError",
    "fix": "clamp rate before multiply",
    "recent_anchor": "RECENT_ANCHOR",
}

_TOOL_ROUNDS = 8
_CHAT_ROUNDS = 8
_IMAGE_ROUNDS = 6


def _user(text: str) -> dict[str, Any]:
    return {"role": "user", "content": text}


def _assistant(text: str) -> dict[str, Any]:
    return {"role": "assistant", "content": text}


def _gold_user() -> dict[str, Any]:
    return _user(
        "Please fix {error} in {path} function {function}. "
        "Constraint: {constraint}. "
        "Fix already tried: {fix}.".format(**GOLD)
    )


def _gold_assistant() -> dict[str, Any]:
    return _assistant(
        "Noted. I will edit {function} in {path} and respect {constraint}.".format(**GOLD)
    )


def _recent() -> list[dict[str, Any]]:
    return [
        _user(
            "{recent_anchor} continue editing {path}".format(**GOLD)
        ),
        _assistant(
            "Continuing from {recent_anchor} on {function}.".format(**GOLD)
        ),
    ]


def _split_pad(pad_chars: int, parts: int) -> list[int]:
    """Spread pad_chars across parts; remainder goes to the earliest parts."""
    if parts <= 0:
        return []
    base, extra = divmod(pad_chars, parts)
    return [base + (1 if i < extra else 0) for i in range(parts)]


def _filler(n: int, label: str) -> str:
    if n <= 0:
        return ""
    unit = f"{label} {GOLD['path']} {GOLD['function']}\n"
    reps = (n // len(unit)) + 1
    return (unit * reps)[:n]


def _tool_call(cid: str) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": cid,
                "type": "function",
                "function": {
                    "name": "read_file",
                    "arguments": '{"path": "%s"}' % GOLD["path"],
                },
            }
        ],
    }


def _body_chat(pad_chars: int) -> list[dict[str, Any]]:
    sizes = _split_pad(pad_chars, _CHAT_ROUNDS)
    out: list[dict[str, Any]] = []
    for i, n in enumerate(sizes):
        out.append(_user(f"round {i} notes\n" + _filler(n, f"chat-{i}")))
        out.append(_assistant(f"ack round {i} on {GOLD['function']}"))
    return out


def _body_tools(pad_chars: int) -> list[dict[str, Any]]:
    sizes = _split_pad(pad_chars, _TOOL_ROUNDS)
    out: list[dict[str, Any]] = []
    for i, n in enumerate(sizes):
        cid = f"call-{i}"
        header = (
            f"TOOL-{i} {GOLD['error']}: rate overflow in {GOLD['function']}\n"
            f"{GOLD['fix']}\n"
        )
        out.append(_user(f"round {i} read {GOLD['path']}"))
        out.append(_tool_call(cid))
        out.append(
            {
                "role": "tool",
                "tool_call_id": cid,
                "content": header + _filler(n, f"tool-{i}"),
            }
        )
        out.append(_assistant(f"done round {i}"))
    return out


def _body_images(pad_chars: int) -> list[dict[str, Any]]:
    sizes = _split_pad(pad_chars, _IMAGE_ROUNDS)
    out: list[dict[str, Any]] = []
    for i, n in enumerate(sizes):
        # One inline data-URI (string path) plus an image_url block.
        inline = "data:image/png;base64," + ("B" * min(n, 64))
        block_n = max(0, n - len(inline))
        out.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": f"round {i} screenshot {inline}",
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": "data:image/png;base64," + ("A" * block_n),
                        },
                    },
                ],
            }
        )
        out.append(_assistant(f"saw screenshot round {i}"))
    return out


_BODY = {
    "chat": _body_chat,
    "tools": _body_tools,
    "images": _body_images,
}


def _assemble(shape: str, pad_chars: int) -> list[dict[str, Any]]:
    return [
        {"role": "system", "content": "You are a coding agent."},
        _gold_user(),
        _gold_assistant(),
        *_BODY[shape](pad_chars),
        *_recent(),
    ]


def _fit_pad(shape: str, lo: int, hi: int) -> tuple[list[dict[str, Any]], int, int]:
    """Pick a pad size whose estimated tokens land inside [lo, hi]."""
    target = (lo + hi) // 2
    high = 1_024
    while estimate_messages(_assemble(shape, high)) < lo:
        high *= 2
        if high > 8_000_000:
            raise RuntimeError(f"{shape} could not reach {lo} tokens")

    best: tuple[list[dict[str, Any]], int, int] | None = None
    best_dist = 10**12
    left, right = 0, high
    while left <= right:
        mid = (left + right) // 2
        messages = _assemble(shape, mid)
        tokens = estimate_messages(messages)
        if lo <= tokens <= hi:
            dist = abs(tokens - target)
            if dist < best_dist:
                best = (messages, tokens, mid)
                best_dist = dist
            if tokens < target:
                left = mid + 1
            elif tokens > target:
                right = mid - 1
            else:
                break
        elif tokens < lo:
            left = mid + 1
        else:
            right = mid - 1

    if best is None:
        raise RuntimeError(f"{shape} pad search missed window [{lo}, {hi}]")
    return best


@lru_cache(maxsize=None)
def _cached(shape: str, band: str) -> tuple[tuple[dict[str, Any], ...], int, int]:
    if shape not in SHAPES:
        raise ValueError(f"unknown shape {shape!r}")
    if band not in BAND_RANGES:
        raise ValueError(f"unknown band {band!r}")
    lo, hi = BAND_RANGES[band]
    messages, tokens, pad_chars = _fit_pad(shape, lo, hi)
    frozen = tuple(copy.deepcopy(messages))
    return frozen, tokens, pad_chars


def build_fixture(shape: str, band: str) -> dict[str, Any]:
    """Return one matrix cell. ``messages`` is a fresh list each call."""
    messages, tokens, pad_chars = _cached(shape, band)
    lo, hi = BAND_RANGES[band]
    return {
        "name": f"{shape}_{band}",
        "shape": shape,
        "band": band,
        "token_min": lo,
        "token_max": hi,
        "tokens": tokens,
        "pad_chars": pad_chars,
        "messages": copy.deepcopy(list(messages)),
        "gold": dict(GOLD),
    }


def iter_fixtures() -> list[dict[str, Any]]:
    """All nine cells, shape-major then band."""
    return [build_fixture(shape, band) for shape in SHAPES for band in BANDS]
