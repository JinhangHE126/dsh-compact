# Phase 5 部署与演练清单

> **安装步骤照做：** [`HOW_TO_DEPLOY.md`](../HOW_TO_DEPLOY.md)  
> 配套：`DEPLOYMENT.md` §7 / §8。代码侧已提供：文件日志、摘要 429 重试、空闲后再压、登录自启、独立摘要 key、稠密会话切点。

## 0. 一次性本机准备

```powershell
cd C:\Users\<你>\Desktop\dsh-compact
# 必须 User 级密钥（登录自启读不到临时 $env:）
[Environment]::SetEnvironmentVariable("COMPANY_LLM_KEY", "sk-...", "User")
# 推荐：摘要另用一把 key，避免抢聊天的 max_parallel_requests
[Environment]::SetEnvironmentVariable("DASH_COMPACT_SUMMARIZE_KEY", "sk-sum-...", "User")
[Environment]::SetEnvironmentVariable("DASH_COMPACT_SUMMARIZE_BASE", "http://llm-gateway.example:4000/v1", "User")

powershell -ExecutionPolicy Bypass -File .\scripts\setup_home.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\install_autostart.ps1
# 立即启动（HKCU Run / Startup；计划任务可能因权限不可用）
Start-Process -FilePath "$env:USERPROFILE\.dsh-compact\run_proxy_task.cmd"
# 新开终端再 curl（才能读到刚写的 User 环境变量）
curl.exe http://127.0.0.1:4001/health
```

确认 `health` 里：`version` ≥ `0.4.0`，`enabled: true`，`trigger` = `180000`；若配了独立摘要 key，则 `summarizer_separate: true`。

日志：`%USERPROFILE%\.dsh-compact\logs\proxy.log`


| 项                 | 状态  |
| ----------------- | --- |
| setup_home + 生产阈值 | ☐   |
| install_autostart | ☐   |
| `/health` 200     | ☐   |
| （推荐）`summarizer_separate: true` | ☐   |




## 1. DSH 日常接入（§7 step 4）

1. CompanyCode Base URL = `http://127.0.0.1:4001/v1` → Apply → 重启 DSH
2. 正常聊几轮；日志出现 `[MSG] Forwarding...`
3. 生产阈值下短会话**不会**每轮压缩（需接近 180K）— 属预期
4. 长会话过 trigger 后应见 `Compression #N` 与后续 `injected=True`（手测可临时降 TRIGGER，测完改回 180000）


| 项                  | 状态  |
| ------------------ | --- |
| Base URL 指向代理      | ☐   |
| 短会话可聊 / Forwarding | ☐   |
| 过阈值后压缩+注入生效 | ☐   |




## 2. 登录自启（§7 step 5）

1. 注销或重启（可选；或 `schtasks /Run` 代替）
2. 不手动开终端，`curl http://127.0.0.1:4001/health` 仍 200


| 项             | 状态  |
| ------------- | --- |
| 自启后 health OK | ☐   |




## 3. 三演练（§7 step 6 / §8.4）



### ① 熔断

连续摘要失败 3 次后引擎冷却；日志有 `cooldown` / `failure`。可用临时错 KEY 验证后改回（谨慎）。


| 项              | 状态  |
| -------------- | --- |
| 熔断行为可接受 / 主聊不崩 | ☐   |




### ② off（机器级关压缩）

```powershell
[Environment]::SetEnvironmentVariable("DASH_COMPACT_DISABLE", "1", "User")
# 重启代理（结束任务后 schtasks /Run，或杀占用 4001 的进程）
```

`/health` 里 `enabled: false`；请求仍透传。测完改回 `0` 并重启代理。


| 项             | 状态  |
| ------------- | --- |
| DISABLE=1 仅透传 | ☐   |
| 改回 0          | ☐   |




### ③ 回滚直连

```powershell
# DSH Base URL → http://llm-gateway.example:4000/v1 ，重启 DSH
# 可选：schtasks /End /TN dsh-compact-proxy
```

旧会话 transcript 仍在；可继续直连。


| 项       | 状态        | ☐   |
| ------- | --------- | --- |
| 回滚后直连可用 |           |     |
|         | （可选）再切回代理 | ☐   |




## 4. 完成


| 项                                          | 状态  |
| ------------------------------------------ | --- |
| 卸载自启（若不要）`scripts\uninstall_autostart.ps1` | ☐   |
| 文档归档 / Wiki                                | ☐   |


卸载：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\uninstall_autostart.ps1
```

