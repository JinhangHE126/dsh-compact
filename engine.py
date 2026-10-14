"""
Rolling-context compression engine for OpenAI chat messages.

Keep recent messages verbatim; summarize older ones into a rolling summary.
"""

from __future__ import annotations

import copy
import logging
import os
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

import httpx

from micro_compact import micro_compact
from strip_heavy import strip_heavy
from tokens import estimate_messages, estimate_message_tokens, group_messages_by_round

log = logging.getLogger("dsh-compact.engine")

SUMMARY_MARKER = "[ROLLING_CONTEXT_SUMMARY]"
SUMMARY_END_MARKER = "[/ROLLING_CONTEXT_SUMMARY]"

DEFAULT_TRIGGER = 180_000
DEFAULT_TARGET = 80_000
DEFAULT_MAX_FAILURES = 3
DEFAULT_COOLDOWN_S = 300
DEFAULT_CHUNK_CHARS = 60_000
DEFAULT_SUMMARY_MAX_TOKENS = 4_000

PROMPT_PATH = Path(__file__).resolve().parent / "prompt.txt"


SummarizeFn = Callable[[str], str]


def load_prompt_template() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def _message_content_str(msg: Mapping[str, Any]) -> str:
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict) and b.get("type") == "text":
                parts.append(b.get("text") or "")
            else:
                parts.append(str(b))
        return "\n".join(parts)
    return "" if content is None else str(content)


def has_summary(messages: Sequence[Mapping[str, Any]]) -> bool:
    if not messages:
        return False
    # summary is usually first user after optional system
    for msg in messages[:3]:
        if SUMMARY_MARKER in _message_content_str(msg):
            return True
    return False


def extract_summary(messages: Sequence[Mapping[str, Any]]) -> str:
    for msg in messages[:3]:
        text = _message_content_str(msg)
        if SUMMARY_MARKER not in text:
            continue
        start = text.find(SUMMARY_MARKER) + len(SUMMARY_MARKER)
        end = text.find(SUMMARY_END_MARKER)
        if end > start:
            return text[start:end].strip()
        return text[start:].strip()
    return ""


def _has_tool_calls(msg: Mapping[str, Any]) -> bool:
    calls = msg.get("tool_calls")
    return bool(calls)


def _is_tool_role(msg: Mapping[str, Any]) -> bool:
    return msg.get("role") == "tool"


def _is_plain_user(msg: Mapping[str, Any]) -> bool:
    return msg.get("role") == "user" and not _is_tool_role(msg)


def find_keep_index(messages: Sequence[Mapping[str, Any]], keep_ratio: float) -> int:
    """Find cut index so messages[cut:] ≈ keep_ratio of total chars/tokens."""
    if len(messages) <= 4:
        return 0
    max_idx = len(messages) - 4
    total = sum(estimate_message_tokens(m) for m in messages)
    target = int(total * keep_ratio)
    accumulated = 0
    for i in range(len(messages) - 1, -1, -1):
        accumulated += estimate_message_tokens(messages[i])
        if accumulated >= target:
            # Prefer next plain user at or after i
            for j in range(i, len(messages)):
                if _is_plain_user(messages[j]):
                    return min(j, max_idx)
            return min(i + 1, max_idx)
    return 0


def safe_cut(messages: Sequence[Mapping[str, Any]], cut: int, floor: int) -> int:
    """
    Walk cut back so messages[cut:] is a valid conversation start.

    - messages[cut] must be a plain user message
    - messages[cut-1] must not have tool_calls (would orphan tool results)
    - messages[cut] must not be role=tool
    """
    while cut > floor:
        m = messages[cut]
        if not _is_plain_user(m):
            cut -= 1
            continue
        prev = messages[cut - 1]
        if _has_tool_calls(prev):
            cut -= 1
            continue
        if _is_tool_role(prev) and cut - 1 > floor:
            # Prefer not to start right after a tool without its assistant
            # but starting at user after tool is ok if prev isn't tool_calls.
            pass
        return cut
    return cut


def messages_to_text(messages: Sequence[Mapping[str, Any]], per_msg_cap: int = 4_000) -> str:
    parts: list[str] = []
    for msg in messages:
        role = msg.get("role", "?")
        text = _message_content_str(msg)
        if msg.get("tool_calls"):
            for call in msg["tool_calls"]:
                if not isinstance(call, dict):
                    continue
                fn = call.get("function") or {}
                args = str(fn.get("arguments") or "")
                if len(args) > 500:
                    args = args[:400] + "...[truncated]"
                text += f"\n[ToolCall: {fn.get('name')}({args})]"
        if len(text) > per_msg_cap:
            text = text[: per_msg_cap - 200] + "\n...[truncated]...\n" + text[-200:]
        parts.append(f"**{role}**: {text}")
    return "\n\n".join(parts)


def build_summarize_prompt(
    conversation: str,
    existing_summary: str = "",
    template: Optional[str] = None,
) -> str:
    tpl = template if template is not None else load_prompt_template()
    if existing_summary:
        existing_section = (
            "## 既有滚动摘要（必须并入：保留全部细节，扩展 Timeline）\n"
            f"{existing_summary}\n"
        )
    else:
        existing_section = ""
    return (
        tpl.replace("{{existing_summary_section}}", existing_section).replace(
            "{{conversation}}", conversation
        )
    )


def make_http_summarizer(
    *,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    timeout: float = 600.0,
    max_tokens: int = DEFAULT_SUMMARY_MAX_TOKENS,
    client: Optional[httpx.Client] = None,
) -> SummarizeFn:
    """Return a summarize(prompt)->text function hitting OpenAI-compatible API."""
    base = (base_url or os.environ.get("COMPANY_LLM_BASE") or "").rstrip("/")
    key = api_key or os.environ.get("COMPANY_LLM_KEY") or ""
    mdl = model or os.environ.get("COMPANY_LLM_MODEL") or "company-code"
    owns_client = client is None
    http = client or httpx.Client(timeout=timeout)

    def summarize(prompt: str) -> str:
        if not base or not key:
            raise RuntimeError("COMPANY_LLM_BASE / COMPANY_LLM_KEY required for summarizer")
        url = f"{base}/chat/completions"
        resp = http.post(
            url,
            headers={"Authorization": f"Bearer {key}"},
            json={
                "model": mdl,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
            },
        )
        if resp.status_code != 200:
            raise RuntimeError(
                f"Summarizer HTTP {resp.status_code}: {resp.text[:500]}"
            )
        data = resp.json()
        try:
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            raise RuntimeError(f"Bad summarizer response: {str(data)[:300]}") from e
        if not text or not str(text).strip():
            raise RuntimeError("Summarizer returned empty content")
        return str(text).strip()

    summarize._client = http  # type: ignore[attr-defined]
    summarize._owns_client = owns_client  # type: ignore[attr-defined]
    return summarize


class CompactEngine:
    def __init__(
        self,
        *,
        trigger_tokens: int = DEFAULT_TRIGGER,
        target_tokens: int = DEFAULT_TARGET,
        max_failures: int = DEFAULT_MAX_FAILURES,
        cooldown_s: float = DEFAULT_COOLDOWN_S,
        chunk_chars: int = DEFAULT_CHUNK_CHARS,
        micro_keep_recent: int = 5,
        summarize: Optional[SummarizeFn] = None,
    ) -> None:
        self.trigger_tokens = trigger_tokens
        self.target_tokens = target_tokens
        self.max_failures = max_failures
        self.cooldown_s = cooldown_s
        self.chunk_chars = chunk_chars
        self.micro_keep_recent = micro_keep_recent
        self.summarize = summarize

        self.consecutive_failures = 0
        self.cooldown_until = 0.0
        self.compression_count = 0
        # Observability for tests: truncate retries inside oversized chunks
        self.last_chunk_truncations = 0

    def reset_circuit_breaker(self) -> None:
        self.consecutive_failures = 0
        self.cooldown_until = 0.0

    def _in_cooldown(self) -> bool:
        return time.time() < self.cooldown_until

    def should_compact(
        self,
        messages: Sequence[Mapping[str, Any]],
        usage: Optional[Mapping[str, Any]] = None,
    ) -> bool:
        if self.consecutive_failures >= self.max_failures:
            return False
        if self._in_cooldown():
            return False
        tokens = estimate_messages(messages, usage)
        return tokens >= self.trigger_tokens

    def _record_failure(self) -> None:
        self.consecutive_failures += 1
        self.cooldown_until = time.time() + self.cooldown_s
        log.warning(
            "compression failure #%s; cooldown %ss",
            self.consecutive_failures,
            self.cooldown_s,
        )

    def _record_success(self) -> None:
        self.consecutive_failures = 0
        self.cooldown_until = 0.0
        self.compression_count += 1

    def _summarize_chunked(
        self,
        to_compress: Sequence[Mapping[str, Any]],
        existing_summary: str,
    ) -> str:
        if not self.summarize:
            raise RuntimeError("No summarize function configured")

        text = messages_to_text(to_compress)
        # Single chunk fast path
        if len(text) <= self.chunk_chars:
            prompt = build_summarize_prompt(text, existing_summary)
            return self.summarize(prompt)

        # Chunk by character budget on the flattened text of message groups
        groups = group_messages_by_round(list(to_compress))
        # Drop leading system-only group from compress text if present
        chunks: list[list[Mapping[str, Any]]] = []
        current: list[Mapping[str, Any]] = []
        current_chars = 0
        for g in groups:
            g_text_len = len(messages_to_text(g))
            if current and current_chars + g_text_len > self.chunk_chars:
                chunks.append(current)
                current = []
                current_chars = 0
            current.extend(g)
            current_chars += g_text_len
        if current:
            chunks.append(current)

        rolling = existing_summary
        truncations = 0
        for idx, chunk in enumerate(chunks):
            conv = messages_to_text(chunk)
            # If still too big, drop oldest 20% of rounds and retry up to 3x
            for attempt in range(3):
                if len(conv) <= self.chunk_chars:
                    break
                sub_groups = group_messages_by_round(list(chunk))
                if len(sub_groups) <= 1:
                    # Cannot shrink further by dropping rounds
                    break
                drop = max(1, int(len(sub_groups) * 0.2))
                chunk = [m for g in sub_groups[drop:] for m in g]
                conv = messages_to_text(chunk)
                truncations += 1
                log.info("chunk %s truncated attempt %s", idx, attempt + 1)
            prompt = build_summarize_prompt(conv, rolling)
            rolling = self.summarize(prompt)
        self.last_chunk_truncations = truncations
        return rolling

    def compress(
        self,
        messages: Sequence[dict[str, Any]],
        usage: Optional[Mapping[str, Any]] = None,
        *,
        skip_micro: bool = False,
    ) -> Optional[dict[str, Any]]:
        """
        Compress messages. Returns dict with keys:
          messages, pre_tokens, post_tokens, summary
        or None if nothing to compress.
        """
        msgs = [copy.deepcopy(m) for m in messages]
        msgs = strip_heavy(msgs)

        if not skip_micro:
            micro = micro_compact(msgs, keep_recent=self.micro_keep_recent)
            msgs = micro["messages"]

        real = estimate_messages(msgs, usage)
        if real <= 0:
            return None

        keep_ratio = min(0.95, self.target_tokens / max(real, 1))
        keep_from = find_keep_index(msgs, keep_ratio)

        leading_system: list[dict[str, Any]] = []
        i = 0
        while i < len(msgs) and msgs[i].get("role") == "system":
            leading_system.append(msgs[i])
            i += 1

        # First index eligible for compression (after system + prior summary/ack)
        floor = i
        existing = has_summary(msgs)
        existing_summary = extract_summary(msgs) if existing else ""
        if existing:
            for j in range(i, min(i + 3, len(msgs))):
                if SUMMARY_MARKER in _message_content_str(msgs[j]):
                    floor = j + 1
                    if floor < len(msgs) and msgs[floor].get("role") == "assistant":
                        floor += 1
                    break

        keep_from = safe_cut(msgs, keep_from, floor)
        if keep_from <= floor:
            log.info("Not enough old messages to compress")
            return None

        to_compress = msgs[floor:keep_from]
        recent = msgs[keep_from:]
        if not to_compress:
            return None

        try:
            new_summary = self._summarize_chunked(to_compress, existing_summary)
        except Exception:
            self._record_failure()
            raise

        summary_message = {
            "role": "user",
            "content": (
                f"{SUMMARY_MARKER}\n"
                f"{new_summary}\n"
                f"{SUMMARY_END_MARKER}\n\n"
                "以上是此前对话的滚动摘要，路径/决策/约束均已保留。请从断点继续。"
            ),
        }
        ack_message = {
            "role": "assistant",
            "content": "已读取滚动摘要中的目标、时间线、当前状态与关键细节，继续执行。",
        }

        compressed = leading_system + [summary_message, ack_message] + recent

        pre = real
        post = estimate_messages(compressed)
        self._record_success()
        log.info(
            "Compression #%s: ~%s -> ~%s tokens (cut=%s kept=%s)",
            self.compression_count,
            pre,
            post,
            keep_from,
            len(recent),
        )
        return {
            "messages": compressed,
            "pre_tokens": pre,
            "post_tokens": post,
            "summary": new_summary,
            "keep_from": keep_from,
        }

    def auto_compact(
        self,
        messages: Sequence[dict[str, Any]],
        usage: Optional[Mapping[str, Any]] = None,
    ) -> dict[str, Any]:
        """
        Micro first; if still over trigger, LLM compress.
        Returns {messages, compacted, result?}
        """
        msgs = [copy.deepcopy(m) for m in messages]
        if not self.should_compact(msgs, usage):
            return {"messages": msgs, "compacted": False}

        micro = micro_compact(msgs, keep_recent=self.micro_keep_recent)
        msgs = micro["messages"]
        tokens = estimate_messages(msgs, usage)
        if tokens < self.trigger_tokens:
            return {
                "messages": msgs,
                "compacted": micro["tool_results_cleared"] > 0,
                "result": micro,
            }

        try:
            result = self.compress(msgs, usage, skip_micro=True)
        except Exception as e:
            log.error("auto_compact LLM failed: %s", e)
            return {
                "messages": msgs,
                "compacted": micro["tool_results_cleared"] > 0,
                "result": micro,
                "error": str(e),
            }

        if result is None:
            return {
                "messages": msgs,
                "compacted": micro["tool_results_cleared"] > 0,
                "result": micro,
            }
        return {"messages": result["messages"], "compacted": True, "result": result}
