"""
reporter.py — Generates signals.json and signals_report.html.

Two output files produced every evening:
  1. signals.json — machine-readable, full structure
  2. signals_report.html — styled human-readable HTML report

HTML features:
  - Market gate status banner (green/red/orange)
  - Long signals table: symbol, entry, stop, risk%, AI score
  - Short signals table: PE strike, expiry, premium, target, theta warning
  - Watchlist table: compression stocks, days, sector
  - Color coding: green (longs), red (shorts), yellow (watchlist)
  - Auto-refreshes every 15 min in intraday mode
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

import config

logger = logging.getLogger(__name__)


def generate_json(gate: dict, long_signals: list[dict], short_signals: list[dict],
                  watchlist: list[dict], pe_signals: list[dict],
                  backtest_summary: dict = None) -> dict:
    """Build the signals.json payload."""
    now = datetime.now()
    output = {
        "date":      now.strftime("%Y-%m-%d"),
        "run_time":  now.strftime("%H:%M") + " IST",
        "market_gate": {
            "nifty_close":       gate.get("nifty_close"),
            "nifty_20ema":       gate.get("nifty_20ema"),
            "nifty_above_20ema": bool(gate.get("nifty_above_20ema")),
            "vix_level":         gate.get("vix_level"),
            "pcr":               gate.get("pcr"),
            "advance_decline":   gate.get("advance_decline"),
            "long_allowed":      bool(gate.get("long_allowed")),
            "short_allowed":     bool(gate.get("short_allowed")),
            "market_regime":     gate.get("market_regime"),
            "global_macro_score": gate.get("global_macro_score"),
        },
        "long_signals":  long_signals,
        "short_signals": pe_signals,             # Enriched with options data
        "watchlist":     watchlist,
        "backtest_summary": backtest_summary or {},
    }
    out_path = config.OUTPUT_DIR / "signals.json"
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2, default=str)
    logger.info("signals.json written → %s", out_path)
    return output


def generate_html(gate: dict, long_signals: list[dict], short_signals: list[dict],
                  watchlist: list[dict], pe_signals: list[dict],
                  auto_refresh: bool = False) -> str:
    """Generate the styled HTML report and write to signals_report.html."""

    # ── Gate banner ──
    if gate.get("long_allowed") and gate.get("short_allowed"):
        banner_class = "gate-mixed"
        banner_icon  = "⚡"
        banner_title = "MARKET GATE MIXED — BOTH DIRECTIONS OPEN"
    elif gate.get("long_allowed"):
        banner_class = "gate-open"
        banner_icon  = "✅"
        banner_title = "MARKET GATE OPEN — LONGS ACTIVE"
    elif gate.get("short_allowed"):
        banner_class = "gate-short"
        banner_icon  = "🔽"
        banner_title = "MARKET GATE — SHORTS ONLY"
    else:
        banner_class = "gate-closed"
        banner_icon  = "🚫"
        banner_title = "MARKET GATE CLOSED — NO TRADES TODAY"

    vix = gate.get("vix_level") or 0
    vix_class = "vix-high" if vix >= 18 else ("vix-warn" if vix >= 15 else "vix-ok")

    refresh_meta = '<meta http-equiv="refresh" content="900">' if auto_refresh else ""
    run_time = datetime.now().strftime("%d-%b-%Y %H:%M IST")

    # ── Long signals rows ──
    long_rows = ""
    for s in long_signals:
        ai_badge = _ai_badge(s.get("ai_confidence", "N/A"))
        qs = s.get("quality_score", 0)
        qs_color = "#00c853" if qs >= 75 else ("#ffab40" if qs >= 60 else "#ff1744")
        qs_badge = "" if s.get("tradeable", True) else " <br><span style='color:#ff1744;font-size:10px;'>⚠ Below Threshold</span>"
        
        long_rows += f"""
        <tr>
          <td><strong>{s['symbol']}</strong><br><small>{s.get('sector','')}</small></td>
          <td class="state-{s['state'].lower()}">{s['state'].replace('_',' ')}</td>
          <td>₹{s.get('entry_zone_low',0):.2f} – ₹{s.get('entry_zone_high',0):.2f}</td>
          <td>₹{s.get('stop_loss',0):.2f}</td>
          <td>{s.get('risk_pct',0):.2f}%</td>
          <td>{s.get('expected_move','N/A')}</td>
          <td style="color:{qs_color};font-weight:bold;">{qs}{qs_badge}</td>
          <td>{ai_badge}</td>
          <td class="ai-insight">{s.get('ai_summary') or '—'}</td>
        </tr>"""

    # ── Short/PE signals rows ──
    short_rows = ""
    for pe in pe_signals:
        theta_icon = "⚠️ Yes" if pe.get("theta_warning") else "✅ No"
        ai_badge = _ai_badge(pe.get("ai_confidence", "N/A"))
        qs = pe.get("quality_score", 0)
        qs_color = "#00c853" if qs >= 75 else ("#ffab40" if qs >= 60 else "#ff1744")
        qs_badge = "" if pe.get("tradeable", True) else " <br><span style='color:#ff1744;font-size:10px;'>⚠ Below Threshold</span>"

        short_rows += f"""
        <tr>
          <td><strong>{pe['symbol']}</strong><br><small>{pe.get('sector','')}</small></td>
          <td class="state-accepted">{pe['state'].replace('_',' ')}</td>
          <td>₹{pe.get('pe_strike',0):.0f} PE</td>
          <td>{pe.get('expiry','—')}</td>
          <td>₹{pe.get('approx_premium',0):.1f}</td>
          <td>₹{pe.get('target_premium',0):.1f}</td>
          <td>₹{pe.get('stop_loss_underlying',0):.2f}</td>
          <td>{theta_icon}</td>
          <td style="color:{qs_color};font-weight:bold;">{qs}{qs_badge}</td>
          <td>{ai_badge}</td>
          <td class="ai-insight">{pe.get('ai_summary') or '—'}</td>
        </tr>"""

    # ── Watchlist rows ──
    watch_rows = ""
    for w in watchlist:
        watch_rows += f"""
        <tr>
          <td><strong>{w['symbol']}</strong></td>
          <td>{w.get('sector','—')}</td>
          <td>{w.get('compression_days','—')}</td>
          <td>{w.get('band_pct','—')}%</td>
          <td>₹{w.get('latest_close',0):.2f}</td>
          <td>₹{w.get('avg_value_cr',0):.0f} Cr</td>
        </tr>"""

    long_empty   = "<tr><td colspan='8' class='empty'>No long signals today</td></tr>" if not long_rows else ""
    short_empty  = "<tr><td colspan='10' class='empty'>No short signals today</td></tr>" if not short_rows else ""
    watch_empty  = "<tr><td colspan='6' class='empty'>No stocks in compression</td></tr>" if not watch_rows else ""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  {refresh_meta}
  <title>Nifty 200 Swing Signals — {run_time}</title>
  <style>
    :root {{
      --green: #00c853; --red: #ff1744; --yellow: #ffd600;
      --bg: #0f1117; --surface: #1a1d27; --surface2: #232636;
      --text: #e8e9f0; --text-dim: #8a8fa8; --border: #2d3148;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ background: var(--bg); color: var(--text); font-family: 'Inter', 'Segoe UI', sans-serif; font-size: 13px; line-height: 1.5; }}
    .header {{ background: var(--surface); padding: 20px 32px; border-bottom: 1px solid var(--border); display: flex; justify-content: space-between; align-items: center; }}
    .header h1 {{ font-size: 20px; font-weight: 700; }}
    .header .runtime {{ color: var(--text-dim); font-size: 12px; }}

    /* Gate Banner */
    .gate-banner {{ padding: 14px 32px; font-size: 15px; font-weight: 700; display: flex; align-items: center; gap: 12px; }}
    .gate-banner.gate-open    {{ background: linear-gradient(90deg, #00c85322, transparent); border-left: 4px solid var(--green); }}
    .gate-banner.gate-short   {{ background: linear-gradient(90deg, #ff174422, transparent); border-left: 4px solid var(--red); }}
    .gate-banner.gate-mixed   {{ background: linear-gradient(90deg, #ffd60022, transparent); border-left: 4px solid var(--yellow); }}
    .gate-banner.gate-closed  {{ background: linear-gradient(90deg, #ff174433, transparent); border-left: 4px solid var(--red); }}

    /* Gate stats */
    .gate-stats {{ padding: 12px 32px; background: var(--surface2); display: flex; gap: 32px; border-bottom: 1px solid var(--border); flex-wrap: wrap; }}
    .gate-stat {{ display: flex; flex-direction: column; gap: 2px; }}
    .gate-stat .label {{ color: var(--text-dim); font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em; }}
    .gate-stat .value {{ font-size: 15px; font-weight: 600; }}
    .vix-ok {{ color: var(--green); }}
    .vix-warn {{ color: var(--yellow); }}
    .vix-high {{ color: var(--red); }}

    /* Sections */
    .section {{ padding: 24px 32px; }}
    .section-title {{ font-size: 14px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.08em; margin-bottom: 12px; padding-bottom: 8px; border-bottom: 1px solid var(--border); }}
    .section-title.long-title  {{ color: var(--green); }}
    .section-title.short-title {{ color: var(--red); }}
    .section-title.watch-title {{ color: var(--yellow); }}

    /* Tables */
    table {{ width: 100%; border-collapse: collapse; background: var(--surface); border-radius: 8px; overflow: hidden; }}
    th {{ background: var(--surface2); padding: 10px 12px; text-align: left; font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em; color: var(--text-dim); font-weight: 600; }}
    td {{ padding: 10px 12px; border-bottom: 1px solid var(--border); vertical-align: top; }}
    tr:last-child td {{ border-bottom: none; }}
    tr:hover td {{ background: var(--surface2); }}
    .empty {{ color: var(--text-dim); font-style: italic; text-align: center; padding: 24px; }}

    /* State badges */
    .state-accepted           {{ color: var(--green); font-weight: 700; font-size: 11px; }}
    .state-expansion_fired    {{ color: #ffab40; font-weight: 700; font-size: 11px; }}
    .state-compression_detected {{ color: var(--yellow); font-weight: 600; font-size: 11px; }}
    .state-invalidated        {{ color: var(--text-dim); font-size: 11px; }}

    /* AI badges */
    .ai-badge {{ display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 10px; font-weight: 700; }}
    .ai-high   {{ background: #00c85330; color: var(--green); }}
    .ai-medium {{ background: #ffab4030; color: #ffab40; }}
    .ai-low    {{ background: #ff174430; color: var(--red); }}
    .ai-na     {{ background: var(--surface2); color: var(--text-dim); }}
    .ai-insight {{ color: var(--text-dim); font-size: 11px; max-width: 250px; }}

    footer {{ padding: 20px 32px; color: var(--text-dim); font-size: 11px; border-top: 1px solid var(--border); }}
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&display=swap');
  </style>
</head>
<body>

<div class="header">
  <h1>📈 Nifty 200 Swing Signal Report</h1>
  <div class="runtime">Generated: {run_time}{"&nbsp;&nbsp;🔄 Auto-refresh ON" if auto_refresh else ""}</div>
</div>

<div class="gate-banner {banner_class}">
  <span style="font-size:20px">{banner_icon}</span>
  <span>{banner_title}</span>
</div>

<div class="gate-stats">
  <div class="gate-stat">
    <span class="label">Nifty 50 Close</span>
    <span class="value">₹{gate.get('nifty_close') or '—'}</span>
  </div>
  <div class="gate-stat">
    <span class="label">20-EMA</span>
    <span class="value">₹{gate.get('nifty_20ema') or '—'}
      <span style="font-size:11px;color:{'var(--green)' if gate.get('nifty_above_20ema') else 'var(--red)'}">
        {'▲ Above' if gate.get('nifty_above_20ema') else '▼ Below'}
      </span>
    </span>
  </div>
  <div class="gate-stat">
    <span class="label">India VIX</span>
    <span class="value {vix_class}">{vix:.1f}</span>
  </div>
  <div class="gate-stat">
    <span class="label">PCR</span>
    <span class="value">{gate.get('pcr') or '—'}</span>
  </div>
  <div class="gate-stat">
    <span class="label">Advance/Decline</span>
    <span class="value">{gate.get('advance_decline') or '—'}</span>
  </div>
  <div class="gate-stat">
    <span class="label">Market Regime</span>
    <span class="value">{gate.get('market_regime') or '—'}</span>
  </div>
</div>

<!-- LONG SIGNALS -->
<div class="section">
  <div class="section-title long-title">🟢 LONG SIGNALS — {len(long_signals)} signals</div>
  <table>
    <thead>
      <tr>
        <th>Symbol</th><th>State</th><th>Entry Zone</th><th>Stop Loss</th>
        <th>Risk %</th><th>Expected Move</th><th>Quality Score</th><th>AI Score</th><th>AI Insight</th>
      </tr>
    </thead>
    <tbody>{long_rows or long_empty}</tbody>
  </table>
</div>

<!-- SHORT SIGNALS (PE) -->
<div class="section">
  <div class="section-title short-title">🔴 SHORT SIGNALS (PE Buying) — {len(pe_signals)} signals</div>
  <table>
    <thead>
      <tr>
        <th>Symbol</th><th>State</th><th>PE Strike</th><th>Expiry</th>
        <th>Est. Premium</th><th>Target Premium</th><th>Underlying SL</th>
        <th>Theta Risk</th><th>Quality Score</th><th>AI Score</th><th>AI Insight</th>
      </tr>
    </thead>
    <tbody>{short_rows or short_empty}</tbody>
  </table>
</div>

<!-- WATCHLIST -->
<div class="section">
  <div class="section-title watch-title">⚡ COMPRESSION WATCHLIST — {len(watchlist)} stocks building up</div>
  <table>
    <thead>
      <tr>
        <th>Symbol</th><th>Sector</th><th>Compression Days</th>
        <th>Band Width</th><th>Last Close</th><th>Avg Value</th>
      </tr>
    </thead>
    <tbody>{watch_rows or watch_empty}</tbody>
  </table>
</div>

<footer>
  <p>⚠️ For educational purposes only. Not investment advice. Always use your own judgment and risk management.</p>
  <p>Data source: Upstox API v2 | AI: {config.AI_PROVIDER.upper() if config.AI_SENTIMENT_ENABLED else "Disabled"}</p>
</footer>
</body>
</html>"""

    out_path = config.OUTPUT_DIR / "signals_report.html"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    logger.info("signals_report.html written → %s", out_path)
    return html


def _ai_badge(confidence: str) -> str:
    conf_map = {
        "HIGH":      ("ai-high",   "AI: HIGH ✓"),
        "MEDIUM":    ("ai-medium", "AI: MED"),
        "LOW":       ("ai-low",    "AI: LOW ⚠"),
        "VERY_LOW":  ("ai-low",    "AI: VERY LOW ⚠"),
        "N/A":       ("ai-na",     "AI: N/A"),
    }
    cls, label = conf_map.get(confidence, ("ai-na", "AI: N/A"))
    return f'<span class="ai-badge {cls}">{label}</span>'


def run_reporter(gate: dict, long_signals: list[dict], short_signals: list[dict],
                 watchlist: list[dict], pe_signals: list[dict],
                 mode: str = "eod") -> dict:
    """Main entry point. Generates both output files."""
    import signal_ranker
    
    # Wire in the Quality Score ranker for both signals sets
    long_signals = signal_ranker.rank_signals(long_signals, gate)
    short_signals = signal_ranker.rank_signals(short_signals, gate)
    pe_signals = signal_ranker.rank_signals(pe_signals, gate)

    auto_refresh = (mode == "intraday")
    payload = generate_json(gate, long_signals, short_signals, watchlist, pe_signals)
    generate_html(gate, long_signals, short_signals, watchlist, pe_signals, auto_refresh)
    return payload
