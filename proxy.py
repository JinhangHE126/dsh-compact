"""
dsh-compact local reverse proxy.

Listens on 127.0.0.1 (default :4001), rewrites OpenAI chat completions
messages with rolling compression, streams upstream responses through.

DSH always re-sends the full local transcript. Cache entries are matched by
hashing a prefix of the incoming messages equal to the length at compress time;
on hit we send entry.compressed + messages[source_len:].
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

import httpx

from engine import (
    DEFAULT_COOLDOWN_S,
    DEFAULT_MAX_FAILURES,
    DEFAULT_TARGET,
    DEFAULT_TRIGGER,
    CompactEngine,
    make_http_summarizer,
)
from tokens import estimate_messages

log = logging.getLogger("dsh-compact.proxy")

VERSION = "0.4.0"
SERVICE = "dsh-compact"

DEFAULT_PORT = 4001
DEFAULT_MAX_CONCURRENT = 1  # company gateway often allows only ~2 parallel requests
HARD_LIMIT_TOKENS = 240_000  # sync-compress above this before forward
DEFAULT_IDLE_WAIT_S = 180.0


def compact_home() -> Path:
    return Path(os.environ.get("DASH_COMPACT_HOME") or (Path.home() / ".dsh-compact"))


def configure_logging() -> Path:
    """Console + rotating file under ~/.dsh-compact/logs/proxy.log."""
    level_name = os.environ.get("DASH_COMPACT_LOG_LEVEL", "INFO")
    level = getattr(logging, level_name.upper(), logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level)

    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    root.addHandler(sh)

    log_dir = compact_home() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    max_mb = int(os.environ.get("DASH_COMPACT_LOG_MAX_MB", "10"))
    backups = int(os.environ.get("DASH_COMPACT_LOG_BACKUPS", "3"))
    fh = RotatingFileHandler(
        log_dir / "proxy.log",
        maxBytes=max_mb * 1024 * 1024,
        backupCount=backups,
        encoding="utf-8",
    )
    fh.setFormatter(fmt)
    root.addHandler(fh)
    return log_dir / "proxy.log"


# ---------------------------------------------------------------------------
# Session key / content hash
# ---------------------------------------------------------------------------

def canonical_messages_bytes(messages: list[Any]) -> bytes:
    return json.dumps(messages, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode(
        "utf-8"
    )


def content_hash(messages: list[Any]) -> str:
    return hashlib.sha256(canonical_messages_bytes(messages)).hexdigest()


def get_session_key(
    messages: list[Any],
    headers: Optional[dict[str, str]] = None,
) -> str:
    """
    Prefer an explicit session header if present; else content-hash of the
    conversation prefix with the latest user message removed (stable within a turn).
    """
    headers = {k.lower(): v for k, v in (headers or {}).items()}
    for name in (
        "x-session-id",
        "x-conversation-id",
        "x-thread-id",
        "x-chat-id",
    ):
        if headers.get(name):
            return f"hdr:{headers[name]}"

    if not messages:
        return "empty"
    # Drop trailing user for turn-stable key (also used as fingerprint hint)
    if messages[-1].get("role") == "user" and len(messages) > 1:
        prefix = messages[:-1]
    else:
        prefix = messages
    return f"ch:{content_hash(prefix)}"


# ---------------------------------------------------------------------------
# Compression cache
# ---------------------------------------------------------------------------

@dataclass
class CacheEntry:
    source_hash: str
    source_len: int
    compressed: list[Any]
    summary: str
    pre_tokens: int
    post_tokens: int
    created_at: float = field(default_factory=time.time)


@dataclass
class SessionStats:
    peak_tokens: int = 0
    compressions: int = 0
    last_summary_chars: int = 0
    last_error: str = ""
    in_flight: bool = False


def resolve_summarizer_config(
    *,
    chat_base: str,
    chat_key: Optional[str] = None,
    chat_model: Optional[str] = None,
) -> dict[str, str]:
    """Resolve summarizer BASE/KEY/MODEL.

    Dedicated ``DASH_COMPACT_SUMMARIZE_*`` vars win so compress traffic can use
    a different api_key (and thus its own max_parallel_requests quota). Falls
    back to the chat/company credentials when unset.
    """
    base = (
        os.environ.get("DASH_COMPACT_SUMMARIZE_BASE")
        or chat_base
        or os.environ.get("COMPANY_LLM_BASE")
        or ""
    ).rstrip("/")
    key = (
        os.environ.get("DASH_COMPACT_SUMMARIZE_KEY")
        or chat_key
        or os.environ.get("COMPANY_LLM_KEY")
        or ""
    )
    model = (
        os.environ.get("DASH_COMPACT_SUMMARIZE_MODEL")
        or chat_model
        or os.environ.get("COMPANY_LLM_MODEL")
        or "company-code"
    )
    separate = bool(os.environ.get("DASH_COMPACT_SUMMARIZE_KEY")) or bool(
        os.environ.get("DASH_COMPACT_SUMMARIZE_BASE")
    )
    return {
        "base_url": base,
        "api_key": key,
        "model": model,
        "separate": "1" if separate else "0",
    }


class ProxyState:
    def __init__(
        self,
        *,
        upstream_base: str,
        engine: CompactEngine,
        max_concurrent: int = DEFAULT_MAX_CONCURRENT,
        disabled: bool = False,
        summarizer_base: Optional[str] = None,
        summarizer_model: Optional[str] = None,
        summarizer_separate: bool = False,
    ) -> None:
        self.upstream_base = upstream_base.rstrip("/")
        self.engine = engine
        self.max_concurrent = max_concurrent
        self.disabled = disabled
        self.summarizer_base = (summarizer_base or upstream_base).rstrip("/")
        self.summarizer_model = summarizer_model or "company-code"
        self.summarizer_separate = summarizer_separate
        self.started_at = time.time()
        self.active_requests = 0
        self.compressions_total = 0
        self.tokens_saved_total = 0
        self._lock = threading.RLock()
        self._entries: list[CacheEntry] = []  # newest first
        self._inflight_keys: set[str] = set()
        self._stats: dict[str, SessionStats] = {}
        self._executor = ThreadPoolExecutor(
            max_workers=max(2, max_concurrent), thread_name_prefix="compact"
        )
        self._http = httpx.Client(timeout=httpx.Timeout(600.0, connect=30.0))

    def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
        self._http.close()

    def find_injection(self, messages: list[Any]) -> Optional[tuple[list[Any], CacheEntry]]:
        """Return (outgoing_messages, entry) if a cache prefix matches."""
        with self._lock:
            for entry in self._entries:
                n = entry.source_len
                if len(messages) < n:
                    continue
                if content_hash(messages[:n]) == entry.source_hash:
                    outgoing = list(entry.compressed) + list(messages[n:])
                    return outgoing, entry
        return None

    def store_entry(self, source_messages: list[Any], result: dict[str, Any]) -> None:
        entry = CacheEntry(
            source_hash=content_hash(source_messages),
            source_len=len(source_messages),
            compressed=result["messages"],
            summary=result.get("summary") or "",
            pre_tokens=int(result.get("pre_tokens") or 0),
            post_tokens=int(result.get("post_tokens") or 0),
        )
        with self._lock:
            self._entries.insert(0, entry)
            # Cap memory
            if len(self._entries) > 64:
                self._entries = self._entries[:64]
            self.compressions_total += 1
            saved = max(0, entry.pre_tokens - entry.post_tokens)
            self.tokens_saved_total += saved

    def session_stats(self, key: str) -> SessionStats:
        with self._lock:
            if key not in self._stats:
                self._stats[key] = SessionStats()
            return self._stats[key]

    def try_begin_compress(self, key: str) -> bool:
        with self._lock:
            if key in self._inflight_keys:
                return False
            # Soft cap; allow burst up to 2x so new sessions are not starved
            if len(self._inflight_keys) >= self.max_concurrent * 2:
                return False
            self._inflight_keys.add(key)
            self.session_stats(key).in_flight = True
            return True

    def end_compress(self, key: str) -> None:
        with self._lock:
            self._inflight_keys.discard(key)
            if key in self._stats:
                self._stats[key].in_flight = False

    def wait_until_idle(self, timeout_s: float) -> bool:
        """Block until no in-flight HTTP handlers (or timeout). Returns True if idle."""
        deadline = time.time() + max(0.0, timeout_s)
        while time.time() < deadline:
            if self.active_requests <= 0:
                time.sleep(0.35)  # settle: avoid racing a just-started follow-up
                if self.active_requests <= 0:
                    return True
            time.sleep(0.25)
        return self.active_requests <= 0

    def schedule_compress(self, key: str, messages: list[Any], usage: Optional[dict]) -> None:
        if self.disabled:
            return
        if not self.engine.should_compact(messages, usage):
            return
        if not self.try_begin_compress(key):
            log.info("[MSG] skip schedule key=%s (inflight or concurrency)", key[:16])
            return

        source = json.loads(json.dumps(messages))  # deep copy via json
        idle_wait = float(
            os.environ.get("DASH_COMPACT_IDLE_WAIT_S", str(DEFAULT_IDLE_WAIT_S))
        )

        def job() -> None:
            try:
                if idle_wait > 0:
                    log.info(
                        "[BG] waiting for idle (active=%s timeout=%.0fs) key=%s",
                        self.active_requests,
                        idle_wait,
                        key[:16],
                    )
                    if not self.wait_until_idle(idle_wait):
                        log.warning(
                            "[BG] idle wait timed out active=%s; compressing anyway",
                            self.active_requests,
                        )
                log.info("[BG] compress start key=%s n=%s", key[:16], len(source))
                result = self.engine.compress(source, usage)
                if result is None:
                    log.info("[BG] compress noop key=%s", key[:16])
                    return
                self.store_entry(source, result)
                st = self.session_stats(key)
                st.compressions += 1
                st.last_summary_chars = len(result.get("summary") or "")
                log.info(
                    "[BG] compress done key=%s %s->%s",
                    key[:16],
                    result.get("pre_tokens"),
                    result.get("post_tokens"),
                )
            except Exception as e:
                st = self.session_stats(key)
                st.last_error = str(e)[:300]
                log.exception("[BG] compress failed key=%s: %s", key[:16], e)
            finally:
                self.end_compress(key)

        self._executor.submit(job)

    def health(self) -> dict[str, Any]:
        with self._lock:
            return {
                "service": SERVICE,
                "version": VERSION,
                "pid": os.getpid(),
                "uptime_s": round(time.time() - self.started_at, 1),
                "active_requests": self.active_requests,
                "enabled": not self.disabled,
                "compressions_total": self.compressions_total,
                "tokens_saved_total": self.tokens_saved_total,
                "cache_entries": len(self._entries),
                "inflight": len(self._inflight_keys),
                "upstream": self.upstream_base,
                "summarizer": self.summarizer_base,
                "summarizer_model": self.summarizer_model,
                "summarizer_separate": self.summarizer_separate,
                "trigger": self.engine.trigger_tokens,
                "target": self.engine.target_tokens,
            }

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "sessions": {
                    k: {
                        "peak_tokens": v.peak_tokens,
                        "compressions": v.compressions,
                        "last_summary_chars": v.last_summary_chars,
                        "last_error": v.last_error,
                        "in_flight": v.in_flight,
                    }
                    for k, v in self._stats.items()
                },
                "cache": [
                    {
                        "source_len": e.source_len,
                        "pre_tokens": e.pre_tokens,
                        "post_tokens": e.post_tokens,
                        "summary_chars": len(e.summary),
                        "age_s": round(time.time() - e.created_at, 1),
                    }
                    for e in self._entries[:20]
                ],
            }


# Global set in main() / create_app for tests
STATE: Optional[ProxyState] = None


def _json_response(handler: BaseHTTPRequestHandler, code: int, obj: Any) -> None:
    data = json.dumps(obj).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def _passthrough_headers(headers: dict[str, str]) -> dict[str, str]:
    # Drop Accept-Encoding: httpx auto-decompresses upstream bodies, so if we
    # forwarded gzip and then re-chunked plaintext while keeping
    # Content-Encoding: gzip, clients (Harness/Stainless) fail with
    # "incorrect header check" / TRANSPORT terminated.
    skip = {
        "host",
        "content-length",
        "transfer-encoding",
        "connection",
        "keep-alive",
        "proxy-connection",
        "te",
        "trailer",
        "upgrade",
        "accept-encoding",
    }
    out = {}
    for k, v in headers.items():
        if k.lower() in skip:
            continue
        out[k] = v
    return out


class ProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:
        log.info("%s - " + fmt, self.address_string(), *args)

    def do_GET(self) -> None:
        assert STATE is not None
        path = urlparse(self.path).path
        if path in ("/health", "/v1/health"):
            _json_response(self, 200, STATE.health())
            return
        if path in ("/stats", "/v1/stats"):
            _json_response(self, 200, STATE.stats())
            return
        self._proxy_raw("GET", body=None)

    def do_POST(self) -> None:
        assert STATE is not None
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""

        if path.rstrip("/").endswith("/chat/completions") or path.endswith(
            "/chat/completions"
        ):
            self._handle_chat_completions(body)
            return
        self._proxy_raw("POST", body)

    def _handle_chat_completions(self, body: bytes) -> None:
        assert STATE is not None
        STATE.active_requests += 1
        try:
            try:
                payload = json.loads(body) if body else {}
            except json.JSONDecodeError:
                _json_response(self, 400, {"error": "invalid json"})
                return

            messages = payload.get("messages")
            if not isinstance(messages, list):
                self._forward_bytes("POST", body, dict(self.headers))
                return

            hdrs = {k: v for k, v in self.headers.items()}
            key = get_session_key(messages, hdrs)
            tokens = estimate_messages(messages)
            st = STATE.session_stats(key)
            st.peak_tokens = max(st.peak_tokens, tokens)

            outgoing_messages = messages
            injected = False
            if not STATE.disabled:
                hit = STATE.find_injection(messages)
                if hit:
                    outgoing_messages, entry = hit
                    injected = True
                    log.info(
                        "[MSG] inject key=%s src_len=%s out_len=%s",
                        key[:16],
                        entry.source_len,
                        len(outgoing_messages),
                    )

            out_tokens = estimate_messages(outgoing_messages)
            # Sync path when still above hard limit
            if (
                not STATE.disabled
                and out_tokens >= HARD_LIMIT_TOKENS
                and STATE.engine.should_compact(outgoing_messages)
            ):
                log.info("[MSG] sync compress (hard limit) tokens≈%s", out_tokens)
                try:
                    result = STATE.engine.compress(outgoing_messages)
                    if result:
                        STATE.store_entry(outgoing_messages, result)
                        outgoing_messages = result["messages"]
                        injected = True
                except Exception as e:
                    log.exception("sync compress failed: %s", e)

            # Schedule async compress on the *original* full transcript
            # (so next turn's prefix hash matches)
            if not STATE.disabled and not injected:
                STATE.schedule_compress(key, messages, usage=None)
            elif not STATE.disabled and injected:
                # Still may need to recompress as conversation grows
                STATE.schedule_compress(key, messages, usage=None)

            new_payload = dict(payload)
            new_payload["messages"] = outgoing_messages
            new_body = json.dumps(new_payload, ensure_ascii=False).encode("utf-8")

            log.info(
                "[MSG] Forwarding to %s tokens≈%s->%s injected=%s stream=%s",
                STATE.upstream_base,
                tokens,
                estimate_messages(outgoing_messages),
                injected,
                bool(payload.get("stream")),
            )
            self._forward_bytes("POST", new_body, hdrs, force_path="/chat/completions")
        finally:
            STATE.active_requests = max(0, STATE.active_requests - 1)

    def _proxy_raw(self, method: str, body: Optional[bytes]) -> None:
        self._forward_bytes(method, body or b"", dict(self.headers))

    def _forward_bytes(
        self,
        method: str,
        body: bytes,
        headers: dict[str, str],
        force_path: Optional[str] = None,
    ) -> None:
        assert STATE is not None
        parsed = urlparse(self.path)
        path = force_path or parsed.path
        # Ensure /v1 prefix alignment with upstream_base
        if not path.startswith("/"):
            path = "/" + path
        # If upstream_base already ends with /v1 and path starts with /v1, avoid double
        up = STATE.upstream_base
        if path.startswith("/v1/") and up.endswith("/v1"):
            url = up + path[3:]  # drop leading /v1
        elif path == "/v1" and up.endswith("/v1"):
            url = up
        else:
            url = up + path
        if parsed.query:
            url += "?" + parsed.query

        req_headers = _passthrough_headers(headers)
        req_headers["content-length"] = str(len(body))
        # Prefer identity even if a client somehow re-injected Accept-Encoding.
        req_headers["Accept-Encoding"] = "identity"

        try:
            with STATE._http.stream(
                method,
                url,
                headers=req_headers,
                content=body if method != "GET" else None,
            ) as resp:
                self.send_response(resp.status_code)
                for k, v in resp.headers.items():
                    lk = k.lower()
                    if lk in (
                        "transfer-encoding",
                        "content-length",
                        "connection",
                        "keep-alive",
                        # Body from iter_bytes() is already decoded; never claim gzip.
                        "content-encoding",
                    ):
                        continue
                    self.send_header(k, v)
                # Stream body
                self.send_header("Transfer-Encoding", "chunked")
                self.send_header("Connection", "close")
                self.end_headers()
                try:
                    for chunk in resp.iter_bytes():
                        if not chunk:
                            continue
                        self.wfile.write(f"{len(chunk):X}\r\n".encode() + chunk + b"\r\n")
                        self.wfile.flush()
                    self.wfile.write(b"0\r\n\r\n")
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    log.warning("client disconnected mid-stream")
        except httpx.RequestError as e:
            log.exception("upstream error: %s", e)
            _json_response(
                self,
                502,
                {"error": {"message": f"upstream error: {e}", "type": "proxy_error"}},
            )


def build_state_from_env() -> ProxyState:
    upstream = os.environ.get("COMPANY_LLM_BASE") or "http://172.17.7.90:4000/v1"
    disabled = os.environ.get("DASH_COMPACT_DISABLE", "0").strip() in ("1", "true", "yes")
    trigger = int(os.environ.get("DASH_COMPACT_TRIGGER", str(DEFAULT_TRIGGER)))
    target = int(os.environ.get("DASH_COMPACT_TARGET", str(DEFAULT_TARGET)))
    max_conc = int(os.environ.get("DASH_COMPACT_MAX_CONCURRENT", str(DEFAULT_MAX_CONCURRENT)))
    cooldown = float(os.environ.get("DASH_COMPACT_FAILURE_COOLDOWN", str(DEFAULT_COOLDOWN_S)))
    max_fail = int(os.environ.get("DASH_COMPACT_MAX_FAILURES", str(DEFAULT_MAX_FAILURES)))
    micro_keep = int(os.environ.get("DASH_COMPACT_MICRO_KEEP", "5"))

    sum_cfg = resolve_summarizer_config(chat_base=upstream)
    summarize = make_http_summarizer(
        base_url=sum_cfg["base_url"],
        api_key=sum_cfg["api_key"],
        model=sum_cfg["model"],
    )
    engine = CompactEngine(
        trigger_tokens=trigger,
        target_tokens=target,
        max_failures=max_fail,
        cooldown_s=cooldown,
        micro_keep_recent=micro_keep,
        summarize=summarize,
    )
    return ProxyState(
        upstream_base=upstream,
        engine=engine,
        max_concurrent=max_conc,
        disabled=disabled,
        summarizer_base=sum_cfg["base_url"],
        summarizer_model=sum_cfg["model"],
        summarizer_separate=sum_cfg["separate"] == "1",
    )


def serve(
    host: str = "127.0.0.1",
    port: Optional[int] = None,
    state: Optional[ProxyState] = None,
) -> ThreadingHTTPServer:
    global STATE
    STATE = state or build_state_from_env()
    port = port if port is not None else int(os.environ.get("DASH_COMPACT_PORT", str(DEFAULT_PORT)))
    httpd = ThreadingHTTPServer((host, port), ProxyHandler)
    h = STATE.health()
    print(
        f"dsh-compact proxy v{VERSION} on http://{host}:{port}\n"
        f"  upstream={STATE.upstream_base}\n"
        f"  summarizer={STATE.summarizer_base} model={STATE.summarizer_model}"
        f" separate={STATE.summarizer_separate}\n"
        f"  trigger={STATE.engine.trigger_tokens} target={STATE.engine.target_tokens}\n"
        f"  disabled={STATE.disabled} pid={h['pid']}",
        flush=True,
    )
    return httpd


def main(argv: Optional[list[str]] = None) -> int:
    log_path = configure_logging()
    httpd = serve()
    print(f"  log={log_path}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down...", flush=True)
    finally:
        if STATE:
            STATE.close()
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
