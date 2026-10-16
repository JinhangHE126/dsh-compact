from __future__ import annotations

import json

import httpx
import pytest

from engine import (
    SUMMARY_MARKER,
    CompactEngine,
    build_summarize_prompt,
    extract_summary,
    find_keep_index,
    has_summary,
    load_prompt_template,
    make_http_summarizer,
    reasoning_tail_summary,
    safe_cut,
)


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


def _long_conversation(n_rounds: int = 20, pad: int = 800) -> list[dict]:
    """Build a conversation large enough to compress with low trigger."""
    msgs: list[dict] = [{"role": "system", "content": "You are a coding agent."}]
    for i in range(n_rounds):
        msgs.append(_user(f"task-{i} " + ("X" * pad)))
        msgs.append(_assistant_tools(f"c{i}"))
        msgs.append(_tool(f"c{i}", f"tool-out-{i} " + ("Y" * pad)))
        msgs.append(_assistant(f"done-{i} " + ("Z" * (pad // 2))))
    msgs.append(_user("FINAL_USER_PING please continue"))
    return msgs


def test_prompt_template_has_required_sections():
    tpl = load_prompt_template()
    for key in (
        "Active Goal",
        "Previous Goals",
        "Timeline",
        "Current State",
        "Key Details",
        "Errors and Fixes",
        "Next Step",
        "{{conversation}}",
        "{{existing_summary_section}}",
    ):
        assert key in tpl


def test_build_summarize_prompt_merge_section():
    p = build_summarize_prompt("CONV", existing_summary="OLD SUMMARY")
    assert "OLD SUMMARY" in p
    assert "CONV" in p
    assert "既有滚动摘要" in p


def test_safe_cut_avoids_orphaning_tool_calls():
    msgs = [
        _user("u0"),
        _assistant_tools("t1"),
        _tool("t1", "result"),
        _assistant("ok"),
        _user("u1"),
        _assistant("a1"),
    ]
    # cut pointing at tool should walk back to a plain user
    cut = safe_cut(msgs, cut=2, floor=0)
    assert msgs[cut]["role"] == "user"
    assert not msgs[cut - 1].get("tool_calls")


def test_find_keep_index_keeps_tail():
    msgs = _long_conversation(10, pad=200)
    idx = find_keep_index(msgs, keep_ratio=0.3)
    assert 0 < idx < len(msgs)
    assert any(m.get("content") == "FINAL_USER_PING please continue" or
               (isinstance(m.get("content"), str) and "FINAL_USER_PING" in m["content"])
               for m in msgs[idx:])


def test_compress_keeps_recent_verbatim_and_adds_summary():
    calls = {"n": 0}

    def fake_summarize(prompt: str) -> str:
        calls["n"] += 1
        assert "待压缩对话" in prompt or "task-" in prompt or "**user**" in prompt
        return (
            "## Active Goal\n- continue\n## Timeline\n1. did stuff\n"
            "## Current State\n- ok\n## Key Details\n- path/foo.py\n"
            "## Errors and Fixes\n- 无\n## Next Step\n- go"
        )

    engine = CompactEngine(
        trigger_tokens=1_000,
        target_tokens=2_000,
        summarize=fake_summarize,
        micro_keep_recent=2,
    )
    msgs = _long_conversation(12, pad=600)
    # Force high usage so keep_ratio is small enough to cut
    usage = {"prompt_tokens": 50_000}
    assert engine.should_compact(msgs, usage)

    result = engine.compress(msgs, usage)
    assert result is not None
    assert calls["n"] >= 1
    out = result["messages"]
    assert has_summary(out)
    assert SUMMARY_MARKER in out[1]["content"] or SUMMARY_MARKER in out[0]["content"]

    # Recent final user message must survive verbatim
    contents = [
        m.get("content") for m in out if isinstance(m.get("content"), str)
    ]
    assert any("FINAL_USER_PING please continue" == c for c in contents)

    # Compressed should be smaller than original (estimated)
    assert result["post_tokens"] < result["pre_tokens"]


def test_second_compress_merges_existing_summary():
    seen_prompts: list[str] = []

    def fake_summarize(prompt: str) -> str:
        seen_prompts.append(prompt)
        return f"## Active Goal\n- g\n## Timeline\n1. step-{len(seen_prompts)}\n## Current State\n- x\n## Key Details\n- k\n## Errors and Fixes\n- 无\n## Next Step\n- n"

    engine = CompactEngine(
        trigger_tokens=500,
        target_tokens=1_500,
        summarize=fake_summarize,
        micro_keep_recent=1,
    )
    msgs = _long_conversation(10, pad=500)
    usage = {"prompt_tokens": 40_000}
    r1 = engine.compress(msgs, usage)
    assert r1 is not None
    # Grow again
    grown = r1["messages"] + [
        _user("more work " + ("M" * 800)),
        _assistant("ack " + ("A" * 400)),
        _user("even more " + ("N" * 800)),
        _assistant_tools("cx"),
        _tool("cx", "big " + ("T" * 2000)),
        _assistant("done more"),
        _user("LATEST"),
    ]
    r2 = engine.compress(grown, {"prompt_tokens": 40_000})
    assert r2 is not None
    assert len(seen_prompts) >= 2
    # Second call should see prior summary merge instruction or marker content
    assert any("既有滚动摘要" in p or "step-1" in p for p in seen_prompts[1:])
    assert extract_summary(r2["messages"])
    assert any(
        m.get("content") == "LATEST" for m in r2["messages"] if m.get("role") == "user"
    )


def test_circuit_breaker_trips():
    def boom(_prompt: str) -> str:
        raise RuntimeError("fail")

    engine = CompactEngine(
        trigger_tokens=100,
        target_tokens=200,
        max_failures=3,
        cooldown_s=60,
        summarize=boom,
        micro_keep_recent=0,
    )
    msgs = _long_conversation(8, pad=400)
    usage = {"prompt_tokens": 20_000}

    for _ in range(3):
        with pytest.raises(RuntimeError):
            engine.compress(msgs, usage, skip_micro=True)

    assert engine.consecutive_failures >= 3
    assert engine.should_compact(msgs, usage) is False
    engine.reset_circuit_breaker()
    assert engine.should_compact(msgs, usage) is True


def test_http_summarizer_with_mock_transport():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["model"] == "company-code"
        assert body["messages"][0]["role"] == "user"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"role": "assistant", "content": "## Active Goal\nok"}}
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            },
        )

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport, base_url="http://mock")
    summarize = make_http_summarizer(
        base_url="http://mock/v1",
        api_key="sk-test",
        model="company-code",
        client=client,
    )
    text = summarize("please summarize")
    assert "Active Goal" in text


def test_summarizer_retries_on_429():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(
                429,
                json={"error": {"message": "Rate limit", "code": "429"}},
            )
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"role": "assistant", "content": "## Active Goal\nok"}}
                ]
            },
        )

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)
    summarize = make_http_summarizer(
        base_url="http://mock/v1",
        api_key="sk-test",
        model="company-code",
        client=client,
        max_retries=4,
    )
    # Avoid multi-second sleeps in unit test
    import engine as eng

    real_sleep = eng.time.sleep
    eng.time.sleep = lambda _s: None
    try:
        text = summarize("please summarize")
    finally:
        eng.time.sleep = real_sleep
    assert "Active Goal" in text
    assert calls["n"] == 3


def test_auto_compact_llm_compresses_when_micro_insufficient():
    # Few tools but huge tool bodies: micro clears 5 of 6, the one kept
    # huge result still leaves us over trigger, so LLM compress must run.
    # (Before the find_keep_index/safe_cut fix, compress no-opped here and
    # auto_compact claimed "compacted" while still ~66K over trigger.)
    msgs = [{"role": "system", "content": "s"}]
    for i in range(6):
        msgs.append(_assistant_tools(f"t{i}"))
        msgs.append(_tool(f"t{i}", "Z" * 50_000))
    msgs.append(_user("hi"))

    engine = CompactEngine(
        trigger_tokens=5_000,
        target_tokens=2_000,
        summarize=lambda p: "summarized",
        micro_keep_recent=1,
    )
    out = engine.auto_compact(msgs)
    assert out["compacted"] is True
    # LLM compress actually ran: summary marker present, no huge body survives
    assert SUMMARY_MARKER in out["messages"][1]["content"]
    tool_contents = [m["content"] for m in out["messages"] if m.get("role") == "tool"]
    assert all(len(str(c)) < 10_000 for c in tool_contents)
    # The recent user survives verbatim
    assert out["messages"][-1] == {"role": "user", "content": "hi"}


def test_compress_dense_agent_session_keeps_recent_user():
    """Phase 5 regression: dense agent session — tail dominated by huge
    assistant/tool results, the only recent user is the last message (the
    DSH "ping"). Old find_keep_index clamped the cut to len-4 and safe_cut
    walked backward away from it into a no-op; it must now compress.
    """
    calls = {"n": 0}

    def fake_summarize(prompt: str) -> str:
        calls["n"] += 1
        return "## Active Goal\n- x\n## Next Step\n- y"

    big = "Z" * 20_000  # each huge tool result dominates the tail
    msgs = [{"role": "system", "content": "sys"}]
    msgs.append(_user("original task"))
    # Old rounds: assistant(tool)/tool pairs, no user in between
    for i in range(30):
        msgs.append(_assistant_tools(f"c{i}"))
        msgs.append(_tool(f"c{i}", f"out {i}"))
    # Huge tail: no plain user inside it
    for i in range(5):
        msgs.append(_assistant_tools(f"h{i}"))
        msgs.append(_tool(f"h{i}", big))
    # The only recent user sits at the very end
    msgs.append(_user("please continue"))

    engine = CompactEngine(
        trigger_tokens=50_000,
        target_tokens=10_000,
        summarize=fake_summarize,
    )
    out = engine.compress(msgs, {"prompt_tokens": 90_000})
    assert out is not None, "dense session must compress, not no-op"
    assert calls["n"] >= 1
    assert SUMMARY_MARKER in out["messages"][1]["content"]
    # The recent ping must survive verbatim
    assert out["messages"][-1] == _user("please continue")


def test_safe_cut_relaxed_allows_unmatched_tool_prev():
    """Strict mode refuses a cut whose prev is an unmatched tool_use; with
    allow_tool_prev the unmatched pair is dropped into the summary instead
    of blocking the whole compression."""
    msgs = [_user("u0"), _assistant_tools("t1"), _user("u1")]
    assert safe_cut(msgs, cut=2, floor=0) == 0  # strict: u1 rejected, falls to u0
    assert safe_cut(msgs, cut=2, floor=0, allow_tool_prev=True) == 2


# ---------------------------------------------------------------------------
# make_http_summarizer: reasoning_effort + reasoning-tail fallback
# ---------------------------------------------------------------------------


def test_summarizer_sends_reasoning_effort():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["model"] == "company-code"
        assert body.get("reasoning_effort") == "minimal"
        assert body["max_tokens"] == 6_000
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"role": "assistant", "content": "## Active Goal\nok"}}
                ],
            },
        )

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)
    summarize = make_http_summarizer(
        base_url="http://mock/v1",
        api_key="sk-test",
        model="company-code",
        client=client,
    )
    assert summarize("please summarize") == "## Active Goal\nok"


def test_summarizer_retries_without_effort_on_400():
    """400 on the enhanced payload means the gateway rejects the param:
    fall back to a plain payload, not a hard failure."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(
                400,
                json={"error": {"message": "unsupported parameter reasoning_effort"}},
            )
        body = json.loads(request.content)
        assert "reasoning_effort" not in body
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"role": "assistant", "content": "ok summary"}}
                ],
            },
        )

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)
    summarize = make_http_summarizer(
        base_url="http://mock/v1",
        api_key="sk-test",
        model="company-code",
        client=client,
    )
    assert summarize("x") == "ok summary"
    assert calls["n"] == 2


def test_summarizer_plain_400_fails():
    """A 400 on the plain payload (e.g. PTL) is a real failure."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(
            400,
            json={"error": {"message": "prompt tokens exceed limit"}},
        )

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)
    summarize = make_http_summarizer(
        base_url="http://mock/v1",
        api_key="sk-test",
        model="company-code",
        client=client,
        max_retries=1,
    )
    with pytest.raises(RuntimeError, match="Summarizer HTTP 400"):
        summarize("x")
    assert calls["n"] == 2


def test_summarizer_falls_back_to_reasoning_tail():
    """Empty content but a non-empty reasoning chain -> summary from the
    thinking chain tail (the final answer is written there)."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "reasoning_content": (
                                "先梳理目标，再梳理时间线与约束。\n"
                                "最终答案\n"
                                "## Active Goal\n- 用户要求压缩会话\n"
                                "## Next Step\n- 继续验证\n"
                            ),
                        }
                    }
                ],
            },
        )

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)
    summarize = make_http_summarizer(
        base_url="http://mock/v1",
        api_key="sk-test",
        model="company-code",
        client=client,
    )
    text = summarize("x")
    assert "## Active Goal" in text
    assert "继续验证" in text


def test_summarizer_both_empty_raises():
    """Empty content AND empty reasoning -> real failure (breaker counts it)."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"role": "assistant", "content": "   "}}
                ],
            },
        )

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)
    summarize = make_http_summarizer(
        base_url="http://mock/v1",
        api_key="sk-test",
        model="company-code",
        client=client,
    )
    with pytest.raises(RuntimeError, match="empty content"):
        summarize("x")


def test_reasoning_tail_summary():
    long_text = (
        "prefix " * 3_000
        + "最终答案：核心结论 A；关键路径 p/a.py；下一步验证。"
    )
    s = reasoning_tail_summary(long_text)
    assert "核心结论 A" in s
    assert "下一步验证。" in s
    assert "prefix" not in s

    tail_text = "no marker here, just the tail: 收尾结论"
    assert "收尾结论" in reasoning_tail_summary(tail_text)
    assert reasoning_tail_summary("") == ""
