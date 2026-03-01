"""
validator.py — CEA Steps 2 & 3: Expansion Candle + Acceptance Validation.

Step 2 — Expansion:
  Detects the breakout candle that ends compression.
  Fires an EXPANSION_FIRED signal.

Step 3 — Acceptance:
  Tracks the stock for up to 2 days after expansion.
  Validates that price is holding (not just a one-day wonder).
  Fires an ACCEPTED signal if acceptance criteria met.

Note: Entry is NEVER on expansion candle day — only on Day+1 or Day+2.
"""

import logging
import math
from datetime import date, datetime, timedelta

import numpy as np

import config
import database as db
from scanner import compute_atr, compute_obv, linear_regression_slope

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# EMA HELPER (used throughout)
# ─────────────────────────────────────────────

def compute_ema(prices: list[float], period: int) -> list[float]:
    """EMA with Wilder/exponential smoothing. Returns same-length list."""
    ema = []
    k = 2 / (period + 1)
    for i, p in enumerate(prices):
        if i < period - 1:
            ema.append(float("nan"))
        elif i == period - 1:
            ema.append(sum(prices[:period]) / period)
        else:
            ema.append(p * k + ema[-1] * (1 - k))
    return ema


# ─────────────────────────────────────────────
# STEP 2: EXPANSION CANDLE CHECK
# ─────────────────────────────────────────────

def check_expansion(symbol: str, ohlcv: list[dict], compression_result: dict) -> dict | None:
    """
    Check if today's candle (last candle in ohlcv) is a valid expansion candle.

    Criteria:
      1. Range >= 2x average range of last 10 days
      2. Close in top 25% of candle (longs) / bottom 25% (shorts)
      3. Volume >= 1.5x 20-day average
      4. Breaks above compression band high (longs) / below band low (shorts)
      5. Close > previous day's high (longs) — cleaner breakout
      6. Upper wick < 20% of total candle range (longs)

    Returns a signal dict for EXPANSION_FIRED, or None if not valid.
    """
    if len(ohlcv) < config.EXPANSION_LOOKBACK + 20:
        return None

    today_bar = ohlcv[-1]
    prev_bars = ohlcv[-(config.EXPANSION_LOOKBACK + 1):-1]

    today_open  = today_bar["open"]
    today_high  = today_bar["high"]
    today_low   = today_bar["low"]
    today_close = today_bar["close"]
    today_vol   = today_bar["volume"]
    today_date  = today_bar["date"]

    # ── 1. Candle range ──
    today_range = today_high - today_low
    avg_range   = sum(b["high"] - b["low"] for b in prev_bars[-config.EXPANSION_LOOKBACK:]) / config.EXPANSION_LOOKBACK
    if avg_range == 0 or today_range < config.EXPANSION_RANGE_MULT * avg_range:
        return None

    # ── 2. Close position — determine LONG or SHORT direction ──
    candle_pct = (today_close - today_low) / today_range if today_range > 0 else 0.5
    is_long  = candle_pct >= (1 - config.EXPANSION_CLOSE_PCT)   # Top 25%
    is_short = candle_pct <= config.EXPANSION_CLOSE_PCT          # Bottom 25%

    if not is_long and not is_short:
        return None  # Close in middle — no directional conviction

    # ── 3. Volume check ──
    avg_vol_20 = sum(b["volume"] for b in ohlcv[-21:-1]) / 20
    if today_vol < config.EXPANSION_VOL_MULT * avg_vol_20:
        return None

    # ── 4. Breaks out of compression band ──
    band_high = compression_result["band_high"]
    band_low  = compression_result["band_low"]
    if is_long  and today_close <= band_high:
        return None   # Didn't actually break above compression
    if is_short and today_close >= band_low:
        return None   # Didn't break below

    # ── 5. Close > previous day's high (longs only — extra confirmation) ──
    prev_high = ohlcv[-2]["high"]
    if is_long and today_close < prev_high:
        return None

    # ── 6. Upper wick check (longs): wick should be small ──
    if is_long:
        upper_wick = today_high - today_close
        if today_range > 0 and upper_wick / today_range > config.MAX_UPPER_WICK_PCT:
            logger.debug(
                "%s: upper wick too large (%.1f%% of range) — rejected",
                symbol, upper_wick / today_range * 100,
            )
            return None

    # ── Direction determined ──
    signal_type = "LONG" if is_long else "SHORT"

    # ── Calculate entry zone and stop loss ──
    entry_low  = today_close
    entry_high = today_close * (1 + config.ENTRY_ZONE_BUFFER_PCT / 100)

    if is_long:
        stop_loss = today_low
        stop_pct  = (entry_low - stop_loss) / entry_low * 100
    else:
        stop_loss = today_high
        stop_pct  = (stop_loss - entry_high) / entry_high * 100

    # ── Reject if stop > MAX_STOP_FROM_ENTRY_PCT ──
    if stop_pct > config.MAX_STOP_FROM_ENTRY_PCT:
        logger.debug(
            "%s: stop %.2f%% from entry — exceeds %.1f%% limit → INVALID",
            symbol, stop_pct, config.MAX_STOP_FROM_ENTRY_PCT,
        )
        return None

    # ── ATR-based stop (take the tighter of the two) ──
    atrs = compute_atr(ohlcv[-30:], period=14)
    atr14 = next((a for a in reversed(atrs) if not math.isnan(a)), None)
    if atr14:
        atr_stop_long  = entry_low  - config.ATR_MULTIPLIER * atr14
        atr_stop_short = entry_high + config.ATR_MULTIPLIER * atr14
        if is_long:
            stop_loss = max(stop_loss, atr_stop_long)   # tighter of candle-low and ATR-stop
        else:
            stop_loss = min(stop_loss, atr_stop_short)

    # ── Expected move estimate (2-4× ATR from entry) ──
    if atr14:
        if is_long:
            exp_low  = entry_low + 2 * atr14
            exp_high = entry_low + 4 * atr14
            expected_move = f"+{exp_low / entry_low * 100 - 100:.1f}% to +{exp_high / entry_low * 100 - 100:.1f}%"
        else:
            exp_low  = entry_high - 4 * atr14
            exp_high = entry_high - 2 * atr14
            expected_move = f"{exp_low / entry_high * 100 - 100:.1f}% to {exp_high / entry_high * 100 - 100:.1f}%"
    else:
        expected_move = "N/A"

    logger.info(
        "EXPANSION_FIRED: %s %s | range=%.1f%% of avg | vol=%.1f× avg | stop=%.1f%% from entry",
        signal_type, symbol,
        today_range / avg_range * 100,
        today_vol / avg_vol_20 if avg_vol_20 > 0 else 0,
        stop_pct,
    )

    return {
        "symbol":               symbol,
        "date":                 today_date,
        "signal_type":          signal_type,
        "state":                "EXPANSION_FIRED",
        "entry_zone_low":       round(entry_low, 2),
        "entry_zone_high":      round(entry_high, 2),
        "stop_loss":            round(stop_loss, 2),
        "risk_pct":             round(stop_pct, 2),
        "expected_move":        expected_move,
        "valid_for_days":       config.DEFAULT_VALID_FOR_DAYS,
        "compression_days":     compression_result["compression_days"],
        "expansion_candle_date": today_date,
        "expansion_high":       round(today_high, 2),
        "expansion_low":        round(today_low, 2),
        "expansion_close":      round(today_close, 2),
        "ai_score":             None,
        "ai_confidence":        "N/A",
        "ai_summary":           None,
        # Extra context (not stored in DB, used for reporting)
        "_range_mult":          round(today_range / avg_range, 2),
        "_vol_mult":            round(today_vol / avg_vol_20, 2) if avg_vol_20 > 0 else 0,
        "_atr14":               round(atr14, 2) if atr14 else None,
        "_candle_pct":          round(candle_pct * 100, 1),
    }


# ─────────────────────────────────────────────
# STEP 3: ACCEPTANCE VALIDATION
# ─────────────────────────────────────────────

def check_acceptance(signal: dict, ohlcv: list[dict]) -> bool:
    """
    Validate acceptance days (Day+1 and Day+2 after expansion candle).
    Called daily on all EXPANSION_FIRED signals that are past their expansion date.

    Acceptance criteria (ALL must pass on Day+1 or Day+2):
      1. Retracement <= 40% of expansion candle body
      2. Price holds above 20-EMA (longs) / below 20-EMA (shorts)
      3. Volume declining on pullback
      4. No long upper wicks > 20% of candle range (longs)

    Returns True if acceptance confirmed → signal should move to ACCEPTED.
    Returns False to keep as EXPANSION_FIRED (still waiting).
    """
    exp_date  = signal["expansion_candle_date"]
    sig_type  = signal["signal_type"]
    exp_close = signal["expansion_close"]
    exp_low   = signal["expansion_low"]
    exp_high  = signal["expansion_high"]

    # Get candles AFTER the expansion day
    post_exp = [b for b in ohlcv if b["date"] > exp_date]
    if not post_exp:
        return False  # Expansion candle was today — wait

    # ── 20-EMA check ──
    closes = [b["close"] for b in ohlcv]
    emas   = compute_ema(closes, 20)
    ema_20 = emas[-1]

    # ── Expansion candle body ──
    exp_body = abs(signal.get("_exp_open", exp_close) - exp_close)
    if exp_body == 0:
        exp_body = abs(exp_high - exp_low) * 0.5  # Fallback

    for day_bar in post_exp[:config.ACCEPTANCE_MAX_DAYS]:
        bar_close = day_bar["close"]
        bar_low   = day_bar["low"]
        bar_high  = day_bar["high"]

        # ── 1. Retracement check ──
        if sig_type == "LONG":
            retracement = exp_close - bar_low
            if retracement / exp_body > config.MAX_RETRACEMENT_PCT:
                return False
        else:   # SHORT
            retracement = bar_high - exp_close
            if retracement / exp_body > config.MAX_RETRACEMENT_PCT:
                return False

        # ── 2. EMA hold check ──
        if sig_type == "LONG" and bar_close < ema_20:
            return False
        if sig_type == "SHORT" and bar_close > ema_20:
            return False

        # ── 3. Volume declining check ──
        recent_vols = [b["volume"] for b in ohlcv[-20:]]
        avg_vol = sum(recent_vols) / len(recent_vols)
        # On pullback days, volume should be below average
        if day_bar["volume"] > avg_vol * 1.2:
            return False

        # ── 4. Upper wick check (longs) ──
        if sig_type == "LONG":
            candle_range = bar_high - bar_low
            upper_wick   = bar_high - bar_close
            if candle_range > 0 and upper_wick / candle_range > config.MAX_UPPER_WICK_PCT:
                return False

    logger.info("ACCEPTANCE confirmed: %s %s", sig_type, signal["symbol"])
    return True


def run_validation(compressed_stocks: list[dict], active_signals: list[dict]) -> dict:
    """
    Run both expansion and acceptance validation:
      - On compressed stocks: check if today is an expansion candle
      - On EXPANSION_FIRED signals: check if acceptance criteria met

    Returns:
      {
        "new_expansions": [signal dicts with state=EXPANSION_FIRED],
        "newly_accepted": [signal_id list that moved to ACCEPTED],
      }
    """
    new_expansions = []
    newly_accepted = []

    # ── Check for new expansion candles on compressed stocks ──
    for comp in compressed_stocks:
        symbol = comp["symbol"]
        ohlcv = db.get_ohlcv(symbol, days=180)
        if not ohlcv:
            continue

        result = check_expansion(symbol, ohlcv, comp)
        if result:
            # Don't duplicate if there's already an active signal
            if not db.symbol_has_active_signal(symbol):
                new_expansions.append(result)

    # ── Check acceptance for existing EXPANSION_FIRED signals ──
    for signal in active_signals:
        if signal["state"] != "EXPANSION_FIRED":
            continue
        symbol = signal["symbol"]
        ohlcv = db.get_ohlcv(symbol, days=180)
        if not ohlcv:
            continue

        if check_acceptance(signal, ohlcv):
            newly_accepted.append(signal["id"])

    return {
        "new_expansions": new_expansions,
        "newly_accepted": newly_accepted,
    }
