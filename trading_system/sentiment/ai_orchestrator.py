"""
sentiment/ai_orchestrator.py — Coordinates all 5 AI sentiment dimensions.

Computes a composite AI confidence score per signal.
Respects config.AI_SENTIMENT_ENABLED and config.AI_FALLBACK_ON_ERROR.

If AI is disabled or all calls fail, signals pass through unmodified.
Score thresholds (from config):
  > 0.75 → HIGH_CONFIDENCE
  0.50–0.75 → MEDIUM
  0.30–0.50 → LOW_CONFIDENCE (flag signal)
  < 0.30 → downgrade to WATCHLIST
"""

import logging

import config
import database as db

# Import individual dimension agents
from sentiment.sector_agent       import analyze_sector
from sentiment.macro_agent        import analyze_macro
from sentiment.valuechain_agent   import analyze_value_chain
from sentiment.event_risk         import analyze_event_risk
from sentiment.institutional      import analyze_institutional

logger = logging.getLogger(__name__)

# Track how many AI calls have been made this run
_call_count = 0


def reset_call_count():
    global _call_count
    _call_count = 0


def _can_call() -> bool:
    return _call_count < config.AI_MAX_CALLS_PER_RUN


def _increment():
    global _call_count
    _call_count += 1


def score_signal(signal: dict) -> dict:
    """
    Run AI sentiment analysis on a signal.

    Dimension selection by signal state:
      COMPRESSION_DETECTED → event_risk only (lightweight)
      EXPANSION_FIRED      → sector + event_risk + macro
      ACCEPTED             → all 5 dimensions

    Returns updated signal dict with ai_score, ai_confidence, ai_summary.
    Signal is NEVER modified negatively by this function — only scored.
    """
    if not config.AI_SENTIMENT_ENABLED:
        return signal

    symbol     = signal["symbol"]
    sig_type   = signal["signal_type"]
    state      = signal["state"]
    sector     = signal.get("sector", "Unknown")
    today_date = signal["date"]

    scores = {}
    insights = []

    try:
        # ── Check for today's cached dimensions ──
        cached = db.get_ai_sentiment_today(symbol)

        # ── Dimension 4: Event Risk — run for all states ──
        if _can_call():
            if "event_risk" not in cached:
                _increment()
                event = analyze_event_risk(symbol, sig_type)
                if event:
                    db.save_ai_sentiment(symbol, "event_risk", event)
                    cached["event_risk"] = event
                    logger.info("[AI] %s event_risk: %s", symbol, event.get("sentiment"))

            ev = cached.get("event_risk", {})
            if ev:
                # High event risk → low score (inverse relation)
                ev_risk = ev.get("confidence", 0.5)
                scores["event_risk"] = 1.0 - ev_risk  # Confidence in risk = lower score
                if ev.get("key_insight"):
                    insights.append(ev["key_insight"])

        # ── Dimension 1: Sector — run for EXPANSION_FIRED and ACCEPTED ──
        if state in ("EXPANSION_FIRED", "ACCEPTED") and _can_call():
            if "sector" not in cached:
                _increment()
                sec = analyze_sector(symbol, sector, sig_type)
                if sec:
                    db.save_ai_sentiment(symbol, "sector", sec)
                    cached["sector"] = sec

            sec = cached.get("sector", {})
            if sec:
                scores["sector"] = sec.get("confidence", 0.5)
                if sec.get("key_insight"):
                    insights.append(sec["key_insight"])

        # ── Dimension 2: Macro — run once per session ──
        if state in ("EXPANSION_FIRED", "ACCEPTED") and _can_call():
            macro_cache = db.get_ai_sentiment_today("__MACRO__")
            if "macro" not in macro_cache:
                _increment()
                macro = analyze_macro()
                if macro:
                    db.save_ai_sentiment("__MACRO__", "macro", macro)
                    macro_cache["macro"] = macro

            macro = macro_cache.get("macro", {})
            if macro:
                raw_score = macro.get("confidence", 0.5)
                # Adjust direction: bearish macro = lower score for longs
                sentiment = macro.get("sentiment", "NEUTRAL")
                if sig_type == "LONG":
                    scores["macro"] = raw_score if sentiment == "BULLISH" else (1 - raw_score)
                else:
                    scores["macro"] = raw_score if sentiment == "BEARISH" else (1 - raw_score)
                if macro.get("key_insight"):
                    insights.insert(0, f"Macro: {macro['key_insight']}")

        # ── Dimensions 3 & 5: Value chain + Institutional — ACCEPTED only ──
        if state == "ACCEPTED":
            if _can_call() and "value_chain" not in cached:
                _increment()
                vc = analyze_value_chain(symbol, sector, sig_type)
                if vc:
                    db.save_ai_sentiment(symbol, "value_chain", vc)
                    cached["value_chain"] = vc

            vc = cached.get("value_chain", {})
            if vc:
                scores["value_chain"] = vc.get("confidence", 0.5)
                if vc.get("key_insight"):
                    insights.append(vc["key_insight"])

            if _can_call() and "institutional" not in cached:
                _increment()
                inst = analyze_institutional(symbol, sig_type)
                if inst:
                    db.save_ai_sentiment(symbol, "institutional", inst)
                    cached["institutional"] = inst

            inst = cached.get("institutional", {})
            if inst:
                scores["institutional"] = inst.get("confidence", 0.5)
                if inst.get("key_insight"):
                    insights.append(inst["key_insight"])

    except Exception as e:
        if config.AI_FALLBACK_ON_ERROR:
            logger.warning("[AI] Error scoring %s — falling back gracefully: %s", symbol, e)
            return signal
        raise

    if not scores:
        return signal  # No AI data — pass through unchanged

    # ── Compute weighted composite score ──
    weights = config.AI_WEIGHTS
    total_weight = 0.0
    weighted_sum = 0.0
    for dim, w in weights.items():
        if dim in scores:
            weighted_sum += scores[dim] * w
            total_weight += w

    ai_score = weighted_sum / total_weight if total_weight > 0 else None

    # ── Classify confidence ──
    if ai_score is None:
        ai_confidence = "N/A"
    elif ai_score >= config.AI_HIGH_CONFIDENCE:
        ai_confidence = "HIGH"
    elif ai_score >= 0.50:
        ai_confidence = "MEDIUM"
    elif ai_score >= config.AI_LOW_CONFIDENCE:
        ai_confidence = "LOW"
    else:
        ai_confidence = "VERY_LOW"
        # Downgrade very-low-confidence signals to watchlist during output
        signal = {**signal, "state": "COMPRESSION_DETECTED", "_ai_downgraded": True}

    ai_summary = "; ".join(insights[:3]) if insights else "No AI insights available"

    logger.info("[AI] %s score=%.2f confidence=%s", symbol, ai_score or 0, ai_confidence)

    return {
        **signal,
        "ai_score":      round(ai_score, 3) if ai_score is not None else None,
        "ai_confidence": ai_confidence,
        "ai_summary":    ai_summary,
    }


def score_all_signals(signals: list[dict]) -> list[dict]:
    """
    Score all signals. Respects AI_MAX_CALLS_PER_RUN budget.
    Returns updated signal list.
    """
    reset_call_count()
    scored = []
    for signal in signals:
        if not _can_call():
            logger.warning("[AI] Call budget exhausted (%d calls) — remaining signals unscored",
                           config.AI_MAX_CALLS_PER_RUN)
            scored.append(signal)
        else:
            scored.append(score_signal(signal))
    logger.info("[AI] Total API calls this run: %d", _call_count)
    return scored
