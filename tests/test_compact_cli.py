import json
from pathlib import Path

from compact import build_brief, split_old_and_recent, write_brief


def _long_session(n: int = 8) -> list[dict]:
    msgs = [{"role": "system", "content": "You are an agent."}]
    for i in range(n):
        msgs.append({"role": "user", "content": f"task-{i} path/f{i}.py"})
        msgs.append({"role": "assistant", "content": f"done-{i}"})
    msgs.append({"role": "user", "content": "FINAL"})
    return msgs


def test_split_old_and_recent():
    msgs = _long_session(6)
    leading, old, recent = split_old_and_recent(msgs, recent_rounds=2)
    assert leading and leading[0]["role"] == "system"
    assert old
    assert any(m.get("content") == "FINAL" for m in recent)
    # recent should be last 2 user-rounds (+ assistants)
    user_in_recent = [m for m in recent if m.get("role") == "user"]
    assert len(user_in_recent) == 2


def test_build_brief_with_fake_summarizer():
    def fake(prompt: str) -> str:
        assert "task-0" in prompt or "待压缩" in prompt or "**user**" in prompt
        return (
            "## Active Goal\n- FINAL\n## Timeline\n1. did work\n"
            "## Current State\n- ok\n## Key Details\n- path/f0.py\n"
            "## Errors and Fixes\n- 无\n## Next Step\n- continue"
        )

    text = build_brief(_long_session(6), summarize=fake, recent_rounds=2)
    assert text.startswith("# 会话交接 Brief")
    assert "path/f0.py" in text or "Active Goal" in text
    assert "FINAL" in text
    assert "近期原文" in text


def test_write_brief_file(tmp_path: Path):
    session = tmp_path / "s.json"
    session.write_text(
        json.dumps({"messages": _long_session(5)}),
        encoding="utf-8",
    )
    out = tmp_path / "brief.md"

    def fake(prompt: str) -> str:
        return "## Active Goal\n- x\n## Timeline\n1. y\n## Current State\n- z\n## Key Details\n- k\n## Errors and Fixes\n- 无\n## Next Step\n- n"

    write_brief(session, out, recent_rounds=2, summarize=fake)
    body = out.read_text(encoding="utf-8")
    assert "会话交接 Brief" in body
    assert "FINAL" in body
