# dsh-compaction-rolling

DSH / Cordis **compaction backend** that will port [dsh-compact](../..) rolling-merge summarization into DeepSeek Harness.

Phase 1 (this scaffold): drop-in replacement for `@deepseek-ai/dsh-compaction-basic` with the same pressure / retention / session events; `summarize()` still delegates to basic.

Phase 2 (next): replace the summarizer instruction with dsh-compact’s rolling-merge `prompt.txt`.

The Python proxy in the repo root stays for non-DSH OpenAI clients. Use **either** this plugin **or** the proxy route inside DSH — not both — to avoid double compaction.

## Requirements

- DeepSeek Harness with `ctx.llm`, `ctx.tokenMeter`, `ctx.sessions`
- Disable built-in `dsh-compaction-basic` (this package’s `cordis.patch.yml` does that on install)

## Local load (dev)

1. From this directory: `npm install` then optionally `npm run build`.
2. In `~/.dsh/cordis.patch.yml`, disable basic and point at the entry (see `cordis.yml`):

```yaml
- id: compaction-basic
  disabled: true
- insert:
  - id: compaction-rolling
    name: 'C:/Users/pg9/Desktop/dsh-compact/plugin/dsh-compaction-rolling/src/index.ts'
    config:
      thresholdRatio: 0.8
      retainRatio: 0.16
      maxTokens: 8192
      auto: true
      rollingMerge: true
```

3. Restart DSH. Log should contain `[dsh-compaction-rolling] registered as the ctx.compaction provider`.
4. Run `/compact` — behaviour should match basic until Phase 2.

## Layout

| File | Role |
|------|------|
| `src/index.ts` | `RollingCompactionEngine` + Cordis `apply` |
| `src/types.ts` | `SummarizationInput` / `SummaryResult` mirror |
| `cordis.patch.yml` | Disable basic + insert this plugin |
| `cordis.yml` | Loader examples |

## Reference

Pattern follows [dsh-compaction-pro](https://github.com/helibeiqi/dsh-compaction-pro) (`extends BasicCompactionEngine`, override only `summarize`).
