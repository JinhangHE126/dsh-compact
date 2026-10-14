from micro_compact import CLEARED_MESSAGE, micro_compact, tokens_after_micro
from tokens import estimate_messages


def _conv(n_tools: int = 8):
    messages = [{"role": "system", "content": "sys"}]
    for i in range(n_tools):
        messages.append(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": f"c{i}",
                        "type": "function",
                        "function": {
                            "name": "web_fetch",
                            "arguments": "{}",
                        },
                    }
                ],
            }
        )
        messages.append(
            {
                "role": "tool",
                "tool_call_id": f"c{i}",
                "content": ("RESULT-%d-" % i) + ("Z" * 2000),
            }
        )
    messages.append({"role": "user", "content": "continue"})
    return messages


def test_keeps_recent_n_tools():
    messages = _conv(8)
    result = micro_compact(messages, keep_recent=3)
    assert result["tool_results_cleared"] == 5
    tool_contents = [
        m["content"] for m in result["messages"] if m.get("role") == "tool"
    ]
    assert tool_contents.count(CLEARED_MESSAGE) == 5
    # last 3 kept
    assert tool_contents[-1].startswith("RESULT-7-")
    assert tool_contents[-2].startswith("RESULT-6-")
    assert tool_contents[-3].startswith("RESULT-5-")
    assert tool_contents[-4] == CLEARED_MESSAGE


def test_tokens_decrease():
    messages = _conv(6)
    new_msgs, before, after = tokens_after_micro(messages, keep_recent=2)
    assert after < before
    assert estimate_messages(new_msgs) == after


def test_non_tool_messages_unchanged():
    messages = _conv(4)
    user_before = [m for m in messages if m["role"] == "user"][0]["content"]
    result = micro_compact(messages, keep_recent=1)
    user_after = [m for m in result["messages"] if m["role"] == "user"][0]["content"]
    assert user_after == user_before
    # originals not mutated
    assert messages[-2]["content"].startswith("RESULT-")


def test_filter_by_tool_name():
    messages = [
        {
            "role": "assistant",
            "tool_calls": [
                {"id": "a", "function": {"name": "web_fetch", "arguments": "{}"}},
                {"id": "b", "function": {"name": "bash", "arguments": "{}"}},
            ],
        },
        {"role": "tool", "tool_call_id": "a", "content": "FETCH" + "x" * 1000},
        {"role": "tool", "tool_call_id": "b", "content": "BASH" + "y" * 1000},
        {
            "role": "assistant",
            "tool_calls": [
                {"id": "c", "function": {"name": "web_fetch", "arguments": "{}"}},
            ],
        },
        {"role": "tool", "tool_call_id": "c", "content": "FETCH2" + "z" * 1000},
    ]
    # keep_recent=0 clears all eligible; only web_fetch eligible
    result = micro_compact(
        messages, keep_recent=0, compactable_tool_names=["web_fetch"]
    )
    tools = [m for m in result["messages"] if m["role"] == "tool"]
    assert tools[0]["content"] == CLEARED_MESSAGE
    assert tools[1]["content"].startswith("BASH")  # not eligible
    assert tools[2]["content"] == CLEARED_MESSAGE
