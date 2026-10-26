# dsh-compact

**English** | [中文](README.zh-CN.md)

Local reverse proxy between [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) (or any OpenAI-compatible client) and your LLM gateway. It **keeps recent turns verbatim** and **summarizes older context**, so the same chat can continue without `ContextWindowExceededError`.

```text
Client  →  http://127.0.0.1:4001/v1  →  dsh-compact  →  your OpenAI-compatible upstream
```

Daily use: start `python proxy.py` in a **foreground terminal** (see Quick Start below).

---

## Set up with a coding agent

Copy this into your coding agent:

```text
Install and configure dsh-compact for local use on this machine.

1. Detect OS (macOS / Windows) and show a short setup plan; wait for my approval before changing anything.
2. Create ~/.dsh-compact/venv (or Windows %USERPROFILE%\.dsh-compact\venv), pip install -r requirements.txt.
3. Ask me for COMPANY_LLM_BASE, COMPANY_LLM_MODEL, and COMPANY_LLM_KEY (optional: DASH_COMPACT_SUMMARIZE_*).
   Write them to ~/.dsh-compact/env.sh on macOS (chmod 600) or User-level environment variables on Windows.
   Never commit secrets to git.
4. Start with: python proxy.py (foreground in a terminal).
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

**Defaults (tune to your window):** `TRIGGER=180000`, `TARGET=80000`, `HARD_LIMIT=240000`; session identity via messages content-hash. Token estimates count CJK, `reasoning_content`, and `tools` schemas; a large request body can also force compaction (`DASH_COMPACT_BODY_TRIGGER_BYTES`).

### Request pipeline

```text
Estimate tokens each request (messages + tools; CJK / reasoning aware)
  → under TRIGGER and body under BODY_TRIGGER: forward as-is
  → over threshold (or large body):
       1) micro: old tool_result → placeholder (no LLM)
       2) summarize old messages only → merge into rolling summary
       3) keep recent messages verbatim
       4) upstream payload = [rolling summary] + [recent]
  → prefer async: allow current turn, inject on the next
  → at HARD_LIMIT (or large body past TRIGGER): sync-compress before forward
```

Logs with `Compression #N`, `injected=True`, and `tokens≈large->small` mean compaction worked.

### Bench snapshot

Synthetic fixtures and a local token estimate (`python -m bench.free_layer`). `free` is the share left after strip + micro. `post` is the size after a summary cut; `end` is `post / raw`. A dash means that cell was not run. Image rows are mostly base64 padding, so their drop is larger than a real screenshot session.

| Sample | raw | strip | micro | free | post | end | recall | headings |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| chat_short | 10500 | 10500 | 10500 | 1.000 | — | — | — | — |
| chat_over_trigger | 195000 | 195000 | 195000 | 1.000 | 97965 | 0.503 | 5/5 | 7/7 |
| chat_near_hard_limit | 233999 | 233999 | 233999 | 1.000 | 87858 | 0.375 | — | — |
| tools_short | 6170 | 6170 | 4094 | 0.664 | — | — | — | — |
| tools_over_trigger | 195000 | 21770 | 13844 | 0.071 | 10978 | 0.056 | — | — |
| tools_near_hard_limit | 233999 | 21770 | 13844 | 0.059 | 10978 | 0.047 | — | — |
| images_short | 10500 | 378 | 378 | 0.036 | — | — | — | — |
| images_over_trigger | 195000 | 378 | 378 | 0.002 | 162 | 0.001 | — | — |
| images_near_hard_limit | 233999 | 378 | 378 | 0.002 | 162 | 0.001 | — | — |

`recall` and `headings` are from a real summarizer (`python -m bench.quality`). Only `chat_over_trigger` has been scored: 195000 → 97965 in 5.88s, all 5 gold facts kept, all 7 `prompt.txt` headings present, `RECENT_ANCHOR` still verbatim, summary 1317 characters. Other `post` / `end` cells are the fake-summarizer size bench.

Plain chat has nothing for the free pass to remove; the summary brings ~190k–234k down to ~88k–98k (`TARGET=80000`). Tool dumps and base64 images shrink in the free pass, before the summary cut.

---

## Requirements

- Python 3.10+
- Any OpenAI-compatible `/v1/chat/completions` upstream
- Client can point Base URL at localhost (**OpenAI Chat Completions** protocol)

---

## Quick Start

### 1. Install (once)

```bash
python3 -m venv ~/.dsh-compact/venv          # Windows: py -3 -m venv %USERPROFILE%\.dsh-compact\venv
source ~/.dsh-compact/venv/bin/activate      # Windows: Activate.ps1 under that venv
cd /path/to/dsh-compact
pip install -r requirements.txt
```

On Windows you can also run: `powershell -ExecutionPolicy Bypass -File .\scripts\setup_home.ps1`.

### 2. Credentials (once)

Secrets from the environment only — **never commit them**.

**macOS** — `~/.dsh-compact/env.sh` (`chmod 600`):

```bash
export COMPANY_LLM_BASE="https://api.deepseek.com/v1"   # or your gateway
export COMPANY_LLM_MODEL="deepseek-chat"
export COMPANY_LLM_KEY="sk-..."
# Recommended: separate summarizer credentials
export DASH_COMPACT_SUMMARIZE_BASE="https://api.deepseek.com/v1"
export DASH_COMPACT_SUMMARIZE_MODEL="deepseek-chat"
export DASH_COMPACT_SUMMARIZE_KEY="sk-..."
export DASH_COMPACT_TRIGGER=180000
export DASH_COMPACT_TARGET=80000
export DASH_COMPACT_PORT=4001
```

**Windows** — **User-level** variables (open a **new** PowerShell afterward):

```powershell
[Environment]::SetEnvironmentVariable("COMPANY_LLM_BASE",  "https://api.deepseek.com/v1", "User")
[Environment]::SetEnvironmentVariable("COMPANY_LLM_MODEL", "deepseek-chat", "User")
[Environment]::SetEnvironmentVariable("COMPANY_LLM_KEY",   "sk-your-key", "User")
[Environment]::SetEnvironmentVariable("DASH_COMPACT_SUMMARIZE_BASE",  "https://api.deepseek.com/v1", "User")
[Environment]::SetEnvironmentVariable("DASH_COMPACT_SUMMARIZE_MODEL", "deepseek-chat", "User")
[Environment]::SetEnvironmentVariable("DASH_COMPACT_SUMMARIZE_KEY",   "sk-summarizer-key", "User")
```

### 3. Start from a terminal (every time)

**macOS:**

```bash
source ~/.dsh-compact/venv/bin/activate
cd /path/to/dsh-compact
source ~/.dsh-compact/env.sh
python proxy.py
```

**Windows (PowerShell):**

```powershell
cd C:\Users\<you>\Desktop\dsh-compact

# Free port 4001 if an old proxy is still listening
Get-NetTCPConnection -LocalPort 4001 -ErrorAction SilentlyContinue |
  ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }

& "$env:USERPROFILE\.dsh-compact\venv\Scripts\Activate.ps1"
$env:NO_PROXY = "127.0.0.1,localhost"
python .\proxy.py
```

Expect:

```text
dsh-compact proxy v0.4.0 on http://127.0.0.1:4001
  trigger=180000 target=80000
```

In **another** terminal:

```bash
curl http://127.0.0.1:4001/health          # Windows: curl.exe
```

Check `enabled: true` and that `/health` `pid` matches the banner. Stop with `Ctrl+C` in the proxy terminal.

### 4. Client (Harness / DSH)

| Field | Value |
| --- | --- |
| Base URL | `http://127.0.0.1:4001/v1` |
| API protocol | **OpenAI Chat Completions** (not Anthropic Messages) |
| API Key | Real upstream key |
| Model | Same as upstream |

Apply, then restart the session.

### 5. How to tell it is working

| Log / symptom | Meaning |
| --- | --- |
| `[MSG] Forwarding ... 200` | Pass-through OK |
| `Compression #N` | Background compress finished |
| `injected=True` and `tokens≈large->small` | Compacted payload in use |
| `injected=False` under trigger | Normal; not compacting yet |

To hand-test: temporarily set `DASH_COMPACT_TRIGGER=50000`, **new terminal + restart proxy**; restore `180000` after.

### Troubleshooting: port in use / no `[MSG]` in this terminal

Usually more than one `proxy.py` is running. Only the process that owns 4001 gets the live logs.

Fix: `curl` `/health` for `pid` → kill whatever owns 4001 → start **one** foreground `python proxy.py` → confirm `/health` `pid` matches the banner → send a short chat; `[MSG]` should appear in that terminal.

macOS: `lsof -iTCP:4001 -sTCP:LISTEN` then `kill <PID>`.  
Or follow the log file: `tail -f ~/.dsh-compact/logs/proxy.log` (Windows: `Get-Content ...\proxy.log -Wait -Tail 50`).

Optional helpers remain under `scripts/` (venv setup, autostart). Prefer a single foreground terminal for daily use.

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
| `DASH_COMPACT_HARD_LIMIT` | Sync-compress before forward above this estimate, default `240000` |
| `DASH_COMPACT_BODY_TRIGGER_BYTES` | Also compress when raw request body ≥ this many bytes, default `350000` (backs up token undercount) |
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
scripts/                 # optional: venv setup, autostart, etc.
tests/
```

---

## Credits

Strategy aligned with [claude-rolling-context](https://github.com/nodenestor/claude-rolling-context) (transparent proxy, keep-recent, async inject). Engine ideas from [contextkit](https://github.com/drandrewlaw/contextkit) (thresholds, micro-compact, circuit breaker). This repo is a Python reimplementation for OpenAI Chat Completions—not a fork.
