"""
sentiment/valuechain_agent.py — Dimension 3: Value Chain & Supplier Scope.

Analyzes upstream (input costs, suppliers) and downstream (demand, customers)
signals for the specific stock — the most differentiated AI dimension.

Only runs for ACCEPTED signals to preserve API budget.
"""

import json
import logging

import config
from sentiment._ai_client import call_ai

logger = logging.getLogger(__name__)

# Sector-specific value chain context to enrich prompts
SECTOR_CONTEXT = {
    "Auto":         "Key inputs: steel, aluminium, rubber. Customers: retail buyers, fleet operators, export markets.",
    "Metal":        "Key inputs: coking coal, iron ore, power. Customers: auto, construction, infrastructure.",
    "IT":           "Key inputs: talent cost (attrition), cloud infrastructure. Customers: BFSI, retail, healthcare globally.",
    "Pharma":       "Key inputs: API (China-dependent), packaging. Customers: domestic hospitals, export regulated markets.",
    "FMCG":         "Key inputs: palm oil, packaging, sugar, crude derivatives. Customers: rural and urban India.",
    "Finance":      "Key inputs: cost of funds (RBI repo rate). Customers: retail borrowers, MSMEs, corporates.",
    "Energy":       "Key inputs: crude oil, gas, coal. Customers: industry, retail consumers.",
    "Cement":       "Key inputs: coal, limestone, power. Customers: real estate, infra projects.",
    "Chemicals":    "Key inputs: feedstock (crude derivatives, specialty chemicals). Customers: pharma, agri, FMCG.",
    "Industrials":  "Key inputs: steel, copper, electronics. Customers: power sector, railways, defense.",
    "Realty":       "Key inputs: steel, cement, labor. Customers: homebuyers, commercial tenants.",
    "Consumer":     "Key inputs: gold/silver (jewellery), cotton (apparel), packaging. Customers: retail India.",
    "Telecom":      "Key inputs: spectrum, tower capex. Customers: mobile subscribers, enterprise.",
    "Infra":        "Key inputs: steel, cement, equipment. Customers: government, PSUs.",
    "Agri":         "Key inputs: natural gas (fertilizer), agrochemical raw materials. Customers: farmers.",
}


def analyze_value_chain(symbol: str, sector: str, signal_type: str) -> dict | None:
    """
    Evaluate value chain health for this specific stock.

    Returns:
    {
        "sentiment": "BULLISH | BEARISH | NEUTRAL",
        "confidence": 0.68,
        "key_insight": "...",
        ...
    }
    """
    context = SECTOR_CONTEXT.get(sector, "Evaluate relevant supply chain factors for this sector.")
    direction = "bullish" if signal_type == "LONG" else "bearish"

    prompt = f"""You are a fundamental analyst specializing in Indian supply chains. Evaluate the 
value chain health for {symbol} ({sector} sector) to assess a {direction} swing trade.

Sector context: {context}

Respond ONLY in JSON with this exact format (no markdown, no extra text):
{{
  "sentiment": "BULLISH or BEARISH or NEUTRAL",
  "supply_chain_risk": "LOW or MEDIUM or HIGH",
  "input_cost_trend": "EASING or RISING or STABLE",
  "demand_signals": "brief description of demand environment",
  "competitor_threat": "LOW or MEDIUM or HIGH",
  "confidence": 0.0 to 1.0,
  "key_insight": "one sentence on the most important value chain factor for this trade"
}}

Focus on:
1. Are key input costs for this company rising or falling?
2. Any supply chain disruption (logistics, China dependency, sanctions)?
3. Demand signals from end customers (index/sector data, recent sales reports)
4. Competitive threats from peers or imports
5. Margin expansion/contraction outlook based on input-output dynamics

Confidence = how clear the value chain picture is."""

    response = call_ai(prompt, prefer_speed=False)   # Use more capable model for this
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
        logger.warning("Value chain agent parse error for %s: %s", symbol, e)
        return None
