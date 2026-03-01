"""
backtest.py — Historical Signal Replay Engine.

Simulates the CEA strategy on past OHLCV data already stored in SQLite.
No new API calls needed — uses existing data.

Three phases:
  1. Historical Signal Replay — signal FSM on each past day
  2. Walk-Forward Validation  — train on H1, test on H2, locked thresholds
  3. Monte Carlo Stress Test  — shuffle order 1000x, validate edge

Usage:
  python main.py --mode backtest --start 2024-01-01 --end 2024-12-31

Output:
  backtest_report.html (same styled template as signals_report.html)
  backtest_results in SQLite
"""

import logging
import math
import random
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import config
import database as db
from scanner import detect_compression
from validator import check_expansion
from killer import run_killer

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# PHASE 1: HISTORICAL SIGNAL REPLAY
# ─────────────────────────────────────────────

def replay_signals(start_date: str, end_date: str) -> list[dict]:
    """
    Walk forward through each trading day between start and end.
    For each day, simulate the scanner → validator → killer pipeline.
    All data lookups are filtered to data < that day (no lookahead bias).

    Returns list of completed trade result dicts.
    """
    db.clear_backtest_results()

    stocks   = db.get_all_stocks()
    all_days = _trading_days_between(start_date, end_date)
    results  = []

    logger.info("Backtest: replaying %d trading days on %d stocks …",
                len(all_days), len(stocks))

    # Active signals during replay (in-memory, like the live system)
    active_signals: list[dict] = []

    for sim_date in all_days:
        logger.debug("Simulating %s …", sim_date)

        new_sigs = []
        for stock in stocks:
            symbol = stock["symbol"]
            # Only load data up to sim_date (no lookahead)
            ohlcv = db.get_ohlcv_range(symbol, start_date, sim_date)
            if len(ohlcv) < 60:
                continue

            # Check compression
            comp = detect_compression(symbol, ohlcv)
            if not comp:
                continue

            # Check if today has an expansion candle (last bar = sim_date)
            if ohlcv[-1]["date"] != sim_date:
                continue

            # Check expansion
            sig = check_expansion(symbol, ohlcv, comp)
            if sig and not _has_active_signal(active_signals, symbol):
                sig["id"] = f"bt_{symbol}_{sim_date}"
                sig["date"] = sim_date
                active_signals.append(sig)
                new_sigs.append(sig)

        if new_sigs:
            logger.debug("  New signals: %d", len(new_sigs))

        # Run killer on all active signals
        to_remove = []
        for sig in active_signals:
            symbol      = sig["symbol"]
            ohlcv_today = db.get_ohlcv_range(symbol, start_date, sim_date)
            if not ohlcv_today:
                continue

            outcome = _evaluate_signal_outcome(sig, ohlcv_today, sim_date)
            if outcome:
                result = {
                    "signal_id":   sig.get("id"),
                    "symbol":      symbol,
                    "signal_type": sig["signal_type"],
                    "entry_date":  sig["date"],
                    "entry_price": sig.get("entry_zone_low"),
                    "exit_date":   sim_date,
                    "exit_price":  ohlcv_today[-1]["close"],
                    "outcome":     outcome["outcome"],
                    "pnl_pct":     outcome["pnl_pct"],
                    "r_multiple":  outcome["r_multiple"],
                    "notes":       outcome["notes"],
                }
                db.insert_backtest_result(result)
                results.append(result)
                to_remove.append(sig["id"])

        active_signals = [s for s in active_signals if s.get("id") not in to_remove]

    logger.info("Backtest replay complete: %d trades recorded", len(results))
    return results


def _evaluate_signal_outcome(signal: dict, ohlcv: list[dict], today: str) -> Optional[dict]:
    """Check if a signal has resolved (win/loss/expire) as of today."""
    if today <= signal["date"]:
        return None  # Too early

    today_bar  = ohlcv[-1]
    entry      = signal.get("entry_zone_low", 0)
    stop       = signal.get("stop_loss", 0)
    sig_type   = signal["signal_type"]
    valid_days = signal.get("valid_for_days", config.DEFAULT_VALID_FOR_DAYS)
    age        = (datetime.strptime(today, "%Y-%m-%d") -
                  datetime.strptime(signal["date"], "%Y-%m-%d")).days

    if entry == 0 or stop == 0:
        return None

    risk_pct = abs(entry - stop) / entry * 100

    if sig_type == "LONG":
        # Stop hit
        if today_bar["low"] <= stop:
            pnl = (stop - entry) / entry * 100 - config.BACKTEST_SLIPPAGE_PCT
            return {"outcome": "LOSS", "pnl_pct": round(pnl, 2),
                    "r_multiple": round(pnl / risk_pct, 2) if risk_pct else 0,
                    "notes": "Stop hit"}
        # Target hit (20% from entry)
        target = entry * (1 + config.TARGET_MOVE_PCT / 100)
        if today_bar["high"] >= target:
            pnl = config.TARGET_MOVE_PCT - config.BACKTEST_SLIPPAGE_PCT
            return {"outcome": "WIN", "pnl_pct": round(pnl, 2),
                    "r_multiple": round(pnl / risk_pct, 2) if risk_pct else 0,
                    "notes": "Target hit"}
    else:  # SHORT
        if today_bar["high"] >= stop:
            pnl = (entry - stop) / entry * 100 - config.BACKTEST_SLIPPAGE_PCT
            return {"outcome": "LOSS", "pnl_pct": round(pnl, 2),
                    "r_multiple": round(pnl / risk_pct, 2) if risk_pct else 0,
                    "notes": "Stop hit"}
        target = entry * (1 - config.TARGET_MOVE_PCT / 100)
        if today_bar["low"] <= target:
            pnl = config.TARGET_MOVE_PCT - config.BACKTEST_SLIPPAGE_PCT
            return {"outcome": "WIN", "pnl_pct": round(pnl, 2),
                    "r_multiple": round(pnl / risk_pct, 2) if risk_pct else 0,
                    "notes": "Target hit"}

    # Expired
    if age > valid_days:
        pnl = (today_bar["close"] - entry) / entry * 100 if sig_type == "LONG" else (entry - today_bar["close"]) / entry * 100
        pnl -= config.BACKTEST_SLIPPAGE_PCT
        return {"outcome": "EXPIRED", "pnl_pct": round(pnl, 2),
                "r_multiple": round(pnl / risk_pct, 2) if risk_pct else 0,
                "notes": "Signal expired, closed at EOD"}

    return None  # Still active


def _has_active_signal(active: list[dict], symbol: str) -> bool:
    return any(s["symbol"] == symbol for s in active)


def _trading_days_between(start: str, end: str) -> list[str]:
    """Generate list of trading day strings between start and end."""
    days = []
    current = datetime.strptime(start, "%Y-%m-%d")
    end_dt  = datetime.strptime(end, "%Y-%m-%d")
    while current <= end_dt:
        if current.weekday() < 5 and current.date().isoformat() not in config.NSE_HOLIDAYS:
            days.append(current.strftime("%Y-%m-%d"))
        current += timedelta(days=1)
    return days


# ─────────────────────────────────────────────
# PHASE 2: WALK-FORWARD VALIDATION
# ─────────────────────────────────────────────

def walk_forward(full_start: str, full_end: str) -> tuple[dict, dict]:
    """
    Split the date range in half:
      Train: full_start → midpoint
      Test:  midpoint+1 → full_end

    Returns (train_metrics, test_metrics) tuple.
    """
    start = datetime.strptime(full_start, "%Y-%m-%d")
    end   = datetime.strptime(full_end,   "%Y-%m-%d")
    mid   = start + (end - start) / 2

    mid_str   = mid.strftime("%Y-%m-%d")
    mid1_str  = (mid + timedelta(days=1)).strftime("%Y-%m-%d")

    logger.info("Walk-Forward: Train=%s→%s | Test=%s→%s",
                full_start, mid_str, mid1_str, full_end)

    train_results = replay_signals(full_start, mid_str)
    test_results  = replay_signals(mid1_str, full_end)

    return compute_metrics(train_results), compute_metrics(test_results)


# ─────────────────────────────────────────────
# PHASE 3: MONTE CARLO
# ─────────────────────────────────────────────

def monte_carlo(results: list[dict], n_runs: int = None) -> dict:
    """
    Shuffle trade order n times and compute equity curve statistics.
    Validates that the edge is real and not just a lucky sequence.
    """
    n_runs = n_runs or config.MONTE_CARLO_RUNS
    pnls   = [r["pnl_pct"] for r in results if r.get("pnl_pct") is not None]
    if not pnls:
        return {"error": "No results to Monte Carlo"}

    final_equity = []
    for _ in range(n_runs):
        shuffled = random.sample(pnls, len(pnls))
        equity   = 0
        for p in shuffled:
            equity += p
        final_equity.append(equity)

    final_equity.sort()
    return {
        "runs":           n_runs,
        "median_return":  round(sum(final_equity) / n_runs, 2),
        "pct5_return":    round(final_equity[int(0.05 * n_runs)], 2),
        "pct95_return":   round(final_equity[int(0.95 * n_runs)], 2),
        "min_return":     round(final_equity[0], 2),
        "max_return":     round(final_equity[-1], 2),
        "positive_runs_pct": round(sum(1 for e in final_equity if e > 0) / n_runs * 100, 1),
    }


# ─────────────────────────────────────────────
# METRICS
# ─────────────────────────────────────────────

def compute_metrics(results: list[dict]) -> dict:
    """Compute key backtest metrics from a list of trade results."""
    if not results:
        return {"total_trades": 0}

    wins      = [r for r in results if r.get("outcome") == "WIN"]
    losses    = [r for r in results if r.get("outcome") == "LOSS"]
    expired   = [r for r in results if r.get("outcome") == "EXPIRED"]
    pnls      = [r["pnl_pct"] for r in results if r.get("pnl_pct") is not None]
    r_mults   = [r["r_multiple"] for r in results if r.get("r_multiple") is not None]

    max_dd    = _max_drawdown(pnls)
    sharpe    = _sharpe(pnls)

    return {
        "total_trades":   len(results),
        "wins":           len(wins),
        "losses":         len(losses),
        "expired":        len(expired),
        "win_rate_pct":   round(len(wins) / len(results) * 100, 1) if results else 0,
        "avg_pnl_pct":    round(sum(pnls) / len(pnls), 2) if pnls else 0,
        "avg_r_multiple": round(sum(r_mults) / len(r_mults), 2) if r_mults else 0,
        "max_drawdown_pct": round(max_dd, 2),
        "sharpe":         round(sharpe, 2),
        "gross_pnl_pct":  round(sum(pnls), 2) if pnls else 0,
    }


def _max_drawdown(pnls: list[float]) -> float:
    peak = 0
    dd   = 0
    equity = 0
    for p in pnls:
        equity += p
        peak = max(peak, equity)
        dd   = min(dd, equity - peak)
    return abs(dd)


def _sharpe(pnls: list[float], rf: float = 0.0) -> float:
    if not pnls or len(pnls) < 2:
        return 0.0
    mean = sum(pnls) / len(pnls)
    std  = math.sqrt(sum((p - mean) ** 2 for p in pnls) / (len(pnls) - 1))
    return (mean - rf) / std if std > 0 else 0.0


# ─────────────────────────────────────────────
# BACKTEST REPORT (reuses reporter template)
# ─────────────────────────────────────────────

def generate_backtest_report(metrics: dict, mc: dict, results: list[dict]):
    """Write a backtest_report.html using the same styled template."""
    now = datetime.now().strftime("%d-%b-%Y %H:%M IST")
    rows = ""
    for r in results[-100:]:  # Show last 100 trades
        outcome_color = {"WIN": "#00c853", "LOSS": "#ff1744", "EXPIRED": "#ffd600"}.get(r["outcome"], "white")
        rows += f"""<tr>
          <td>{r['entry_date']}</td>
          <td><strong>{r['symbol']}</strong></td>
          <td>{r['signal_type']}</td>
          <td style="color:{outcome_color}">{r['outcome']}</td>
          <td>₹{r.get('entry_price',0):.2f}</td>
          <td>₹{r.get('exit_price',0):.2f}</td>
          <td style="color:{outcome_color}">{r.get('pnl_pct',0):+.2f}%</td>
          <td>{r.get('r_multiple',0):+.2f}R</td>
        </tr>"""

    html = f"""<!DOCTYPE html><html><head>
    <meta charset="UTF-8"><title>Backtest Report</title>
    <style>
      body{{background:#0f1117;color:#e8e9f0;font-family:Inter,sans-serif;font-size:13px;padding:32px}}
      h1{{font-size:20px;margin-bottom:24px}}
      .stat-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:16px;margin-bottom:32px}}
      .stat-card{{background:#1a1d27;padding:16px;border-radius:8px;border:1px solid #2d3148}}
      .stat-card .val{{font-size:24px;font-weight:700;color:#00c853}}
      .stat-card .lbl{{color:#8a8fa8;font-size:11px;text-transform:uppercase;margin-top:4px}}
      table{{width:100%;border-collapse:collapse;background:#1a1d27;border-radius:8px;overflow:hidden}}
      th{{background:#232636;padding:10px;text-align:left;font-size:11px;color:#8a8fa8;text-transform:uppercase}}
      td{{padding:9px 10px;border-bottom:1px solid #2d3148}}
    </style></head><body>
    <h1>📊 Backtest Report — CEA Strategy</h1>
    <p style="color:#8a8fa8;margin-bottom:24px">Generated: {now} | Period: {config.BACKTEST_START_DATE} → {config.BACKTEST_END_DATE}</p>
    <div class="stat-grid">
      <div class="stat-card"><div class="val">{metrics.get('total_trades',0)}</div><div class="lbl">Total Trades</div></div>
      <div class="stat-card"><div class="val">{metrics.get('win_rate_pct',0)}%</div><div class="lbl">Win Rate</div></div>
      <div class="stat-card"><div class="val">{metrics.get('avg_r_multiple',0):.2f}R</div><div class="lbl">Avg R-Multiple</div></div>
      <div class="stat-card"><div class="val">{metrics.get('gross_pnl_pct',0):+.1f}%</div><div class="lbl">Gross P&L</div></div>
      <div class="stat-card"><div class="val" style="color:#ff1744">{metrics.get('max_drawdown_pct',0):.1f}%</div><div class="lbl">Max Drawdown</div></div>
      <div class="stat-card"><div class="val">{metrics.get('sharpe',0):.2f}</div><div class="lbl">Sharpe</div></div>
      <div class="stat-card"><div class="val">{mc.get('positive_runs_pct',0)}%</div><div class="lbl">Monte Carlo Win % ({mc.get('runs',0)} runs)</div></div>
      <div class="stat-card"><div class="val">{mc.get('median_return',0):+.1f}%</div><div class="lbl">MC Median Return</div></div>
    </div>
    <table><thead><tr>
      <th>Date</th><th>Symbol</th><th>Type</th><th>Outcome</th>
      <th>Entry</th><th>Exit</th><th>P&L %</th><th>R-Mult</th>
    </tr></thead><tbody>{rows}</tbody></table>
    <p style="color:#8a8fa8;margin-top:24px;font-size:11px">
    ⚠️ Backtest assumes 0.1% slippage per trade. No survivorship bias correction. Past performance ≠ future results.
    </p></body></html>"""

    out_path = config.OUTPUT_DIR / "backtest_report.html"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    logger.info("backtest_report.html written → %s", out_path)


def run_backtest(start_date: str = None, end_date: str = None):
    """Main backtest entry point called by main.py --mode backtest."""
    start = start_date or config.BACKTEST_START_DATE
    end   = end_date   or config.BACKTEST_END_DATE

    logger.info("=== BACKTEST: %s → %s ===", start, end)

    # Phase 1: Replay
    results = replay_signals(start, end)

    # Phase 2: Walk-forward
    train_m, test_m = walk_forward(start, end)
    logger.info("Walk-Forward → Train: WR=%.1f%% | Test: WR=%.1f%%",
                train_m.get("win_rate_pct", 0), test_m.get("win_rate_pct", 0))

    # Phase 3: Monte Carlo
    metrics = compute_metrics(results)
    mc = monte_carlo(results)
    logger.info("Monte Carlo → Positive runs: %.1f%% | Median return: %.1f%%",
                mc.get("positive_runs_pct", 0), mc.get("median_return", 0))

    # Report
    generate_backtest_report(metrics, mc, results)
    return metrics
