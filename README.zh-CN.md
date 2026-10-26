# dsh-compact

[English](README.md) | **中文**

本地透明代理：插在 [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness)（或任意 OpenAI 兼容客户端）与 LLM 网关之间，对长会话做**留新压旧**的自动压缩，同一对话框可继续聊，避免 `ContextWindowExceededError`。

```text
Client  →  http://127.0.0.1:4001/v1  →  dsh-compact  →  你的 OpenAI 兼容上游
```

日常只用**终端前台**启动 `python proxy.py`（见下方「快速开始」）。

---

## 用 coding agent 安装

把下面整段复制到你的 coding agent：

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

密钥和客户端 UI 仍需你本人提供 / 点击；agent 负责本机代理装好并跑通 `/health`。

---

## 特性

- **无感压缩**：超阈值后后台摘要旧消息，近期原文保留，下轮自动注入
- **透明代理**：只改发出去的请求体，不改本地会话文件；只监听 `127.0.0.1`
- **免费前置层**：剥离 base64 图 / 超长 dump；裁旧 tool 结果（micro-compact）
- **稳健**：分片摘要、429 重试、失败冷却与熔断
- **应急 CLI**：会话已打不开时，`compact.py` 可生成可粘贴的 `brief.md`

---

## 架构

不 fork、不引 npm、不加 TLS。自实现一个只监听 `127.0.0.1` 的普通 HTTP 反向代理，对 OpenAI `/v1/chat/completions` 做「留新压旧 + 滚动 merge」；离线 CLI 仅作应急兜底。

```text
                 ┌──────────────────────────────────────────────────────────┐
Client           │  dsh-compact 本地代理  127.0.0.1:4001                     │
  │              │                                                          │
  │ POST /v1/chat/completions                                               │
  │ （Base URL    │  1. 读 messages + 估算 token（真实 usage 优先）             │
  │  指向 :4001）  │  2. strip：base64 图 → 文本占位（strip_heavy）             │
  ▼              │  3. micro：旧 tool 结果 → 占位（micro_compact，免费）        │
                 │  4. 超阈值？只摘要旧消息 → 滚动 merge（engine）              │
                 │     近期原文保留                                            │
上游 LLM 网关     │  5. 组装 [滚动摘要] + [近期原文]，转发到上游                   │
 OpenAI 兼容 /v1 │  6. 响应流式直通回客户端（不缓冲）                              │
                 │                                                          │
                 │  · 状态仅内存；压缩结果按内容 hash 匹配                         │
                 │  · 压缩异步优先：本轮放行，下轮注入                              │
                 └──────────────────────────────────────────────────────────┘
```

| 形态 | 部署物 | 触发 | 定位 |
| --- | --- | --- | --- |
| 主：本地压缩代理 | `proxy.py` | token 超阈值自动 | 同一会话无缝继续 |
| 应急：离线 CLI | `compact.py` | 手动 | 生成 `brief.md` 交接 |

**关键性质：**

- 只改写发往上游的请求体，本地 transcript **永不修改**
- 仅监听回环地址；不做 TLS 终结
- 只特殊处理 `/v1/chat/completions`；其它路径透传；未知字段不破坏
- 协议为 **OpenAI Chat Completions**（`tool_calls` + `role:"tool"`）

**默认阈值（可按窗口调整）：** `TRIGGER=180000`，`TARGET=80000`，`HARD_LIMIT=240000`；会话身份用 messages 内容 hash。估算会计入中文、`reasoning_content` 与 `tools` schema；请求体过大也会强制压缩（`DASH_COMPACT_BODY_TRIGGER_BYTES`）。

### 请求处理流程

```text
每轮请求估算 token（messages + tools；中文 / reasoning 敏感）
  → 未超 TRIGGER 且 body 未超 BODY_TRIGGER：原样转发
  → 超阈值（或 body 过大）：
       1) micro：旧 tool_result → 占位（不调 LLM）
       2) 只摘要旧消息 → 与滚动摘要 merge
       3) 近期消息原文保留
       4) 发给上游 = [滚动摘要] + [近期原文]
  → 尽量异步：本轮可先放行，下轮注入
  → 达 HARD_LIMIT（或 body 过大且已超 TRIGGER）：转发前同步压缩
```

日志里看到 `Compression #N` 与 `injected=True` 且 `tokens≈大->小`，说明压缩已生效。

### 压缩比快照

合成样本、本地 token 估算（`python -m bench.free_layer`）。`free` 是 strip + micro 之后剩下的比例。`post` 是摘要切分后的大小，`end` 是 `post / raw`。横线表示这一格没有跑。带图样本几乎全是 base64 填充，掉得比真实截图会话更多。

| 样本 | raw | strip | micro | free | post | end | recall | headings |
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

`recall` 和 `headings` 来自真实摘要（`python -m bench.quality`）。目前只评过 `chat_over_trigger`：195000 → 97965，耗时 5.88 秒；5 条埋点事实全部保留，`prompt.txt` 的 7 个标题齐全，`RECENT_ANCHOR` 仍在原文里，摘要 1317 字。其余行的 `post` / `end` 仍是假摘要的体积结果。

纯对话没有图，也没有超长 tool 结果，免费层减不掉体积；摘要把大约 19 万–23.4 万压到 8.8 万–9.8 万（`TARGET=80000`）。超长 tool 输出和 base64 图在免费层就会缩小，摘要再切一刀。

---

## 环境要求

- Python 3.10+
- 任意 OpenAI 兼容的 `/v1/chat/completions` 上游
- 客户端可把 Base URL 指到本机（协议选 **OpenAI Chat Completions**）

---

## 快速开始

### 1. 安装（做一次）

```bash
# 建议把 venv 放在家目录，勿提交密钥
python3 -m venv ~/.dsh-compact/venv          # Windows: py -3 -m venv %USERPROFILE%\.dsh-compact\venv
source ~/.dsh-compact/venv/bin/activate      # Windows: .\.dsh-compact\venv\Scripts\Activate.ps1（路径按实际）
cd /path/to/dsh-compact
pip install -r requirements.txt
```

Windows 也可：`powershell -ExecutionPolicy Bypass -File .\scripts\setup_home.ps1`（建目录 + venv + 装依赖）。

### 2. 配置密钥（做一次）

密钥只放环境变量，**不要写进仓库**。

**macOS** — 写入 `~/.dsh-compact/env.sh`（`chmod 600`）：

```bash
export COMPANY_LLM_BASE="https://api.deepseek.com/v1"   # 或你的公司网关
export COMPANY_LLM_MODEL="deepseek-chat"                # 或 company-code 等
export COMPANY_LLM_KEY="sk-..."                         # 摘要回退；聊天 key 也可只配在客户端
# 推荐：摘要单独一把 key
export DASH_COMPACT_SUMMARIZE_BASE="https://api.deepseek.com/v1"
export DASH_COMPACT_SUMMARIZE_MODEL="deepseek-chat"
export DASH_COMPACT_SUMMARIZE_KEY="sk-..."
export DASH_COMPACT_TRIGGER=180000
export DASH_COMPACT_TARGET=80000
export DASH_COMPACT_PORT=4001
```

**Windows** — 写成 **User 级**变量（改完后**新开** PowerShell）：

```powershell
[Environment]::SetEnvironmentVariable("COMPANY_LLM_BASE",  "https://api.deepseek.com/v1", "User")
[Environment]::SetEnvironmentVariable("COMPANY_LLM_MODEL", "deepseek-chat", "User")
[Environment]::SetEnvironmentVariable("COMPANY_LLM_KEY",   "sk-你的key", "User")
[Environment]::SetEnvironmentVariable("DASH_COMPACT_SUMMARIZE_BASE",  "https://api.deepseek.com/v1", "User")
[Environment]::SetEnvironmentVariable("DASH_COMPACT_SUMMARIZE_MODEL", "deepseek-chat", "User")
[Environment]::SetEnvironmentVariable("DASH_COMPACT_SUMMARIZE_KEY",   "sk-你的摘要key", "User")
```

公司内网把 `BASE` 换成网关地址、`MODEL` 换成实际模型名即可。

### 3. 每次用终端启动

**macOS：**

```bash
source ~/.dsh-compact/venv/bin/activate
cd /path/to/dsh-compact
source ~/.dsh-compact/env.sh
python proxy.py
```

**Windows（PowerShell）：**

```powershell
cd C:\Users\<你>\Desktop\dsh-compact

# 若 4001 已被占用，先结束旧进程（避免「聊天正常但本终端无日志」）
Get-NetTCPConnection -LocalPort 4001 -ErrorAction SilentlyContinue |
  ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }

& "$env:USERPROFILE\.dsh-compact\venv\Scripts\Activate.ps1"
# 避免系统 HTTP(S)_PROXY 劫持回环 / 内网网关
$env:NO_PROXY = "127.0.0.1,localhost"
python .\proxy.py
```

期望看到：

```text
dsh-compact proxy v0.4.0 on http://127.0.0.1:4001
  trigger=180000 target=80000
```

**另开一个终端**做健康检查：

```bash
curl http://127.0.0.1:4001/health          # Windows 可用 curl.exe
```

关注：`enabled: true`，以及 `/health` 返回的 `pid` 与启动行里的 `pid` 一致。停掉：前台窗口 `Ctrl+C`。

### 4. 客户端（Harness / DSH）

| 项 | 值 |
| --- | --- |
| Base URL | `http://127.0.0.1:4001/v1` |
| API protocol | **OpenAI Chat Completions**（不要用 Anthropic Messages） |
| API Key | 上游真实 key |
| Model | 与上游一致 |

Apply 后重启会话。

### 5. 怎么确认在工作

| 日志 / 现象 | 含义 |
| --- | --- |
| `[MSG] Forwarding ... 200` | 透传成功 |
| `Compression #N` | 后台压缩完成 |
| `injected=True` 且 `tokens≈大->小` | 已注入压缩结果 |
| `injected=False` 且未过 trigger | 正常，尚未压缩 |

手测压缩：临时把 `DASH_COMPACT_TRIGGER` 降到 `50000`，**新开终端并重启代理**；测完改回 `180000`。

### 排错：端口被占 / 终端没有 `[MSG]`

常见原因是同时有多个 `proxy.py`（旧终端或自启残留）。真正占 4001 的进程才会写实时日志；你正在看的窗口可能不是 listener。

处理：先 `curl` `/health` 看 `pid` → 清掉 4001 上旧进程 → **只留一个前台** `python proxy.py` → 再验 `/health` 的 `pid` 与启动行一致 → 在客户端发一条短消息，前台应出现 `[MSG]`。

macOS 查占用：`lsof -iTCP:4001 -sTCP:LISTEN` 后 `kill <PID>`。  
也可盯日志文件：`Get-Content "$env:USERPROFILE\.dsh-compact\logs\proxy.log" -Wait -Tail 50`（macOS：`tail -f ~/.dsh-compact/logs/proxy.log`）。

可选脚本仍在 `scripts/`（如 `setup_home.ps1`、自启相关）；日常推荐只用终端前台，调试期间避免多个实例。

---

## 配置

密钥只从环境变量读，不要写进仓库。

| 变量 | 说明 |
| --- | --- |
| `COMPANY_LLM_BASE` | 聊天上游 Base URL（必填） |
| `COMPANY_LLM_MODEL` | 上游模型名 |
| `COMPANY_LLM_KEY` | 摘要未单独配置时的回退 key |
| `DASH_COMPACT_SUMMARIZE_*` | 可选：摘要专用 BASE / MODEL / KEY |
| `DASH_COMPACT_TRIGGER` | 触发线，默认 `180000` |
| `DASH_COMPACT_TARGET` | 压缩后近期原文目标上限，默认 `80000` |
| `DASH_COMPACT_HARD_LIMIT` | 估算超过此值则转发前同步压缩，默认 `240000` |
| `DASH_COMPACT_BODY_TRIGGER_BYTES` | 请求体字节数 ≥ 此值也触发压缩，默认 `350000`（兜底低估） |
| `DASH_COMPACT_PORT` | 本地端口，默认 `4001` |
| `DASH_COMPACT_DISABLE` | `1` = 关闭压缩，纯透传 |

---

## 应急 CLI

会话已打不开时：

```bash
python compact.py /path/to/session.jsonl -o brief.md
```

新开会话粘贴 `brief.md` 继续。

---

## 目录

```text
proxy.py                 # 本地 HTTP 代理（主入口）
engine.py / prompt.txt   # 留新压旧 + 滚动 merge
tokens.py / strip_heavy.py / micro_compact.py
compact.py / session_reader.py   # 应急 CLI
scripts/                 # 可选：venv 安装、自启等
tests/
```

---

## 致谢

策略对齐 [claude-rolling-context](https://github.com/nodenestor/claude-rolling-context)（透明代理、留新压旧、异步注入）；引擎零件借鉴 [contextkit](https://github.com/drandrewlaw/contextkit)（阈值、micro-compact、熔断）。本仓库为 Python 自实现，面向 OpenAI Chat Completions，不 fork 上述项目。
