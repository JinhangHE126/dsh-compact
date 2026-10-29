/**
 * Local mirror of the BasicCompactionEngine `summarize()` contract.
 *
 * `@deepseek-ai/dsh-compaction-basic` does not re-export these from its root,
 * and its `./src/*` subpath is not in the published tarball. Copied from
 * `lib/types/summarizer.d.ts` so we stay wire-compatible with the protected hook.
 */

import type {
  ContentBlock,
  Message,
  TokenUsage,
  ToolSchema,
} from '@deepseek-ai/dsh-llm'

/** Replayed conversation prefix the summarizer condenses. */
export interface SummarizationInput {
  readonly system?: string
  readonly tools?: readonly ToolSchema[]
  readonly messages: readonly Message[]
}

/** Safe summary content plus the auxiliary call envelope. */
export type SummaryResult = {
  summary: ContentBlock[]
  provider: string
  model: string
  maxTokens?: number
  usage?: TokenUsage
} & (
  | {
      rawOutput: ContentBlock[]
      llmStreamCall: true
    }
  | {
      rawOutput?: ContentBlock[]
      llmStreamCall?: never
    }
)
