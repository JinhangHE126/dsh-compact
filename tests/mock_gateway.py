"""
Thread-local OpenAI-compatible mock gateway for engine e2e tests.

Configurable per-request via MockGatewayState:
  status, delay_s, empty_choices, fixed_usage, custom_content, fail_n_times
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Optional
from urllib.parse import urlparse


@dataclass
class MockGatewayState:
    status: int = 200
    delay_s: float = 0.0
    empty_choices: bool = False
    content: str = (
        "## Active Goal\n- test\n## Timeline\n1. ok\n"
        "## Current State\n- ok\n## Key Details\n- path/a.py\n"
        "## Errors and Fixes\n- 无\n## Next Step\n- continue"
    )
    usage: dict[str, int] = field(
        default_factory=lambda: {
            "prompt_tokens": 12_000,
            "completion_tokens": 200,
            "total_tokens": 12_200,
        }
    )
    # After N failures (non-200 or empty), auto-succeed
    fail_remaining: int = 0
    fail_status: int = 500
    request_log: list[dict[str, Any]] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def configure(self, **kwargs: Any) -> None:
        with self.lock:
            for k, v in kwargs.items():
                if not hasattr(self, k):
                    raise AttributeError(k)
                setattr(self, k, v)

    def snapshot_log(self) -> list[dict[str, Any]]:
        with self.lock:
            return list(self.request_log)


def make_handler(state: MockGatewayState):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args) -> None:  # quiet
            return

        def do_GET(self) -> None:
            body = json.dumps({"object": "list", "data": []}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:
            n = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(n)
            try:
                payload = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                payload = {"_raw": raw.decode("utf-8", errors="replace")[:200]}

            with state.lock:
                state.request_log.append(
                    {
                        "path": self.path,
                        "payload": payload,
                        "headers": {k: v for k, v in self.headers.items()},
                    }
                )
                delay = state.delay_s
                status = state.status
                empty = state.empty_choices
                content = state.content
                usage = dict(state.usage)
                if state.fail_remaining > 0:
                    status = state.fail_status
                    state.fail_remaining -= 1
                    # keep failing until exhausted
                elif state.fail_remaining == 0 and state.status != 200:
                    # if permanently configured non-200, use that
                    pass

            if delay:
                time.sleep(delay)

            if status != 200:
                err = json.dumps({"error": {"message": f"mock {status}", "type": "mock"}}).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(err)))
                self.end_headers()
                self.wfile.write(err)
                return

            if empty:
                resp = {"choices": [], "usage": usage}
            else:
                resp = {
                    "id": "chatcmpl-mock",
                    "object": "chat.completion",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": content},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": usage,
                }
            data = json.dumps(resp).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    return Handler


@dataclass
class MockGateway:
    state: MockGatewayState
    base_url: str  # e.g. http://127.0.0.1:PORT/v1
    server: HTTPServer
    thread: threading.Thread

    def stop(self) -> None:
        self.server.shutdown()
        self.thread.join(timeout=5)


def start_mock_gateway(host: str = "127.0.0.1", port: int = 0) -> MockGateway:
    state = MockGatewayState()
    server = HTTPServer((host, port), make_handler(state))
    real_port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://{host}:{real_port}/v1"
    return MockGateway(state=state, base_url=base, server=server, thread=thread)
