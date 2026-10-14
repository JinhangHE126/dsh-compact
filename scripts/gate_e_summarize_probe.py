"""
Gate E：打一次分片量级的摘要请求，测耗时与 usage。

需要环境变量：
  COMPANY_LLM_KEY / COMPANY_LLM_BASE / COMPANY_LLM_MODEL

用法：
  python scripts/gate_e_summarize_probe.py
"""

from __future__ import annotations

import os
import sys
import time

try:
    import httpx
except ImportError:
    print("Install httpx first: pip install httpx")
    sys.exit(1)


def main() -> int:
    base = (os.environ.get("COMPANY_LLM_BASE") or "").rstrip("/")
    key = os.environ.get("COMPANY_LLM_KEY") or ""
    model = os.environ.get("COMPANY_LLM_MODEL") or ""
    if not (base and key and model):
        print("Missing COMPANY_LLM_KEY / COMPANY_LLM_BASE / COMPANY_LLM_MODEL")
        return 1

    # ~60k chars：对齐 DEPLOYMENT 单段输入上限
    blob = ("这是用于压测摘要延迟的填充文本。文件 path/to/foo.py 曾改过。\n" * 3000)
    prompt = (
        "请用中文写一份结构化技术摘要（目标/结论/路径/未完成/坑点），尽量短。\n\n"
        + blob
    )
    print(f"prompt chars ≈ {len(prompt):,}")
    print(f"POST {base}/chat/completions model={model}")

    t0 = time.time()
    try:
        r = httpx.post(
            f"{base}/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 4000,
            },
            timeout=600.0,
        )
    except Exception as e:
        print(f"request failed: {e}")
        return 2

    dt = time.time() - t0
    print(f"status={r.status_code} elapsed_s={dt:.1f}")
    if r.status_code != 200:
        print(r.text[:800])
        return 3

    data = r.json()
    usage = data.get("usage") or {}
    content = (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""
    print(f"usage={usage}")
    print(f"content_len={len(content)}")
    print()
    print("填进 notes/phase0-checklist.md 的 Gate E：")
    print(f"  elapsed_s = {dt:.1f}")
    print(f"  prompt_tokens = {usage.get('prompt_tokens')}")
    print(f"  completion_tokens = {usage.get('completion_tokens')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
