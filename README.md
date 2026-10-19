# dsh-compact

**English** | [中文](README.zh-CN.md)

Local reverse proxy between [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) (or any OpenAI-compatible client) and your LLM gateway. It **keeps recent turns verbatim** and **summarizes older context**, so the same chat can continue without `ContextWindowExceededError`.

```text
Client  →  http://127.0.0.1:4001/v1  →  dsh-compact  →  your OpenAI-compatible upstream
```

> **Daily start:** [Mac](HOW_TO_START_MAC.md) · [Windows](HOW_TO_START_WINDOWS.md)

---

## Set up with a coding agent

Copy this into your coding agent:

```text
Install and configure dsh-compact for local use on this machine.

1. Detect OS (macOS / Windows) and show a short setup plan; wait for my approval before changing anything.
2. Create ~/.dsh-compact/venv (or Windows equivalent), pip install -r requirements.txt.
3. Ask me for COMPANY_LLM_BASE, COMPANY_LLM_MODEL, and COMPANY_LLM_KEY (optional: DASH_COMPACT_SUMMARIZE_*).
   Write them to ~/.dsh-compact/env.sh on macOS (chmod 600) or User-level environment variables on Windows.
   Never commit secrets to git.
4. Follow HOW_TO_START_MAC.md or HOW_TO_START_WINDOWS.md; start `python proxy.py` (background OK).
5. Verify with: curl http://127.0.0.1:4001/health  — expect enabled=true and the configured upstream.
6. Tell me to set the client (DeepSeek Harness / DSH) Base URL to http://127.0.0.1:4001/v1,
   API protocol = OpenAI Chat Completions, then Apply and restart the session.
```

You still provide API keys and click Apply in the client UI; the agent installs the local proxy and verifies `/health`.

---

## Features

- **Invisible compaction**: past the threshold, older messages are summarized in the background; recent turns stay verbatim and are injected on the next request
- **Transparent proxy**: rewrites outbound request bodies only; never mutates local transcripts; binds to `127.0.0.1` only
- **Free first pass**: strip base64 images / huge dumps; shrink old tool results (micro-compact)
- **Resilient**: chunked summarization, 429 retries, cooldown and circuit breaker
- **Emergency CLI**: if a session cannot open, `compact.py` produces a pasteable `brief.md`

---

## Architecture

No fork, no npm, no TLS termination. A plain HTTP reverse proxy on `127.0.0.1` runs **keep-recent / summarize-old + rolling merge** on OpenAI `/v1/chat/completions`. Offline CLI is emergency-only.

```text
                 ┌──────────────────────────────────────────────────────────┐
Client           │  dsh-compact local proxy  127.0.0.1:4001                  │
  │              │                                                          │
  │ POST /v1/chat/completions                                               │
  │ (Base URL    │  1. Read messages + estimate tokens (prefer real usage)  │
  │  → :4001)    │  2. strip: base64 images → placeholders (strip_heavy)    │
  ▼              │  3. micro: old tool results → placeholders (free)        │
                 │  4. Over threshold? Summarize old msgs → rolling merge   │
                 │     Keep recent turns verbatim                           │
Upstream LLM     │  5. Send [rolling summary] + [recent] upstream           │
 OpenAI-compat   │  6. Stream response back to client (no full buffer)      │
                 │                                                          │
                 │  · In-memory state; match cache by content hash          │
                 │  · Prefer async compress: allow this turn, inject next   │
                 └──────────────────────────────────────────────────────────┘
```

| Mode | Artifact | Trigger | Role |
| --- | --- | --- | --- |
| Primary: local proxy | `proxy.py` | Auto when tokens exceed threshold | Continue same session |
| Emergency: offline CLI | `compact.py` | Manual | Produce `brief.md` handoff |

**Invariants:**

- Only rewrites the body sent upstream; local transcript is **never** modified
- Loopback only; no TLS MITM
- Special-cases `/v1/chat/completions`; other paths pass through; unknown fields preserved
- Protocol is **OpenAI Chat Completions** (`tool_calls` + `role:"tool"`)

**Defaults (tune to your window):** `TRIGGER=180000`, `TARGET=80000`; session identity via messages content-hash.

### Request pipeline

```text
Estimate tokens each request
  → under TRIGGER: forward as-is
  → over threshold:
       1) micro: old tool_result → placeholder (no LLM)
       2) summarize old messages only → merge into rolling summary
       3) keep recent messages verbatim
       4) upstream payload = [rolling summary] + [recent]
  → prefer async: allow current turn, inject on the next
```

Logs with `Compression #N`, `injected=True`, and `tokens≈large->small` mean compaction worked.

---

## Requirements

- Python 3.10+
- Any OpenAI-compatible `/v1/chat/completions` upstream
- Client can point Base URL at localhost (**OpenAI Chat Completions** protocol)

---

## Quick Start

```bash
pip install -r requirements.txt

export COMPANY_LLM_BASE="https://api.example.com/v1"   # your upstream
export COMPANY_LLM_MODEL="your-model"
export COMPANY_LLM_KEY="sk-..."                        # summarizer fallback; chat key may live only in the client
# Recommended: separate summarizer credentials
# export DASH_COMPACT_SUMMARIZE_BASE=... MODEL=... KEY=...

python proxy.py
# curl http://127.0.0.1:4001/health
```

In the client:

| Field | Value |
| --- | --- |
| Base URL | `http://127.0.0.1:4001/v1` |
| API protocol | OpenAI Chat Completions |
| API Key | Real upstream key |
| Model | Same as upstream |

Apply, then restart the session. Full steps: [HOW_TO_START_MAC.md](HOW_TO_START_MAC.md) / [HOW_TO_START_WINDOWS.md](HOW_TO_START_WINDOWS.md).

---

## Configuration

Read secrets from environment variables only; never commit them.

| Variable | Meaning |
| --- | --- |
| `COMPANY_LLM_BASE` | Chat upstream Base URL (required) |
| `COMPANY_LLM_MODEL` | Upstream model name |
| `COMPANY_LLM_KEY` | Fallback key when summarizer vars unset |
| `DASH_COMPACT_SUMMARIZE_*` | Optional summarizer BASE / MODEL / KEY |
| `DASH_COMPACT_TRIGGER` | Trigger line, default `180000` |
| `DASH_COMPACT_TARGET` | Post-compact recent-context budget, default `80000` |
| `DASH_COMPACT_PORT` | Local port, default `4001` |
| `DASH_COMPACT_DISABLE` | `1` = compaction off, pass-through only |

---

## Emergency CLI

If a session cannot open:

```bash
python compact.py /path/to/session.jsonl -o brief.md
```

Paste `brief.md` into a new session to continue.

---

## Layout

```text
proxy.py                 # local HTTP proxy (main entry)
engine.py / prompt.txt   # keep-recent + rolling merge
tokens.py / strip_heavy.py / micro_compact.py
compact.py / session_reader.py   # emergency CLI
tests/
```

---

## Credits

Strategy aligned with [claude-rolling-context](https://github.com/nodenestor/claude-rolling-context) (transparent proxy, keep-recent, async inject). Engine ideas from [contextkit](https://github.com/drandrewlaw/contextkit) (thresholds, micro-compact, circuit breaker). This repo is a Python reimplementation for OpenAI Chat Completions—not a fork.
