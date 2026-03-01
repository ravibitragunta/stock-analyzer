"""
sentiment/institutional.py — Dimension 5: Institutional Footprint Analysis.

Evaluates smart money positioning via FII/DII shareholding trends and
F&O Open Interest patterns.

Only runs for ACCEPTED signals (deepest analysis, most tokens).
"""

import json
import logging

import config
from sentiment._ai_client import call_ai

logger = logging.getLogger(__name__)


def analyze_institutional(symbol: str, signal_type: str) -> dict | None:
    """
    Evaluate institutional positioning in this stock.

    Returns:
    {
        "sentiment": "BULLISH | BEARISH | NEUTRAL",
        "confidence": 0.72,
        "key_insight": "FII increasing stake for 3 consecutive quarters...",
        ...
    }
    """
    direction = "LONG" if signal_type == "LONG" else "SHORT"

    prompt = f"""You are an institutional flow analyst for Indian equity markets. Evaluate the 
smart money positioning in {symbol} (NSE-listed) for a {direction} swing trade.

Respond ONLY in JSON with this exact format (no markdown, no extra text):
{{
  "sentiment": "BULLISH or BEARISH or NEUTRAL",
  "institutional_trend": "ACCUMULATION or DISTRIBUTION or NEUTRAL",
  "fii_trend": "INCREASING or DECREASING or STABLE",
  "dii_trend": "INCREASING or DECREASING or STABLE",
  "promoter_pledge_risk": true or false,
  "oi_signal": "brief description of F&O OI trend",
  "confidence": 0.0 to 1.0,
  "key_insight": "one sentence on institutional positioning relevance to this trade"
}}

Analyze:
1. FII shareholding % — trend over last 4 quarters (increasing = bullish)
2. DII (Mutual Fund) shareholding % — trend over last 4 quarters
3. Promoter pledge % — any increase is a red flag (forced selling risk)
4. F&O Open Interest trend:
   - Rising OI + rising price = fresh longs = bullish
   - Rising OI + falling price = fresh shorts = bearish
   - Falling OI + rising price = short covering = neutral-bullish
5. Any recent bulk/block deal by institutional investors on NSE/BSE

For {direction}:
- LONG: Look for accumulation signals
- SHORT: Look for distribution or FII reduction signals

Confidence = how clear the institutional picture is from available data."""

    response = call_ai(prompt, prefer_speed=False)
    if not response:
        return None

    try:
        data = json.loads(response["text"])
        return {
            "sentiment":    data.get("sentiment", "NEUTRAL"),
            "confidence":   float(data.get("confidence", 0.5)),
            "key_insight":  data.get("key_insight", ""),
            "raw_response": response["text"],
            "model_used":   response["model"],
            "tokens_used":  response.get("tokens", 0),
        }
    except (json.JSONDecodeError, KeyError, ValueError) as e:
        logger.warning("Institutional agent parse error for %s: %s", symbol, e)
        return None
