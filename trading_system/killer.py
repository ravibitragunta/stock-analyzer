"""
killer.py — Signal Invalidation (Killer Module).

Auto-invalidates signals when ANY one of these triggers fires:
  1. Price breaks below expansion candle low (longs) / above high (shorts)
  2. Retracement > 40% of expansion candle body
  3. Volume spike against direction on acceptance day
  4. Signal older than valid_for_days with no entry opportunity
  5. AI event risk detected (earnings, block deal within 5 days) — optional

Runs daily on ALL active signals (EXPANSION_FIRED and ACCEPTED states).
"""

import logging
from datetime import date, datetime

import config
import database as db

logger = logging.getLogger(__name__)


def run_killer(active_signals: list[dict]) -> list[int]:
    """
    Check all active signals for invalidation triggers.

    Args:
        active_signals: list of signal dicts from DB (EXPANSION_FIRED or ACCEPTED)

    Returns:
        List of signal IDs that were invalidated.
    """
    invalidated_ids = []
    today = date.today().isoformat()

    for signal in active_signals:
        if signal["state"] in ("INVALIDATED", "TARGET_HIT"):
            continue

        signal_id     = signal["id"]
        symbol        = signal["symbol"]
        sig_type      = signal["signal_type"]
        exp_low       = signal["expansion_low"]
        exp_high      = signal["expansion_high"]
        exp_close     = signal["expansion_close"]
        created_date  = signal["date"]
        valid_for     = signal.get("valid_for_days", config.DEFAULT_VALID_FOR_DAYS)

        # Get recent OHLCV for this stock
        ohlcv = db.get_ohlcv(symbol, days=30)
        if not ohlcv:
            continue

        today_bar = ohlcv[-1]
        today_close = today_bar["close"]
        today_high  = today_bar["high"]
        today_low   = today_bar["low"]
        today_vol   = today_bar["volume"]

        reason = None

        # ── Trigger 1: Price breaks below expansion candle low (longs) ──
        if sig_type == "LONG" and today_low < exp_low:
            reason = f"Price broke below expansion low ₹{exp_low:.2f} (today low: ₹{today_low:.2f})"

        # ── Trigger 1: Price breaks above expansion candle high (shorts) ──
        elif sig_type == "SHORT" and today_high > exp_high:
            reason = f"Price broke above expansion high ₹{exp_high:.2f} (today high: ₹{today_high:.2f})"

        # ── Trigger 2: Retracement > 40% of expansion body ──
        if reason is None:
            exp_body = abs(exp_close - exp_low) if sig_type == "LONG" else abs(exp_high - exp_close)
            if exp_body > 0:
                if sig_type == "LONG":
                    retracement = exp_close - today_close
                else:
                    retracement = today_close - exp_close
                if retracement / exp_body > config.MAX_RETRACEMENT_PCT:
                    reason = (
                        f"Retracement {retracement / exp_body * 100:.1f}% "
                        f"> {config.MAX_RETRACEMENT_PCT * 100:.0f}% limit"
                    )

        # ── Trigger 3: Adverse volume spike (large candle against direction) ──
        if reason is None:
            avg_vol_20 = sum(b["volume"] for b in ohlcv[-21:-1]) / 20 if len(ohlcv) > 20 else 0
            candle_direction = today_close > today_bar["open"]
            is_adverse = (
                (sig_type == "LONG"  and not candle_direction) or
                (sig_type == "SHORT" and candle_direction)
            )
            if avg_vol_20 > 0 and today_vol > 2.0 * avg_vol_20 and is_adverse:
                reason = (
                    f"Adverse volume spike: {today_vol / avg_vol_20:.1f}× avg "
                    f"on opposite-direction candle"
                )

        # ── Trigger 4: Signal expired (too old, never got entry) ──
        if reason is None:
            days_alive = (
                datetime.strptime(today, "%Y-%m-%d")
                - datetime.strptime(created_date, "%Y-%m-%d")
            ).days
            if days_alive > valid_for:
                reason = f"Signal expired after {days_alive} days (limit: {valid_for})"

        # ── TARGET HIT check (20% move from entry zone low) ──
        target_price = signal["entry_zone_low"] * (1 + config.TARGET_MOVE_PCT / 100) if signal.get("entry_zone_low") else None
        if target_price and sig_type == "LONG" and today_close >= target_price:
            logger.info("TARGET_HIT %s %s — close ₹%.2f >= target ₹%.2f",
                        sig_type, symbol, today_close, target_price)
            db.update_signal_state(signal_id, "TARGET_HIT")
            db.log_signal_outcome(signal_id, "TARGET_HIT", today_close,
                                  f"Price hit 20% target at ₹{today_close:.2f}")
            continue

        # ── Invalidate if any trigger fired ──
        if reason:
            logger.info("INVALIDATED %s %s — %s", sig_type, symbol, reason)
            db.update_signal_state(signal_id, "INVALIDATED")
            db.log_signal_outcome(signal_id, "INVALIDATED", today_close, reason)
            invalidated_ids.append(signal_id)

    logger.info("Killer module: %d signal(s) invalidated", len(invalidated_ids))
    return invalidated_ids
