from tokens import (
    estimate_messages,
    estimate_tokens,
    group_messages_by_round,
)


def test_estimate_tokens_empty():
    assert estimate_tokens("") == 0


def test_estimate_tokens_scales_with_length():
    short = estimate_tokens("abcd")  # 4 chars -> ~1 * 1.33 -> 1
    long = estimate_tokens("x" * 400)  # 400/4*1.33 = 133
    assert short >= 1
    assert long > short
    assert long == int(400 / 4 * 1.33)


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
