"""
scheduler.py — Daily task scheduler for the trading system.

Two jobs:
  1. EOD Scanner: runs at 9:30 PM IST every weekday
  2. Intraday PE Engine: runs 10:15 AM – 2:15 PM IST every weekday

Usage:
  python scheduler.py          # runs forever as a daemon
  python scheduler.py --once   # run EOD job once immediately (for testing)

Note: For production, consider using cron instead:
  30 21 * * 1-5  /usr/bin/python3 /path/to/trading_system/main.py
  15 10 * * 1-5  /usr/bin/python3 /path/to/trading_system/main.py --mode intraday-pe
"""

import argparse
import logging
import sys
import time
from datetime import date, datetime, timedelta

import config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("scheduler.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# TIME HELPERS
# ─────────────────────────────────────────────

def _seconds_until(time_str: str) -> float:
    """Seconds until the next occurrence of HH:MM IST (today if future, else tomorrow)."""
    h, m   = map(int, time_str.split(":"))
    now    = datetime.now()
    target = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


def _is_trading_day() -> bool:
    """True if today is a weekday and not an NSE holiday."""
    today = date.today()
    if today.weekday() >= 5:   # Saturday=5, Sunday=6
        return False
    return today.isoformat() not in config.NSE_HOLIDAYS


def _wait_until(time_str: str, label: str):
    """Sleep until the next occurrence of time_str."""
    secs = _seconds_until(time_str)
    wake = datetime.now() + timedelta(seconds=secs)
    logger.info("⏰ Next %s at %s (in %.0f min)", label, wake.strftime("%H:%M"), secs / 60)
    time.sleep(secs)


# ─────────────────────────────────────────────
# JOB: EOD SCAN (9:30 PM IST)
# ─────────────────────────────────────────────

def run_eod_job():
    """Run the full EOD pipeline and send Telegram watchlist."""
    logger.info("=" * 50)
    logger.info("  EOD JOB STARTING — %s", datetime.now().strftime("%d-%b-%Y %H:%M"))
    logger.info("=" * 50)

    try:
        # Import here to avoid circular at module load
        import database as db
        import data_fetcher as fetcher
        import stocks_list
        import market_gate as gate_module
        import universe_filter
        import scanner
        import validator
        import killer
        import options_module
        import reporter
        from sentiment import ai_orchestrator
        from telegram_notifier import send_eod_watchlist, send_system_message

        today = date.today().isoformat()

        # Step 1: Refresh + incremental update
        fetcher.refresh_instruments_if_stale()
        stocks = db.get_all_stocks()
        fetcher.fetch_incremental_update(stocks)

        # Step 2: Market Gate
        gate = gate_module.run_market_gate()

        # Step 3: Universe filter + compression scan
        filtered   = universe_filter.filter_universe(stocks)
        compressed = scanner.scan_for_compression(filtered)

        # Step 4: Expansion + acceptance
        active = db.get_active_signals(as_of_date=today)
        val    = validator.run_validation(compressed, active)

        # Step 5: Save new signals (SHORT only — this run is for PE watchlist)
        for sig in val["new_expansions"]:
            if sig["signal_type"] == "SHORT" and gate.get("short_allowed"):
                db.insert_signal(sig)
        for sig_id in val["newly_accepted"]:
            db.update_signal_state(sig_id, "ACCEPTED")

        # Step 6: Killer
        killer.run_killer(db.get_active_signals(as_of_date=today))

        # Step 7: AI scoring (batch — cheaper)
        short_signals = db.get_signals_by_state("EXPANSION_FIRED") + db.get_signals_by_state("ACCEPTED")
        short_signals = [s for s in short_signals if s["signal_type"] == "SHORT"]

        if config.AI_SENTIMENT_ENABLED and short_signals:
            short_signals = ai_orchestrator.score_all_signals(short_signals)

        # Step 8: Watchlist (compression candidates not yet fired)
        watchlist = [
            {
                "symbol":           c["symbol"],
                "sector":           c.get("sector", ""),
                "compression_days": c["compression_days"],
                "band_pct":         c["band_pct"],
                "latest_close":     c.get("latest_close", 0),
                "avg_value_cr":     c.get("avg_value_cr", 0),
            }
            for c in compressed if not db.symbol_has_active_signal(c["symbol"])
        ]

        # Step 9: HTML + JSON report
        pe_signals = options_module.process_short_signals(short_signals)
        long_sigs  = db.get_signals_by_state("ACCEPTED") + db.get_signals_by_state("EXPANSION_FIRED")
        long_sigs  = [s for s in long_sigs if s["signal_type"] == "LONG"]
        reporter.run_reporter(gate, long_sigs, short_signals, watchlist, pe_signals)

        # Step 10: Telegram — nightly SHORT watchlist
        send_eod_watchlist(gate, pe_signals, watchlist)

        # Save watchlist to DB for PE runner to use tomorrow
        db.save_pe_watchlist([s["symbol"] for s in pe_signals] + [w["symbol"] for w in watchlist[:5]])

        logger.info("EOD job complete ✅")

    except Exception as e:
        logger.exception("EOD job FAILED: %s", e)
        try:
            from telegram_notifier import send_system_message
            send_system_message(f"⚠️ EOD job failed: {e}")
        except Exception:
            pass


# ─────────────────────────────────────────────
# JOB: INTRADAY PE ENGINE (10:15 AM – 2:15 PM)
# ─────────────────────────────────────────────

def run_pe_job():
    """Start the intraday PE alert engine using last night's watchlist."""
    logger.info("=" * 50)
    logger.info("  PE ENGINE STARTING — %s", datetime.now().strftime("%d-%b-%Y %H:%M"))
    logger.info("=" * 50)

    try:
        import database as db
        from intraday_pe_runner import run_pe_alert_engine

        # Load the watchlist saved by last night's EOD job
        symbols = db.get_pe_watchlist()
        if not symbols:
            logger.info("PE watchlist empty — nothing to monitor today")
            return

        logger.info("Monitoring %d symbols for PE alerts: %s", len(symbols), ", ".join(symbols))
        run_pe_alert_engine(symbols)

    except Exception as e:
        logger.exception("PE engine FAILED: %s", e)
        try:
            from telegram_notifier import send_system_message
            send_system_message(f"⚠️ PE engine error: {e}")
        except Exception:
            pass


# ─────────────────────────────────────────────
# MAIN LOOP
# ─────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Trading System Scheduler")
    parser.add_argument("--once", action="store_true",
                        help="Run EOD job once immediately (for testing)")
    args = parser.parse_args()

    if args.once:
        logger.info("--once flag: running EOD job now")
        run_eod_job()
        return

    logger.info("Scheduler started. Watching for trading days …")

    while True:
        if not _is_trading_day():
            logger.info("Not a trading day — sleeping 1 hour")
            time.sleep(3600)
            continue

        now = datetime.now()
        now_str = now.strftime("%H:%M")

        # ── Intraday PE run: 10:15 AM ──
        if now_str < config.INTRADAY_START_IST:
            _wait_until("10:15", "PE Engine")
            if _is_trading_day():
                run_pe_job()

        # ── EOD run: 9:30 PM ──
        elif now_str < config.EOD_RUN_TIME_IST:
            _wait_until(config.EOD_RUN_TIME_IST, "EOD Scanner")
            if _is_trading_day():
                run_eod_job()

        else:
            # Past 9:30 PM — jump to next trading day's morning
            logger.info("Past EOD run time — sleeping until next trading day 10:15 AM")
            _wait_until("10:15", "next day PE Engine")
            if _is_trading_day():
                run_pe_job()


if __name__ == "__main__":
    main()
