# dsh-compact deploy guide (macOS)

**English** | [中文](HOW_TO_DEPLOY_MAC.zh-CN.md)

From-zero install → foreground test → optional login autostart.  
Windows → [`HOW_TO_DEPLOY.md`](HOW_TO_DEPLOY.md). Product overview → [`README.md`](README.md).

> Repo `scripts/*.ps1` are Windows-only. Use the shell / LaunchAgent steps below on Mac.

**Already installed?** [`HOW_TO_START_MAC.md`](HOW_TO_START_MAC.md)

---

## 1. What it does

Local transparent proxy (`127.0.0.1:4001`) between the client and your LLM upstream:

```text
Client  →  http://127.0.0.1:4001/v1  →  dsh-compact  →  company gateway or public API
```

Past the threshold it summarizes older messages and keeps recent turns. Loopback only; does not rewrite local session files.

---

## 2. Checklist

| Item | Requirement |
| --- | --- |
| OS | macOS 12+ (Intel / Apple Silicon) |
| Python | 3.10+ (`python3 --version`) |
| Network | Reach your upstream (VPN if intranet) |
| Chat key | Virtual Key in the client |
| Summarizer key | Recommended separate credential (e.g. DeepSeek) |
| Repo | Cloned locally |

```bash
python3 --version   # need 3.10+
```

---

## 3. Install deps (once)

```bash
cd ~/Desktop/Projects/dsh-compact   # your path

mkdir -p ~/.dsh-compact/{logs,samples,notes}

python3 -m venv ~/.dsh-compact/venv
source ~/.dsh-compact/venv/bin/activate
pip install -U pip
pip install -r requirements.txt

# optional
pytest tests -q
```

---

## 4. Environment

In the **same** shell that will run the proxy (or via `~/.dsh-compact/env.sh`).

### Chat upstream

```bash
export COMPANY_LLM_BASE="http://llm-gateway.example:4000/v1"
export COMPANY_LLM_MODEL="company-code"
export COMPANY_LLM_KEY="sk-your-company-key"
```

### Summarizer (recommended)

```bash
export DASH_COMPACT_SUMMARIZE_BASE="https://api.deepseek.com/v1"
export DASH_COMPACT_SUMMARIZE_MODEL="deepseek-chat"
export DASH_COMPACT_SUMMARIZE_KEY="sk-your-deepseek-key"
```

### Policy

```bash
export DASH_COMPACT_PORT=4001
export DASH_COMPACT_TRIGGER=180000
export DASH_COMPACT_TARGET=80000
export DASH_COMPACT_MAX_CONCURRENT=1
export DASH_COMPACT_DISABLE=0
```

Hand-test compression: temporarily `TRIGGER=50000` / `TARGET=20000`, then restore.

Recommended secrets file:

```bash
# ~/.dsh-compact/env.sh  (chmod 600)
```

---

## 5. Foreground start

```bash
source ~/.dsh-compact/venv/bin/activate
cd ~/Desktop/Projects/dsh-compact
source ~/.dsh-compact/env.sh
python proxy.py
```

Health (other terminal):

```bash
curl -s http://127.0.0.1:4001/health | python3 -m json.tool
```

Port busy:

```bash
lsof -iTCP:4001 -sTCP:LISTEN
kill <PID>
```

---

## 6. Client settings

| Field | Value |
| --- | --- |
| Base URL | `http://127.0.0.1:4001/v1` |
| API protocol | **OpenAI Chat Completions** |
| API Key | Chat upstream key |
| Model | As required by upstream |

Apply + restart session.

---

## 7. Verify

| Log | Meaning |
| --- | --- |
| `[MSG] Forwarding ... 200` | Pass-through OK |
| `Compression #N` | Compress finished |
| `injected=True` | Injection applied |

---

## 8. Optional LaunchAgent

1. `~/.dsh-compact/run_proxy.sh` sourcing `env.sh`, activating venv, `cd` to your **real** repo path, `exec python proxy.py`
2. `~/Library/LaunchAgents/com.dsh-compact.proxy.plist` with `RunAtLoad` + `KeepAlive`
3. `launchctl load ...` then `curl` health

Do not load LaunchAgent while a foreground `python proxy.py` already holds `:4001`.

Unload:

```bash
launchctl unload ~/Library/LaunchAgents/com.dsh-compact.proxy.plist
rm -f ~/Library/LaunchAgents/com.dsh-compact.proxy.plist
```

Full copy-paste blocks: see Chinese guide or expand from [`HOW_TO_START_MAC.md`](HOW_TO_START_MAC.md).

---

## 9. FAQ (Mac)

- **`summarizer_separate: false`:** `DASH_COMPACT_SUMMARIZE_*` missing in the process env.
- **Cannot reach intranet host:** VPN; or use a public BASE.
- **Apple Silicon pip issues:** ensure `which python` → `~/.dsh-compact/venv/bin/python`.
- **Session already broken:** `python compact.py session.jsonl -o brief.md`
