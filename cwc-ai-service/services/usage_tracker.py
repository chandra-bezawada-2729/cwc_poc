"""
Token usage and cost, per fax, per stage.

The Anthropic console shows what a day cost in total but cannot say which
request belonged to which fax: its rows carry a request ID, never a tracking
ID. Every API response does carry exact token counts in `response.usage`, and
until now every call site threw them away with `response.content[0].text`.

This module keeps them. A Flask endpoint opens a scope with `track(...)`; every
`messages.create` inside it — the vision call, the text-only fallback, the
schema-repair retry — is added to that scope with `record(...)`; when the scope
closes, one line is appended to logs/ai_usage.jsonl and the totals are available
to put in the response.

A context variable rather than a changed return type, because the call sites sit
three and four functions below the endpoint. Threading a usage object up through
classify_fax and extract_metadata would change the signature of every function
on the way and of every test that calls them, for data none of them use.

PHI: nothing here reads or stores prompt or response content. Only counts, the
model name and the tracking ID, which is already a random UUID.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Prices, USD per million tokens ─────────────────────────────────────────────
# From the published price list (platform.claude.com/docs/en/about-claude/pricing)
# at the time of writing. Override any of them with CWC_PRICE_<MODEL>_<KIND>, e.g.
# CWC_PRICE_OPUS_4_5_INPUT=5, rather than editing this file when a price moves.
#
# Model matching is by prefix so "claude-opus-4-5-20251101" and the alias
# "claude-opus-4-5" resolve to the same row.
# Every model is listed explicitly. Prefix matching means a missing row does
# not fail loudly - it falls through to a shorter key - and "claude-opus-4"
# would otherwise price claude-opus-4-8 at the old $15/$75 rate, three times
# what it costs. Cache prices follow the published 1.25x write / 0.1x read.
_DEFAULT_PRICES: dict[str, dict[str, float]] = {
    "claude-opus-5":     {"input": 5.00,  "output": 25.00, "cache_write": 6.25, "cache_read": 0.50},
    "claude-opus-4-8":   {"input": 5.00,  "output": 25.00, "cache_write": 6.25, "cache_read": 0.50},
    "claude-opus-4-7":   {"input": 5.00,  "output": 25.00, "cache_write": 6.25, "cache_read": 0.50},
    "claude-opus-4-6":   {"input": 5.00,  "output": 25.00, "cache_write": 6.25, "cache_read": 0.50},
    "claude-sonnet-5":   {"input": 2.00,  "output": 10.00, "cache_write": 2.50, "cache_read": 0.20},
    "claude-sonnet-4-6": {"input": 3.00,  "output": 15.00, "cache_write": 3.75, "cache_read": 0.30},
    "claude-opus-4-5":   {"input": 5.00,  "output": 25.00, "cache_write": 6.25, "cache_read": 0.50},
    "claude-opus-4-1":   {"input": 15.00, "output": 75.00, "cache_write": 18.75, "cache_read": 1.50},
    "claude-opus-4":     {"input": 15.00, "output": 75.00, "cache_write": 18.75, "cache_read": 1.50},
    "claude-sonnet-4-5": {"input": 3.00,  "output": 15.00, "cache_write": 3.75, "cache_read": 0.30},
    "claude-sonnet-4":   {"input": 3.00,  "output": 15.00, "cache_write": 3.75, "cache_read": 0.30},
    "claude-haiku-4-5":  {"input": 1.00,  "output": 5.00,  "cache_write": 1.25, "cache_read": 0.10},
}


def _env_key(model: str, kind: str) -> str:
    return "CWC_PRICE_" + model.replace("claude-", "").replace("-", "_").upper() + "_" + kind.upper()


def price_for(model: str | None) -> dict[str, float] | None:
    """Price row for a model, longest-prefix match. None when unknown."""
    if not model:
        return None
    match = None
    for key in sorted(_DEFAULT_PRICES, key=len, reverse=True):
        if model.startswith(key):
            match = key
            break
    if match is None:
        return None
    row = dict(_DEFAULT_PRICES[match])
    for kind in row:
        override = os.getenv(_env_key(match, kind))
        if override:
            try:
                row[kind] = float(override)
            except ValueError:
                logger.warning(f"[USAGE] Ignoring non-numeric {_env_key(match, kind)}={override!r}")
    return row


def cost_usd(model: str | None, input_tokens: int, output_tokens: int,
             cache_write: int = 0, cache_read: int = 0) -> float | None:
    """
    Cost of one call. None — not 0 — for a model with no price row, so a report
    can say "unknown" instead of quietly under-counting the bill.
    """
    p = price_for(model)
    if p is None:
        return None
    return (input_tokens * p["input"]
            + output_tokens * p["output"]
            + cache_write * p["cache_write"]
            + cache_read * p["cache_read"]) / 1_000_000


# ── Scope ─────────────────────────────────────────────────────────────────────

_scope: ContextVar[dict | None] = ContextVar("cwc_usage_scope", default=None)

_LEDGER = Path(os.getenv(
    "CWC_USAGE_LEDGER",
    str(Path(__file__).parent.parent / "logs" / "ai_usage.jsonl"),
))
_ledger_lock = threading.Lock()


def _empty(tracking_id: str, stage: str) -> dict:
    return {
        "trackingId": tracking_id,
        "stage": stage,
        "calls": [],
        "inputTokens": 0,
        "outputTokens": 0,
        "cacheWriteTokens": 0,
        "cacheReadTokens": 0,
        "costUsd": 0.0,
        "costKnown": True,
        "model": None,
    }


@contextmanager
def track(tracking_id: str, stage: str, **context):
    """
    Collect usage for one endpoint call, then write it to the ledger.

    `context` is copied onto the ledger line as-is — the original filename and
    the category, so a report can group by document without joining to the
    backend's database.
    """
    acc = _empty(tracking_id, stage)
    acc.update({k: v for k, v in context.items() if v is not None})
    started = time.time()
    token = _scope.set(acc)
    try:
        yield acc
    finally:
        _scope.reset(token)
        acc["durationMs"] = int((time.time() - started) * 1000)
        acc["at"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(started))
        # A scope with no calls is an early-exit path (unreadable file, OCR-only
        # UNKNOWN). Still worth a line: it proves the fax was seen and cost $0.
        _append(acc)


def record(response, call: str, local: bool = False) -> None:
    """
    Add one Anthropic response's usage to the open scope.

    Never raises. Losing a usage line is a reporting gap; failing a
    classification because of it would be a production outage over a log.
    """
    acc = _scope.get()
    if acc is None:
        return
    try:
        u = getattr(response, "usage", None)
        if u is None:
            return
        # Anthropic names first, OpenAI-compatible names as the fallback.
        i  = int(getattr(u, "input_tokens", 0) or getattr(u, "prompt_tokens", 0) or 0)
        o  = int(getattr(u, "output_tokens", 0) or getattr(u, "completion_tokens", 0) or 0)
        cw = int(getattr(u, "cache_creation_input_tokens", 0) or 0)
        cr = int(getattr(u, "cache_read_input_tokens", 0) or 0)
        model = getattr(response, "model", None)

        # A self-hosted model has no per-token price: the GPU is already paid for.
        c = 0.0 if local else cost_usd(model, i, o, cw, cr)
        acc["calls"].append({
            "call": call, "model": model,
            "inputTokens": i, "outputTokens": o,
            "cacheWriteTokens": cw, "cacheReadTokens": cr,
            "costUsd": round(c, 6) if c is not None else None,
            "requestId": getattr(response, "id", None),
        })
        acc["inputTokens"]      += i
        acc["outputTokens"]     += o
        acc["cacheWriteTokens"] += cw
        acc["cacheReadTokens"]  += cr
        acc["model"] = acc["model"] or model
        if c is None:
            acc["costKnown"] = False
        else:
            acc["costUsd"] += c
    except Exception as exc:                          # noqa: BLE001
        logger.warning(f"[USAGE] Could not record usage: {exc}")


def summary(acc: dict) -> dict:
    """The block returned to the backend alongside the classification."""
    return {
        "model":            acc.get("model"),
        "calls":            len(acc.get("calls", [])),
        "inputTokens":      acc.get("inputTokens", 0),
        "outputTokens":     acc.get("outputTokens", 0),
        "cacheWriteTokens": acc.get("cacheWriteTokens", 0),
        "cacheReadTokens":  acc.get("cacheReadTokens", 0),
        "costUsd":          round(acc["costUsd"], 6) if acc.get("costKnown", True) else None,
    }


def _append(acc: dict) -> None:
    try:
        _LEDGER.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps({**acc, "costUsd": round(acc["costUsd"], 6) if acc["costKnown"] else None})
        with _ledger_lock, open(_LEDGER, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        logger.info(
            f"[USAGE] {acc['stage']} tracking_id={acc['trackingId']} "
            f"calls={len(acc['calls'])} in={acc['inputTokens']} out={acc['outputTokens']} "
            f"cost=${acc['costUsd']:.4f}"
        )
    except Exception as exc:                          # noqa: BLE001
        logger.warning(f"[USAGE] Could not write ledger {_LEDGER}: {exc}")
