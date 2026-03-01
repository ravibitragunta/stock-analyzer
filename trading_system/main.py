"""
main.py — Entry Point for the Nifty 200 Swing Trading Signal Pipeline.

Run modes:
  python main.py                          → EOD mode (default)
  python main.py --mode eod               → EOD mode (explicit)
  python main.py --mode intraday          → Intraday WebSocket mode
  python main.py --mode backtest          → Historical backtest
  python main.py --mode backtest --start 2024-01-01 --end 2024-12-31

First run:
  - Initialises SQLite database
  - Downloads NSE instruments file
  - Populates Nifty 200 stock universe
  - Fetches 1 year of historical OHLCV data (takes ~10–15 minutes)

Subsequent runs (EOD mode):
  - Incremental update only (< 2 minutes)
  - Runs full CEA signal pipeline
  - Optionally calls AI sentiment layer
  - Generates signals.json and signals_report.html
"""

import argparse
import logging
import sys
from datetime import date, datetime

# ── configure logging before any imports ──
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)-20s  %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("trading_system.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

import config
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


# ─────────────────────────────────────────────
# FIRST-RUN SETUP
# ─────────────────────────────────────────────

def setup_first_run():
    """
    Initialise everything needed on first run.
    Safe to call on every startup — all operations are idempotent.
    """
    logger.info("=== FIRST RUN SETUP ===")

    # 1. Initialise DB schema
    db.init_db()

    # 2. Refresh instruments file if stale
    fetcher.refresh_instruments_if_stale()

    # 3. Populate stock universe from instruments file
    stocks_list.populate_stocks_from_instruments()

    # 4. Fetch historical data for all stocks
    stocks = db.get_all_stocks()
    fetcher.fetch_all_historical(stocks)

    logger.info("=== SETUP COMPLETE ===")


def is_first_run() -> bool:
    """Check if this is a first run (no OHLCV data in DB at all)."""
    stocks = db.get_all_stocks()
    if not stocks:
        return True
    # Check if the first stock has any data
    sample = stocks[0]["symbol"]
    return db.get_latest_date(sample) is None


# ─────────────────────────────────────────────
# EOD PIPELINE
# ─────────────────────────────────────────────

def run_eod():
    """
    Main EOD pipeline. Run at 3:45 PM IST every evening.

    Flow:
      1. Refresh instruments (if stale)
      2. Incremental OHLCV update
      3. Market Gate check
      4. Universe filter
      5. Compression scanner
      6. Expansion validator
      7. Acceptance checker on existing signals
      8. Killer module (invalidation)
      9. AI sentiment scoring (if enabled)
     10. Options module for SHORT signals
     11. Report generation
    """
    today = date.today().isoformat()
    logger.info("==============================")
    logger.info("  EOD RUN: %s", today)
    logger.info("==============================")

    # ── Step 1: Refresh instruments file ──
    fetcher.refresh_instruments_if_stale()

    # ── Step 2: Incremental data update ──
    stocks = db.get_all_stocks()
    logger.info("Incremental update for %d stocks …", len(stocks))
    fetcher.fetch_incremental_update(stocks)

    # ── Step 3: Market Gate ──
    gate = gate_module.run_market_gate()
    long_allowed  = bool(gate.get("long_allowed"))
    short_allowed = bool(gate.get("short_allowed"))

    if not long_allowed and not short_allowed:
        logger.warning("MARKET GATE CLOSED — NO TRADES TODAY")
        reporter.run_reporter(gate, [], [], [], [], mode="eod")
        print("\n🚫 MARKET GATE CLOSED — NO TRADES TODAY\n")
        return

    # ── Step 4: Universe filter ──
    filtered = universe_filter.filter_universe(stocks, min_days_data=60)
    logger.info("Universe: %d/%d stocks passed filter", len(filtered), len(stocks))

    # ── Step 5: Compression scanner ──
    compressed = scanner.scan_for_compression(filtered)

    # ── Step 6 & 7: Expansion + Acceptance on compressed + existing signals ──
    active_signals = db.get_active_signals(as_of_date=today)
    val_result = validator.run_validation(compressed, active_signals)

    # Save new expansion signals
    new_signals_db = []
    for sig in val_result["new_expansions"]:
        if long_allowed  and sig["signal_type"] == "LONG":
            sig_id = db.insert_signal(sig)
            sig["id"] = sig_id
            new_signals_db.append(sig)
        elif short_allowed and sig["signal_type"] == "SHORT":
            sig_id = db.insert_signal(sig)
            sig["id"] = sig_id
            new_signals_db.append(sig)

    # Update newly accepted signals
    for sig_id in val_result["newly_accepted"]:
        db.update_signal_state(sig_id, "ACCEPTED")
        db.log_signal_outcome(sig_id, "ACTIVE", notes="Acceptance confirmed")

    # ── Step 8: Killer module ──
    all_active = db.get_active_signals(as_of_date=today)
    killer.run_killer(all_active)

    # ── Fetch final signal state for output ──
    todays_signals = db.get_signals_for_date(today)
    # Also include recently accepted older signals
    accepted_older = [s for s in db.get_signals_by_state("ACCEPTED") if s["date"] < today]
    expansion_older = [s for s in db.get_signals_by_state("EXPANSION_FIRED") if s["date"] < today]

    all_output_signals = todays_signals + accepted_older + expansion_older
    # Deduplicate by signal ID
    seen = set()
    unique_signals = []
    for s in all_output_signals:
        if s["id"] not in seen:
            seen.add(s["id"])
            unique_signals.append(s)

    long_signals   = [s for s in unique_signals if s["signal_type"] == "LONG"  and s["state"] not in ("INVALIDATED", "TARGET_HIT")]
    short_signals  = [s for s in unique_signals if s["signal_type"] == "SHORT" and s["state"] not in ("INVALIDATED", "TARGET_HIT")]
    watchlist      = [_compression_to_signal(c, today) for c in compressed
                      if not db.symbol_has_active_signal(c["symbol"])]

    logger.info("Output: %d longs | %d shorts | %d watchlist",
                len(long_signals), len(short_signals), len(watchlist))

    # ── Step 9: AI Sentiment ──
    if config.AI_SENTIMENT_ENABLED:
        all_for_ai = long_signals + short_signals
        all_for_ai = ai_orchestrator.score_all_signals(all_for_ai)
        # Split back
        long_signals  = [s for s in all_for_ai if s["signal_type"] == "LONG"]
        short_signals = [s for s in all_for_ai if s["signal_type"] == "SHORT"]
        # Save AI scores to DB
        for s in all_for_ai:
            if s.get("ai_score") is not None:
                db.update_signal_ai(s["id"], s["ai_score"], s["ai_confidence"], s.get("ai_summary"))

    # ── Step 10: Options Module ──
    pe_signals = options_module.process_short_signals(short_signals)

    # ── Step 11: Report ──
    reporter.run_reporter(gate, long_signals, short_signals, watchlist, pe_signals, mode="eod")

    # ── Console summary ──
    _print_summary(gate, long_signals, short_signals, pe_signals, watchlist)


# ─────────────────────────────────────────────
# INTRADAY PIPELINE (WebSocket mode)
# ─────────────────────────────────────────────

def run_intraday():
    """
    Intraday mode: subscribe to WebSocket for live 15-min candles.
    Re-runs scanner/validator/killer on every candle close.
    Refreshes HTML report automatically after each signal state change.
    """
    logger.info("=== INTRADAY MODE STARTED ===")

    # Run a quick EOD pipeline first to get the day's context
    fetcher.refresh_instruments_if_stale()
    stocks   = db.get_all_stocks()
    gate     = gate_module.run_market_gate()
    filtered = universe_filter.filter_universe(stocks, min_days_data=60)
    compressed = scanner.scan_for_compression(filtered)

    def on_candle_close(symbol: str, candle: dict):
        """Called by WebSocket handler on each 15-min candle close."""
        active = db.get_active_signals()
        killer.run_killer(active)
        # Re-scan this stock only
        stock = db.get_stock(symbol)
        if not stock:
            return
        ohlcv = db.get_ohlcv(symbol, days=180) + [candle]
        comp = scanner.detect_compression(symbol, ohlcv)
        if comp:
            sig = validator.check_expansion(symbol, ohlcv, comp)
            if sig and not db.symbol_has_active_signal(symbol):
                sig_id = db.insert_signal(sig)
                logger.info("[INTRADAY] New expansion signal: %s %s", sig["signal_type"], symbol)

        # Refresh report
        longs   = db.get_signals_by_state("LONG")
        shorts  = db.get_signals_by_state("SHORT")
        pe_sigs = options_module.process_short_signals(shorts)
        reporter.run_reporter(gate, longs, shorts, [], pe_sigs, mode="intraday")

    from websocket_handler import run_intraday as ws_run
    ws_run(filtered, on_candle_close)


# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def _compression_to_signal(comp: dict, today: str) -> dict:
    """Convert a compression result dict to a minimal watchlist signal dict."""
    return {
        "symbol":           comp["symbol"],
        "date":             today,
        "signal_type":      "WATCHLIST",
        "state":            "COMPRESSION_DETECTED",
        "compression_days": comp["compression_days"],
        "band_pct":         comp["band_pct"],
        "latest_close":     comp.get("latest_close", 0),
        "avg_value_cr":     comp.get("avg_value_cr", 0),
        "sector":           comp.get("sector", "—"),
    }


def _print_summary(gate: dict, longs: list, shorts: list, pe_sigs: list, watchlist: list):
    """Print a quick console summary after EOD run."""
    print("\n" + "=" * 52)
    print(f"  📊 NIFTY 200 SIGNAL REPORT — {date.today().isoformat()}")
    print("=" * 52)
    print(f"  Market Regime : {gate.get('market_regime','—')}")
    print(f"  VIX           : {gate.get('vix_level','—')}")
    print(f"  Longs gate    : {'✅ OPEN' if gate.get('long_allowed') else '❌ CLOSED'}")
    print(f"  Shorts gate   : {'✅ OPEN' if gate.get('short_allowed') else '❌ CLOSED'}")
    print(f"\n  🟢 Long signals    : {len(longs)}")
    print(f"  🔴 Short signals   : {len(shorts)} ({len(pe_sigs)} with PE data)")
    print(f"  ⚡ Watchlist       : {len(watchlist)} stocks in compression")
    print(f"\n  📄 Report → trading_system/signals_report.html")
    print(f"  📋 JSON   → trading_system/signals.json")
    print("=" * 52 + "\n")


# ─────────────────────────────────────────────
# CLI ENTRY POINT
# ─────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Nifty 200 Swing Trading Signal Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py                              # EOD run (default)
  python main.py --mode eod                   # EOD run (explicit)
  python main.py --mode intraday              # Live WebSocket session
  python main.py --mode backtest              # Backtest (uses config dates)
  python main.py --mode backtest --start 2024-01-01 --end 2024-12-31
        """,
    )
    parser.add_argument("--mode", choices=["eod", "intraday", "backtest"], default="eod")
    parser.add_argument("--start", help="Backtest start date (YYYY-MM-DD)")
    parser.add_argument("--end",   help="Backtest end date (YYYY-MM-DD)")
    parser.add_argument("--force-historical", action="store_true",
                        help="Force re-fetch of all historical data")
    args = parser.parse_args()

    logger.info("Mode: %s", args.mode.upper())

    # ── Auto-load Upstox token from auth.py cache ──
    try:
        from auth import get_valid_token
        cached_token = get_valid_token()
        if cached_token and not config.UPSTOX_ACCESS_TOKEN:
            config.UPSTOX_ACCESS_TOKEN = cached_token
            import data_fetcher as _df
            _df._session = None   # Force session rebuild with new token
            logger.info("Loaded today's cached Upstox token from auth.py")
    except Exception:
        pass   # Auth module is optional; errors are non-fatal

    # ── Check token is set ──
    if not config.UPSTOX_ACCESS_TOKEN:
        print("\n❌ UPSTOX_ACCESS_TOKEN is not set.")
        print("   Run this first to authenticate:")
        print("   python auth.py\n")
        if args.mode != "backtest":
            sys.exit(1)

    # ── DB init (always) ──
    db.init_db()

    # ── First run or forced refresh ──
    if is_first_run() or args.force_historical:
        logger.info("First run detected — running full setup …")
        setup_first_run()

    if args.mode == "eod":
        run_eod()

    elif args.mode == "intraday":
        run_intraday()

    elif args.mode == "backtest":
        from backtest import run_backtest
        run_backtest(start_date=args.start, end_date=args.end)


if __name__ == "__main__":
    main()
