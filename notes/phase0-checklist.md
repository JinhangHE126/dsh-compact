# Phase 0 验收清单

> 按顺序做：A → B/D（同一次）→ C → E。  
> 工具：仓库根目录 `probe_server.py`；抓包落在 `probe_captures/`（已 gitignore）。

日期：2026-10-14    操作人：pg9

**Phase 0 结论：全部 PASS，代理方案成立，可进入 Phase 1。**

---

## Gate A — DSH API base 可指向本地

**步骤：**

1. `python probe_server.py`（保持运行）
2. DSH → Settings → Models → CompanyCode（Custom）→ Base URL = `http://127.0.0.1:4001/v1` → Apply，重启/新开会话
3. 在已有长会话发：`ping`

| 项 | 填写 |
|---|---|
| 结果 | ☑ PASS |
| probe 是否出现 POST | ☑ 是 |
| 实际 path | `/v1/chat/completions` |
| 备注 | `user-agent: deepseek-harness/0.2.0-rc.2`；`model: company-code`；协议 OpenAI Chat Completions。同一 `ping` 因 probe 假响应被客户端重试多次（body 相同），不影响判定。 |

**FAIL → 代理方案作废，转嵌入链路立项；CLI 可先行。**

---

## Gate B — 每轮全量重发 messages

**实测方式：** 未做空会话 Alice 双轮；在长会话只发 `ping`，body 仍带完整历史。

| 项 | 填写 |
|---|---|
| 结果 | ☑ PASS |
| round1 messages 数 | 59（含 system / user / assistant / tool） |
| round2 messages 数 | （同批重试均为 59；证据为「单轮 ping 仍带全历史」） |
| round2 是否仍含「Alice」 | ☐ N/A（用 ping + 全历史替代） |
| 备注 | body ≈ 414KB；含大量 `role:tool`。证明客户端全量重发，非服务端会话。代理改 `messages` 会生效。 |

**PASS 条件：** 客户端每轮携带全量历史。  
**FAIL → 代理改 body 无效，方案不可行。**

---

## Gate D — 会话标识头（与 A/B 同一次）

对照 probe 控制台打印的 **全部 header**，以及 body 顶层字段。

| 项 | 填写 |
|---|---|
| 结果方案 | ☑ content-hash |
| 找到的会话相关头 | none |
| body 里的 session/conversation/thread 字段 | none（顶层为 `model`, `messages`, `stream`, `stream_options`, `store`, `max_completion_tokens`, `tools`） |
| 同会话两轮该 id 是否相同 | ☑ N/A |
| 备注 | 仅有 `Authorization`、`X-Stainless-*`、`user-agent: deepseek-harness/...`。实现：`get_session_key` → **messages 内容 hash**（DEPLOYMENT §4.3）。 |

**有稳定同会话 id → `get_session_key` 用头；否则 → 内容 hash。**

---

## Gate C — 脱敏真实 session 样本

**步骤：** 从 `probe_captures/req_105045.bin` 拷贝到本地 samples 并脱敏。

| 项 | 填写 |
|---|---|
| 结果 | ☑ PASS（真实样本） |
| 样本路径 | `%USERPROFILE%\.dsh-compact\samples\session_probe.json` |
| 含超长 tool 输出 | ☑ 是（如 `web_fetch` 整页 README） |
| 含 base64 图（或已插入假 base64） | ☑ 是（`data:image` + `base64,` 检查为 True；file ≈ 0.41 MB） |
| 已脱敏（无 key/内网 IP/客户名） | ☑ 是 |

无真实文件可用假 jsonl，不挡 Phase 1。

---

## Gate E — 摘要成本 / 延迟

**步骤：** `python scripts/gate_e_summarize_probe.py`（公司网关，~117k chars 输入）。

| 项 | 填写 |
|---|---|
| 结果 | ☑ 已测 |
| HTTP status | 200 |
| elapsed_s | 27.7 |
| prompt_tokens | 63074 |
| completion_tokens | 375 |
| 建议 TRIGGER | 180000 |
| 建议 TARGET | 80000 |
| 同步压缩 | ☑ 可行 |
| 备注 | `cached_tokens: 0` → **无 prompt cache**，每次全价。单段 ~28s；若一次压缩切 3～5 段串行 ≈ 80～140s ≪ 600s 超时 → 同步路径可行。 |

粗判：单段耗时 T 秒、预计一次压缩切 K 段 → 同步最坏 ≈ K×T；若 > 客户端超时（如 600s）则同步不可行。

---

## 汇总（写完再开写 Phase 1 代码）

| Gate | 结论 |
|---|---|
| A | PASS — Base URL 可改本地，`POST /v1/chat/completions` 落地 |
| B | PASS — 全量重发（ping 仍带 59 条历史） |
| C | PASS — `~\.dsh-compact\samples\session_probe.json` |
| D | 身份方案：**content-hash** |
| E | TRIGGER=**180000** TARGET=**80000** 同步：**可行**；无 cache |

**Phase 0 完成 → 进入 Phase 1（`tokens.py` / `strip_heavy.py` / `micro_compact.py`）。**

### 抓包附带发现（实现时注意）

- 请求带 `stream` / `stream_options` → 代理须**流式透传**
- 请求带 `tools` → 切点须处理 `tool_calls` + `role:tool`
- 须透传未知字段：`store`、`max_completion_tokens` 等
- probe 假响应会导致 DSH 重试同一 body；正式代理需回真实上游流
