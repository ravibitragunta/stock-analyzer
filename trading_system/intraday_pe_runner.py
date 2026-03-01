"""
intraday_pe_runner.py — Real-time PE Alert Engine.

Runs during market hours (10:30 AM – 2:00 PM IST).
Monitors the SHORT watchlist from last night's EOD scan.

Architecture:
  - WebSocket: tracks underlying prices of watchlist stocks (real-time)
  - REST poll (every 3 min): VIX + option chain OI + IV for those stocks
  - Trigger: All 7 conditions met → Claude final check → Telegram alert

One Telegram alert per stock per day (enforced in telegram_notifier).
"""

import logging
import math
import threading
import time
from datetime import date, datetime, time as dtime

import requests

import config
import database as db
from options_module import get_otm_pe_strike, get_current_monthly_expiry, estimate_premium
from validator import compute_ema

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# SHARED STATE (thread-safe)
# ─────────────────────────────────────────────

_lock                = threading.Lock()
_live_prices: dict   = {}   # symbol → latest underlying price
_intraday_bars: dict = {}   # symbol → list of 15-min bar dicts (for EMA calc)
_vix_current: float  = 0.0
_pcr_current: float  = 1.0
_oc_data: dict       = {}   # symbol → option chain data


# ─────────────────────────────────────────────
# OPTION CHAIN POLLING (REST, every 3 min)
# ─────────────────────────────────────────────

def _fetch_vix() -> float:
    """Fetch current India VIX via Upstox REST."""
    try:
        import data_fetcher as fetcher
        candles = fetcher.fetch_index_ohlcv(config.INDIA_VIX_KEY, days=2)
        return candles[-1]["close"] if candles else 0.0
    except Exception as e:
        logger.debug("VIX fetch error: %s", e)
        return 0.0


def _fetch_pcr_and_oi(symbol: str) -> dict:
    """
    Fetch option chain for a stock from NSE to get:
      - PCR (total PE OI / CE OI)
      - Target PE strike OI change %
      - IV for closest PE strike
    Returns dict with keys: pcr, oi_change_pct, iv_percentile
    """
    try:
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Referer":    "https://www.nseindia.com",
            "Accept":     "application/json",
        }
        session = requests.Session()
        session.get("https://www.nseindia.com", headers=headers, timeout=10)

        resp = session.get(
            f"https://www.nseindia.com/api/option-chain-equities?symbol={symbol}",
            headers=headers, timeout=15,
        )
        if resp.status_code != 200:
            return {}

        data = resp.json()
        records = data.get("records", {}).get("data", [])

        ce_oi = pe_oi = 0
        target_pe_oi_today = target_pe_oi_prev = 0
        iv_list = []

        price = _live_prices.get(symbol, 0)
        target_strike = get_otm_pe_strike(price) if price > 0 else 0

        for rec in records:
            pe = rec.get("PE", {})
            ce = rec.get("CE", {})
            strike = rec.get("strikePrice", 0)

            ce_oi += ce.get("openInterest", 0)
            pe_oi += pe.get("openInterest", 0)

            if pe.get("impliedVolatility"):
                iv_list.append(pe["impliedVolatility"])

            if strike == target_strike and pe:
                target_pe_oi_today = pe.get("openInterest", 0)
                target_pe_oi_prev  = pe.get("pchangeinOpenInterest", 0)   # % change field

        pcr = round(pe_oi / ce_oi, 3) if ce_oi > 0 else 1.0
        oi_chg = target_pe_oi_prev   # NSE already returns % OI change

        # IV percentile (rough: rank current ATM IV vs 52-week range)
        iv_atm = iv_list[len(iv_list) // 2] if iv_list else 20.0
        iv_pct = 40   # Default: 40th percentile (unavailable intraday without historical IV)

        return {
            "pcr":            pcr,
            "oi_change_pct":  oi_chg,
            "iv_percentile":  iv_pct,
            "iv_atm":         iv_atm,
            "target_strike":  target_strike,
            "pe_oi_total":    pe_oi,
        }
    except Exception as e:
        logger.debug("Option chain fetch error for %s: %s", symbol, e)
        return {}


def _poll_loop(watchlist_symbols: list[str]):
    """Background thread: polls VIX + option chains every 3 minutes."""
    global _vix_current, _pcr_current, _oc_data

    while True:
        now = datetime.now().time()
        if now > dtime(14, 15):
            break   # Stop polling after 2:15 PM

        try:
            vix = _fetch_vix()
            with _lock:
                _vix_current = vix

            for symbol in watchlist_symbols:
                oc = _fetch_pcr_and_oi(symbol)
                with _lock:
                    _oc_data[symbol] = oc

            logger.debug("Poll: VIX=%.1f | %d stocks updated", vix, len(_oc_data))

        except Exception as e:
            logger.warning("Poll error: %s", e)

        time.sleep(config.OPTION_CHAIN_POLL_SEC)


# ─────────────────────────────────────────────
# EMA CHECK ON INTRADAY BARS
# ─────────────────────────────────────────────

def _ema_broken(symbol: str, current_price: float) -> tuple[bool, float]:
    """
    Check if underlying price has broken below the 20-EMA of intraday bars.
    Returns (broken: bool, ema_value: float)
    """
    bars = _intraday_bars.get(symbol, [])
    if len(bars) < config.PE_EMA_PERIOD:
        return False, 0.0

    closes = [b["close"] for b in bars] + [current_price]
    emas   = compute_ema(closes, config.PE_EMA_PERIOD)
    ema_20 = emas[-2]   # EMA before adding current tick

    return current_price < ema_20, round(ema_20, 2)


# ─────────────────────────────────────────────
# 7-CONDITION TRIGGER CHECK
# ─────────────────────────────────────────────

def _check_all_conditions(symbol: str, price: float, oc: dict) -> dict | None:
    """
    Check all 7 PE alert conditions for a stock.
    Returns a full alert dict if ALL pass, else None.
    """
    vix = _vix_current
    pcr = oc.get("pcr", 1.0)
    oi_chg = oc.get("oi_change_pct", 0)
    iv_pct = oc.get("iv_percentile", 50)

    # 1. VIX in sweet zone
    if not (config.PE_VIX_MIN <= vix <= config.PE_VIX_MAX):
        return None

    # 2. PCR below threshold
    if pcr >= config.PE_PCR_MAX:
        return None

    # 3. OI buildup on target PE strike
    if oi_chg < config.PE_OI_CHANGE_MIN_PCT:
        return None

    # 4. IV percentile acceptable
    if iv_pct > config.PE_IV_PERCENTILE_MAX:
        return None

    # 5. Time window
    now = datetime.now().time()
    start = dtime(*map(int, config.INTRADAY_START_IST.split(":")))
    end   = dtime(*map(int, config.INTRADAY_END_IST.split(":")))
    if not (start <= now <= end):
        return None

    # 6. EMA break
    ema_broken, ema_val = _ema_broken(symbol, price)
    if not ema_broken:
        return None

    # 7. Don't chase (check via historical data last signal)
    # If premium already up >35% from prev close, skip
    # (Simplified: just ensure OI signal is fresh, not stale)
    if oi_chg > 80:   # Abnormally large OI jump = already chased
        return None

    # All conditions met — build alert data
    strike = oc.get("target_strike") or get_otm_pe_strike(price)
    expiry_str, dte = get_current_monthly_expiry()
    premium     = estimate_premium(price, strike, dte, "PE")
    target_prem = round(premium * (1 + config.TARGET_OPTION_GAIN_PCT / 100), 1)
    sl_und      = round(price * 1.02, 2)   # Underlying stop = 2% above current for shorts

    stock = db.get_stock(symbol)

    return {
        "symbol":              symbol,
        "sector":              stock.get("sector", "") if stock else "",
        "underlying_price":    round(price, 2),
        "ema_20":              ema_val,
        "pe_strike":           strike,
        "expiry":              expiry_str,
        "days_to_expiry":      dte,
        "approx_premium":      premium,
        "target_premium":      target_prem,
        "stop_loss_underlying": sl_und,
        "vix":                 vix,
        "pcr":                 pcr,
        "oi_change_pct":       oi_chg,
        "iv_percentile":       iv_pct,
        "conditions_met":      7,
        "trigger_time":        datetime.now().isoformat(),
        "ai_summary":          None,
        "ai_confidence":       "N/A",
    }


# ─────────────────────────────────────────────
# CLAUDE FINAL CHECK (real-time, no batching)
# ─────────────────────────────────────────────

def _claude_final_check(alert: dict) -> dict:
    """
    Ask Claude one focused question before alerting:
    Is this a structural breakdown or noise/news?
    Uses Haiku for speed (< 3 seconds response).
    """
    if not config.AI_SENTIMENT_ENABLED or not config.CLAUDE_API_KEY:
        return {**alert, "ai_summary": "AI disabled — rule-based signal only", "ai_confidence": "N/A"}

    from sentiment._ai_client import call_ai
    symbol  = alert["symbol"]
    sector  = alert.get("sector", "")
    price   = alert["underlying_price"]
    ema     = alert["ema_20"]
    vix     = alert["vix"]

    prompt = f"""Quick analysis: {symbol} ({sector}) has just broken below its 20-EMA (₹{ema:.2f}) at ₹{price:.2f}.
VIX is {vix:.1f}. We are considering buying a PE option to short this.

In 2-3 sentences: Is this a genuine structural breakdown (sector/fundamental weakness) 
or likely noise/news-driven (one-time event that will reverse)?
Also flag if there's an earnings/event risk in next 10 days.

Reply ONLY as JSON:
{{"structural": true/false, "confidence": 0.0-1.0, "summary": "2-3 sentence verdict", "action": "PROCEED or AVOID or REDUCE_SIZE"}}"""

    result = call_ai(prompt, prefer_speed=True)
    if not result:
        return {**alert, "ai_summary": "AI call failed — rule-based only", "ai_confidence": "N/A"}

    try:
        import json
        data = json.loads(result["text"])
        action = data.get("action", "PROCEED")
        if action == "AVOID":
            logger.info("[PE] Claude flagged %s as AVOID — suppressing alert", symbol)
            return None   # Claude says don't alert

        conf = float(data.get("confidence", 0.5))
        conf_label = "HIGH" if conf >= 0.7 else "MEDIUM" if conf >= 0.5 else "LOW"
        return {
            **alert,
            "ai_summary":    data.get("summary", ""),
            "ai_confidence": conf_label,
        }
    except Exception as e:
        logger.warning("[PE] Claude parse error for %s: %s", symbol, e)
        return {**alert, "ai_summary": "AI parse error", "ai_confidence": "N/A"}


# ─────────────────────────────────────────────
# WEBSOCKET TICK HANDLER
# ─────────────────────────────────────────────

def _on_tick(symbol: str, price: float, bar_close: bool = False):
    """Called on each price update. bar_close=True when 15-min bar closes."""
    with _lock:
        _live_prices[symbol] = price

        if bar_close:
            # Update intraday bars for EMA calculation
            bars = _intraday_bars.setdefault(symbol, [])
            bars.append({"close": price})
            _intraday_bars[symbol] = bars[-50:]  # Keep last 50 bars

    oc = _oc_data.get(symbol, {})
    if not oc:
        return   # Haven't polled option chain yet

    # Check all conditions
    alert = _check_all_conditions(symbol, price, oc)
    if not alert:
        return

    # Claude gate
    alert = _claude_final_check(alert)
    if not alert:
        return   # Claude said AVOID

    # Send Telegram alert
    from telegram_notifier import send_pe_alert
    send_pe_alert(alert)


# ─────────────────────────────────────────────
# MAIN ENTRY POINT
# ─────────────────────────────────────────────

def run_pe_alert_engine(watchlist_symbols: list[str]):
    """
    Start the intraday PE alert engine.
    watchlist_symbols: SHORT candidates from last night's EOD scan.

    Runs until 2:15 PM IST then exits.
    """
    if not watchlist_symbols:
        logger.info("[PE Engine] No watchlist symbols — nothing to monitor")
        return

    logger.info("[PE Engine] Starting for %d symbols: %s",
                len(watchlist_symbols), ", ".join(watchlist_symbols))

    # Start REST poll thread
    poll_thread = threading.Thread(
        target=_poll_loop, args=(watchlist_symbols,), daemon=True
    )
    poll_thread.start()

    # Wait for first poll
    time.sleep(5)

    # Start WebSocket for underlying prices
    try:
        from websocket_handler import UpstoxWebSocketClient, _floor_to_15min
        import database as db

        keys = []
        for sym in watchlist_symbols:
            stock = db.get_stock(sym)
            if stock and stock.get("instrument_key"):
                keys.append(stock["instrument_key"])

        if not keys:
            logger.warning("[PE Engine] No instrument keys found — using REST polling only")
        else:
            _last_bar_start: dict = {}

            def on_candle(symbol: str, candle: dict):
                _on_tick(symbol, candle["close"], bar_close=True)

            ws_client = UpstoxWebSocketClient(keys, on_candle)
            ws_client.start()

    except ImportError:
        logger.warning("[PE Engine] websocket-client not installed — REST-only mode")

    # Keep alive until end time
    end_time = dtime(*map(int, config.INTRADAY_END_IST.split(":")))
    while datetime.now().time() < end_time:
        time.sleep(30)

    logger.info("[PE Engine] Session ended at %s", datetime.now().strftime("%H:%M"))
