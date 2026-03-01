"""
sentiment/sector_agent.py — Dimension 1: Sector Sentiment Analysis.

Queries Claude or Gemini to evaluate the macro-level trend of the stock's sector.
Results are cached per symbol per day to avoid redundant API calls.
"""

import json
import logging

import config
from sentiment._ai_client import call_ai

logger = logging.getLogger(__name__)


def analyze_sector(symbol: str, sector: str, signal_type: str) -> dict | None:
    """
    Ask the AI to evaluate sector-level sentiment for the given stock.

    Returns:
    {
        "sentiment": "BULLISH | BEARISH | NEUTRAL",
        "confidence": 0.75,
        "key_insight": "...",
        "model_used": "gemini-1.5-flash",
        "tokens_used": 350,
    }
    """
    direction = "bullish" if signal_type == "LONG" else "bearish"

    prompt = f"""You are a sector analyst for Indian equity markets. Analyze the current macro-level 
sentiment for the {sector} sector for a potential {direction} trade on {symbol}.

Respond ONLY in JSON with this exact format (no markdown, no extra text):
{{
  "sentiment": "BULLISH or BEARISH or NEUTRAL",
  "confidence": 0.0 to 1.0,
  "key_driver": "single most important factor driving sector now",
  "sector_risk": "biggest risk to this sector in next 2-3 weeks",
  "recommendation": "SUPPORT_LONG or SUPPORT_SHORT or NEUTRAL or AVOID",
  "key_insight": "one sentence summary for a trader"
}}

Focus on:
1. Current global and domestic demand trends for this sector
2. Input cost trends (commodity prices relevant to this sector)
3. Recent government policy or regulatory changes
4. Sector ETF (NSE) performance vs Nifty in last 2 weeks
5. Any sector-wide news in past 5 trading days

Be concise. Confidence should reflect how clear the directional signal is."""

    response = call_ai(prompt, prefer_speed=True)
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
        logger.warning("Sector agent parse error for %s: %s", symbol, e)
        return None
