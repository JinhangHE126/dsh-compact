# dsh-compact deploy guide (Windows)

**English** | [中文](HOW_TO_DEPLOY.zh-CN.md)

From-zero install → daily use → troubleshooting. Product overview: [`README.md`](README.md).

> **Already installed, just start:** [`HOW_TO_START_WINDOWS.md`](HOW_TO_START_WINDOWS.md)  
> **macOS:** [`HOW_TO_START_MAC.md`](HOW_TO_START_MAC.md) / [`HOW_TO_DEPLOY_MAC.md`](HOW_TO_DEPLOY_MAC.md) (no `.ps1` scripts).

---

## 1. What it does

Local transparent proxy on `127.0.0.1:4001` between **DeepSeek Harness (DSH)** and your LLM gateway:

```text
DSH  →  http://127.0.0.1:4001/v1  →  dsh-compact  →  http://llm-gateway.example:4000/v1
```

Near the token threshold it summarizes **older** messages in the background and keeps **recent** turns verbatim. Same chat continues—no new session / paste brief.

- Rewrites outbound request bodies only; **does not** edit DSH local transcripts
- Loopback only
- Secrets in environment variables only—**never** commit them

---

## 2. Checklist

| Item | Requirement |
| --- | --- |
| OS | Windows 10/11 |
| Python | 3.10+ (`py` / `python`) |
| Network | Reach your gateway (intranet / VPN as needed) |
| Chat key | Employee Virtual Key in DSH; **not** Master Key |
| Summarizer key (recommended) | **Separate** key so summarize does not steal chat parallel quota |
| This repo | Cloned locally, e.g. `C:\Users\<you>\Desktop\dsh-compact` |

---

## 3. One-shot deploy (recommended)

In **PowerShell** (replace paths and keys):

```powershell
cd C:\Users\<you>\Desktop\dsh-compact

[Environment]::SetEnvironmentVariable("COMPANY_LLM_KEY",   "sk-your-chat-key", "User")
[Environment]::SetEnvironmentVariable("COMPANY_LLM_BASE",  "http://llm-gateway.example:4000/v1", "User")
[Environment]::SetEnvironmentVariable("COMPANY_LLM_MODEL", "company-code", "User")

[Environment]::SetEnvironmentVariable("DASH_COMPACT_SUMMARIZE_KEY",   "sk-your-summary-key", "User")
[Environment]::SetEnvironmentVariable("DASH_COMPACT_SUMMARIZE_BASE",  "http://llm-gateway.example:4000/v1", "User")
[Environment]::SetEnvironmentVariable("DASH_COMPACT_SUMMARIZE_MODEL", "company-code", "User")

powershell -ExecutionPolicy Bypass -File .\scripts\setup_home.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\install_autostart.ps1

Start-Process -FilePath "$env:USERPROFILE\.dsh-compact\run_proxy_task.cmd"

# New PowerShell window (so User env is visible)
curl.exe http://127.0.0.1:4001/health
```

Expect roughly:

```json
{
  "service": "dsh-compact",
  "version": "0.4.0",
  "enabled": true,
  "upstream": "http://llm-gateway.example:4000/v1",
  "summarizer_separate": true,
  "trigger": 180000,
  "target": 80000
}
```

---

## 4. Configure DSH

1. Settings → Models (CompanyCode / custom OpenAI provider)
2. Base URL → `http://127.0.0.1:4001/v1`
3. API Key → chat Virtual Key
4. Protocol → **OpenAI Chat Completions** if selectable
5. Apply + **restart session / DSH**

---

## 5. Verify

```powershell
curl.exe http://127.0.0.1:4001/health
curl.exe http://127.0.0.1:4001/stats
```

Logs: `%USERPROFILE%\.dsh-compact\logs\proxy.log`

| Log | Meaning |
| --- | --- |
| `Compression #N` | Background summary done |
| `injected=True` and `tokens≈A->B` | Injection applied |
| `injected=False` | No cache hit / under trigger |

---

## 6. Daily ops

Foreground debug:

```powershell
cd C:\Users\<you>\Desktop\dsh-compact
Get-NetTCPConnection -LocalPort 4001 -ErrorAction SilentlyContinue |
  ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
python .\proxy.py
```

After changing User env: **new** terminal + restart proxy.

Disable compaction: `DASH_COMPACT_DISABLE=1` (User), restart proxy.

Uninstall autostart:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\uninstall_autostart.ps1
```

---

## 7. Environment variables

| Variable | Required | Notes |
| --- | --- | --- |
| `COMPANY_LLM_KEY` | Recommended | Summarizer fallback; chat auth uses DSH headers |
| `COMPANY_LLM_BASE` | Recommended | Default placeholder gateway URL |
| `COMPANY_LLM_MODEL` | Recommended | e.g. `company-code` |
| `DASH_COMPACT_SUMMARIZE_KEY` | Strongly recommended | Separate from chat key |
| `DASH_COMPACT_TRIGGER` | Prod | `180000` |
| `DASH_COMPACT_TARGET` | Prod | `80000` |
| `DASH_COMPACT_PORT` | Optional | `4001` |
| `DASH_COMPACT_DISABLE` | Optional | `1` = pass-through |

Hand-test with lower TRIGGER, then restore `180000`.

---

## 8. FAQ

- **User env not picked up:** open a new shell after `SetEnvironmentVariable`.
- **Cannot reach gateway:** VPN / company network, or point BASE at a public API.
- **TRANSPORT / gzip:** use current `proxy.py` that strips `Content-Encoding` after httpx decode.
- **429 max_parallel_requests:** use a separate summarizer key.

More detail: [`HOW_TO_START_WINDOWS.md`](HOW_TO_START_WINDOWS.md).
