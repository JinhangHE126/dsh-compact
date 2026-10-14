"""Optional smoke test against Phase-0 sample if present on this machine."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from micro_compact import micro_compact
from strip_heavy import strip_heavy
from tokens import estimate_messages

SAMPLE = Path.home() / ".dsh-compact" / "samples" / "session_probe.json"


@pytest.mark.skipif(not SAMPLE.exists(), reason="no local session_probe.json")
def test_sample_pipeline_reduces_size():
    data = json.loads(SAMPLE.read_text(encoding="utf-8"))
    messages = data["messages"]
    before = estimate_messages(messages)
    stripped = strip_heavy(messages, tool_max_chars=4000)
    micro = micro_compact(stripped, keep_recent=5)
    after = estimate_messages(micro["messages"])
    assert before > 0
    assert after < before
    assert micro["tool_results_cleared"] >= 1 or after < before
