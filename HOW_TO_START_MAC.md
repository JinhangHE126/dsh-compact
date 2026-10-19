# dsh-compact: macOS terminal start (daily)

**English** | [中文](HOW_TO_START_MAC.zh-CN.md)

Full install from scratch: [`HOW_TO_DEPLOY_MAC.md`](HOW_TO_DEPLOY_MAC.md) (local/internal).  
This page covers **start / stop / health** after install.

Windows → [`HOW_TO_START_WINDOWS.md`](HOW_TO_START_WINDOWS.md).

---

## Prerequisites (once)

- Repo on disk, e.g. `~/Desktop/Projects/dsh-compact`
- venv at `~/.dsh-compact/venv`
- Secrets file `~/.dsh-compact/env.sh` (`chmod 600`)

Example `env.sh` (home DeepSeek; company intranet → your gateway + model):

```bash
export COMPANY_LLM_BASE="https://api.deepseek.com/v1"
export DASH_COMPACT_SUMMARIZE_BASE="https://api.deepseek.com/v1"
export DASH_COMPACT_SUMMARIZE_MODEL="deepseek-chat"
export DASH_COMPACT_SUMMARIZE_KEY="sk-your-key"
export DASH_COMPACT_TRIGGER=180000
export DASH_COMPACT_TARGET=80000
export DASH_COMPACT_PORT=4001
```

---

## Start each time

Replace the repo path with yours:

```bash
source ~/.dsh-compact/venv/bin/activate
cd ~/Desktop/Projects/dsh-compact
source ~/.dsh-compact/env.sh
python proxy.py
```

Expected output:

```text
dsh-compact proxy v0.4.0 on http://127.0.0.1:4001
  upstream=https://api.deepseek.com/v1
  summarizer=... separate=True
  trigger=180000 target=80000
```

**Another terminal** for health:

```bash
curl -s http://127.0.0.1:4001/health | python3 -m json.tool
```

Check: `enabled: true`, and `trigger` / `target` look right.

---

## Harness / DSH settings

| Field | Value |
| --- | --- |
| Base URL | `http://127.0.0.1:4001/v1` |
| API protocol | **OpenAI Chat Completions** (not Anthropic Messages) |
| API Key | Real upstream key |
| Model | e.g. `deepseek-chat` or `company-code` |

Apply, then restart the session. Only route the models that should go through the proxy.

---

## Stop / port in use

In the `python proxy.py` terminal: `Ctrl+C`.

If the port is busy:

```bash
lsof -iTCP:4001 -sTCP:LISTEN
kill <PID>
```

---

## Verify it works

Foreground logs or `~/.dsh-compact/logs/proxy.log`:

| Log | Meaning |
| --- | --- |
| `[MSG] Forwarding ... 200` | Pass-through OK |
| `Compression #N` | Background compress finished |
| `injected=True` and `tokens≈large->small` | Injection applied |
| `injected=False` under trigger | Normal; not compacting yet |

Hand-test compression (then restore and restart):

```bash
export DASH_COMPACT_TRIGGER=50000
export DASH_COMPACT_TARGET=20000
```

---

## Optional: login autostart

See [`HOW_TO_DEPLOY_MAC.md`](HOW_TO_DEPLOY_MAC.md) §8 (LaunchAgent).  
`run_proxy.sh` must `cd` to your real repo path.

---

## Common pitfalls

1. **Wrong protocol**: built-in DeepSeek Messages → `/v1/messages` → 404. Use OpenAI Chat Completions.
2. **IDE terminal missing env**: always `source ~/.dsh-compact/env.sh` before start.
3. **Intranet gateway**: use VPN or switch `COMPANY_LLM_BASE` to a public API.
