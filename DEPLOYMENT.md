# dsh-compact 部署方案（v1）

> **只想按步骤装好用：** Windows → [`HOW_TO_DEPLOY.md`](HOW_TO_DEPLOY.md)；Mac → [`HOW_TO_DEPLOY_MAC.md`](HOW_TO_DEPLOY_MAC.md)。  
> 配套：`README.md`（需求与算法设计）、`REFERENCE_MAPPING.md`（从开源抄什么）。  
> 本文件回答：**建议的架构是什么、关键取舍为什么、以及从零到一的实现方案、怎么部署、怎么验收、怎么回滚。**  
> 适用范围：v1 —— 本地透明代理（主）+ 离线 CLI（应急 fallback），Windows，Python 3.10+。

---

## 0. 一句话架构

**不 fork、不引 npm、不加 TLS。** 自实现一个只监听 `127.0.0.1` 的普通 HTTP 反向代理，插在 DSH 与公司 LiteLLM 之间，对 OpenAI `/v1/chat/completions` 做「留新压旧 + 滚动 merge」的自动压缩；离线 CLI 仅作应急兜底。

---

## 1. 建议架构

```
                 ┌──────────────────────────────────────────────────────────┐
DSH（客户端）      │  dsh-compact 本地代理  127.0.0.1:4001                     │
  │               │                                                        │
  │ POST /v1/chat/completions                                             │
  │ （base 改指向   │  1. 读 messages + 估算 token（真实 usage 优先）           │
  │   本机 4001）   │  2. strip：base64 图 → 文本占位（strip_heavy）           │
  ▼               │  3. micro：旧 tool 结果 → 占位（micro_compact，免费）      │
                  │  4. 超阈值？只摘要旧消息 → 滚动 merge（engine）            │
                  │     近期 N 轮原文保留                                      │
                  │  5. 组装 [滚动摘要] + [近期原文]，转发到上游                 │
                  │  6. 响应流式直通回 DSH（不缓冲）                            │
                  │                                                        │
公司 LiteLLM       │  · 状态仅内存；压缩结果按「会话 key / 内容 hash」匹配        │
 http://172.17.7.90:4000/v1 │  · 压缩异步优先：本轮放行，下轮注入                  │
 (company-code, ctx 256K)  └──────────────────────────────────────────────────┘
```

**两条部署形态，一个项目：**


| 形态        | 常驻进程    | 部署物            | 触发方式          | 定位               |
| --------- | ------- | -------------- | ------------- | ---------------- |
| 主：本地压缩代理  | 是（登录自启） | `proxy.py` 单进程 | 自动（token 超阈值） | 同一会话无缝继续         |
| 应急：离线 CLI | 否       | `compact.py`   | 手动（会话已爆）      | 生成 `brief.md` 交接 |


**关键性质（保证可逆、无损）：**

- 代理**只改写发往网关的请求体**，DSH 本地 transcript（jsonl）**永不修改**
- 仅监听 `127.0.0.1`；LiteLLM 为明文 HTTP，**不做 TLS 终结、无 CA 证书**（rolling-context 的 MITM 部分整段不需要）
- 只处理 `/v1/chat/completions`；`/v1/models` 等直通；未知字段**透传不破坏**
- 数据格式是 **OpenAI chat completions**（`tool_calls` + `role:"tool"`），不是 Anthropic 的 `tool_use/tool_result`

---

## 2. 关键设计决策（对照 rolling-context 的取舍）


| 决策                      | 选型                                | 理由                                                                                       |
| ----------------------- | --------------------------------- | ---------------------------------------------------------------------------------------- |
| 是否 fork rolling-context | **否**                             | 它 60%+ 代码绑死 Anthropic/Claude Code（MITM、HTTPS_PROXY、native cache、插件生命周期），clone 后要删大半再重写大半 |
| 代理类型                    | **普通 HTTP 反向代理**                  | 上游明文 HTTP，无需 TLS 终结                                                                      |
| 数据格式                    | **OpenAI `/v1/chat/completions`** | DSH 走此格式；工具调用 = `tool_calls`，结果 = `role:"tool"`                                          |
| 摘要成本模型                  | **全价 fresh 重发**，无 cache           | `company-code` 无 prompt cache，rolling-context 的 native 零成本路径不成立                          |
| 压缩触发参数                  | **按 256K 窗口重推，实测校准**              | 不继承 Anthropic 的 100K/40K 默认                                                              |
| 会话身份                    | **content-hash（已冻结）**             | Phase 0 确认无会话头；见 §3.3 / §4.3                                                             |
| 压缩时序                    | **异步为主，超安全线同步**                   | 见 §5.4                                                                                   |


### 2.1 trigger / target 的推导（不照搬默认）

约束链：

```
硬窗口 256K
  − 输出预留 maxOutput（默认 32K）
  = effectiveWindow ≈ 224K

TRIGGER = effectiveWindow − 触发缓冲
TARGET  = 压缩后应落到的近期原文上限
```

**v1 冻结值（Gate-E 已校准）：`TRIGGER = 180000`、`TARGET = 80000`。**

Gate-E 实测：单段 ~117k chars → `prompt_tokens=63074`、`elapsed_s=27.7`、`cached_tokens=0`。无 cache，压得越勤越亏，故触发线比 Anthropic 默认（100K）更靠后。同步最坏 3～5 段 ≈ 80～140s ≪ 600s，同步路径保留。若上线后压太勤/太慢，再微调这两个数。

---

## 3. 实现前必须确认（Phase 0，阻塞项优先）

### 3.1 五个 Gate（不过则停，不写代码）


| #   | 确认项                                             | 验证方式                                                | 不过则                            |
| --- | ----------------------------------------------- | --------------------------------------------------- | ------------------------------ |
| A   | DSH 的 API base 可改成指向本地地址                        | 改配置后 curl 本地确认流量落地                                  | 代理方案作废，转「嵌入 DSH 链路」立项；CLI 先行   |
| B   | DSH 每轮全量重发 messages（非服务端上下文）                    | 抓两轮 body 对比                                         | 同上                             |
| C   | 脱敏真实 session 样本（≥1 份超长 tool 输出、≥1 份 base64 图）   | README 查找命令 + 脱敏                                    | 无真实样本则用假数据                     |
| D   | **DSH 请求是否带会话标识头**（决定会话身份方案）                    | 抓包看 header；尤其查 `session`/`conversation`/`thread` 类头 | 有→用头做 key；无→退内容 hash（见 §4.3）   |
| E   | **实测摘要成本/延迟**：打一个 ~120K 会话，测一次分片摘要的耗时与 token 成本 | mock 或真实网关                                          | 据此定 TRIGGER/TARGET 与「同步压缩是否可行」 |


> Gate-A 为否 → 本方案作废（转嵌入链路，v1 范围外）；Gate-B 为否 → 代理不可行。D/E 是决定参数与可行性的实测项，**不要跳过直接写代理**。

### 3.2 验证工具（仓库已提供）


| 文件                                  | 用途                                                      |
| ----------------------------------- | ------------------------------------------------------- |
| `probe_server.py`                   | 本地假 API：收 DSH 流量、打印 header、落盘 body → **Gate A / B / D** |
| `scripts/compare_probe_rounds.py`   | 对比最近两轮 `messages` 长度 → **Gate B**                       |
| `scripts/gate_e_summarize_probe.py` | 打一次 ~60k chars 摘要，打耗时与 usage → **Gate E**               |
| `notes/phase0-checklist.md`         | 填空验收表，做完再开写 Phase 1                                     |


```powershell
# 终端 1
python probe_server.py

# 终端 2：改 DSH base → http://127.0.0.1:4001/v1 ，重启 DSH，发两轮消息后：
python scripts/compare_probe_rounds.py

# Gate E（需 COMPANY_LLM_* 与公司网络）
python scripts/gate_e_summarize_probe.py
```

抓包文件在 `probe_captures/`（已 gitignore）。把结论填进 `notes/phase0-checklist.md`。

### 3.3 Phase 0 实测结论（2026-10-14）— 全部 PASS

详见 `notes/phase0-checklist.md`。冻结决策如下：


| Gate | 结论               | 对实现的约束                                                                                                               |
| ---- | ---------------- | -------------------------------------------------------------------------------------------------------------------- |
| A    | PASS             | DSH CompanyCode Base URL 可改为 `http://127.0.0.1:4001/v1`；path = `/v1/chat/completions`                                |
| B    | PASS             | 客户端全量重发 messages（实测：只发 `ping` 仍带 59 条历史 / ~414KB）                                                                    |
| C    | PASS             | 样本：`%USERPROFILE%\.dsh-compact\samples\session_probe.json`（含超长 tool + `data:image`/base64）                           |
| D    | **content-hash** | 无 session/conversation/thread 头；body 顶层无会话 id。`get_session_key` 用内容 hash                                             |
| E    | PASS             | 单段摘要 **27.7s** / prompt **63074** / `cached_tokens=0`；**TRIGGER=180000、TARGET=80000**；同步压缩可行（3～5 段 ≈ 80～140s ≪ 600s） |


抓包还确认：请求含 `stream`/`stream_options`/`tools`/`store`/`max_completion_tokens` → 代理须流式透传、正确处理 tool 切点、未知字段原样转发。`user-agent: deepseek-harness/0.2.0-rc.2`。

**Phase 0–5 代码已齐；同事安装见 [`HOW_TO_DEPLOY.md`](HOW_TO_DEPLOY.md)；人工勾选见 [`notes/phase5-checklist.md`](notes/phase5-checklist.md)。**

---

## 4. 模块划分与数据流

### 4.1 文件布局

```text
dsh-compact/
├── README.md               # 需求与算法
├── DEPLOYMENT.md           # 本文件
├── REFERENCE_MAPPING.md    # 从开源抄什么
├── proxy.py                # 主入口：本地 HTTP 代理
├── engine.py               # 压缩引擎：留新压旧 + 滚动 merge
├── micro_compact.py        # 免费层：清旧 tool 结果 + 图片占位
├── tokens.py               # token 估算：usage 优先 + 字符估算 + 分组
├── strip_heavy.py          # base64 / 超长 dump 剥离
├── prompt.txt              # 摘要 prompt（滚动 merge）
├── session_reader.py       # 应急 CLI 读会话
├── compact.py              # 应急 CLI：session → brief.md
├── requirements.txt        # httpx（唯一运行期依赖）
├── tests/                  # mock 网关 e2e + 单测
└── .gitignore              # 忽略 venv/ logs/ samples/ *.key
```

### 4.2 一次请求的处理流程

```
收到 POST /v1/chat/completions
  → 估算 token（tokens.py：真实 usage 优先，否则字符估算 + 33% buffer）
  → strip_heavy：base64 图 → 占位、超长 dump 截断
  → 查内存：是否有该「会话 key / 内容 hash」的已压缩结果？
      命中 → 用 [滚动摘要]+[近期原文] 替换 messages 前缀
      未命中且超阈值 → 触发压缩（异步）
  → 转发到 LiteLLM（响应流式直通）
  → 若触发过异步压缩，后台线程跑 engine.compress()，结果存内存，下轮注入
```

### 4.3 会话身份（Gate-D 已冻结：content-hash）

Phase 0 抓包确认：**无会话头、body 无会话 id**。v1 采用：

- **messages 内容 hash**：取「去掉最新一条用户消息后的前缀」做稳定 hash 做 key（rolling-context server 做法）。
- 「按会话冷却/熔断」降级为「按前缀 hash」计；子 agent/分支可能各自成 key（可接受）。

**实现约束**：`proxy.py` 里 `get_session_key(request) -> str` 默认 content-hash；保留接口以便将来 DSH 增加会话头时切换。引擎（`engine.py`）不感知身份来源。

### 4.4 压缩引擎核心（engine.compress）

对应 `REFERENCE_MAPPING.md` 的 `rolling-context/compressor.py`：

```
输入：messages（OpenAI 格式）、真实 usage（可选）
1. keep_ratio = TARGET / real_tokens（无 usage 用估算）
2. _find_keep_index：从尾部倒推切割点
3. _safe_cut：切点必须是纯 user 条；前一条不能有未闭合 tool_calls
   （OpenAI 语义：assistant 带 tool_calls 的，其 role:"tool" 结果必须同侧）
4. 摘要旧段 [start, cut)：
   - 若已有滚动摘要，merge：新摘要并入旧摘要、扩展 timeline
5. 输出 [summary_msg(带 marker)] + [近期原文]
```

### 4.5 三层压缩策略（contextkit 节奏）


| 层        | 触发       | 成本  | 模块                                     |
| -------- | -------- | --- | -------------------------------------- |
| strip    | 每次压缩前    | 免费  | `strip_heavy.py`                       |
| micro    | 超阈值先试    | 免费  | `micro_compact.py`（清旧 tool 结果，留最近 N 条） |
| LLM 滚动摘要 | micro 不够 | 全价  | `engine.py`                            |


熔断：同 key 连败 N 次（默认 3）后 `should_compact` 返回否，手动重置。

---

## 5. 从零到一实现方案（分阶段，每阶段可验收）

### Phase 1：基础模块（无 LLM，可独立验证）— **已完成**


| 交付                 | 内容                                                                            | 验收  |
| ------------------ | ----------------------------------------------------------------------------- | --- |
| `tokens.py`        | `estimate_tokens` / `estimate_messages(usage 优先)` / `group_messages_by_round` | ✅   |
| `strip_heavy.py`   | `strip_images` / `truncate_long_tool_outputs` / `strip_heavy`                 | ✅   |
| `micro_compact.py` | `micro_compact(keep_recent=5)`，OpenAI `role:tool`                             | ✅   |
| `tests/`           | tokens / strip / micro + `session_probe.json` smoke                           | ✅   |


```powershell
pip install -r requirements.txt
python -m pytest tests -v   # 全仓现 56 passed
```

**本阶段不依赖公司网关。**

### Phase 2：压缩引擎（接 mock 网关）— **已完成（含 e2e 加固）**


| 交付                                                   | 内容                                                                   | 验收  |
| ---------------------------------------------------- | -------------------------------------------------------------------- | --- |
| `prompt.txt`                                         | 滚动 merge 中文模板（Active Goal / Timeline / … / Next Step）                | ✅   |
| `engine.py`                                          | `should_compact` / `compress` / `auto_compact`；切点安全（含稠密 agent / assistant 边界兜底）；滚动 merge；分片摘要；`reasoning_content` 回退；熔断 | ✅   |
| `tests/test_engine.py`                               | fake summarizer + httpx MockTransport                                | ✅   |
| `tests/mock_gateway.py` + `tests/test_engine_e2e.py` | 线程内 mock（可配 200/500/空 choices/usage）；混合会话；分片截断；HTTP 熔断；多次 merge      | ✅   |


e2e 覆盖：

- 混合会话（base64 图 + 超长 tool）→ 压缩后变短、近期原文保留、无 400
- 分片超长 → 丢最老 20% 轮次重试（`last_chunk_truncations`）
- 摘要端连续 500 / 空 choices → 计入失败；3 连败熔断
- 二次 compress → prompt 含「既有滚动摘要」，timeline/路径累积

```powershell
python -m pytest tests -v
```

### Phase 3：代理（端到端）— **已完成**


| 交付                 | 内容                                                                                                             | 验收                                                                                          |
| ------------------ | -------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------- |
| `proxy.py`         | `get_session_key`（header 优先，否则 content-hash）；前缀 hash 缓存注入；异步/`HARD_LIMIT` 同步压缩；chunked 流式透传；`/health` `/stats` | ✅ `tests/test_proxy.py`                                                                     |
| 注入模型               | DSH 全量重发 transcript → 缓存按 `source_len` 前缀匹配 → 发出 `compressed + messages[n:]`                                   | ✅ 后台压完后下一轮含 `SUMMARY_MARKER` + 最新 user                                                      |
| DSH 手测（2026-10-14 / 10-16） | Base URL → `:4001`；低 TRIGGER 与真实长会话验证压/注 | ✅ `Compression #N` / `injected=True`；429 为并行配额（用 `DASH_COMPACT_SUMMARIZE_KEY` 隔离） |


```powershell
# 前台启动（需 COMPANY_LLM_*；生产阈值）
$env:DASH_COMPACT_TRIGGER = "180000"
$env:DASH_COMPACT_TARGET  = "80000"
python proxy.py
# 健康检查
curl http://127.0.0.1:4001/health
# DSH CompanyCode Base URL → http://127.0.0.1:4001/v1
```

`/health`：`service, version, pid, uptime_s, active_requests, enabled, compressions_total, tokens_saved_total, upstream, summarizer, summarizer_model, summarizer_separate, trigger, target, …`  
`/stats`：每会话 peak/compressions/error + cache 摘要。

手测压压缩时可临时降 `DASH_COMPACT_TRIGGER`（如 2000）；**测完必须改回 180000**，否则会过于频繁摘要并易触发 429。

### Phase 4：应急 CLI（兜底）— **已完成**


| 交付                  | 内容                                                   | 状态                               |
| ------------------- | ---------------------------------------------------- | -------------------------------- |
| `session_reader.py` | JSON / JSONL / `messages` 包装；可读 `session_probe.json` | ✅ `tests/test_session_reader.py` |
| `compact.py`        | strip → micro → 留新压旧摘要 → `brief.md` + 近期 verbatim    | ✅ `tests/test_compact_cli.py`    |


```powershell
# 需 COMPANY_LLM_*（假摘要测见 pytest）
python compact.py "$env:USERPROFILE\.dsh-compact\samples\session_probe.json" -o brief.md
# 或指定近期轮数
python compact.py path\to\session.jsonl -o brief.md --recent-rounds 5
```

### Phase 5：部署与演练 — **代码已齐（安装手册 + 人工 checklist）**

| 交付 | 内容 | 状态 |
|---|---|---|
| 文件日志 | `~\.dsh-compact\logs\proxy.log` 轮转 | ✅ `proxy.configure_logging`（v0.4.0） |
| 429 缓解 | 摘要遇 429/503 指数退避重试；后台压前等 HTTP 空闲 | ✅ + `DASH_COMPACT_MAX_CONCURRENT` 默认 **1** |
| 独立摘要 key | `DASH_COMPACT_SUMMARIZE_BASE/KEY/MODEL`（不抢聊天并行配额） | ✅ `/health.summarizer_separate` |
| 本机目录 + 生产 env | `scripts/setup_home.ps1` | ✅ |
| 登录自启 | `scripts/install_autostart.ps1` / `uninstall_autostart.ps1` | ✅ |
| 安装手册 | [`HOW_TO_DEPLOY.md`](HOW_TO_DEPLOY.md) | ✅ |
| 演练清单 | `notes/phase5-checklist.md`（DSH 接入 / 自启 / 三演练） | ✅ 待勾选 |

一步步安装请直接跟 [`HOW_TO_DEPLOY.md`](HOW_TO_DEPLOY.md)。最短命令：

```powershell
[Environment]::SetEnvironmentVariable("COMPANY_LLM_KEY", "sk-...", "User")
[Environment]::SetEnvironmentVariable("DASH_COMPACT_SUMMARIZE_KEY", "sk-sum-...", "User")
powershell -ExecutionPolicy Bypass -File .\scripts\setup_home.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\install_autostart.ps1
Start-Process "$env:USERPROFILE\.dsh-compact\run_proxy_task.cmd"
curl.exe http://127.0.0.1:4001/health
```

人工步骤（无法由仓库代做）：写 User 级密钥、DSH Base URL、注销验证自启、三演练勾选 → `notes/phase5-checklist.md`。

---

## 6. 环境准备

### 6.1 本机安装（一次性）

```powershell
py -3.10 -m venv "$env:USERPROFILE\.dsh-compact\venv"
& "$env:USERPROFILE\.dsh-compact\venv\Scripts\Activate.ps1"
pip install -r requirements.txt   # 仅 httpx
```

目录约定：

```text
%USERPROFILE%\.dsh-compact\
├── venv\      # Python 环境
├── logs\      # 代理日志（自动轮转）
├── samples\   # 脱敏真实会话样本（测试用，不进 git）
└── notes\     # 抓包、部署记录（可选）
```

### 6.2 环境变量（用户级，**密钥禁止入仓库/脚本**）

```powershell
# --- LLM 凭证（聊天透传上游） ---
[Environment]::SetEnvironmentVariable("COMPANY_LLM_KEY",   "sk-...",   "User")
[Environment]::SetEnvironmentVariable("COMPANY_LLM_BASE",  "http://172.17.7.90:4000/v1", "User")
[Environment]::SetEnvironmentVariable("COMPANY_LLM_MODEL", "company-code", "User")

# --- 摘要专用凭证（强烈推荐：另一把 api_key，不抢聊天的 max_parallel_requests=2） ---
# 不设则回退用上面的 COMPANY_LLM_*（会与聊天抢并行配额）
[Environment]::SetEnvironmentVariable("DASH_COMPACT_SUMMARIZE_KEY",   "sk-sum-...", "User")
[Environment]::SetEnvironmentVariable("DASH_COMPACT_SUMMARIZE_BASE",  "http://172.17.7.90:4000/v1", "User")
[Environment]::SetEnvironmentVariable("DASH_COMPACT_SUMMARIZE_MODEL", "company-code", "User")

# --- 代理策略（Gate-E 已校准：TRIGGER=180K / TARGET=80K） ---
[Environment]::SetEnvironmentVariable("DASH_COMPACT_PORT",             "4001",  "User")
[Environment]::SetEnvironmentVariable("DASH_COMPACT_TRIGGER",          "180000","User")
[Environment]::SetEnvironmentVariable("DASH_COMPACT_TARGET",           "80000", "User")
[Environment]::SetEnvironmentVariable("DASH_COMPACT_MICRO_KEEP",       "5",     "User")
[Environment]::SetEnvironmentVariable("DASH_COMPACT_MAX_CONCURRENT",   "1",     "User")
[Environment]::SetEnvironmentVariable("DASH_COMPACT_IDLE_WAIT_S",      "180",   "User")
[Environment]::SetEnvironmentVariable("DASH_COMPACT_SUMMARIZE_RETRIES","4",     "User")
[Environment]::SetEnvironmentVariable("DASH_COMPACT_FAILURE_COOLDOWN", "300",   "User")
[Environment]::SetEnvironmentVariable("DASH_COMPACT_LOG_LEVEL",        "INFO",  "User")
[Environment]::SetEnvironmentVariable("DASH_COMPACT_LOG_MAX_MB",       "10",    "User")
[Environment]::SetEnvironmentVariable("DASH_COMPACT_LOG_BACKUPS",      "3",     "User")

# --- 机器级开关：1 = 本机所有会话关闭自动压缩（回滚开关） ---
[Environment]::SetEnvironmentVariable("DASH_COMPACT_DISABLE",          "0",     "User")
```

改环境变量后：已运行的 DSH 与代理需新起才生效。`/health` 里 `summarizer_separate: true` 表示已启用独立摘要 key。

### 6.3 端口

- 默认 `4001`，先 `netstat -ano | findstr :4001` 确认空闲
- 仅绑定 `127.0.0.1`；**不加**入站防火墙规则
- 冲突换端口，同步改 DSH 侧 base

---

## 7. 上线步骤（按序，每步设 gate，不过则停）


| #   | 步骤                                  | 验收 gate                                                 |
| --- | ----------------------------------- | ------------------------------------------------------- |
| 0   | Phase 0 五 Gate（§3.1 / §3.3）         | **已完成 2026-10-14**：content-hash；TRIGGER=180K；TARGET=80K |
| 1   | Phase 1 基础模块                        | **已完成**                                                 |
| 2   | Phase 2 引擎 + mock 网关 e2e            | **已完成**                                                 |
| 3   | Phase 3 代理 + 单测 + DSH 烟测            | **已完成**：`pytest` 56；DSH 经 `:4001` 透传/压缩/注入已验证           |
| 4   | DSH 接本地代理（§8.1），生产 TRIGGER=180K 日常用 | **待勾选** `notes/phase5-checklist.md` §1 |
| 5   | 登录自启（§8.2）+ `install_autostart.ps1`     | **脚本已提供**；注销验证待勾选 checklist §2 |
| 6   | 三演练：①熔断 ②off ③回滚（§8.4）              | **待勾选** checklist §3 |
| 7   | 宣布完成；归档                                | checklist §4 |


---

## 8. 部署与运维

### 8.1 DSH 侧接入


| 配置项                 | 值                                        |
| ------------------- | ---------------------------------------- |
| DSH LLM base（代理在时）  | `http://127.0.0.1:4001/v1`               |
| DSH LLM base（直连/回滚） | `http://172.17.7.90:4000/v1`             |
| 鉴权                  | 原 Virtual Key 不变（代理透传 `Authorization` 头） |


改完**重启 DSH**，验证代理日志出现 `[MSG] ... Forwarding to 172.17.7.90:4000/v1`。

### 8.2 后台常驻（登录自启）

**推荐一键安装（无需管理员）：**

```powershell
cd C:\Users\pg9\Desktop\dsh-compact
# 密钥必须写成 User 环境变量（自启进程读不到会话里临时 $env:）
[Environment]::SetEnvironmentVariable("COMPANY_LLM_KEY", "sk-...", "User")
[Environment]::SetEnvironmentVariable("DASH_COMPACT_SUMMARIZE_KEY", "sk-sum-...", "User")  # 推荐
powershell -ExecutionPolicy Bypass -File .\scripts\install_autostart.ps1
Start-Process -FilePath "$env:USERPROFILE\.dsh-compact\run_proxy_task.cmd"
curl.exe http://127.0.0.1:4001/health
# 完整步骤见 HOW_TO_DEPLOY.md
```

卸载：`.\scripts\uninstall_autostart.ps1`。

自启方式（无需管理员）：**HKCU Run** + **Startup 文件夹** 的 `run_proxy_task.cmd` → `pythonw` + 仓库 `proxy.py`。若权限允许会额外注册 `schtasks` ONLOGON（失败可忽略）。日志：`%USERPROFILE%\.dsh-compact\logs\proxy.log`。

### 8.3 升级 / 改配置

1. 确认无关键任务（或接受该会话下轮才反映新配置）
2. 停代理：`taskkill /IM pythonw.exe /F`
3. 替换代码 / 改环境变量
4. 重启（前台 `python proxy.py` 调试，或靠登录自启）
5. `curl http://127.0.0.1:4001/health` 确认版本

> 代理重启丢失**内存摘要**；下轮超阈值自动重压一次，可接受；transcript 不受影响。

### 8.4 回滚（会话无损）

```powershell
[Environment]::SetEnvironmentVariable("DASH_COMPACT_DISABLE", "1", "User")
taskkill /IM pythonw.exe /F 2>$null
# DSH 配置 LLM base = http://172.17.7.90:4000/v1，重启 DSH
```

已压缩会话继续可用（transcript 从未被改）；新会话直接走直连。

### 8.5 故障排查


| 症状                             | 先查                        | 处理                                                     |
| ------------------------------ | ------------------------- | ------------------------------------------------------ |
| DSH 报 ConnectionRefused        | 代理未起 / 端口错                | 前台 `python proxy.py` 看 banner                          |
| 流量不到本地                         | DSH base 未切               | 改 base 重启 DSH；`netstat` 看 4001                         |
| 请求 400（仍超窗）                    | 同步压缩未触发或摘要失败              | 查 `[BG]`/`[ERROR]`；确认 key/base/超时 ≥600s                |
| 摘要空/拒绝                         | 安全分类或限流                   | 冷却 300s；连败熔断；查网关；Gate E 对照                             |
| 摘要 `429 max_parallel_requests` | 主聊 + 后台摘要并行超限（实测 limit=2） | 等主请求结束再压；手测勿把 TRIGGER 过低；后续可加摘要排队 / 降 `MAX_CONCURRENT` |
| 多会话只有一个在压                      | 全局并发占满                    | 查 `[MSG] ... skipping`；调 `MAX_CONCURRENT`              |


### 8.6 日志、容量与隐私

- 日志只落本机；轮转 10 MB × 3（~40 MB 上限）
- **永不记录完整请求体**：INFO 只记 token/字符数；DEBUG 仅记首尾各 500 字截断
- 密钥只从 env 读；不写日志、不进异常堆栈
- 磁盘无请求内容存储（状态全内存）

---

## 9. 风险与降级矩阵


| 风险                                   | 概率         | 影响                 | 应对 / 降级                                                      |
| ------------------------------------ | ---------- | ------------------ | ------------------------------------------------------------ |
| DSH base 不可配本地（Gate-A 失败）            | 中          | 代理不可行              | 转「嵌入 DSH 链路」立项；CLI 独立交付                                      |
| **无 cache，摘要成本/延迟超预期（Gate-E 暴露）**    | 高          | 压缩贵、同步压缩超时         | 调高 TRIGGER 减少频次；超时极端轮次走 CLI；分片串行改并行                          |
| 同步压缩（>256K 安全线）耗时 > 客户端超时            | 中          | 最需压缩的轮次反而 400      | 该路径只低频触发；给 DSH 调大超时；否则明确走 CLI 兜底                             |
| 内容 hash 匹配错位                         | 中          | 注入错误/漏注入           | 已选 content-hash；取「去最新 user 后前缀」稳定化；Phase 3 压测验证              |
| 代理崩溃/重启                              | 低          | 内存摘要丢失             | 计划任务重跑；下轮超阈值自动重压一次                                           |
| 网关限流/故障（含 `max_parallel_requests=2`） | **高**（已实测） | 后台摘要 429，主聊多半仍 200 | 冷却+熔断；TRIGGER=180K；**空闲后再压** + 429 重试 + `MAX_CONCURRENT=1`；极端走 CLI |
| DSH 更新改请求 shape                      | 中          | 解析错位               | 抓包 diff；未知字段透传不破坏                                            |
| 密钥可见性（代理见 Authorization）             | 低          | 凭证暴露面              | 仅 `127.0.0.1`、env、日志脱敏、无磁盘存储                                 |
| 单会话想全量历史（无 per-session off）          | 中          | 影响体验               | v1 先机器级 off；v1.1 补 per-session off（学 rolling-context `/off`） |


---

## 10. 本阶段明确不做

- 不改 DSH 源码 / 不做 DSH 内置压缩命令
- LiteLLM 服务端改造、第二模型部署
- 多机/中心化部署与遥测
- GUI（CLI + 日志）
- per-session off（v1 仅机器级开关，v1.1 补）

---

## 附录 A：环境变量速查


| 变量                                      | 默认        | 说明                      |
| --------------------------------------- | --------- | ----------------------- |
| `COMPANY_LLM_KEY/BASE/MODEL`            | —         | 聊天上游 / 摘要回退凭证，只从 env 读 |
| `DASH_COMPACT_SUMMARIZE_KEY/BASE/MODEL` | 回退 COMPANY_* | **推荐**：摘要专用凭证（另一把 key，不抢并行配额） |
| `DASH_COMPACT_PORT`                     | 4001      | 本地代理端口                  |
| `DASH_COMPACT_TRIGGER`                  | 180000    | 触发线（Gate-E 冻结）          |
| `DASH_COMPACT_TARGET`                   | 80000     | 压缩后近期原文上限（Gate-E 冻结）    |
| `DASH_COMPACT_MICRO_KEEP`               | 5         | micro 层保留最近 N 个 tool 结果 |
| `DASH_COMPACT_MAX_CONCURRENT`           | 1         | 跨会话并发压缩上限（网关并行常≈2）      |
| `DASH_COMPACT_IDLE_WAIT_S`              | 180       | 后台摘要前等待 HTTP 空闲；`0`=不等   |
| `DASH_COMPACT_SUMMARIZE_RETRIES`        | 4         | 摘要 429/503 重试次数         |
| `DASH_COMPACT_FAILURE_COOLDOWN`         | 300       | 同 key 失败冷却（秒）           |
| `DASH_COMPACT_MAX_FAILURES`             | 3         | 熔断阈值                    |
| `DASH_COMPACT_DISABLE`                  | 0         | 1 = 机器级关闭               |
| `DASH_COMPACT_LOG_LEVEL/MAX_MB/BACKUPS` | INFO/10/3 | 日志级别与轮转                 |
| `DASH_COMPACT_HOME`                     | `~\.dsh-compact` | 日志家目录            |


## 附录 B：术语对照（避免实现时写错）


| OpenAI `/v1/chat/completions`（本项目用）                 | 易误用（rolling-context 的 Anthropic 术语）           |
| --------------------------------------------------- | --------------------------------------------- |
| 消息 `role`: `system` / `user` / `assistant` / `tool` | `tool_use` / `tool_result` 是 content block 类型 |
| 工具调用：assistant 消息的 `tool_calls[]`                   | `tool_use` block                              |
| 工具结果：`role:"tool"` 消息 + `tool_call_id`              | `tool_result` block                           |
| 流式：SSE `data: {...}`，`delta`                        | Anthropic 的 `content_block_delta`             |
| 用量：`usage.prompt_tokens/completion_tokens`          | `input_tokens/output_tokens`                  |


---

## 附录 C：给执行 AI 的一句话

**Phase 0–5 代码已齐（proxy v0.4.0）。** 同事安装 → `HOW_TO_DEPLOY.md`；人工勾选 → `notes/phase5-checklist.md`。冻结：TRIGGER=180K、TARGET=80K、身份=content-hash；摘要宜配独立 `DASH_COMPACT_SUMMARIZE_KEY`。