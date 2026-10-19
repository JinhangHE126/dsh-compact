# dsh-compact: Windows start (daily)

**English** | [中文](HOW_TO_START_WINDOWS.zh-CN.md)

One-shot install from scratch: [`HOW_TO_DEPLOY.md`](HOW_TO_DEPLOY.md).  
This page covers **start / stop / autostart / health** after install.

Mac → [`HOW_TO_START_MAC.md`](HOW_TO_START_MAC.md).

---

## Prerequisites (once)

- Repo on disk, e.g. `C:\Users\<you>\Desktop\dsh-compact`
- Ran `scripts\setup_home.ps1` (venv + dirs + production thresholds)
- Secrets in **User**-scoped environment variables (needed for login autostart)

### Company intranet (example)

```powershell
[Environment]::SetEnvironmentVariable("COMPANY_LLM_KEY",   "sk-your-chat-key", "User")
[Environment]::SetEnvironmentVariable("COMPANY_LLM_BASE",  "http://llm-gateway.example:4000/v1", "User")
[Environment]::SetEnvironmentVariable("COMPANY_LLM_MODEL", "company-code", "User")
[Environment]::SetEnvironmentVariable("DASH_COMPACT_SUMMARIZE_KEY",   "sk-your-summary-key", "User")
[Environment]::SetEnvironmentVariable("DASH_COMPACT_SUMMARIZE_BASE",  "http://llm-gateway.example:4000/v1", "User")
[Environment]::SetEnvironmentVariable("DASH_COMPACT_SUMMARIZE_MODEL", "company-code", "User")
```

### Public API (e.g. DeepSeek)

Set `BASE` to `https://api.deepseek.com/v1`, `MODEL` to `deepseek-chat`, summarizer key = DeepSeek key.

After changing User env vars: **open a new** PowerShell, then start the proxy.

---

## Mode A: foreground terminal (debug)

```powershell
cd C:\Users\<you>\Desktop\dsh-compact

# Free port 4001 if needed
Get-NetTCPConnection -LocalPort 4001 -ErrorAction SilentlyContinue |
  ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }

& "$env:USERPROFILE\.dsh-compact\venv\Scripts\python.exe" .\proxy.py
# or: python .\proxy.py
```

Expected:

```text
dsh-compact proxy v0.4.0 on http://127.0.0.1:4001
  trigger=180000 target=80000
```

**New** PowerShell:

```powershell
curl.exe http://127.0.0.1:4001/health
```

Stop: `Ctrl+C` in the proxy window.

---

## Mode B: login autostart (recommended)

```powershell
cd C:\Users\<you>\Desktop\dsh-compact
powershell -ExecutionPolicy Bypass -File .\scripts\setup_home.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\install_autostart.ps1

Start-Process -FilePath "$env:USERPROFILE\.dsh-compact\run_proxy_task.cmd"

# New terminal to verify
curl.exe http://127.0.0.1:4001/health
```

What autostart registers:

- Registry `HKCU\...\Run` → `dsh-compact-proxy`
- Startup folder `.cmd`
- Optional scheduled task (skipped if no permission)

Uninstall:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\uninstall_autostart.ps1
```

Logs: `%USERPROFILE%\.dsh-compact\logs\proxy.log`

---

## DSH / Harness settings

| Field | Value |
| --- | --- |
| Base URL | `http://127.0.0.1:4001/v1` |
| API protocol | **OpenAI Chat Completions** (if selectable) |
| API Key | Chat Virtual Key / DeepSeek key |
| Model | `company-code` or `deepseek-chat` |

Apply, then **restart session / restart DSH**.

---

## Verify it works

| Log / signal | Meaning |
| --- | --- |
| `[MSG] Forwarding ... 200` | Pass-through OK |
| `Compression #N` | Background compress finished |
| `injected=True` and `tokens≈large->small` | Injection applied |
| `injected=False` under trigger | Normal; not compacting yet |

Hand-test: temporarily set User `DASH_COMPACT_TRIGGER` to `50000`, **new terminal + restart proxy**; restore `180000` after.

Disable compaction (still pass-through):

```powershell
[Environment]::SetEnvironmentVariable("DASH_COMPACT_DISABLE", "1", "User")
# restart proxy; /health shows enabled=false
```

Set back to `"0"` and restart.

---

## Common pitfalls

1. **Only `$env:FOO=...`**: current shell only; login autostart will not see it → use `SetEnvironmentVariable(..., "User")`.
2. **Changed User env but no new shell**: old process keeps old values.
3. **Cannot reach intranet gateway**: company network / VPN, or switch to a public `BASE`.
4. **gzip / TRANSPORT**: if the client sends `Accept-Encoding: gzip`, use a `proxy.py` that strips `Content-Encoding` after decode (current repo HEAD).
