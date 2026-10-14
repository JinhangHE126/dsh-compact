import json
from pathlib import Path

import pytest

from session_reader import load_messages, peek_format


def test_load_openai_request_json(tmp_path: Path):
    p = tmp_path / "req.json"
    p.write_text(
        json.dumps(
            {
                "model": "company-code",
                "messages": [
                    {"role": "system", "content": "s"},
                    {"role": "user", "content": "hi"},
                ],
            }
        ),
        encoding="utf-8",
    )
    msgs = load_messages(p)
    assert len(msgs) == 2
    assert msgs[0]["role"] == "system"
    assert peek_format(p) == "openai_request_json"


def test_load_messages_array(tmp_path: Path):
    p = tmp_path / "arr.json"
    p.write_text(
        json.dumps([{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}]),
        encoding="utf-8",
    )
    assert len(load_messages(p)) == 2


def test_load_jsonl(tmp_path: Path):
    p = tmp_path / "chat.jsonl"
    lines = [
        json.dumps({"role": "user", "content": "u1"}),
        json.dumps({"role": "assistant", "content": "a1"}),
    ]
    p.write_text("\n".join(lines), encoding="utf-8")
    msgs = load_messages(p)
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert peek_format(p) == "jsonl"


def test_load_missing_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        load_messages(tmp_path / "nope.json")


@pytest.mark.skipif(
    not (Path.home() / ".dsh-compact" / "samples" / "session_probe.json").exists(),
    reason="no local sample",
)
def test_load_phase0_sample():
    p = Path.home() / ".dsh-compact" / "samples" / "session_probe.json"
    msgs = load_messages(p)
    assert len(msgs) >= 10
    assert any(m.get("role") == "tool" for m in msgs)
