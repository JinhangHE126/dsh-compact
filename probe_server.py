"""
Phase 0 抓包假服务器 —— 仅用于验证 Gate A / B / D，不进产品。

用法：
  python probe_server.py
  # 另开终端：把 DSH 的 API base 改成 http://127.0.0.1:4001/v1 ，重启 DSH，发两轮消息

会把每个 POST body 存成 req_HHMMSS.bin，并在控制台打印全部 header。
假 OpenAI 响应让 DSH 不立刻报错（内容固定为 probe-ok）。
"""

from __future__ import annotations

import json
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

HOST = "127.0.0.1"
PORT = 4001
OUT_DIR = Path(__file__).resolve().parent / "probe_captures"
OUT_DIR.mkdir(exist_ok=True)


class ProbeHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        print(f"[{self.log_date_time_string()}] {fmt % args}")

    def do_GET(self) -> None:
        body = json.dumps({"ok": True, "object": "list", "data": []}).encode()
        self._reply(200, body)

    def do_POST(self) -> None:
        n = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(n)
        ts = time.strftime("%H%M%S")
        path = OUT_DIR / f"req_{ts}.bin"
        path.write_bytes(raw)

        print("\n" + "=" * 60)
        print(f"PATH   {self.command} {self.path}")
        print(f"BODY   -> {path} ({len(raw)} bytes)")
        print("--- headers ---")
        for k, v in self.headers.items():
            print(f"{k}: {v}")
        print("--- body preview (first 800 chars) ---")
        try:
            preview = raw.decode("utf-8", errors="replace")[:800]
        except Exception:
            preview = repr(raw[:200])
        print(preview)
        print("=" * 60 + "\n")

        # 解析 messages 数量，方便 Gate B 肉眼核对
        try:
            data = json.loads(raw)
            msgs = data.get("messages") or []
            print(
                f"[parse] messages={len(msgs)} "
                f"roles={[m.get('role') for m in msgs]}"
            )
            if msgs:
                last = msgs[-1]
                content = last.get("content")
                if isinstance(content, str):
                    snippet = content[:120]
                else:
                    snippet = str(content)[:120]
                print(f"[parse] last role={last.get('role')} content={snippet!r}")
        except Exception as e:
            print(f"[parse] not JSON / parse error: {e}")

        resp = {
            "id": "chatcmpl-probe",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": "probe",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "probe-ok"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 2,
                "total_tokens": 12,
            },
        }
        self._reply(200, json.dumps(resp).encode())

    def _reply(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    print(f"Phase-0 probe listening on http://{HOST}:{PORT}")
    print(f"Captures -> {OUT_DIR}")
    print("Set DSH API base to http://127.0.0.1:4001/v1 , restart DSH, send 2 turns.")
    HTTPServer((HOST, PORT), ProbeHandler).serve_forever()


if __name__ == "__main__":
    main()
