# dsh-compact 部署手册（macOS / MacBook）

在 Mac 上**从零安装 → 前台测试 →（可选）登录自启**。  
Windows 同事请看 [`HOW_TO_DEPLOY.md`](HOW_TO_DEPLOY.md)；架构细节见 [`DEPLOYMENT.md`](DEPLOYMENT.md)。

> 当前仓库的 `scripts/*.ps1` 仅适用于 Windows。Mac 用本文的 shell / LaunchAgent 步骤即可，**不需要**那些 ps1。

---

## 1. 它是干什么的

本机透明代理（`127.0.0.1:4001`），插在客户端与公司 LiteLLM 之间：

```text
DSH / 兼容客户端  →  http://127.0.0.1:4001/v1  →  dsh-compact  →  公司网关或直连上游
```

上下文过阈值后后台摘要压缩，下一轮注入「滚动摘要 + 近期原文」。只改发出去的请求体，不改本地会话文件；只监听回环地址。

---

## 2. 准备清单

| 项 | 要求 |
| --- | --- |
| 系统 | macOS 12+（Intel / Apple Silicon 均可） |
| Python | 3.10+（`python3 --version`） |
| 网络 | 能访问公司 LiteLLM（常需连公司 VPN / 内网），例如 `http://172.17.7.90:4000/v1` |
| 聊天 Key | 公司 Virtual Key（填在 DSH 里） |
| 摘要 Key（强烈推荐） | **另一路**凭证，例如 DeepSeek API，避免抢公司 `max_parallel_requests=2` |
| 本仓库 | clone 到本机，例如 `~/Desktop/dsh-compact` |

检查 Python：

```bash
python3 --version   # 需要 3.10+
```

若没有：用 [python.org](https://www.python.org/downloads/) 或 `brew install python@3.12`。

---

## 3. 安装依赖（一次）

```bash
cd ~/Desktop/dsh-compact   # 改成你的仓库路径

mkdir -p ~/.dsh-compact/{logs,samples,notes}

python3 -m venv ~/.dsh-compact/venv
source ~/.dsh-compact/venv/bin/activate
pip install -U pip
pip install -r requirements.txt

# 可选：跑单测
pytest tests -q
```

以后每次前台启动前：

```bash
source ~/.dsh-compact/venv/bin/activate
cd ~/Desktop/dsh-compact
```

---

## 4. 配置环境变量

在**将要启动 proxy 的同一个终端**里 `export`（或写入 `~/.zshrc` 后 `source ~/.zshrc`）。

### 4.1 聊天上游（公司网关）

```bash
export COMPANY_LLM_BASE="http://172.17.7.90:4000/v1"
export COMPANY_LLM_MODEL="company-code"
export COMPANY_LLM_KEY="sk-你的公司key"   # 摘要未单独配置时也会用到
```

### 4.2 摘要专用（推荐：DeepSeek，不抢公司并行配额）

```bash
export DASH_COMPACT_SUMMARIZE_BASE="https://api.deepseek.com/v1"
export DASH_COMPACT_SUMMARIZE_MODEL="deepseek-chat"
export DASH_COMPACT_SUMMARIZE_KEY="sk-你的deepseek密钥"
```

### 4.3 代理策略

**日常 / 接近生产：**

```bash
export DASH_COMPACT_PORT=4001
export DASH_COMPACT_TRIGGER=180000
export DASH_COMPACT_TARGET=80000
export DASH_COMPACT_MAX_CONCURRENT=1
export DASH_COMPACT_DISABLE=0
```

**Mac 上想尽快看到压缩（手测）：**

```bash
export DASH_COMPACT_TRIGGER=50000
export DASH_COMPACT_TARGET=20000
# 测完改回 180000 / 80000，避免过于频繁摘要
```

写入 `~/.zshrc` 示例（密钥勿提交 git）：

```bash
# --- dsh-compact（按需取消注释） ---
# export COMPANY_LLM_BASE="http://172.17.7.90:4000/v1"
# export COMPANY_LLM_MODEL="company-code"
# export COMPANY_LLM_KEY="sk-..."
# export DASH_COMPACT_SUMMARIZE_BASE="https://api.deepseek.com/v1"
# export DASH_COMPACT_SUMMARIZE_MODEL="deepseek-chat"
# export DASH_COMPACT_SUMMARIZE_KEY="sk-..."
# export DASH_COMPACT_TRIGGER=180000
# export DASH_COMPACT_TARGET=80000
```

---

## 5. 前台启动（Mac 测试主路径）

```bash
source ~/.dsh-compact/venv/bin/activate
cd ~/Desktop/dsh-compact

# 确认摘要变量已在「当前进程」里（不要为空）
echo "$DASH_COMPACT_SUMMARIZE_BASE"
echo "$DASH_COMPACT_SUMMARIZE_MODEL"

python proxy.py
```

期望启动输出类似：

```text
dsh-compact proxy v0.4.0 on http://127.0.0.1:4001
  upstream=http://172.17.7.90:4000/v1
  summarizer=https://api.deepseek.com/v1 model=deepseek-chat separate=True
  trigger=180000 target=80000
  disabled=False pid=...
  log=/Users/<你>/.dsh-compact/logs/proxy.log
```

另开一个终端做健康检查：

```bash
curl -s http://127.0.0.1:4001/health | python3 -m json.tool
```

关注：

| 字段 | 期望 |
| --- | --- |
| `enabled` | `true` |
| `summarizer_separate` | `true`（若配了独立摘要） |
| `summarizer` | `https://api.deepseek.com/v1` |
| `trigger` / `target` | 与你 export 的一致 |

停掉：在跑 `python proxy.py` 的终端按 `Ctrl+C`。

端口被占用时：

```bash
lsof -iTCP:4001 -sTCP:LISTEN
kill <PID>
```

---

## 6. 配置 DSH（或其它 OpenAI 兼容客户端）

1. Base URL → `http://127.0.0.1:4001/v1`
2. API Key → **公司** Virtual Key（聊天透传）
3. 模型名按客户端要求（多为 `company-code`）
4. Apply 后**重启会话 / 重启客户端**

Mac 上若 DSH 不可用，可用任意指向该 Base URL 的 OpenAI 兼容客户端做烟测。

---

## 7. 怎么确认在工作

日志：前台终端，或 `~/.dsh-compact/logs/proxy.log`。

| 日志 | 含义 |
| --- | --- |
| `[MSG] Forwarding ... 200` | 聊天透传成功 |
| `Compression #N` | 后台摘要完成（会打到 DeepSeek / 摘要上游） |
| `injected=True` 且 `tokens≈大->小` | 本轮已注入压缩结果 |
| `injected=False` 且 token 未过 trigger | 正常，尚未触发压缩 |
| 公司侧 `429 max_parallel_requests` | 多半是**聊天自己**并发打满；摘要已走 DeepSeek 时不应再因摘要抢槽 |

手测压缩：临时 `DASH_COMPACT_TRIGGER=50000`，聊到估算超过阈值，应出现 `Compression #N`；测完改回生产值并重启 proxy。

---

## 8.（可选）登录自启 — LaunchAgent

测试阶段**不必**做；需要开机常驻时再用。

1. 建启动脚本（路径按你的仓库改）：

```bash
mkdir -p ~/.dsh-compact
cat > ~/.dsh-compact/run_proxy.sh <<'EOF'
#!/bin/bash
set -euo pipefail
# 从你平时用的环境文件加载（推荐单独放密钥文件，权限 600）
ENV_FILE="${HOME}/.dsh-compact/env.sh"
if [[ -f "$ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$ENV_FILE"
fi
source "${HOME}/.dsh-compact/venv/bin/activate"
cd "${HOME}/Desktop/dsh-compact"
exec python proxy.py
EOF
chmod 700 ~/.dsh-compact/run_proxy.sh
```

2. 密钥文件示例（**不要**进 git）：

```bash
cat > ~/.dsh-compact/env.sh <<'EOF'
export COMPANY_LLM_BASE="http://172.17.7.90:4000/v1"
export COMPANY_LLM_MODEL="company-code"
export COMPANY_LLM_KEY="sk-公司key"
export DASH_COMPACT_SUMMARIZE_BASE="https://api.deepseek.com/v1"
export DASH_COMPACT_SUMMARIZE_MODEL="deepseek-chat"
export DASH_COMPACT_SUMMARIZE_KEY="sk-deepseek密钥"
export DASH_COMPACT_TRIGGER=180000
export DASH_COMPACT_TARGET=80000
export DASH_COMPACT_PORT=4001
EOF
chmod 600 ~/.dsh-compact/env.sh
```

3. LaunchAgent plist：

```bash
cat > ~/Library/LaunchAgents/com.dsh-compact.proxy.plist <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.dsh-compact.proxy</string>
  <key>ProgramArguments</key>
  <array>
    <string>${HOME}/.dsh-compact/run_proxy.sh</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>${HOME}/.dsh-compact/logs/launchd.out.log</string>
  <key>StandardErrorPath</key>
  <string>${HOME}/.dsh-compact/logs/launchd.err.log</string>
</dict>
</plist>
EOF

launchctl unload ~/Library/LaunchAgents/com.dsh-compact.proxy.plist 2>/dev/null || true
launchctl load ~/Library/LaunchAgents/com.dsh-compact.proxy.plist
curl -s http://127.0.0.1:4001/health
```

卸载自启：

```bash
launchctl unload ~/Library/LaunchAgents/com.dsh-compact.proxy.plist
rm -f ~/Library/LaunchAgents/com.dsh-compact.proxy.plist
```

注意：前台已跑着 `python proxy.py` 时不要再 load LaunchAgent，否则端口冲突。

---

## 9. 常见问题（Mac）

### Q1：`summarizer_separate: false`，摘要仍指向公司网关

当前 shell 里没有 `DASH_COMPACT_*`。先 `echo $DASH_COMPACT_SUMMARIZE_BASE`；为空则重新 `export`，再启动 `python proxy.py`。  
从 IDE 集成终端启动时，IDE 可能没继承你的 `~/.zshrc`——在同一终端里显式 `export` 最稳。

### Q2：连不上 `172.17.7.90`

Mac 需连公司 VPN / 内网。可先：

```bash
curl -sS -o /dev/null -w "%{http_code}\n" --connect-timeout 5 http://172.17.7.90:4000/v1/models
```

### Q3：公司聊天仍 429 `max_parallel_requests`

摘要已走 DeepSeek 后，这类 429 多半是**客户端并发聊天**打满公司配额（Limit=2），与压缩无关。错开请求或降低客户端并行即可。

### Q4：Apple Silicon 上 pip / pytest 报错

确认用的是 venv 里的解释器：`which python` 应指向 `~/.dsh-compact/venv/bin/python`。

### Q5：会话已打不开（应急）

```bash
source ~/.dsh-compact/venv/bin/activate
cd ~/Desktop/dsh-compact
python compact.py /path/to/session.jsonl -o brief.md
```

---

## 10. 最短检查清单（Mac 测试）

1. [ ] `python3` ≥ 3.10，venv + `pip install -r requirements.txt`  
2. [ ] 已 `export` 公司 `COMPANY_LLM_*`  
3. [ ] （推荐）已 `export` DeepSeek `DASH_COMPACT_SUMMARIZE_*`  
4. [ ] `python proxy.py` 显示 `separate=True` 且 summarizer 为 DeepSeek  
5. [ ] `curl http://127.0.0.1:4001/health` → 200  
6. [ ] 客户端 Base URL = `http://127.0.0.1:4001/v1`  
7. [ ] 过 trigger 后日志出现 `Compression #N` / `injected=True`  

---

## 11. 与 Windows 文档的差异

| | Windows | macOS |
| --- | --- | --- |
| 安装脚本 | `scripts/setup_home.ps1` 等 | 本文手动 venv |
| 环境变量 | User 注册表 + 注意 Cursor 进程不刷新 | `export` / `~/.zshrc` / `~/.dsh-compact/env.sh` |
| 自启 | HKCU Run / Startup | 可选 LaunchAgent |
| 健康检查 | `curl.exe` | `curl` |
| 日志 | `%USERPROFILE%\.dsh-compact\logs\` | `~/.dsh-compact/logs/` |

代码本身跨平台（Python + httpx）；Mac 与 Windows 共用同一套 `proxy.py` / `engine.py`。
