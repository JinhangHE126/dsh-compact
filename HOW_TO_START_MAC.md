# dsh-compact：Mac 终端启动（日常）

从零安装见 [`HOW_TO_DEPLOY_MAC.md`](HOW_TO_DEPLOY_MAC.md)。  
本文只讲：**已经装好后，用终端启动 / 停止 / 自检**。

Windows 同事 → [`HOW_TO_START_WINDOWS.md`](HOW_TO_START_WINDOWS.md)。

---

## 前提（做一次即可）

- 仓库在本机，例如：`/Users/<你>/Desktop/Projects/dsh-compact`
- 已建 venv：`~/.dsh-compact/venv`
- 已有密钥文件：`~/.dsh-compact/env.sh`（`chmod 600`）

`env.sh` 示例（**家用 DeepSeek**；公司内网请改成 `172.17.7.90` + `company-code`）：

```bash
export COMPANY_LLM_BASE="https://api.deepseek.com/v1"
export DASH_COMPACT_SUMMARIZE_BASE="https://api.deepseek.com/v1"
export DASH_COMPACT_SUMMARIZE_MODEL="deepseek-chat"
export DASH_COMPACT_SUMMARIZE_KEY="sk-你的密钥"
export DASH_COMPACT_TRIGGER=180000
export DASH_COMPACT_TARGET=80000
export DASH_COMPACT_PORT=4001
```

---

## 每次启动（复制即用）

把仓库路径换成你的：

```bash
source ~/.dsh-compact/venv/bin/activate
cd /Users/yvchuan/Desktop/Projects/dsh-compact
source ~/.dsh-compact/env.sh
python proxy.py
```

期望看到类似：

```text
dsh-compact proxy v0.4.0 on http://127.0.0.1:4001
  upstream=https://api.deepseek.com/v1
  summarizer=... separate=True
  trigger=180000 target=80000
```

**另开一个终端**做健康检查：

```bash
curl -s http://127.0.0.1:4001/health | python3 -m json.tool
```

关注：`enabled: true`，`trigger` / `target` 正确。

---

## Harness / DSH 配置

| 项 | 值 |
| --- | --- |
| Base URL | `http://127.0.0.1:4001/v1` |
| API protocol | **OpenAI Chat Completions**（不要用 Anthropic Messages） |
| API Key | 上游真实 key（DeepSeek / 公司 Virtual Key） |
| 模型 | 如 `deepseek-chat` 或 `company-code` |

Apply 后重启会话。聊天走代理；其它直连模型不要改 Base URL。

---

## 停止 / 端口占用

跑 `python proxy.py` 的终端：`Ctrl+C`。

端口被占：

```bash
lsof -iTCP:4001 -sTCP:LISTEN
kill <PID>
```

---

## 怎么确认在工作

看前台日志或 `~/.dsh-compact/logs/proxy.log`：

| 日志 | 含义 |
| --- | --- |
| `[MSG] Forwarding ... 200` | 透传成功 |
| `Compression #N` | 后台压缩完成 |
| `injected=True` 且 `tokens≈大->小` | 已注入压缩结果 |
| `injected=False` 且未过 trigger | 正常，尚未压缩 |

手测压缩（测完改回并重启）：

```bash
export DASH_COMPACT_TRIGGER=50000
export DASH_COMPACT_TARGET=20000
```

---

## （可选）登录自启

需要开机常驻时，见 [`HOW_TO_DEPLOY_MAC.md`](HOW_TO_DEPLOY_MAC.md) §8（LaunchAgent）。  
`run_proxy.sh` 里的 `cd` 必须指向你的真实仓库路径。

---

## 常见坑

1. **协议选错**：内置 DeepSeek 若走 Anthropic `/v1/messages`，代理会 404；请用 OpenAI Chat Completions 的第三方/自定义入口。  
2. **IDE 终端没读到 `env.sh`**：启动前务必 `source ~/.dsh-compact/env.sh`。  
3. **公司内网**：家用 Mac 需 VPN；否则把 `COMPANY_LLM_BASE` 换成公网 API。
