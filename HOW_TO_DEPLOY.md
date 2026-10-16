# dsh-compact 部署手册（Windows）

给同事的**从零安装 → 日常使用 → 排错**说明。设计细节见 [`DEPLOYMENT.md`](DEPLOYMENT.md)；验收勾选见 [`notes/phase5-checklist.md`](notes/phase5-checklist.md)。

---

## 1. 它是干什么的

在本机跑一个透明代理（`127.0.0.1:4001`），插在 **DeepSeek Harness（DSH）** 和 **公司 LiteLLM** 之间：

```text
DSH  →  http://127.0.0.1:4001/v1  →  dsh-compact  →  http://172.17.7.90:4000/v1
```

上下文接近阈值时，代理会在后台把**旧消息摘要压缩**，下一轮请求自动换成「滚动摘要 + 近期原文」，同一对话框可以继续聊，而不是开新会话粘贴 brief。

- 只改发往网关的请求，**不改** DSH 本地会话文件
- 只监听本机回环地址，不对外网开放
- 密钥只放在环境变量里，**不要写进仓库或脚本**

---

## 2. 你需要准备什么

| 项 | 要求 |
| --- | --- |
| 系统 | Windows 10/11 |
| Python | 3.10+（建议已装 `py` / `python`） |
| 网络 | 能访问内网 `172.17.7.90:4000`（公司网） |
| 聊天用 Key | 员工 Virtual Key（`sk-...`），填在 DSH 里；**不要用 Master Key** |
| 摘要用 Key（推荐） | **另一把** Virtual Key，给压缩摘要专用，避免抢聊天的并行配额（公司侧常见 `max_parallel_requests=2`） |
| 本仓库 | 已 clone / 拷贝到本机，例如 `C:\Users\<你>\Desktop\dsh-compact` |

---

## 3. 一键部署（推荐）

在 **PowerShell** 中执行（把路径和 key 换成你的）：

```powershell
cd C:\Users\<你>\Desktop\dsh-compact

# --- 1) 用户级环境变量（登录自启也能读到；不要只设 $env:） ---
# 聊天上游（摘要未单独配置时也会回退用这些）
[Environment]::SetEnvironmentVariable("COMPANY_LLM_KEY",   "sk-你的聊天key", "User")
[Environment]::SetEnvironmentVariable("COMPANY_LLM_BASE",  "http://172.17.7.90:4000/v1", "User")
[Environment]::SetEnvironmentVariable("COMPANY_LLM_MODEL", "company-code", "User")

# 强烈推荐：摘要单独一把 key（不抢聊天的 2 并行）
[Environment]::SetEnvironmentVariable("DASH_COMPACT_SUMMARIZE_KEY",   "sk-你的摘要key", "User")
[Environment]::SetEnvironmentVariable("DASH_COMPACT_SUMMARIZE_BASE",  "http://172.17.7.90:4000/v1", "User")
[Environment]::SetEnvironmentVariable("DASH_COMPACT_SUMMARIZE_MODEL", "company-code", "User")

# --- 2) 家目录 + venv + 生产阈值 + 登录自启 ---
powershell -ExecutionPolicy Bypass -File .\scripts\setup_home.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\install_autostart.ps1

# --- 3) 立刻启动（之后登录也会自启） ---
Start-Process -FilePath "$env:USERPROFILE\.dsh-compact\run_proxy_task.cmd"

# --- 4) 健康检查（新开一个 PowerShell 窗口再跑，才能读到刚写的 User 环境变量） ---
curl.exe http://127.0.0.1:4001/health
```

期望 `/health` 大致包含：

```json
{
  "service": "dsh-compact",
  "version": "0.4.0",
  "enabled": true,
  "upstream": "http://172.17.7.90:4000/v1",
  "summarizer_separate": true,
  "trigger": 180000,
  "target": 80000
}
```

- `summarizer_separate: true` → 已启用独立摘要 key  
- `trigger` / `target` 应为 **180000 / 80000**（生产默认；若仍是 50000/20000，多半是旧进程或进程级环境变量未清）

---

## 4. 配置 DSH（必做）

1. 打开 DSH → **Settings → Models**（或你司对应的 CompanyCode 模型配置）
2. 把 **Base URL** 改成：

   ```text
   http://127.0.0.1:4001/v1
   ```

3. API Key 仍填你的**聊天用** Virtual Key（透传给上游）
4. **Apply**，并**重启当前会话 / 重启 DSH**（否则可能仍直连网关）

之后流量路径变为：DSH → 本机代理 → 公司 LiteLLM。

---

## 5. 怎么确认在工作

### 5.1 健康与统计

```powershell
curl.exe http://127.0.0.1:4001/health
curl.exe http://127.0.0.1:4001/stats
```

### 5.2 看日志

- 前台跑：终端里直接刷日志  
- 自启跑：`%USERPROFILE%\.dsh-compact\logs\proxy.log`

成功压缩时会出现类似：

```text
Compression #N: ~xxxxx -> ~yyyyy tokens (...)
[BG] compress done key=ch:...
[MSG] ... tokens≈A->B injected=True ...
```

含义：

| 日志 | 含义 |
| --- | --- |
| `Compression #N` | 后台摘要压缩完成 |
| `injected=True` 且 `tokens≈大->小` | 本轮已把压缩结果注入上游 |
| `injected=False` | 本轮未命中缓存（可能还在涨、或未到 trigger） |
| `429` + `max_parallel_requests` | 上游并行配额满了（见下文排错） |

### 5.3 客户端界面

DSH 里显示的上下文占用是**客户端视角**（常仍显示涨），真正发给公司网关的长度以代理日志里的 `tokens≈入->出` 为准。

---

## 6. 日常操作

### 前台调试启动

```powershell
cd C:\Users\<你>\Desktop\dsh-compact
# 若 4001 已被占用，先结束旧进程
Get-NetTCPConnection -LocalPort 4001 -ErrorAction SilentlyContinue |
  ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
python .\proxy.py
```

### 改环境变量之后

必须：

1. **新开** PowerShell / 终端（User 环境变量对已开进程不刷新）
2. **重启** proxy（杀掉旧 PID 再起）
3. 若改了 DSH Base URL，重启 DSH 会话

### 临时关闭自动压缩（透传）

```powershell
[Environment]::SetEnvironmentVariable("DASH_COMPACT_DISABLE", "1", "User")
# 重启 proxy 后 /health 里 enabled=false
```

恢复：设回 `"0"` 并重启 proxy。

### 卸载登录自启

```powershell
cd C:\Users\<你>\Desktop\dsh-compact
powershell -ExecutionPolicy Bypass -File .\scripts\uninstall_autostart.ps1
```

---

## 7. 环境变量一览

密钥与地址写在 **用户级**（`"User"`），不要提交到 git。

| 变量 | 必填 | 说明 |
| --- | --- | --- |
| `COMPANY_LLM_KEY` | 建议填 | 摘要回退用；聊天鉴权以 DSH 请求头为准 |
| `COMPANY_LLM_BASE` | 建议 | 默认 `http://172.17.7.90:4000/v1` |
| `COMPANY_LLM_MODEL` | 建议 | 默认 `company-code` |
| `DASH_COMPACT_SUMMARIZE_KEY` | **强烈推荐** | 摘要专用 key（须与聊天 key **不同**） |
| `DASH_COMPACT_SUMMARIZE_BASE` | 推荐 | 摘要上游；默认同 BASE |
| `DASH_COMPACT_SUMMARIZE_MODEL` | 可选 | 摘要模型名 |
| `DASH_COMPACT_TRIGGER` | 生产 | `180000` |
| `DASH_COMPACT_TARGET` | 生产 | `80000` |
| `DASH_COMPACT_PORT` | 可选 | 默认 `4001` |
| `DASH_COMPACT_MAX_CONCURRENT` | 可选 | 默认 `1`（同时最多几个后台压缩） |
| `DASH_COMPACT_DISABLE` | 可选 | `1` = 关闭压缩，纯透传 |

手测时可临时把 `TRIGGER` 降到例如 `50000`，**测完务必改回 180000**，否则会过于频繁摘要。

---

## 8. 常见问题

### Q1：`429 Rate limit ... max_parallel_requests ... Remaining: 0`

**不是**上下文没压下去，而是公司网关对**同一把 api_key** 同时只允许很少并发（常见 2）。

- 聊天请求 + 摘要请求共用一把 key → 会撞车  
- **解法**：配置 `DASH_COMPACT_SUMMARIZE_KEY` 为**另一把** Virtual Key，重启 proxy，确认 `/health` 里 `summarizer_separate: true`  
- 摘要进行中若仍偶发 429，等几秒再发即可（代理会对摘要侧做重试，并尽量等 HTTP 空闲再压）

### Q2：压缩成功了，但界面上下文还在涨

正常。DSH 本地仍保存完整历史；代理只缩短**发给上游**的 messages。看日志里的 `injected=True` 与 `tokens≈大->小`。

### Q3：`/health` 连不上

- proxy 没起来：看自启脚本或手动 `python .\proxy.py`  
- 端口被占：`netstat -ano | findstr :4001`，结束对应 PID  
- 改过环境变量却像没生效：旧进程还在跑，杀掉后用**新终端**再起

### Q4：一直 `injected=False`，没有 `Compression #N`

- 估算 token 还没到 `trigger`（生产 180K）  
- 或后台压缩失败进了冷却（看 log 里的 `compress failed` / `cooldown`）  
- 确认 `enabled: true`，且 DSH Base URL 指向 `http://127.0.0.1:4001/v1`

### Q5：会话已经彻底打不开了

用应急离线 CLI（不依赖代理）：

```powershell
cd C:\Users\<你>\Desktop\dsh-compact
python .\compact.py <会话文件路径> -o brief.md
```

把 `brief.md` 贴进**新会话**继续。主路径仍是代理自动压缩；CLI 只作急救。

---

## 9. 文件与目录

| 路径 | 作用 |
| --- | --- |
| 本仓库 `proxy.py` | 代理主程序 |
| `%USERPROFILE%\.dsh-compact\venv\` | 自启用的 Python 虚拟环境 |
| `%USERPROFILE%\.dsh-compact\run_proxy_task.cmd` | 登录自启启动器 |
| `%USERPROFILE%\.dsh-compact\logs\proxy.log` | 运行日志 |
| `scripts\setup_home.ps1` | 建目录 / venv / 写生产默认阈值 |
| `scripts\install_autostart.ps1` | 注册登录自启 |
| `scripts\uninstall_autostart.ps1` | 取消自启 |

---

## 10. 最短检查清单（部署完成打勾）

1. [ ] User 环境已设 `COMPANY_LLM_*`  
2. [ ] （推荐）已设 `DASH_COMPACT_SUMMARIZE_KEY`（另一把 key）  
3. [ ] 已跑 `setup_home.ps1` + `install_autostart.ps1`  
4. [ ] `curl.exe http://127.0.0.1:4001/health` → 200，`enabled: true`  
5. [ ] `/health` 里 `trigger=180000`，且 `summarizer_separate: true`（若配了独立 key）  
6. [ ] DSH Base URL = `http://127.0.0.1:4001/v1`，会话已重启  
7. [ ] 长聊后日志出现 `Compression #N` 与 `injected=True`  

完成以上即可日常使用。架构与参数推导见 [`DEPLOYMENT.md`](DEPLOYMENT.md)。
