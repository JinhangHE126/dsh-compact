from strip_heavy import (
    IMAGE_PLACEHOLDER,
    strip_heavy,
    strip_images,
    truncate_long_tool_outputs,
)


def test_strip_image_url_block():
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "see this"},
                {
                    "type": "image_url",
                    "image_url": {"url": "data:image/png;base64,AAAA"},
                },
            ],
        }
    ]
    out = strip_images(messages)
    assert messages[0]["content"][1]["type"] == "image_url"  # original intact
    blocks = out[0]["content"]
    assert blocks[0]["text"] == "see this"
    assert blocks[1] == {"type": "text", "text": IMAGE_PLACEHOLDER}


def test_strip_data_uri_in_string():
    messages = [
        {
            "role": "tool",
            "content": "prefix data:image/webp;base64,QUJDREVGR0g= suffix",
        }
    ]
    out = strip_images(messages)
    assert "data:image" not in out[0]["content"]
    assert IMAGE_PLACEHOLDER in out[0]["content"]


def test_truncate_long_tool_output():
    big = "A" * 20_000 + "TAIL"
    messages = [
        {"role": "user", "content": "hi"},
        {"role": "tool", "tool_call_id": "t1", "content": big},
    ]
    out = truncate_long_tool_outputs(messages, max_chars=500)
    assert out[0]["content"] == "hi"
    assert len(out[1]["content"]) < len(big)
    assert "truncated" in out[1]["content"]
    assert out[1]["content"].endswith("TAIL") or "TAIL" in out[1]["content"][-20:]


def test_strip_heavy_combo():
    messages = [
        {
            "role": "tool",
            "content": "data:image/png;base64," + ("x" * 100) + "\n" + ("Y" * 20_000),
        }
    ]
    out = strip_heavy(messages, tool_max_chars=1000)
    assert "data:image" not in out[0]["content"]
    assert len(out[0]["content"]) < 5000
