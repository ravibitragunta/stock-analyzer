"""
sentiment/event_risk.py — Dimension 4: News & Event Risk Detection.

Detects upcoming catalysts that could invalidate a trade:
  - Earnings / Q results
  - RBI, SEBI regulatory actions
  - AGM, Board meetings, dividend announcements
  - FII/Promoter block deals
  - Analyst rating changes
  - M&A / QIP news

High event risk → signal confidence reduced → may block SHORT signals near earnings.
"""

import json
import logging

import config
from sentiment._ai_client import call_ai

logger = logging.getLogger(__name__)


def analyze_event_risk(symbol: str, signal_type: str) -> dict | None:
    """
    Check for upcoming or recent events that could invalidate this trade.

    Returns:
    {
        "sentiment": "POSITIVE | NEGATIVE | NEUTRAL",  ← net news sentiment
        "confidence": 0.85,  ← confidence in the event risk assessment
        "event_risk": true/false,
        "event_type": "EARNINGS | REGULATORY | BLOCK_DEAL | NONE",
        "days_to_event": 6,
        "key_insight": "Q3 results due in 6 days, beat expected — avoid short",
        ...
    }
    """
    direction = "LONG" if signal_type == "LONG" else "SHORT"

    prompt = f"""You are an event-driven risk analyst for Indian equities. Assess event risk for 
{symbol} listed on NSE, specifically for a {direction} swing trade being considered today.

Respond ONLY in JSON with this exact format (no markdown, no extra text):
{{
  "sentiment": "POSITIVE or NEGATIVE or NEUTRAL",
  "event_risk": true or false,
  "event_type": "EARNINGS or AGM or REGULATORY or BLOCK_DEAL or MANAGEMENT or NONE",
  "days_to_event": null or integer (days until next major event),
  "expected_outcome": "brief expectation if event known",
  "action": "PROCEED or REDUCE_SIZE or AVOID",
  "confidence": 0.0 to 1.0,
  "key_insight": "one sentence on the biggest event risk or lack thereof"
}}

Check:
1. Quarterly earnings — is the result date within 14 calendar days?
2. Board meeting date for dividend or fund-raise announcement
3. Any recent (past 7 days) SEBI/RBI regulatory action on this company or sector
4. Promoter pledge increase or block deal in past 5 days
5. Analyst upgrades or downgrades in past 7 days
6. M&A rumour, open offer, buyback announcement

For {direction} trades:
- If LONG: earnings beat expected = OK, surprise risk = flag
- If SHORT: earnings due soon + beat expected = HIGH RISK, avoid

Confidence = how certain you are about the event risk picture."""

    response = call_ai(prompt, prefer_speed=False)   # Use Claude for precision
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
            # Extra fields for reporter
            "event_risk":   data.get("event_risk", False),
            "event_type":   data.get("event_type", "NONE"),
            "days_to_event": data.get("days_to_event"),
            "action":       data.get("action", "PROCEED"),
        }
    except (json.JSONDecodeError, KeyError, ValueError) as e:
        logger.warning("Event risk agent parse error for %s: %s", symbol, e)
        return None
