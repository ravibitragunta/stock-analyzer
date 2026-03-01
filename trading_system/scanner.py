"""
scanner.py — CEA Step 1: Compression Detection.

Identifies stocks in a "compressed" (low-volatility, accumulation) phase.
Sets signal state to COMPRESSION_DETECTED.

Compression criteria (ALL must pass):
  1. Average candle range (high-low)/close < MAX_CANDLE_RANGE_PCT (1.2%)
  2. ATR(14) in lowest ATR_PERCENTILE_MAX (20th) percentile of last 6 months
  3. Volume declining: OBV trending down AND linear regression slope < 0
  4. Price contained within a COMPRESSION_BAND_PCT (4%) band
  5. Bollinger Band width in lowest BB_WIDTH_PERCENTILE (20th) percentile
  6. Minimum COMPRESSION_MIN_DAYS (10) consecutive days meeting criteria
  7. No VSA distribution (narrow range + abnormally high volume)
"""

import logging
import math
from datetime import date

import numpy as np

import config
import database as db

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# TECHNICAL INDICATORS
# ─────────────────────────────────────────────

def compute_atr(ohlcv: list[dict], period: int = 14) -> list[float]:
    """Average True Range — measures volatility per candle."""
    trs = []
    atrs = []
    for i, bar in enumerate(ohlcv):
        if i == 0:
            tr = bar["high"] - bar["low"]
        else:
            prev_close = ohlcv[i - 1]["close"]
            tr = max(
                bar["high"] - bar["low"],
                abs(bar["high"] - prev_close),
                abs(bar["low"] - prev_close),
            )
        trs.append(tr)
        if i < period - 1:
            atrs.append(float("nan"))
        elif i == period - 1:
            atrs.append(sum(trs[:period]) / period)
        else:
            # Wilder smoothing
            atrs.append((atrs[-1] * (period - 1) + tr) / period)
    return atrs


def compute_obv(ohlcv: list[dict]) -> list[float]:
    """On-Balance Volume — momentum indicator using volume direction."""
    obv = []
    running = 0
    for i, bar in enumerate(ohlcv):
        if i == 0:
            running = bar["volume"]
        elif bar["close"] > ohlcv[i - 1]["close"]:
            running += bar["volume"]
        elif bar["close"] < ohlcv[i - 1]["close"]:
            running -= bar["volume"]
        obv.append(running)
    return obv


def linear_regression_slope(values: list[float]) -> float:
    """Calculate the slope of the least-squares regression line. Positive = uptrend."""
    n = len(values)
    if n < 2:
        return 0.0
    x = list(range(n))
    x_mean = sum(x) / n
    y_mean = sum(values) / n
    num = sum((x[i] - x_mean) * (values[i] - y_mean) for i in range(n))
    den = sum((x[i] - x_mean) ** 2 for i in range(n))
    return num / den if den != 0 else 0.0


def compute_bollinger_bands(closes: list[float], period: int = 20, std_dev: float = 2.0):
    """
    Returns (upper, lower, width_pct) for each bar.
    Width % = (upper - lower) / middle_band * 100
    """
    results = []
    for i in range(len(closes)):
        if i < period - 1:
            results.append((float("nan"), float("nan"), float("nan")))
            continue
        window = closes[i - period + 1: i + 1]
        mid = sum(window) / period
        std = math.sqrt(sum((p - mid) ** 2 for p in window) / period)
        upper = mid + std_dev * std
        lower = mid - std_dev * std
        width_pct = (upper - lower) / mid * 100 if mid != 0 else 0
        results.append((upper, lower, width_pct))
    return results


def percentile_rank(value: float, series: list[float]) -> float:
    """Rank of value within series as a percentile [0–100]."""
    valid = [v for v in series if not math.isnan(v)]
    if not valid:
        return 50.0
    below = sum(1 for v in valid if v < value)
    return below / len(valid) * 100


# ─────────────────────────────────────────────
# COMPRESSION DETECTION
# ─────────────────────────────────────────────

def detect_compression(symbol: str, ohlcv: list[dict]) -> dict | None:
    """
    Run compression scan on a stock. Returns a compression result dict if
    the stock is in compression, or None if not.

    Result dict:
      {symbol, compression_days, band_high, band_low, band_pct,
       avg_range_pct, atr14, atr_percentile, obv_slope, vol_slope,
       bb_width_pct, bb_percentile}
    """
    if len(ohlcv) < config.COMPRESSION_LOOKBACK + 30:
        return None  # Not enough data

    closes = [b["close"] for b in ohlcv]
    highs  = [b["high"] for b in ohlcv]
    lows   = [b["low"] for b in ohlcv]
    vols   = [b["volume"] for b in ohlcv]

    # ── ATR(14) over full 6-month lookback ──
    atrs = compute_atr(ohlcv, period=14)
    valid_atrs = [a for a in atrs if not math.isnan(a)]

    # ── Bollinger Bands over full history ──
    bb_results = compute_bollinger_bands(closes, period=20)
    valid_bb_widths = [r[2] for r in bb_results if not math.isnan(r[2])]

    # ── OBV ──
    obv = compute_obv(ohlcv)

    # Now examine recent COMPRESSION_LOOKBACK candles
    lookback = config.COMPRESSION_LOOKBACK
    recent = ohlcv[-lookback:]
    recent_closes = closes[-lookback:]
    recent_vols   = vols[-lookback:]
    recent_atrs   = [a for a in atrs[-lookback:] if not math.isnan(a)]
    recent_obv    = obv[-lookback:]
    recent_bb     = [r[2] for r in bb_results[-lookback:] if not math.isnan(r[2])]

    if not recent_atrs:
        return None

    # ── Check 1: Average candle range % ──
    avg_range_pct = (
        sum((b["high"] - b["low"]) / b["close"] * 100 for b in recent)
        / len(recent)
    )
    if avg_range_pct >= config.MAX_CANDLE_RANGE_PCT:
        return None

    # ── Check 2: ATR in lowest 20th percentile ──
    latest_atr = recent_atrs[-1]
    atr_pct_rank = percentile_rank(latest_atr, valid_atrs)
    if atr_pct_rank > config.ATR_PERCENTILE_MAX:
        return None

    # ── Check 3: Volume declining (OBV slope < 0 AND raw volume slope < 0) ──
    obv_slope = linear_regression_slope(recent_obv)
    vol_slope = linear_regression_slope([float(v) for v in recent_vols])
    if obv_slope > 0 or vol_slope > 0:
        return None  # Must have declining or flat volume

    # ── Check 4: Price band width within 4% ──
    band_high = max(b["high"] for b in recent)
    band_low  = min(b["low"] for b in recent)
    band_pct  = (band_high - band_low) / band_low * 100 if band_low > 0 else 999
    if band_pct > config.COMPRESSION_BAND_PCT:
        return None

    # ── Check 5: Bollinger Band width in lowest 20th percentile ──
    if recent_bb:
        latest_bb_width = recent_bb[-1]
        bb_pct_rank = percentile_rank(latest_bb_width, valid_bb_widths)
        if bb_pct_rank > config.BB_WIDTH_PERCENTILE:
            return None
    else:
        bb_pct_rank = 50.0
        latest_bb_width = 0.0

    # ── Check 6: VSA distribution check — reject if narrow range + high volume ──
    # Distribution signal: avg_range_pct < 0.8% but volume > 2x 20-day avg
    twenty_day_avg_vol = sum(vols[-20:]) / 20
    recent_avg_vol = sum(recent_vols) / len(recent_vols)
    if avg_range_pct < 0.8 and recent_avg_vol > 2 * twenty_day_avg_vol:
        logger.debug("%s: VSA distribution detected — skipping compression", symbol)
        return None

    # ── Count consecutive compression days ──
    compression_days = _count_compression_days(ohlcv, config.COMPRESSION_LOOKBACK)
    if compression_days < config.COMPRESSION_MIN_DAYS:
        return None

    logger.info(
        "COMPRESSION: %s | %d days | band=%.1f%% | ATR_pctile=%.0f | BB_pctile=%.0f",
        symbol, compression_days, band_pct, atr_pct_rank, bb_pct_rank,
    )

    return {
        "symbol":          symbol,
        "compression_days": compression_days,
        "band_high":       round(band_high, 2),
        "band_low":        round(band_low, 2),
        "band_pct":        round(band_pct, 2),
        "avg_range_pct":   round(avg_range_pct, 3),
        "atr14":           round(latest_atr, 2),
        "atr_percentile":  round(atr_pct_rank, 1),
        "obv_slope":       round(obv_slope, 2),
        "vol_slope":       round(vol_slope, 2),
        "bb_width_pct":    round(latest_bb_width, 2),
        "bb_percentile":   round(bb_pct_rank, 1),
    }


def _count_compression_days(ohlcv: list[dict], max_look: int) -> int:
    """
    Count how many of the most recent n days satisfy the basic range criterion.
    Used to ensure the compression is sustained, not just 1–2 narrow days.
    """
    count = 0
    for bar in reversed(ohlcv[-max_look:]):
        r = (bar["high"] - bar["low"]) / bar["close"] * 100
        if r < config.MAX_CANDLE_RANGE_PCT:
            count += 1
        else:
            break   # Compression is continuous from the most recent candle backwards
    return count


# ─────────────────────────────────────────────
# MAIN SCAN
# ─────────────────────────────────────────────

def check_sector_strength(sector_name: str, ohlcv_fetcher) -> bool:
    """Check if the sector index is above its 20-EMA."""
    if sector_name not in config.SECTOR_INDICES:
        return True
        
    index_key = config.SECTOR_INDICES[sector_name]
    candles = ohlcv_fetcher(index_key, days=30)
    if not candles:
        return True
        
    closes = [c["close"] for c in candles]
    from validator import compute_ema
    emas = compute_ema(closes, period=20)
    
    if len(emas) < 20 or math.isnan(emas[-1]):
        return True
        
    return closes[-1] > emas[-1]


def scan_for_compression(filtered_stocks: list[dict], ohlcv_fetcher=None) -> list[dict]:
    """
    Run compression detection on all stocks in the filtered universe.

    Returns a list of compression result dicts for stocks currently in compression.
    These will be saved as COMPRESSION_DETECTED signals.
    """
    if ohlcv_fetcher is None:
        from data_fetcher import fetch_index_ohlcv
        ohlcv_fetcher = fetch_index_ohlcv

    sector_cache = {}
    results = []
    
    for stock in filtered_stocks:
        symbol = stock["symbol"]
        sector = stock.get("sector", "Unknown")

        if getattr(config, "SECTOR_FILTER_ENABLED", False):
            if sector not in sector_cache:
                sector_cache[sector] = check_sector_strength(sector, ohlcv_fetcher)
            
            if not sector_cache[sector]:
                logger.debug("%s: sector %s below 20-EMA — skipped", symbol, sector)
                continue

        ohlcv = db.get_ohlcv(symbol, days=180)   # 6 months for ATR percentile calc
        if not ohlcv:
            continue

        result = detect_compression(symbol, ohlcv)
        if result:
            result["sector"] = stock.get("sector", "Unknown")
            result["latest_close"] = ohlcv[-1]["close"]
            result["avg_value_cr"] = stock.get("avg_value_cr", 0)
            results.append(result)

    logger.info("Compression scan complete: %d stocks in compression", len(results))
    return results
