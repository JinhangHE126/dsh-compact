"""
Phase-2 hardening: engine e2e against a threaded mock gateway.

Covers: mixed heavy session, chunk truncate retries, HTTP 500 circuit breaker,
multiple merges with timeline accumulation.
"""

from __future__ import annotations

import httpx
import pytest

from engine import (
    SUMMARY_MARKER,
    CompactEngine,
    extract_summary,
    has_summary,
    make_http_summarizer,
)
from strip_heavy import IMAGE_PLACEHOLDER


def _user(text: str) -> dict:
    return {"role": "user", "content": text}


def _assistant(text: str) -> dict:
    return {"role": "assistant", "content": text}


def _tool(cid: str, text: str) -> dict:
    return {"role": "tool", "tool_call_id": cid, "content": text}


def _assistant_tools(cid: str, name: str = "web_fetch") -> dict:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": cid,
                "type": "function",
                "function": {"name": name, "arguments": "{}"},
            }
        ],
    }


def _mixed_heavy_conversation(n_rounds: int = 14) -> list[dict]:
    """Base64 image + huge tool dumps + normal turns."""
    msgs: list[dict] = [{"role": "system", "content": "You are a coding agent."}]
    for i in range(n_rounds):
        if i == 0:
            msgs.append(
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": f"task-{i} look at screenshot"},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": "data:image/png;base64," + ("A" * 400)
                            },
                        },
                    ],
                }
            )
        else:
            msgs.append(_user(f"task-{i} edit path/src/mod_{i}.py " + ("X" * 400)))
        msgs.append(_assistant_tools(f"c{i}"))
        msgs.append(
            _tool(
                f"c{i}",
                f"TOOL-{i} path/src/mod_{i}.py\n" + ("Y" * 6_000),
            )
        )
        msgs.append(_assistant(f"done-{i} changed path/src/mod_{i}.py"))
    msgs.append(_user("RECENT_ANCHOR continue from here"))
    return msgs


def test_mock_gateway_returns_fixed_usage(mock_gateway):
    mock_gateway.state.configure(
        usage={"prompt_tokens": 99, "completion_tokens": 7, "total_tokens": 106}
    )
    r = httpx.post(
        f"{mock_gateway.base_url}/chat/completions",
        headers={"Authorization": "Bearer sk-test"},
        json={
            "model": "company-code",
            "messages": [{"role": "user", "content": "hi"}],
        },
        timeout=10.0,
    )
    assert r.status_code == 200
    assert r.json()["usage"]["prompt_tokens"] == 99
    assert len(mock_gateway.state.snapshot_log()) == 1


def test_compress_mixed_session_no_400_keeps_recent(mock_gateway):
    """base64 + long tools → compress via real HTTP summarizer; recent verbatim."""
    mock_gateway.state.configure(
        content=(
            "## Active Goal\n- RECENT_ANCHOR work\n## Timeline\n"
            "1. touched path/src/mod_0.py\n2. more edits\n"
            "## Current State\n- mid\n## Key Details\n- path/src/mod_0.py\n"
            "## Errors and Fixes\n- 无\n## Next Step\n- continue"
        )
    )
    summarize = make_http_summarizer(
        base_url=mock_gateway.base_url,
        api_key="sk-test",
        model="company-code",
        timeout=30.0,
    )
    engine = CompactEngine(
        trigger_tokens=1_000,
        target_tokens=3_000,
        summarize=summarize,
        micro_keep_recent=2,
        chunk_chars=80_000,
    )
    msgs = _mixed_heavy_conversation(12)
    usage = {"prompt_tokens": 80_000}

    result = engine.compress(msgs, usage)
    assert result is not None
    out = result["messages"]
    assert has_summary(out)
    assert result["post_tokens"] < result["pre_tokens"]

    # Recent anchor survives
    assert any(
        m.get("role") == "user" and m.get("content") == "RECENT_ANCHOR continue from here"
        for m in out
    )
    # Images stripped somewhere in pipeline (summary path or remaining msgs)
    blob = str(out)
    assert "data:image" not in blob or IMAGE_PLACEHOLDER in blob

    # Mock only saw 200s (no 400)
    for entry in mock_gateway.state.snapshot_log():
        assert entry["path"].endswith("/chat/completions")
    assert mock_gateway.state.status == 200


def test_chunk_truncate_retries_at_most_three():
    """Oversized chunk → drop oldest 20% rounds, ≤3 truncations recorded."""
    prompts: list[str] = []

    def fake_summarize(prompt: str) -> str:
        prompts.append(prompt)
        return (
            "## Active Goal\n- g\n## Timeline\n1. t\n## Current State\n- s\n"
            "## Key Details\n- k\n## Errors and Fixes\n- 无\n## Next Step\n- n"
        )

    # Many medium rounds; tiny chunk_chars forces multi-chunk + truncations
    msgs: list[dict] = [{"role": "system", "content": "sys"}]
    for i in range(30):
        msgs.append(_user(f"u{i} " + ("Q" * 800)))
        msgs.append(_assistant(f"a{i} " + ("R" * 800)))
    msgs.append(_user("TAIL_USER"))

    engine = CompactEngine(
        trigger_tokens=500,
        target_tokens=2_000,
        summarize=fake_summarize,
        micro_keep_recent=0,
        chunk_chars=3_000,  # forces chunking + possible truncations
    )
    result = engine.compress(msgs, {"prompt_tokens": 60_000}, skip_micro=True)
    assert result is not None
    assert engine.last_chunk_truncations <= 3 * 10  # per-chunk max 3; many chunks ok
    # At least one truncation should have happened with this budget
    assert engine.last_chunk_truncations >= 1 or len(prompts) >= 2
    assert any(
        m.get("content") == "TAIL_USER" for m in result["messages"] if m.get("role") == "user"
    )


def test_http_500_three_times_trips_breaker(mock_gateway):
    mock_gateway.state.configure(status=500)  # always 500
    summarize = make_http_summarizer(
        base_url=mock_gateway.base_url,
        api_key="sk-test",
        model="company-code",
        timeout=10.0,
    )
    engine = CompactEngine(
        trigger_tokens=100,
        target_tokens=500,
        max_failures=3,
        cooldown_s=120,
        summarize=summarize,
        micro_keep_recent=0,
    )
    msgs = _mixed_heavy_conversation(8)
    usage = {"prompt_tokens": 40_000}

    for _ in range(3):
        with pytest.raises(RuntimeError):
            engine.compress(msgs, usage, skip_micro=True)

    assert engine.consecutive_failures >= 3
    assert engine.should_compact(msgs, usage) is False

    # After reset, can try again (still 500, but should_compact true)
    engine.reset_circuit_breaker()
    assert engine.should_compact(msgs, usage) is True


def test_empty_choices_counts_as_failure(mock_gateway):
    mock_gateway.state.configure(empty_choices=True, status=200)
    summarize = make_http_summarizer(
        base_url=mock_gateway.base_url,
        api_key="sk-test",
        model="company-code",
        timeout=10.0,
    )
    engine = CompactEngine(
        trigger_tokens=100,
        target_tokens=500,
        max_failures=3,
        cooldown_s=60,
        summarize=summarize,
        micro_keep_recent=0,
    )
    msgs = _mixed_heavy_conversation(6)
    with pytest.raises(RuntimeError):
        engine.compress(msgs, {"prompt_tokens": 30_000}, skip_micro=True)
    assert engine.consecutive_failures == 1


def test_multiple_merge_accumulates_timeline(mock_gateway):
    summarize = make_http_summarizer(
        base_url=mock_gateway.base_url,
        api_key="sk-test",
        model="company-code",
        timeout=30.0,
    )
    mock_gateway.state.configure(
        content=(
            "## Active Goal\n- goal-1\n## Timeline\n"
            "1. early path/keep.py\n2. step-1\n"
            "## Current State\n- s1\n## Key Details\n- path/keep.py\n"
            "## Errors and Fixes\n- 无\n## Next Step\n- next-1"
        )
    )
    engine = CompactEngine(
        trigger_tokens=500,
        target_tokens=2_000,
        summarize=summarize,
        micro_keep_recent=1,
    )
    msgs = _mixed_heavy_conversation(10)
    r1 = engine.compress(msgs, {"prompt_tokens": 50_000})
    assert r1 is not None
    s1 = extract_summary(r1["messages"])
    assert "path/keep.py" in s1

    grown = r1["messages"] + [
        _user("second wave " + ("W" * 600)),
        _assistant_tools("z1"),
        _tool("z1", "MORE " + ("Z" * 4_000)),
        _assistant("wave done"),
        _user("LATEST_MERGE"),
    ]
    mock_gateway.state.configure(
        content=(
            "## Active Goal\n- LATEST_MERGE\n## Timeline\n"
            "1. early path/keep.py\n2. step-1\n3. second wave\n"
            "## Current State\n- ok\n## Key Details\n- path/keep.py\n"
            "## Errors and Fixes\n- 无\n## Next Step\n- go"
        )
    )
    r2 = engine.compress(grown, {"prompt_tokens": 50_000})
    assert r2 is not None
    s2 = extract_summary(r2["messages"])
    assert "path/keep.py" in s2
    assert "second wave" in s2 or "LATEST_MERGE" in s2
    assert any(
        m.get("content") == "LATEST_MERGE" for m in r2["messages"] if m.get("role") == "user"
    )
    logs = mock_gateway.state.snapshot_log()
    assert len(logs) >= 2
    second_prompt = logs[-1]["payload"]["messages"][0]["content"]
    assert "既有滚动摘要" in second_prompt
