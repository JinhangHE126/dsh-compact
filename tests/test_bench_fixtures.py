"""Fixture matrix: size band × session shape."""

from __future__ import annotations

import pytest

from bench.fixtures import BANDS, BAND_RANGES, GOLD, SHAPES, build_fixture, iter_fixtures
from engine import DEFAULT_TRIGGER
from proxy import HARD_LIMIT_TOKENS
from tokens import estimate_messages


def _texts(messages: list[dict]) -> str:
    chunks: list[str] = []

    def walk(node: object) -> None:
        if isinstance(node, str):
            chunks.append(node)
        elif isinstance(node, dict):
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(messages)
    return "\n".join(chunks)


@pytest.mark.parametrize("shape", SHAPES)
@pytest.mark.parametrize("band", BANDS)
def test_cell_lands_in_band(shape: str, band: str):
    fix = build_fixture(shape, band)
    lo, hi = BAND_RANGES[band]
    tokens = estimate_messages(fix["messages"])
    assert fix["name"] == f"{shape}_{band}"
    assert fix["tokens"] == tokens
    assert lo <= tokens <= hi
    if band == "short":
        assert tokens < DEFAULT_TRIGGER
    elif band == "over_trigger":
        assert DEFAULT_TRIGGER < tokens < HARD_LIMIT_TOKENS - 20_000
    else:
        assert HARD_LIMIT_TOKENS - 12_000 <= tokens < HARD_LIMIT_TOKENS


def test_matrix_has_nine_unique_names():
    rows = iter_fixtures()
    assert len(rows) == 9
    assert len({row["name"] for row in rows}) == 9


@pytest.mark.parametrize("shape", SHAPES)
@pytest.mark.parametrize("band", BANDS)
def test_gold_facts_and_recent_anchor(shape: str, band: str):
    fix = build_fixture(shape, band)
    blob = _texts(fix["messages"])
    for key in ("path", "function", "constraint", "error", "fix", "recent_anchor"):
        assert GOLD[key] in blob
    last_user = [m for m in fix["messages"] if m["role"] == "user"][-1]
    assert isinstance(last_user["content"], str)
    assert last_user["content"].startswith(GOLD["recent_anchor"])


@pytest.mark.parametrize("band", BANDS)
def test_chat_has_no_tools_or_images(band: str):
    messages = build_fixture("chat", band)["messages"]
    blob = _texts(messages)
    assert all(m["role"] in {"system", "user", "assistant"} for m in messages)
    assert "tool_calls" not in blob
    assert "data:image" not in blob


@pytest.mark.parametrize("band", BANDS)
def test_tools_shape_has_tool_results(band: str):
    messages = build_fixture("tools", band)["messages"]
    tools = [m for m in messages if m["role"] == "tool"]
    assert len(tools) >= 6
    assert any(
        m.get("tool_calls")
        and m["tool_calls"][0]["function"]["name"] == "read_file"
        for m in messages
    )
    longest = max(len(m["content"]) for m in tools)
    if band == "short":
        assert longest < 8_000
    else:
        assert longest > 8_000


@pytest.mark.parametrize("band", BANDS)
def test_images_shape_has_image_blocks(band: str):
    messages = build_fixture("images", band)["messages"]
    urls: list[str] = []
    for msg in messages:
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "image_url":
                urls.append(block["image_url"]["url"])
    assert urls
    assert all(url.startswith("data:image/png;base64,") for url in urls)
    if band != "short":
        assert max(len(url) for url in urls) > 10_000


def test_build_is_deterministic_and_isolated():
    first = build_fixture("chat", "short")
    first["messages"][0]["content"] = "mutated"
    second = build_fixture("chat", "short")
    assert second["messages"][0]["content"] == "You are a coding agent."
    again = build_fixture("chat", "short")
    assert again["messages"] == second["messages"]
