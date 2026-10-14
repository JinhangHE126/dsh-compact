"""Phase-3 proxy tests: content-hash, injection, stream passthrough, health."""

from __future__ import annotations

import json
import threading
import time

import httpx
import pytest

from engine import CompactEngine, SUMMARY_MARKER
from mock_gateway import start_mock_gateway
from proxy import (
    ProxyState,
    content_hash,
    get_session_key,
    serve,
)


def _msgs(*texts: str) -> list[dict]:
    out: list[dict] = [{"role": "system", "content": "sys"}]
    for i, t in enumerate(texts):
        out.append({"role": "user", "content": t})
        out.append({"role": "assistant", "content": f"a{i}"})
    return out


def test_content_hash_stable():
    a = [{"role": "user", "content": "hi"}]
    b = [{"role": "user", "content": "hi"}]
    assert content_hash(a) == content_hash(b)


def test_get_session_key_prefers_header():
    msgs = _msgs("x")
    key = get_session_key(msgs, {"X-Session-Id": "abc-123"})
    assert key == "hdr:abc-123"


def test_get_session_key_content_hash_drops_last_user():
    msgs = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "u2"},
    ]
    k1 = get_session_key(msgs)
    k2 = get_session_key(msgs[:-1] + [{"role": "user", "content": "u2-changed"}])
    # Different last user → same prefix hash key
    assert k1 == k2
    assert k1.startswith("ch:")


@pytest.fixture
def proxy_pair():
    """Start mock upstream + proxy; yield (proxy_base, mock_gateway, state)."""
    upstream = start_mock_gateway()
    upstream.state.configure(
        content="upstream-ok",
        usage={"prompt_tokens": 100, "completion_tokens": 5, "total_tokens": 105},
    )

    def fake_summarize(prompt: str) -> str:
        return (
            "## Active Goal\n- g\n## Timeline\n1. t\n## Current State\n- s\n"
            "## Key Details\n- k\n## Errors and Fixes\n- 无\n## Next Step\n- n"
        )

    engine = CompactEngine(
        trigger_tokens=500,
        target_tokens=800,
        summarize=fake_summarize,
        micro_keep_recent=1,
    )
    state = ProxyState(
        upstream_base=upstream.base_url,
        engine=engine,
        max_concurrent=4,
        disabled=False,
    )
    httpd = serve(host="127.0.0.1", port=0, state=state)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    proxy_base = f"http://127.0.0.1:{port}"
    try:
        # wait health
        for _ in range(50):
            try:
                r = httpx.get(f"{proxy_base}/health", timeout=1.0)
                if r.status_code == 200:
                    break
            except Exception:
                time.sleep(0.05)
        yield proxy_base, upstream, state
    finally:
        httpd.shutdown()
        thread.join(timeout=5)
        state.close()
        upstream.stop()


def test_health_and_stats(proxy_pair):
    proxy_base, _upstream, _state = proxy_pair
    h = httpx.get(f"{proxy_base}/health", timeout=5.0).json()
    assert h["service"] == "dsh-compact"
    assert "version" in h
    assert h["enabled"] is True
    s = httpx.get(f"{proxy_base}/stats", timeout=5.0).json()
    assert "sessions" in s
    assert "cache" in s


def test_stream_passthrough_chat(proxy_pair):
    proxy_base, upstream, _state = proxy_pair
    # Non-stream JSON response from mock (still works)
    upstream.state.configure(content="hello-from-upstream")
    body = {
        "model": "company-code",
        "stream": False,
        "messages": [
            {"role": "user", "content": "ping"},
        ],
    }
    r = httpx.post(
        f"{proxy_base}/v1/chat/completions",
        json=body,
        headers={"Authorization": "Bearer sk-test"},
        timeout=10.0,
    )
    assert r.status_code == 200
    assert "hello-from-upstream" in r.text
    assert len(upstream.state.snapshot_log()) >= 1


def test_inject_after_background_compress(proxy_pair):
    proxy_base, upstream, state = proxy_pair
    # Build a long conversation over trigger
    messages = [{"role": "system", "content": "sys"}]
    for i in range(20):
        messages.append({"role": "user", "content": f"u{i} " + ("X" * 400)})
        messages.append(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": f"c{i}",
                        "type": "function",
                        "function": {"name": "web_fetch", "arguments": "{}"},
                    }
                ],
            }
        )
        messages.append(
            {
                "role": "tool",
                "tool_call_id": f"c{i}",
                "content": f"tool{i} " + ("Y" * 800),
            }
        )
        messages.append({"role": "assistant", "content": f"done{i}"})
    messages.append({"role": "user", "content": "TURN1"})

    # First request: should forward and schedule compress
    r1 = httpx.post(
        f"{proxy_base}/v1/chat/completions",
        json={"model": "company-code", "messages": messages, "stream": False},
        headers={"Authorization": "Bearer sk-test"},
        timeout=30.0,
    )
    assert r1.status_code == 200

    # Wait for background compress
    deadline = time.time() + 10
    while time.time() < deadline and state.compressions_total < 1:
        time.sleep(0.1)
    assert state.compressions_total >= 1, "background compress did not finish"

    # Second request: same history + new user turn (full transcript style)
    messages2 = messages + [
        {"role": "assistant", "content": "ack1"},
        {"role": "user", "content": "TURN2_LATEST"},
    ]
    r2 = httpx.post(
        f"{proxy_base}/v1/chat/completions",
        json={"model": "company-code", "messages": messages2, "stream": False},
        headers={"Authorization": "Bearer sk-test"},
        timeout=30.0,
    )
    assert r2.status_code == 200

    # Upstream should have received an injected (shorter) body on 2nd call
    logs = upstream.state.snapshot_log()
    assert len(logs) >= 2
    second_msgs = logs[-1]["payload"]["messages"]
    assert any(
        isinstance(m.get("content"), str) and SUMMARY_MARKER in m["content"]
        for m in second_msgs
    )
    assert any(
        m.get("role") == "user" and m.get("content") == "TURN2_LATEST"
        for m in second_msgs
    )
    # Injected body should be shorter than full transcript
    assert len(second_msgs) < len(messages2)


def test_disable_skips_inject(proxy_pair):
    proxy_base, upstream, state = proxy_pair
    state.disabled = True
    messages = [{"role": "user", "content": "only"}]
    r = httpx.post(
        f"{proxy_base}/v1/chat/completions",
        json={"model": "company-code", "messages": messages},
        timeout=10.0,
    )
    assert r.status_code == 200
    sent = upstream.state.snapshot_log()[-1]["payload"]["messages"]
    assert sent == messages
