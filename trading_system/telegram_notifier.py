"""
telegram_notifier.py — Telegram Bot integration.

Sends two types of messages:
  1. EOD Watchlist (9:30 PM) — SHORT candidates from scanner
  2. Intraday PE Alert — triggered by PE alert engine, gated by Claude

One alert per stock per day is enforced at the DB level.
All messages use Telegram MarkdownV2 format.

Setup: Create bot via @BotFather → get token
       Send any message to bot → get chat_id via getUpdates
"""

import logging
import re
from datetime import date

import requests

import config
import database as db

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"


# ─────────────────────────────────────────────
# CORE SEND
# ─────────────────────────────────────────────

def _send(text: str, parse_mode: str = "HTML") -> bool:
    """Send a message to the configured Telegram chat."""
    if not config.TELEGRAM_ENABLED:
        logger.info("[Telegram] Disabled — message not sent")
        return False

    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        logger.warning("[Telegram] BOT_TOKEN or CHAT_ID missing — skipping")
        return False

    url = TELEGRAM_API.format(token=config.TELEGRAM_BOT_TOKEN, method="sendMessage")
    payload = {
        "chat_id":    config.TELEGRAM_CHAT_ID,
        "text":       text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }
    try:
        r = requests.post(url, json=payload, timeout=15)
        if r.status_code == 200:
            return True
        logger.warning("[Telegram] Send failed [%d]: %s", r.status_code, r.text[:200])
        return False
    except Exception as e:
        logger.error("[Telegram] Request error: %s", e)
        return False


def _already_alerted(symbol: str, alert_type: str) -> bool:
    """Check if this stock was already alerted today (deduplication)."""
    return db.alert_sent_today(symbol, alert_type)


def _mark_alerted(symbol: str, alert_type: str):
    """Record that this stock was alerted today."""
    db.save_alert_log(symbol, alert_type)


# ─────────────────────────────────────────────
# MESSAGE 1: EOD SHORT WATCHLIST (9:30 PM)
# ─────────────────────────────────────────────

def send_eod_watchlist(gate: dict, short_signals: list[dict], watchlist: list[dict]) -> bool:
    """
    Send the nightly 9:30 PM SHORT watchlist summary.
    Includes confirmed signals + compression watchlist.
    """
    today   = date.today().strftime("%d-%b-%Y")
    regime  = gate.get("market_regime", "UNKNOWN")
    vix     = gate.get("vix_level") or 0
    pcr     = gate.get("pcr") or 0
    nifty   = gate.get("nifty_close") or 0
    ema     = gate.get("nifty_20ema") or 0
    gate_ok = gate.get("short_allowed", 0)

    gate_line = "✅ SHORT gate OPEN" if gate_ok else "⚠️ SHORT gate CLOSED"

    # Gate summary block
    text = f"""📋 <b>SHORT WATCHLIST — {today} | 9:30 PM</b>

<b>Market :</b> {gate_line}
<b>Regime :</b> {regime} | Nifty {nifty:,.0f}
<b>VIX    :</b> {vix:.1f} {"🔴" if vix > 18 else ("🟡" if vix > 15 else "🟢")}   <b>PCR:</b> {pcr:.2f}

──────────────────────────
"""
    # Confirmed SHORT setups
    if short_signals:
        text += f"<b>🔴 {len(short_signals)} CONFIRMED SHORT SETUP(S)</b>\n\n"
        for s in short_signals[:5]:   # cap at 5 in message
            symbol    = s["symbol"]
            state     = s["state"].replace("_", " ")
            ai_conf   = s.get("ai_confidence", "N/A")
            ai_text   = s.get("ai_summary", "")
            entry     = s.get("entry_zone_low", 0)
            stop      = s.get("stop_loss", 0)
            risk      = s.get("risk_pct", 0)
            exp_move  = s.get("expected_move", "N/A")
            sector    = s.get("sector", "")
            conf_icon = {"HIGH": "🟢", "MEDIUM": "🟡", "LOW": "🔴"}.get(ai_conf, "⚪")

            text += (
                f"<b>{symbol}</b>  <i>{sector}</i>\n"
                f"  State   : {state}\n"
                f"  Entry   : ₹{entry:.2f} | SL: ₹{stop:.2f} | Risk: {risk:.1f}%\n"
                f"  Move    : {exp_move}\n"
                f"  AI      : {conf_icon} {ai_conf}"
            )
            if ai_text:
                text += "\n  <i>\"" + ai_text[:120] + "\"</i>"
            text += "\n\n"
    else:
        text += "<i>No confirmed short setups today.</i>\n\n"

    # Watchlist (compression building)
    if watchlist:
        text += f"──────────────────────────\n"
        text += f"<b>⚡ {len(watchlist)} IN COMPRESSION</b> (watch for breakout)\n"
        watch_names = ", ".join(w["symbol"] for w in watchlist[:8])
        text += f"<i>{watch_names}</i>\n"

    text += "\n<i>PE alerts will fire intraday 10:30–14:00 when conditions met.</i>"

    return _send(text)


# ─────────────────────────────────────────────
# MESSAGE 2: INTRADAY PE ALERT
# ─────────────────────────────────────────────

def send_pe_alert(pe_data: dict) -> bool:
    """
    Send an intraday PE buy alert.
    pe_data must contain all alert fields from intraday_pe_runner.

    Deduplicated: one alert per (symbol, PE_ALERT) per day.
    """
    symbol = pe_data["symbol"]

    # ── Deduplication gate ──
    if _already_alerted(symbol, "PE_ALERT"):
        logger.info("[Telegram] %s PE alert already sent today — skipped", symbol)
        return False

    strike    = pe_data.get("pe_strike", 0)
    expiry    = pe_data.get("expiry", "—")
    premium   = pe_data.get("approx_premium", 0)
    target    = pe_data.get("target_premium", 0)
    sl_und    = pe_data.get("stop_loss_underlying", 0)
    und_price = pe_data.get("underlying_price", 0)
    ema_20    = pe_data.get("ema_20", 0)
    vix       = pe_data.get("vix", 0)
    pcr       = pe_data.get("pcr", 0)
    oi_chg    = pe_data.get("oi_change_pct", 0)
    iv_pct    = pe_data.get("iv_percentile", 0)
    dte       = pe_data.get("days_to_expiry", 0)
    theta_ok  = "✅ OK" if dte > 10 else "⚠️ WARN"
    ai_text   = pe_data.get("ai_summary", "")
    ai_conf   = pe_data.get("ai_confidence", "MEDIUM")
    conds_met = pe_data.get("conditions_met", 0)
    sector    = pe_data.get("sector", "")
    trigger_t = pe_data.get("trigger_time", "—")

    conf_emoji = {"HIGH": "🟢", "MEDIUM": "🟡", "LOW": "🔴", "VERY_LOW": "🔴"}.get(ai_conf, "⚪")

    text = f"""🔴 <b>PE ALERT — {symbol} | {trigger_time(trigger_t)}</b>
<i>{sector}</i>

📉 ₹{und_price:.2f} broke 20-EMA (₹{ema_20:.2f})

<b>🎯 TRADE</b>
  Strike  : {strike:.0f} PE | Expiry: {expiry}
  Premium : ₹{premium:.1f}  →  Target: ₹{target:.1f} (+{config.TARGET_OPTION_GAIN_PCT:.0f}%)
  Und SL  : ₹{sl_und:.2f}   | DTE: {dte}d {theta_ok}

<b>📊 CONDITIONS ({conds_met}/7)</b>
  VIX  : {vix:.1f}  {"✅" if config.PE_VIX_MIN <= vix <= config.PE_VIX_MAX else "⚠️"}
  PCR  : {pcr:.2f}  {"✅" if pcr < config.PE_PCR_MAX else "⚠️"}
  PE OI: {oi_chg:+.1f}%  {"✅" if oi_chg >= config.PE_OI_CHANGE_MIN_PCT else "⚠️"}
  IV % : {iv_pct:.0f}th  {"✅" if iv_pct < config.PE_IV_PERCENTILE_MAX else "⚠️"}"""

    if ai_text:
        text += f"\n\n<b>🤖 CLAUDE</b>\n<i>{ai_text[:220]}</i>  {conf_emoji}"

    text += "\n\n<i>⚠️ Not investment advice. Verify before trading.</i>"

    success = _send(text)
    if success:
        _mark_alerted(symbol, "PE_ALERT")
        logger.info("[Telegram] PE alert sent for %s", symbol)
    return success


def trigger_time(t: str) -> str:
    """Format trigger time string for readability."""
    try:
        from datetime import datetime
        dt = datetime.fromisoformat(t)
        return dt.strftime("%I:%M %p IST")
    except Exception:
        return str(t)


# ─────────────────────────────────────────────
# UTILITY: Get Chat ID (run once during setup)
# ─────────────────────────────────────────────

def get_chat_id() -> str | None:
    """
    Helper to find your Telegram chat ID.
    Send any message to your bot first, then run this.
    """
    if not config.TELEGRAM_BOT_TOKEN:
        print("TELEGRAM_BOT_TOKEN not set — cannot get chat ID")
        return None
    url = TELEGRAM_API.format(token=config.TELEGRAM_BOT_TOKEN, method="getUpdates")
    r = requests.get(url, timeout=10)
    if r.status_code == 200:
        data = r.json()
        results = data.get("result", [])
        if results:
            chat_id = str(results[-1]["message"]["chat"]["id"])
            print(f"✅ Your chat ID: {chat_id}")
            print(f"   Set: export TELEGRAM_CHAT_ID='{chat_id}'")
            return chat_id
        else:
            print("No messages found. Send any message to your bot first.")
    return None


def send_system_message(text: str) -> bool:
    """Send a plain system/debug message."""
    return _send(f"ℹ️ <b>System</b>\n{text}")


if __name__ == "__main__":
    """
    Standalone test: posts directly to the configured TELEGRAM_CHAT_ID.
    
    Before running:
      1. Open Telegram → your channel/group → Settings → Administrators
      2. Add your bot (@Itraderrb_bot) as administrator
      3. Ensure 'Post Messages' permission is ON
      4. Then run: python3 telegram_notifier.py
    """
    import sys

    print("=" * 50)
    print(f"  Bot    : @{config.TELEGRAM_BOT_TOKEN.split(':')[0]}")
    print(f"  ChatID : {config.TELEGRAM_CHAT_ID}")
    print(f"  Enabled: {config.TELEGRAM_ENABLED}")
    print("=" * 50)
    print()

    # First verify the bot is valid
    r = requests.get(
        f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/getMe",
        timeout=10
    )
    bot = r.json().get("result", {})
    if not bot:
        print("❌ Bot token invalid. Check TELEGRAM_BOT_TOKEN in config.py")
        sys.exit(1)
    print(f"✅ Bot verified: @{bot.get('username')} ({bot.get('first_name')})")
    print()

    # Try to post directly
    print(f"Posting test message to chat {config.TELEGRAM_CHAT_ID} ...")
    ok = send_system_message(
        "✅ <b>Trading System Connected!</b>\n\n"
        "📋 EOD scan runs at: 9:30 PM IST\n"
        "🔴 PE alerts active: 10:15 AM – 2:00 PM IST\n\n"
        "<i>Nifty 200 scanner is online.</i>"
    )

    if ok:
        print("✅ SUCCESS — test message sent to your channel!")
        print("   The trading system is ready to send alerts.")
    else:
        print()
        print("❌ FAILED — most likely the bot is not an admin in your channel.")
        print()
        print("Fix in 3 steps:")
        print("  1. Open Telegram → go to your channel")
        print("  2. Channel Settings → Administrators → Add Admin")
        print(f"  3. Search for your bot and add it with 'Post Messages' permission")
        print()
        print("Then run this script again.")
        sys.exit(1)

