# dsh-compact：Windows 启动（日常）

从零一键安装见 [`HOW_TO_DEPLOY.md`](HOW_TO_DEPLOY.md)。  
本文只讲：**已经装好后，用终端启动 / 停止 / 自启 / 自检**。

Mac 同事 → [`HOW_TO_START_MAC.md`](HOW_TO_START_MAC.md)。

---

## 前提（做一次即可）

- 仓库在本机，例如：`C:\Users\<你>\Desktop\dsh-compact`
- 已跑过：`scripts\setup_home.ps1`（venv + 目录 + 生产阈值）
- 密钥写在 **User 级**环境变量（登录自启才能读到）

### 公司内网（默认）

```powershell
[Environment]::SetEnvironmentVariable("COMPANY_LLM_KEY",   "sk-你的聊天key", "User")
[Environment]::SetEnvironmentVariable("COMPANY_LLM_BASE",  "http://172.17.7.90:4000/v1", "User")
[Environment]::SetEnvironmentVariable("COMPANY_LLM_MODEL", "company-code", "User")
[Environment]::SetEnvironmentVariable("DASH_COMPACT_SUMMARIZE_KEY",   "sk-你的摘要key", "User")
[Environment]::SetEnvironmentVariable("DASH_COMPACT_SUMMARIZE_BASE",  "http://172.17.7.90:4000/v1", "User")
[Environment]::SetEnvironmentVariable("DASH_COMPACT_SUMMARIZE_MODEL", "company-code", "User")
```

### 家用公网（例如 DeepSeek）

把 `BASE` 改成 `https://api.deepseek.com/v1`，`MODEL` 改成 `deepseek-chat`，摘要 key 用 DeepSeek key。

改完 User 变量后：**新开** PowerShell，再启动代理。

---

## 方式 A：前台终端启动（调试用）

```powershell
cd C:\Users\<你>\Desktop\dsh-compact

# 若 4001 已被占用，先结束旧进程
Get-NetTCPConnection -LocalPort 4001 -ErrorAction SilentlyContinue |
  ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }

# 用 venv 或系统 python
& "$env:USERPROFILE\.dsh-compact\venv\Scripts\python.exe" .\proxy.py
# 或：python .\proxy.py
```

期望看到：

```text
dsh-compact proxy v0.4.0 on http://127.0.0.1:4001
  trigger=180000 target=80000
```

**新开**一个 PowerShell：

```powershell
curl.exe http://127.0.0.1:4001/health
```

停掉：前台窗口 `Ctrl+C`。

---

## 方式 B：登录自启（推荐日常）

```powershell
cd C:\Users\<你>\Desktop\dsh-compact
powershell -ExecutionPolicy Bypass -File .\scripts\setup_home.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\install_autostart.ps1

# 立刻启动（之后每次登录也会起）
Start-Process -FilePath "$env:USERPROFILE\.dsh-compact\run_proxy_task.cmd"

# 新开终端再验
curl.exe http://127.0.0.1:4001/health
```

自启做了什么：

- 注册表 `HKCU\...\Run` → `dsh-compact-proxy`
- Startup 文件夹放启动 `.cmd`
- 可选计划任务（没权限会跳过，不影响）

卸载自启：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\uninstall_autostart.ps1
```

日志：`%USERPROFILE%\.dsh-compact\logs\proxy.log`

---

## DSH / Harness 配置

| 项 | 值 |
| --- | --- |
| Base URL | `http://127.0.0.1:4001/v1` |
| API protocol | **OpenAI Chat Completions**（若客户端可选） |
| API Key | 聊天用 Virtual Key / DeepSeek key |
| 模型 | `company-code` 或 `deepseek-chat` |

Apply 后**重启会话 / 重启 DSH**。

---

## 怎么确认在工作

| 日志 / 现象 | 含义 |
| --- | --- |
| `[MSG] Forwarding ... 200` | 透传成功 |
| `Compression #N` | 后台压缩完成 |
| `injected=True` 且 `tokens≈大->小` | 已注入压缩结果 |
| `injected=False` 且未过 trigger | 正常，尚未压缩 |

手测压缩：临时把 User 级 `DASH_COMPACT_TRIGGER` 降到 `50000`，**新开终端 + 重启代理**；测完改回 `180000`。

临时关压缩（仍透传）：

```powershell
[Environment]::SetEnvironmentVariable("DASH_COMPACT_DISABLE", "1", "User")
# 重启代理；/health 里 enabled=false
```

改回 `"0"` 再重启。

---

## 常见坑

1. **只设了 `$env:FOO=...`**：当前窗口有效，登录自启读不到 → 必须用 `SetEnvironmentVariable(..., "User")`。  
2. **改完 User 变量不新开终端**：旧进程仍是旧值。  
3. **公司内网连不上**：需在公司网 / VPN；家里请改成公网 `BASE`。  
4. **gzip / TRANSPORT**：若客户端带 `Accept-Encoding: gzip` 曾报 TRANSPORT，请确认用的是含「丢弃 Content-Encoding」修复的 `proxy.py`（≥ 当前仓库 HEAD）。
