"""
universe_filter.py — Filters the Nifty 200 universe to liquid, tradeable candidates.

Applied BEFORE the CEA scanner to eliminate illiquid or low-price stocks.

Criteria (ALL must pass):
  - Average daily traded value > ₹200 Cr (volume × close)
  - Price (latest close) > ₹150
  - Stock in our Nifty 200 DB list
"""

import logging
from typing import Optional

import config
import database as db

logger = logging.getLogger(__name__)

# 1 Crore = 10,000,000
CR_MULTIPLIER = 10_000_000


def compute_avg_traded_value(ohlcv: list[dict], lookback: int = 20) -> float:
    """
    Compute average daily traded value over the last `lookback` days.
    Traded Value = close × volume.
    Returns value in Crores (÷ 10,000,000).
    """
    recent = ohlcv[-lookback:] if len(ohlcv) >= lookback else ohlcv
    if not recent:
        return 0.0
    vals = [row["close"] * row["volume"] for row in recent]
    return sum(vals) / len(vals) / CR_MULTIPLIER


def filter_universe(stocks: list[dict], min_days_data: int = 60) -> list[dict]:
    """
    Apply the universe filter to all stocks in the DB.

    Args:
        stocks: list of stock dicts from DB (must have symbol, sector, etc.)
        min_days_data: minimum days of OHLCV we need to even attempt scanning

    Returns:
        Filtered list of stock dicts that pass all criteria.
    """
    passed = []
    rejected_price = 0
    rejected_value = 0
    rejected_data = 0

    for stock in stocks:
        symbol = stock["symbol"]
        ohlcv = db.get_ohlcv(symbol, days=min_days_data)

        if len(ohlcv) < min_days_data:
            logger.debug("%s: insufficient data (%d days) — skipping", symbol, len(ohlcv))
            rejected_data += 1
            continue

        latest_close = ohlcv[-1]["close"]

        # Filter 1: Minimum price
        if latest_close < config.MIN_PRICE:
            logger.debug("%s: price ₹%.2f < ₹%d minimum", symbol, latest_close, config.MIN_PRICE)
            rejected_price += 1
            continue

        # Filter 2: Minimum traded value
        avg_value_cr = compute_avg_traded_value(ohlcv, lookback=20)
        if avg_value_cr < config.MIN_TRADED_VALUE_CR:
            logger.debug(
                "%s: avg traded value ₹%.1f Cr < ₹%d Cr minimum",
                symbol, avg_value_cr, config.MIN_TRADED_VALUE_CR,
            )
            rejected_value += 1
            continue

        # All filters passed
        stock_enriched = {**stock, "latest_close": latest_close, "avg_value_cr": round(avg_value_cr, 1)}
        passed.append(stock_enriched)

    logger.info(
        "Universe filter: %d passed | %d rejected (price) | %d rejected (value) | %d rejected (data)",
        len(passed), rejected_price, rejected_value, rejected_data,
    )
    return passed
