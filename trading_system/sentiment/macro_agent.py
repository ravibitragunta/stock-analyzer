"""
sentiment/macro_agent.py — Dimension 2: Global Macro Sentiment.

Runs ONCE per session (cached to __MACRO__ symbol in ai_sentiment table).
Evaluates global risk appetite, FII flow bias, DXY, crude, and US rates.
"""

import json
import logging

import config
from sentiment._ai_client import call_ai

logger = logging.getLogger(__name__)


def analyze_macro() -> dict | None:
    """
    Evaluate global macro conditions relevant to Indian equity markets.

    Returns:
    {
        "sentiment": "BULLISH | BEARISH | NEUTRAL",
        "confidence": 0.65,
        "key_insight": "...",
        "model_used": "...",
        "tokens_used": 400,
    }
    """
    prompt = """You are a macro analyst focused on Indian equity markets. Assess the current 
global macro environment for Indian equities (NSE/BSE).

Respond ONLY in JSON with this exact format (no markdown, no extra text):
{
  "sentiment": "BULLISH or BEARISH or NEUTRAL",
  "global_risk": "RISK_ON or RISK_OFF or NEUTRAL",
  "fii_flow_bias": "INFLOW or OUTFLOW or MIXED",
  "crude_impact": "POSITIVE or NEGATIVE or NEUTRAL",
  "dollar_impact": "POSITIVE or NEGATIVE or NEUTRAL",
  "confidence": 0.0 to 1.0,
  "top_risk": "single biggest macro risk for Indian markets in next 2 weeks",
  "key_insight": "one sentence macro summary for an Indian equity swing trader"
}

Evaluate:
1. US Fed tone — recent statements (hawkish/dovish) and rate outlook
2. DXY (US Dollar Index) trend — strong dollar pressures FII inflows to India
3. Brent crude price trend — India is import-dependent, rising crude is negative
4. US 10-year yield direction — spiking yields = risk-off globally
5. S&P 500 and Nasdaq trend — global risk appetite barometer
6. China macro signals (PMI, property) — affects EM sentiment broadly
7. Any major geopolitical event in past 48 hours

Confidence = how clear and unambiguous the macro picture is right now."""

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
        logger.warning("Macro agent parse error: %s", e)
        return None
