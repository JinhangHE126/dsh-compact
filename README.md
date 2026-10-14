# dsh-compact

DeepSeek Harness（DSH）会话上下文**自动压缩**：在 DSH 与公司 LLM 网关之间（或请求链路内）自动收短上下文，**同一会话继续聊**，避免 `ContextWindowExceededError`（400）。

**偏好：程序自动压缩，而不是**「跑工具出 `brief.md` → 开新会话粘贴续聊」。

---

## 要解决的问题

1. 旧 DSH 会话无法继续发消息（ContextWindowExceeded）
2. 不想靠人工交接：压缩应在后台/请求链路上自动发生
3. 压缩过程本身也不能超 256K（分段摘要 / 只压旧留新）
4. 会话里常有 WebP/base64 图片与超大工具输出，必须先剥离或裁掉再摘要

## 目标形态（优先）

**自动压缩（主路径）** — 学 [claude-rolling-context](https://github.com/nodenestor/claude-rolling-context)：

- 对话不停、用户无感
- 超阈值后自动触发
- **只压旧消息**，近期原文保留
- 发给 API 的 context ≈ **滚动摘要 + 近期原文**
- 异步优先：尽量不挡当前这一轮请求

实现落点（择一或组合，后续单独立项细化）：

1. **透明代理**：DSH → 本地代理 → LiteLLM（最接近 rolling-context）
2. **嵌入请求链路**：若 DSH/中间层可改，在发请求前或每轮回复后自动 compact（学 [contextkit](https://github.com/drandrewlaw/contextkit) 的引擎，但策略用「留新压旧」）

## 次要路径（应急，非首选）

离线 CLI：读取已爆窗会话 → 生成 `brief.md` → 新会话粘贴。仅在自动压缩未就绪、或会话已彻底打不开时使用。

## 非目标

- 不改成「用户每次手动跑工具再开新对话」的主流程
- 第一版不部署第二个 1M 上下文模型 / 不做「靠更大窗口硬撑」
- 不替代公司网关鉴权与限流
- 第一版不做复杂 GUI

## 成功标准

1. 正常长会话中，用户**无需**手动 compact / 粘贴 brief，即可跨多轮继续任务
2. 触发后发出去的上下文明显短于原文，且保留：目标、结论、关键路径、未完成项、坑点
3. **近期若干轮原文保留**；更早内容进滚动摘要（可多次 merge，而不是整段一刀切成一份 summary）
4. 自动剥离或占位 `data:image...;base64,...` 等重内容；旧工具大输出可先「免费裁切」再视情况摘要
5. compact 自身过程不报 400；失败有冷却 / 熔断，不死循环烧网关
6. （应急）离线 CLI 仍能对已超窗会话产出可粘贴的 `brief.md`

---

## 公司环境

| 项 | 值 |
| --- | --- |
| LLM 网关 | LiteLLM @ `http://172.17.7.90:4000/v1` |
| 模型名 | `company-code`（上游 Bonsai，ctx ≈ 256K / `--ctx-size 262144`） |
| 鉴权 | 员工 Virtual Key（`sk-...`），**不要用 Master Key** |
| 超时 | 网关 `request_timeout=600`，客户端建议 ≥ 600s |
| 网络 | 需能访问内网 `172.17.7.90`（公司网络） |
| 客户端 OS | Windows 为主 |

**背景事实：**

- DSH / Coding Agent 长会话 + 工具输出 + 图片，经常把单次请求打到 40 万+ tokens，超过 256K 后网关直接 400
- DSH **没有**内置类似 Claude Code `/compact` 的功能
- 服务器上 **没有**更大上下文的第二模型
- LiteLLM 配置在服务器：`~/CaelumHE/llm-gateway/litellm_config.yaml`，目前只有 `company-code`
- Bonsai 已加 ffmpeg 镜像 tag（解决 WebP 解码）；与本工具无关，但说明多模态请求真实存在

---

## 产品形态（方向）

```text
dsh-compact/
  README.md
  # 主路径（待定实现形态）
  proxy/ 或 middleware/   # 透明代理 或 嵌入式 compact 引擎
  # 应急路径（可选）
  compact.py              # 离线 CLI：session → brief.md
  session_reader.py
  chunker.py
  summarizer.py
  prompt.txt
  requirements.txt
```

### 环境变量

```bash
export COMPANY_LLM_KEY=sk-xxxx
export COMPANY_LLM_BASE=http://172.17.7.90:4000/v1
export COMPANY_LLM_MODEL=company-code
```

Windows PowerShell：

```powershell
$env:COMPANY_LLM_KEY = "sk-xxxx"
$env:COMPANY_LLM_BASE = "http://172.17.7.90:4000/v1"
$env:COMPANY_LLM_MODEL = "company-code"
```

密钥**只从环境变量读**，禁止写进仓库。

### 启动代理（主路径）

```powershell
$env:COMPANY_LLM_KEY = "sk-..."
$env:COMPANY_LLM_BASE = "http://172.17.7.90:4000/v1"
$env:COMPANY_LLM_MODEL = "company-code"
python proxy.py
# curl http://127.0.0.1:4001/health
```

DSH → Settings → Models → CompanyCode → Base URL = `http://127.0.0.1:4001/v1` → Apply，重启会话。

### 用户流程（目标）

1. 正常使用 DSH（流量经本地代理）
2. 上下文接近阈值 → **自动**只压旧、留近（后台压缩，下轮注入）
3. 同一会话继续聊，无需开新会话、无需粘贴 brief

### 应急流程（仅当会话已打不开）

```bash
python compact.py /path/to/session_xxx.jsonl -o brief.md
```

1. 运行工具得到 `brief.md`
2. DSH 开新会话
3. 粘贴 brief + 「按上述 brief 继续」

---

## 核心算法（留新压旧）

学 rolling-context，**不要**做成 contextkit 默认那种「整段对话 → 一份摘要」：

```text
每轮请求 / 回复后检查 token（警戒线 < 硬上限）
  → 未超阈值：原样转发
  → 超阈值：
       1) 可选 micro：旧 tool_result 换成占位（免费，不调 LLM）
       2) 仍超：只摘要「旧消息」→ 与既有滚动摘要 merge
       3) 近期消息原文保留
       4) 发给网关的 messages = [滚动摘要] + [近期原文]
  → 压缩尽量异步；本轮可先放行，下一轮再注入结果（若架构允许）
```

同时保留「防自身超窗」手段：

- strip 重内容（base64 图、超长 dump）后再摘要
- 单次摘要输入保守切段（例如 ~60k chars）
- 失败缩小 chunk 重试；连续失败熔断（学 contextkit）

### 摘要应保留的信息

- 任务目标 / 已确定结论
- 关键文件路径与改动
- 未完成项与下一步
- 坑点与环境约束
- 禁止逐句复述、禁止大段代码；中文、Markdown 小标题

---

## 参考开源（学思路）

| 优先级 | 项目 | 学什么 | 注意 |
| --- | --- | --- | --- |
| 1 | [claude-rolling-context](https://github.com/nodenestor/claude-rolling-context) | **主策略**：透明代理；只压旧、留近；滚动 merge；异步不挡当前轮；自定义 summarizer 指向公司网关 | Claude Code 插件，**不能直接装到 DSH**；要自实现同构 |
| 2 | [contextkit](https://github.com/drandrewlaw/contextkit) | 阈值检测、`autoCompact` 节奏、micro-compact（裁旧 tool result）、熔断、token 估算 | 默认 full compact 是「整段 → 一份摘要」；我们要改成留新压旧 |
| 3（可选） | [ctxintel](https://github.com/nccsuzzi/ctxintel) | 第一层免费粗过滤（不调 LLM） | — |

**对照（避免选型搞反）：**

| | rolling-context | contextkit |
| --- | --- | --- |
| 形态 | 透明代理（Claude Code ↔ API） | 嵌入式库（你每轮调 `autoCompact`） |
| 压完形态 | 滚动摘要 + **近期原文** | 边界标记 + **整段摘要**（默认） |
| 时机 | 超阈值后台压，尽量不挡本轮 | 你显式 await，常同步 |
| 对我们 | **产品主策略应对齐它** | **引擎零件可借鉴，策略别照搬** |

不要优先去找「DSH 专用 compact」——基本不存在；本项目就是补这个缺口。  
contextkit 是 TypeScript；自动压缩层与应急 CLI 均用 **Python 自实现**，不嵌 npm。

---

## 技术约束

- Python 3.10+
- HTTP 客户端：httpx 或 requests
- 超时 ≥ 600s
- 密钥只从环境变量读
- 自动路径需能对接 OpenAI 兼容的 `/v1`（公司 LiteLLM）

---

## 查找真实 DSH session（Windows）

路径因安装方式而异，可先搜：

```powershell
Get-ChildItem -Path $env:USERPROFILE -Recurse -Include *.jsonl,*.json -ErrorAction SilentlyContinue |
  Where-Object { $_.Length -gt 100KB -and $_.FullName -match 'session|transcript|chat|dsh|harness|agent' } |
  Select-Object FullName, Length, LastWriteTime |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 30
```

脱敏样例：删掉 API key、内网 IP、客户名；**保留** `role`/`content` 结构、一两段超长 tool 输出、一段 `data:image/...;base64,...`，便于验证 strip / micro-compact。

没有真实样例前：可用通用 OpenAI chat messages json/jsonl 假数据跑通应急 CLI 或压缩引擎单测。

---

## 验收步骤（建议）

**主路径（自动压缩）：**

1. 配置 `COMPANY_LLM_*`，启用代理或嵌入式引擎
2. 用长会话 / 重复填充把上下文推过触发阈值
3. 确认：同一会话可继续；发出去的 messages 已变短；近期轮次仍是原文；摘要含目标/结论/路径/未完成/坑点
4. 确认过程无 400；失败有熔断而不是疯狂重试

**应急路径（离线 brief）：**

1. `pip install -r requirements.txt`
2. `python compact.py <session> -o brief.md`
3. 新开 DSH 粘贴 brief，验证可交接

---

## 实现状态

| 模块 | 状态 |
| --- | --- |
| Phase 0 五 Gate（抓包 / 样本 / 摘要压测） | **已完成 2026-10-14**（见 `notes/phase0-checklist.md`） |
| 冻结决策：content-hash；TRIGGER=180K；TARGET=80K；同步可行 | **已冻结** |
| Phase 1：`tokens.py` / `strip_heavy.py` / `micro_compact.py` + 单测 | **已完成** |
| Phase 2：`prompt.txt` / `engine.py` + mock e2e 加固 | **已完成** |
| Phase 3：`proxy.py`（content-hash / 异步注入 / 流式透传 / health） | **已完成**（`pytest tests` 全绿） |
| 留新压旧 + 滚动摘要 merge | **已实现并挂到代理** |
| 应急 CLI（session → brief.md） | 待实现（次要） |
| 真实 DSH session 样本 | **有**：`~\.dsh-compact\samples\session_probe.json` |
| 端到端超窗自测 | 待实现 |

---

## 给执行 AI 的一句话

Phase 0 已过：身份用 **content-hash**，TRIGGER/TARGET=**180K/80K**，无 cache、同步压缩可行。按 DEPLOYMENT Phase 1→5 实现**自动、留新压旧**（对齐 rolling-context；OpenAI `/v1/chat/completions` + 流式透传；摘要走公司网关）。contextkit 只借鉴阈值、micro-compact、熔断。离线 `brief.md` CLI 仅作应急。
