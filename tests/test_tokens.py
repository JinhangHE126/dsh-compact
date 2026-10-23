from tokens import (
    estimate_chat_request,
    estimate_messages,
    estimate_tokens,
    estimate_tools_tokens,
    group_messages_by_round,
)


def test_estimate_tokens_empty():
    assert estimate_tokens("") == 0


def test_estimate_tokens_scales_with_length():
    short = estimate_tokens("abcd")  # 4 chars -> 1 * 1.5 -> 1
    long = estimate_tokens("x" * 400)  # 400/4*1.5 = 150
    assert short >= 1
    assert long > short
    assert long == int(400 / 4 * 1.5)


def test_estimate_tokens_cjk_higher_than_latin_same_len():
    latin = estimate_tokens("a" * 100)
    cjk = estimate_tokens("中" * 100)
    # CJK ≈ 1 tok/char; latin ≈ 1/4 — CJK must be much larger
    assert cjk > latin * 2


def test_usage_preferred_over_estimate():
    messages = [{"role": "user", "content": "hello " * 1000}]
    estimated = estimate_messages(messages)
    assert estimate_messages(messages, usage={"prompt_tokens": 42}) == 42
    assert estimate_messages(messages, usage={"input_tokens": 99}) == 99
    assert estimated > 42


def test_estimate_includes_tool_calls():
    msg = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "c1",
                "type": "function",
                "function": {"name": "web_fetch", "arguments": '{"url":"' + "u" * 200 + '"}'},
            }
        ],
    }
    assert estimate_messages([msg]) > 50


def test_estimate_includes_reasoning_content():
    without = estimate_messages(
        [{"role": "assistant", "content": "ok", "reasoning_content": ""}]
    )
    with_r = estimate_messages(
        [
            {
                "role": "assistant",
                "content": "ok",
                "reasoning_content": "推" * 500,
            }
        ]
    )
    assert with_r > without + 400


def test_estimate_tools_and_chat_request():
    messages = [{"role": "user", "content": "hi"}]
    tools = [
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "d" * 200,
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]
    tool_tok = estimate_tools_tokens(tools)
    assert tool_tok > 40
    base = estimate_messages(messages)
    assert estimate_chat_request(messages, tools=tools) == base + tool_tok
    assert estimate_chat_request(messages, tools=tools, usage={"prompt_tokens": 7}) == 7


def test_group_messages_by_round():
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1", "tool_calls": [{"id": "t1", "function": {"name": "f", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "t1", "content": "result1"},
        {"role": "assistant", "content": "done1"},
        {"role": "user", "content": "u2"},
        {"role": "assistant", "content": "a2"},
    ]
    groups = group_messages_by_round(messages)
    assert len(groups) == 3
    assert [m["role"] for m in groups[0]] == ["system"]
    assert [m["role"] for m in groups[1]] == ["user", "assistant", "tool", "assistant"]
    assert [m["role"] for m in groups[2]] == ["user", "assistant"]
