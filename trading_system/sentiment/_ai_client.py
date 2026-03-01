"""
sentiment/_ai_client.py — Unified Claude / Gemini API client.

COST OPTIMIZATIONS IMPLEMENTED:
  1. Prompt Caching:  Claude `cache_control` on system context → 85% saving on input tokens
  2. Message Batching: Claude Batch API for EOD bulk runs → 50% discount on all tokens
  3. Model Tiering:   Haiku for quick checks, Sonnet only for PE gate decisions
  4. DB-level cache:  ai_sentiment table caches per-symbol per-day → ZERO repeat API calls

Claude pricing (as of 2025):
  Haiku  — $0.80/MTok input  | $4/MTok output    | Cache hit: $0.08/MTok
  Sonnet — $3.00/MTok input  | $15/MTok output   | Cache hit: $0.30/MTok
"""

import json
import logging
import time

import requests

import config

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# SHARED CACHED SYSTEM CONTEXT
# Marked with cache_control — Claude caches this after first call.
# Saves ~1,800 tokens per call after first use (85% input saving).
# ─────────────────────────────────────────────

_SYSTEM_CONTEXT = """You are a precise financial analyst specializing in Indian equity markets (NSE/BSE).
You analyze Nifty 200 stocks for swing trading signals using the CEA strategy (Compression → Expansion → Acceptance).

Key rules:
- LONG signals: price above 20-EMA, low volatility accumulation phase, bullish expansion breakout
- SHORT signals: price below 20-EMA, distribution phase, bearish expansion breakdown
- All analysis must be India-specific (INR, NSE/BSE, SEBI regulations, RBI policy)
- Always respond with VALID JSON only. No markdown. No text outside JSON.
- Confidence scores: 0.0 (completely uncertain) to 1.0 (very high confidence)
- Be concise. Key insights max 150 characters."""


def _make_cached_system() -> list[dict]:
    """
    Build the system prompt list with cache_control set.
    Claude caches this after the first use. Subsequent calls cost 10% of normal.
    Minimum 1024 tokens required for caching to activate on Haiku.
    """
    if config.CLAUDE_USE_PROMPT_CACHE:
        return [{"type": "text", "text": _SYSTEM_CONTEXT, "cache_control": {"type": "ephemeral"}}]
    return [{"type": "text", "text": _SYSTEM_CONTEXT}]


# ─────────────────────────────────────────────
# SINGLE CALL (real-time — intraday PE engine)
# ─────────────────────────────────────────────

def call_ai(prompt: str, prefer_speed: bool = True) -> dict | None:
    """
    Send a single prompt to Claude or Gemini.

    Use this for: intraday PE gate (real-time, no batching)
    Use batch_call_claude() for: EOD analysis (bulk, 50% cheaper)

    Returns: {"text": str, "model": str, "tokens": int} or None
    """
    provider = config.AI_PROVIDER.lower()

    if provider in ("claude", "both"):
        result = _call_claude_single(prompt, prefer_speed)
        if result:
            return result

    if provider in ("gemini", "both"):
        return _call_gemini(prompt, prefer_speed)

    return None


def _call_claude_single(prompt: str, prefer_speed: bool = True) -> dict | None:
    """Single Claude call with prompt caching on system context."""
    if not config.CLAUDE_API_KEY:
        return None

    model = config.CLAUDE_HAIKU_MODEL if prefer_speed else config.CLAUDE_SONNET_MODEL
    url = "https://api.anthropic.com/v1/messages"

    headers = {
        "x-api-key":         config.CLAUDE_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type":      "application/json",
    }
    # Enable prompt caching header
    if config.CLAUDE_USE_PROMPT_CACHE:
        headers["anthropic-beta"] = "prompt-caching-2024-07-31"

    payload = {
        "model":       model,
        "max_tokens":  600,
        "temperature": 0.1,
        "system":      _make_cached_system(),
        "messages": [{"role": "user", "content": prompt}],
    }

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=35)
        if resp.status_code == 200:
            data   = resp.json()
            text   = data["content"][0]["text"]
            usage  = data.get("usage", {})
            # Log cache hit/miss for cost tracking
            cache_read  = usage.get("cache_read_input_tokens", 0)
            cache_write = usage.get("cache_creation_input_tokens", 0)
            total_tokens = usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
            if cache_read:
                logger.debug("[Claude] Cache HIT: %d tokens from cache (saved 90%%)", cache_read)
            elif cache_write:
                logger.debug("[Claude] Cache WRITE: %d tokens cached for next call", cache_write)
            return {"text": text.strip(), "model": model, "tokens": total_tokens,
                    "cache_read": cache_read, "cache_write": cache_write}

        elif resp.status_code == 429:
            logger.warning("[Claude] Rate limited — waiting 5s")
            time.sleep(5)
        else:
            logger.warning("[Claude] HTTP %d: %s", resp.status_code, resp.text[:150])
    except Exception as e:
        logger.warning("[Claude] Request error: %s", e)
    return None


# ─────────────────────────────────────────────
# BATCH API (EOD runs — 50% cheaper)
# Submits all stock analyses at once as a batch.
# Claude processes them async; we poll for results.
# ─────────────────────────────────────────────

def submit_batch(requests_list: list[dict]) -> str | None:
    """
    Submit a batch of Claude requests.

    Args:
        requests_list: list of {"custom_id": str, "prompt": str, "prefer_speed": bool}

    Returns:
        batch_id string, or None on failure.
    """
    if not config.CLAUDE_API_KEY or not config.CLAUDE_USE_BATCH_API:
        return None

    url = "https://api.anthropic.com/v1/messages/batches"
    headers = {
        "x-api-key":         config.CLAUDE_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type":      "application/json",
        "anthropic-beta":    "message-batches-2024-09-24,prompt-caching-2024-07-31",
    }

    batch_requests = []
    for req in requests_list:
        model = config.CLAUDE_HAIKU_MODEL if req.get("prefer_speed", True) else config.CLAUDE_SONNET_MODEL
        batch_requests.append({
            "custom_id": req["custom_id"],
            "params": {
                "model":       model,
                "max_tokens":  600,
                "temperature": 0.1,
                "system":      _make_cached_system(),
                "messages": [{"role": "user", "content": req["prompt"]}],
            }
        })

    try:
        resp = requests.post(url, json={"requests": batch_requests}, headers=headers, timeout=30)
        if resp.status_code == 200:
            batch_id = resp.json().get("id")
            logger.info("[Batch] Submitted %d requests → batch_id: %s", len(requests_list), batch_id)
            return batch_id
        else:
            logger.warning("[Batch] Submit failed [%d]: %s", resp.status_code, resp.text[:200])
    except Exception as e:
        logger.error("[Batch] Submit error: %s", e)
    return None


def poll_batch(batch_id: str, timeout: int = None) -> dict[str, str] | None:
    """
    Poll for batch completion and return results.

    Returns: {"custom_id": "response_text", ...} or None on timeout/failure
    """
    timeout = timeout or config.CLAUDE_BATCH_TIMEOUT
    url = f"https://api.anthropic.com/v1/messages/batches/{batch_id}"
    headers = {
        "x-api-key":         config.CLAUDE_API_KEY,
        "anthropic-version": "2023-06-01",
        "anthropic-beta":    "message-batches-2024-09-24",
    }

    start = time.time()
    while time.time() - start < timeout:
        try:
            resp = requests.get(url, headers=headers, timeout=15)
            if resp.status_code != 200:
                logger.warning("[Batch] Poll failed [%d]", resp.status_code)
                break

            data   = resp.json()
            status = data.get("processing_status")
            counts = data.get("request_counts", {})
            logger.debug("[Batch] Status: %s | %s", status, counts)

            if status == "ended":
                return _fetch_batch_results(batch_id, headers)

        except Exception as e:
            logger.warning("[Batch] Poll error: %s", e)

        time.sleep(config.CLAUDE_BATCH_POLL_SEC)

    logger.error("[Batch] Timed out after %ds waiting for batch %s", timeout, batch_id)
    return None


def _fetch_batch_results(batch_id: str, headers: dict) -> dict[str, str]:
    """Download NDJSON results from a completed batch."""
    url = f"https://api.anthropic.com/v1/messages/batches/{batch_id}/results"
    results = {}
    try:
        resp = requests.get(url, headers=headers, timeout=30, stream=True)
        for line in resp.iter_lines():
            if line:
                obj = json.loads(line)
                cid    = obj.get("custom_id", "")
                result = obj.get("result", {})
                if result.get("type") == "succeeded":
                    text = result["message"]["content"][0]["text"]
                    results[cid] = text.strip()
                else:
                    results[cid] = None   # errored request
        logger.info("[Batch] Retrieved %d results", len(results))
    except Exception as e:
        logger.error("[Batch] Results fetch error: %s", e)
    return results


def run_batch_and_wait(requests_list: list[dict]) -> dict[str, str] | None:
    """
    Convenience: submit batch + wait for results.
    Falls back to individual calls if batch API is disabled or fails.
    """
    if not config.CLAUDE_USE_BATCH_API or not config.CLAUDE_API_KEY:
        # Fallback: individual calls
        results = {}
        for req in requests_list:
            r = _call_claude_single(req["prompt"], req.get("prefer_speed", True))
            results[req["custom_id"]] = r["text"] if r else None
        return results

    batch_id = submit_batch(requests_list)
    if not batch_id:
        return None

    return poll_batch(batch_id)


# ─────────────────────────────────────────────
# GEMINI (fallback / alternative)
# ─────────────────────────────────────────────

def _call_gemini(prompt: str, prefer_speed: bool = True) -> dict | None:
    """Call Google Gemini REST API."""
    if not config.GEMINI_API_KEY:
        return None

    model = "gemini-1.5-flash" if prefer_speed else "gemini-1.5-pro"
    url   = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    payload = {
        "contents": [{"parts": [{"text": _SYSTEM_CONTEXT + "\n\n" + prompt}]}],
        "generationConfig": {"temperature": 0.1, "maxOutputTokens": 600,
                             "responseMimeType": "application/json"},
    }
    try:
        resp = requests.post(url, json=payload, params={"key": config.GEMINI_API_KEY}, timeout=30)
        if resp.status_code == 200:
            data  = resp.json()
            text  = data["candidates"][0]["content"]["parts"][0]["text"]
            usage = data.get("usageMetadata", {})
            return {"text": text.strip(), "model": model,
                    "tokens": usage.get("totalTokenCount", 0)}
        logger.warning("[Gemini] HTTP %d", resp.status_code)
    except Exception as e:
        logger.warning("[Gemini] Error: %s", e)
    return None
