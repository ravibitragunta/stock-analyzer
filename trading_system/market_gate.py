"""
market_gate.py — Market Gate Module.

Checks overall market health BEFORE running any stock scanning.
If the gate is closed for both longs and shorts, the system exits early.

Gate logic:
  LONG gate (ALL must pass):
    - Nifty 50 close > 20-day EMA
    - India VIX < VIX_BLOCK_THRESHOLD (20)
    - PCR >= PCR_BULL_MIN (0.7)
    - Advance-Decline ratio > AD_RATIO_MIN (0.4)
    - No NSE holiday tomorrow

  SHORT gate (ANY one triggers):
    - Nifty 50 close < 20-day EMA
    - OR VIX >= VIX_SHORT_THRESHOLD (18)
    - OR PCR < PCR_BEAR_MAX (0.7)
"""

import logging
from datetime import date, timedelta

import config
import database as db
import data_fetcher as fetcher

logger = logging.getLogger(__name__)


def compute_ema(prices: list[float], period: int) -> list[float]:
    """Calculate EMA for a list of prices. Returns list of same length (NaN-padded at start)."""
    ema = []
    k = 2 / (period + 1)
    for i, p in enumerate(prices):
        if i < period - 1:
            ema.append(float("nan"))
        elif i == period - 1:
            ema.append(sum(prices[:period]) / period)   # SMA as seed
        else:
            ema.append(p * k + ema[-1] * (1 - k))
    return ema


def is_nse_holiday(check_date: date) -> bool:
    """Check if a given date is an NSE holiday or weekend."""
    if check_date.weekday() >= 5:  # Saturday=5, Sunday=6
        return True
    return check_date.isoformat() in config.NSE_HOLIDAYS


def is_trading_day_tomorrow() -> bool:
    """Returns True if tomorrow is a valid NSE trading day."""
    tomorrow = date.today() + timedelta(days=1)
    return not is_nse_holiday(tomorrow)


def run_market_gate() -> dict:
    """
    Execute the full market gate check.
    Returns a gate dict. Saves to market_breadth table.
    """
    today = date.today().isoformat()
    logger.info("=== Running Market Gate for %s ===", today)

    # ── Fetch Nifty 50 data (50 days for EMA calculation) ──
    nifty_candles = fetcher.fetch_index_ohlcv(config.NIFTY_50_KEY, days=60)
    if not nifty_candles:
        logger.error("Could not fetch Nifty 50 data — market gate FAILED")
        return _gate_failure("Failed to fetch Nifty 50 data")

    closes = [c["close"] for c in nifty_candles]
    nifty_close = closes[-1]
    emas = compute_ema(closes, config.EMA_PERIOD)
    nifty_20ema = emas[-1]
    nifty_above_ema = nifty_close > nifty_20ema

    logger.info("Nifty 50 Close: %.2f | 20-EMA: %.2f | Above EMA: %s",
                nifty_close, nifty_20ema, nifty_above_ema)

    # ── Fetch India VIX ──
    vix_candles = fetcher.fetch_index_ohlcv(config.INDIA_VIX_KEY, days=5)
    vix_level = vix_candles[-1]["close"] if vix_candles else None

    if vix_level is None:
        logger.warning("Could not fetch VIX — using neutral assumption (15.0)")
        vix_level = 15.0

    logger.info("India VIX: %.2f", vix_level)

    # ── VIX assessment ──
    vix_ok_for_longs = vix_level < config.VIX_BLOCK_THRESHOLD
    vix_short_trigger = vix_level >= config.VIX_SHORT_THRESHOLD
    if config.VIX_WARN_THRESHOLD <= vix_level < config.VIX_BLOCK_THRESHOLD:
        logger.warning("VIX WARNING: %.2f is in the caution zone (%.0f–%.0f)",
                       vix_level, config.VIX_WARN_THRESHOLD, config.VIX_BLOCK_THRESHOLD)

    # ── PCR (Put-Call Ratio) — fetched from NSE or approximated ──
    pcr = _fetch_pcr()
    pcr_ok_for_longs = pcr >= config.PCR_BULL_MIN
    pcr_short_trigger = pcr < config.PCR_BEAR_MAX
    logger.info("PCR: %.3f", pcr)

    # ── Advance-Decline ratio ──
    ad_ratio = fetcher.fetch_advance_decline()
    ad_ok_for_longs = ad_ratio > config.AD_RATIO_MIN
    logger.info("Advance-Decline ratio: %.3f", ad_ratio)

    # ── Holiday check ──
    trading_tomorrow = is_trading_day_tomorrow()
    if not trading_tomorrow:
        logger.info("Tomorrow is NSE holiday — longs gate additionally blocked")

    # ── Gate decisions ──
    long_allowed = all([
        nifty_above_ema,
        vix_ok_for_longs,
        pcr_ok_for_longs,
        ad_ok_for_longs,
        trading_tomorrow,
    ])

    short_allowed = any([
        not nifty_above_ema,
        vix_short_trigger,
        pcr_short_trigger,
    ])

    # ── Market regime classification ──
    regime = _classify_regime(nifty_above_ema, vix_level, ad_ratio, pcr)

    gate = {
        "date":              today,
        "nifty_close":       round(nifty_close, 2),
        "nifty_20ema":       round(nifty_20ema, 2),
        "nifty_above_20ema": int(nifty_above_ema),
        "vix_level":         round(vix_level, 2),
        "pcr":               round(pcr, 3),
        "advance_decline":   round(ad_ratio, 3),
        "long_allowed":      int(long_allowed),
        "short_allowed":     int(short_allowed),
        "market_regime":     regime,
        "global_macro_score": None,  # filled by AI layer later
    }

    db.save_market_gate(gate)
    _log_gate_result(gate)
    return gate


def _classify_regime(nifty_above_ema: bool, vix: float, ad_ratio: float, pcr: float) -> str:
    """Classify the current market regime into a readable label."""
    if vix >= config.VIX_BLOCK_THRESHOLD:
        return "VOLATILE"
    if nifty_above_ema and ad_ratio > 0.6 and vix < 15:
        return "BULLISH"
    if not nifty_above_ema and ad_ratio < 0.4 and vix > 15:
        return "BEARISH"
    return "NEUTRAL"


def _fetch_pcr() -> float:
    """
    Fetch Nifty PCR from NSE. Falls back to neutral 1.0 if unavailable.
    NSE publishes PCR in their option chain data.
    """
    import requests
    try:
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://www.nseindia.com",
            "Accept": "application/json",
        }
        # Use a fresh session for NSE (they check cookies/referer)
        s = requests.Session()
        # Prime the session with the main NSE page to get cookies
        s.get("https://www.nseindia.com", headers=headers, timeout=10)
        resp = s.get(
            "https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY",
            headers=headers, timeout=15
        )
        if resp.status_code == 200:
            data = resp.json()
            pcr = data.get("filtered", {}).get("CE", {})
            # PCR = total PE OI / total CE OI
            ce_oi = data.get("filtered", {}).get("CE", {}).get("totOI", 0)
            pe_oi = data.get("filtered", {}).get("PE", {}).get("totOI", 0)
            if ce_oi > 0:
                return round(pe_oi / ce_oi, 3)
    except Exception as e:
        logger.warning("PCR fetch failed: %s — using neutral 1.0", e)
    return 1.0   # neutral fallback


def _gate_failure(reason: str) -> dict:
    """Return a CLOSED gate dict when we can't determine market conditions."""
    today = date.today().isoformat()
    logger.error("Market gate FAILURE: %s", reason)
    return {
        "date":               today,
        "nifty_close":        None,
        "nifty_20ema":        None,
        "nifty_above_20ema":  0,
        "vix_level":          None,
        "pcr":                None,
        "advance_decline":    None,
        "long_allowed":       0,
        "short_allowed":      0,
        "market_regime":      "UNKNOWN",
        "global_macro_score": None,
        "error":              reason,
    }


def _log_gate_result(gate: dict):
    """Human-readable gate summary in logs."""
    long_str = "✅ OPEN" if gate["long_allowed"] else "❌ CLOSED"
    short_str = "✅ OPEN" if gate["short_allowed"] else "❌ CLOSED"
    logger.info(
        "Market Gate Result → Long: %s | Short: %s | Regime: %s | VIX: %.1f | PCR: %.2f",
        long_str, short_str, gate["market_regime"],
        gate["vix_level"] or 0, gate["pcr"] or 0,
    )
