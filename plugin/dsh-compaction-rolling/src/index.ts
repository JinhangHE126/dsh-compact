/**
 * dsh-compaction-rolling — rolling-merge compaction backend for DeepSeek Harness.
 *
 * Extends official `BasicCompactionEngine` and overrides ONLY `summarize()`
 * (the documented extension point). Phase 1 delegates to `super.summarize()`
 * so pressure / retention / session events match basic. Phase 2 will swap in
 * dsh-compact's rolling-merge prompt (`prompt.txt` / `build_summarize_prompt`).
 *
 * @module dsh-compaction-rolling
 */

import type { Context } from '@deepseek-ai/cordis'
import z from '@deepseek-ai/schemastery'
import {
  BasicCompactionEngine,
  type BasicCompactionConfig,
} from '@deepseek-ai/dsh-compaction-basic'
import type { Agent } from '@deepseek-ai/dsh-agent'

import type { SummarizationInput, SummaryResult } from './types.js'

export type { SummarizationInput, SummaryResult } from './types.js'

const num = z.number()
const str = z.string()

const modelPolicy = z.object({
  provider: z.string().required(),
  model: z.string().required(),
  thresholdRatio: num,
  retainRatio: num,
  retainTokens: z.number().step(1).min(0),
  summarizationProvider: str,
  summarizationModel: str,
  maxTokens: z.number().step(1).min(1),
  compactionRetries: z.number().step(1).min(0),
  maxOverflowRetries: z.number().step(1).min(0),
})

/** Keys accepted by `BasicCompactionEngine` (unknown keys throw in `super`). */
const BASE_KEYS = [
  'thresholdRatio',
  'retainRatio',
  'retainTokens',
  'summarizationProvider',
  'summarizationModel',
  'maxTokens',
  'compactionRetries',
  'maxOverflowRetries',
  'modelPolicies',
  'auto',
] as const

/** Plugin-facing config: basic policy fields + rolling-only flags. */
export interface RollingCompactionConfig extends BasicCompactionConfig {
  /**
   * When true (default), Phase 2 will merge into a prior rolling summary.
   * Phase 1 ignores this and still uses basic's one-shot summarizer.
   */
  rollingMerge?: boolean
}

export interface ResolvedRollingConfig {
  readonly rollingMerge: boolean
}

function resolveRollingConfig(
  config: RollingCompactionConfig = {},
): ResolvedRollingConfig {
  return {
    rollingMerge: config.rollingMerge ?? true,
  }
}

function pickBaseConfig(config: RollingCompactionConfig): BasicCompactionConfig {
  const cfg = config as Record<string, unknown>
  const base: Record<string, unknown> = {}
  for (const k of BASE_KEYS) {
    if (k in cfg) base[k] = cfg[k]
  }
  return base as BasicCompactionConfig
}

/**
 * Drop-in replacement for `dsh-compaction-basic`.
 * Registers as `ctx.compaction` — disable the built-in backend first.
 */
export class RollingCompactionEngine extends BasicCompactionEngine {
  static inject = ['llm', 'tokenMeter', 'sessions']

  static Config = z.object({
    thresholdRatio: num,
    retainRatio: num,
    retainTokens: z.number().step(1).min(0),
    summarizationProvider: str,
    summarizationModel: str,
    maxTokens: z.number().step(1).min(1),
    compactionRetries: z.number().step(1).min(0),
    maxOverflowRetries: z.number().step(1).min(0),
    modelPolicies: z.array(modelPolicy),
    auto: z.boolean(),
    rollingMerge: z.boolean(),
  })

  /** Additive rolling config (Phase 2 will drive summarize behaviour). */
  readonly rollingConfig: ResolvedRollingConfig

  constructor(ctx: Context, config: RollingCompactionConfig = {}) {
    super(ctx, pickBaseConfig(config))
    this.rollingConfig = resolveRollingConfig(config)
  }

  /**
   * Sole subclass hook. Phase 1 → basic summarizer.
   * Phase 2 → rolling-merge prompt from dsh-compact `prompt.txt`.
   */
  protected override async summarize(
    input: SummarizationInput,
    agent: Agent,
    signal?: AbortSignal,
  ): Promise<SummaryResult> {
    // Placeholder: keep behaviour identical to basic until Phase 2 lands.
    void this.rollingConfig
    return super.summarize(input, agent, signal)
  }
}

export default RollingCompactionEngine

export const name = 'dsh-compaction-rolling'

/** Cordis plugin entry — registers this backend as `ctx.compaction`. */
export function apply(ctx: Context): void {
  ctx.plugin(RollingCompactionEngine)
  const msg =
    '[dsh-compaction-rolling] registered as the `ctx.compaction` provider ' +
    '(Phase 1: basic summarizer; rolling-merge prompt lands in Phase 2). ' +
    'If the harness fails to start with a conflicting `ctx.compaction` error, ' +
    'disable dsh-compaction-basic in cordis.patch.yml.'
  if (ctx.logger?.info) ctx.logger.info(msg)
  else console.log(msg)
}
