# dsh-compact 参考实现对照清单

> 配套：`README.md`（需求）、`DEPLOYMENT.md`（部署与模块布局）。
> 本文件回答：**从 rolling-context / contextkit 各抄什么设计，落到哪个 Python 模块，抄的时候要改什么。**
>
> 总原则：**不 fork，只抄策略、不抄代码。** 两个项目的代码大量绑定各自生态（Anthropic / Claude Code / npm），直接 clone 反而要删掉大半再重写大半。

---

## 0. 模块落点（与 DEPLOYMENT.md 附录 A 一致）

| Python 模块 | 职责 |
| --- | --- |
| `proxy.py` | 本地 HTTP 代理（127.0.0.1），收 DSH 流量、转 LiteLLM、注入压缩结果、异步触发 |
| `engine.py` | 留新压旧 + 滚动 merge 的核心压缩引擎（对应 rolling-context 的 `compressor.py`） |
| `micro_compact.py` | 免费层：清旧 tool_result + 图片占位（对应 contextkit 的 `micro.ts`） |
| `tokens.py` | token 估算：真实 usage 优先 + 字符估算 + 33% buffer + 按轮分组 |
| `strip_heavy.py` | base64 / 超长 dump 剥离 |
| `prompt.txt` | 中文摘要 prompt |
| `session_reader.py` / `compact.py` | 应急离线 CLI（次要路径） |

---

## 1. 从 rolling-context 抄什么

来源文件：`proxy/compressor.py`（核心）、`proxy/server.py`（无状态匹配/异步注入，README 描述）。

| 源码位置 | 抄的设计 | 落到模块 | DSH 适配要点 |
| --- | --- | --- | --- |
| `RollingCompressor.compress()` 主流程 | 压缩总流程：算 keep_ratio → 找切点 → 摘要旧段 → 拼 `[summary]+[ack]+recent` | `engine.py` | 换 OpenAI `/v1/chat/completions` 的 messages 结构 |
| `_find_keep_index()` | 按 `target_tokens / real_tokens` 比例从尾部倒推切割点 | `engine.py` | 真实 usage 优先；无 usage 用字符估算 + 33% buffer |
| `_safe_cut()` | 切点安全：切点必须是纯 user 条、前一条不能有未闭合的工具调用 | `engine.py` | OpenAI 里是 assistant 消息带 `tool_calls`，结果在 `role:"tool"` |
| `_has_tool_use()` / `_has_tool_result()` | 工具孤儿检测 | `engine.py` | 语义不变，字段从 `tool_use`/`tool_result` 改成 `tool_calls`/`tool` |
| `SUMMARY_RULES` 模板 | 摘要结构：Active Goal / Previous Goals / Timeline / Current State / Key Details | `prompt.txt` | 中文化；可补 contextkit 的「Errors/Fixes、Next Step」等字段 |
| `SUMMARIZE_PROMPT` + `existing_summary_section` | **滚动 merge**：新摘要并入既有摘要（保留细节、扩展 timeline） | `prompt.txt` / `engine.py` | 直接抄这个思路，是「留新压旧」的核心 |
| `summary_message` + `ack_message` 结构 | 注入 `[ROLLING_CONTEXT_SUMMARY]` 包裹 + 一条 assistant ack | `engine.py` | marker 可自定义；ack 可选 |
| `_parse_message_stream()` / `_describe()` | 摘要失败诊断：记录 stop_reason / usage / 错误体首 500 字 | `engine.py` | 改成 OpenAI 的 `choices` / `usage` / error body 结构 |
| 无状态 + 内容 hash 匹配（server 层） | 压缩结果按 messages hash 存内存，下一轮 hash 命中即注入 | `proxy.py` | **先确认 DSH 是否带会话标识头**；若无则退到内容 hash（见 §3 风险） |
| 异步注入：本轮放行、下轮注入 | 后台压 + 下一轮注入，尽量不挡当前轮 | `proxy.py` | 同；例外（超 256K 安全线）见 DEPLOYMENT §4.4 |

---

## 2. 从 contextkit 抄什么

来源文件：`src/compactor/engine.ts`、`src/compactor/micro.ts`、`src/compactor/prompt.ts`、`src/tokens/`。

| 源码位置 | 抄的设计 | 落到模块 | 适配要点 |
| --- | --- | --- | --- |
| `ContextKit.shouldCompact()` + `autoCompactThreshold` | 阈值判定：`effectiveWindow - autoCompactBuffer` | `engine.py` | 窗口按 256K，buffer 需实测校准（勿照搬 13K） |
| `ContextKit.autoCompact()` | 三层节奏：micro 免费层 → full 摘要 → 熔断 | `engine.py` | 主流程顺序直接用 |
| `microCompact()`（micro.ts） | 裁旧 tool_result、只留最近 N 条，其余换占位 | `micro_compact.py` | OpenAI：结果在 `role:"tool"` 消息里 |
| `stripImages()` | 图片块换文本占位 | `strip_heavy.py` | 加 WebP / base64 / 超长 dump 场景 |
| `groupMessagesByRound()` | 按轮分组；摘要超长时丢最老 20% 重试 | `tokens.py` / `engine.py` | 防摘要请求自身超窗 |
| `buildCompactPrompt()`（9 段） | 摘要字段：Request/Concepts/Files/Errors/Decisions/UserMsgs/Pending/Current/Next | `prompt.txt` | 中文化；与 rolling-context 模板合并取舍 |
| 熔断 `consecutiveFailures` + `maxConsecutiveFailures` | 连败熔断 | `engine.py` | 同；`shouldCompact` 熔断后返回否 |
| `estimateTokens` 混合估算 | usage 优先 + 字符估算（1 tok ≈ 4 chars + 33% buffer） | `tokens.py` | 同 |

---

## 3. 明确「不搬」的清单（clone 的坏处所在）

这些是 rolling-context 里和你环境无关、搬过来只会拖累的部分：

| 不搬 | 原因 |
| --- | --- |
| `mitm_frontend.py`（HTTPS 代理 + 本地 CA 解 TLS） | LiteLLM 明文 HTTP，无需 TLS 终结 |
| 走 `HTTPS_PROXY` 绕过 Anthropic base URL 门禁 | DSH base 可直接改成 `127.0.0.1` |
| Native 模式（克隆请求 + prompt cache 零成本摘要） | `company-code` 无 cache，直接走 flattened 全价摘要 |
| Native 模式绕 OAuth 订阅 429 overage 分类 | 无此问题 |
| Anthropic `/v1/messages` 解析（`tool_use`/`tool_result`、system block） | 你是 OpenAI `/v1/chat/completions` |
| 插件生命周期：`hooks/`、marketplace、install/uninstall、upgrade drain、PID 回收、transport flip | 全是 Claude Code 插件机制，DSH 用不上 |
| contextkit 的 TS 类型/构建（tsup、vitest） | 你是 Python |

---

## 4. 实现前硬缺口 — Phase 0 已关闭（2026-10-14）

详见 `notes/phase0-checklist.md` / DEPLOYMENT §3.3。

| 缺口 | 冻结结论 |
|---|---|
| 会话身份 | **content-hash**（无 session 头；前缀 hash 做 key） |
| TRIGGER / TARGET | **180000 / 80000** |
| 摘要延迟 | 单段 27.7s / ~63K prompt；`cached_tokens=0`；同步可行 |
| 请求形态 | OpenAI `/v1/chat/completions` + `stream` + `tools`；须流式透传 |

---

## 5. 落地顺序建议

**Phase 0–2 已完成 → 下一步 Phase 3：**

1. ~~`tokens.py`~~ ✅
2. ~~`strip_heavy.py` + `micro_compact.py`~~ ✅
3. ~~`prompt.txt`~~ ✅
4. ~~`engine.py` + mock e2e（混合会话 / 熔断 / merge）~~ ✅
5. ~~`proxy.py`~~ ✅
6. 应急 `session_reader.py` / `compact.py` ← **可选下一步**
7. 真网关超窗烟测 + 登录自启演练（DEPLOYMENT §7 step 3+）
