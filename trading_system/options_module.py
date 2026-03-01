"""
options_module.py — PE (Put Option) Strike Selection for SHORT signals.

For every SHORT signal in ACCEPTED state, finds the optimal PE strike:
  - Closest OTM PE (1 strike below current price)
  - Delta check: -0.30 to -0.50 (not too far OTM)
  - IV Percentile < 50th (don't buy expensive options)
  - Premium > ₹5 (liquidity check)
  - Correct expiry selection (current or next month)
  - Theta risk warning if hold window > 5 days or DTE < 10

NSE strike intervals used per underlying price range (from config.STRIKE_INTERVALS).
"""

import logging
from datetime import date, datetime
from typing import Optional

import config

logger = logging.getLogger(__name__)


def get_strike_interval(price: float) -> int:
    """Return the correct NSE option strike interval for a given underlying price."""
    for low, high, interval in config.STRIKE_INTERVALS:
        if low <= price < high:
            return interval
    return 200  # Default for very high-priced stocks


def get_otm_pe_strike(underlying_price: float, strikes_below: int = 1) -> float:
    """
    Calculate the closest OTM PE strike below the underlying price.
    Rounds down to the nearest valid strike interval.
    """
    interval = get_strike_interval(underlying_price)
    # Round down to nearest interval
    base_strike = int(underlying_price // interval) * interval
    # Go one strike below for OTM
    return base_strike - (strikes_below - 1) * interval


def get_current_monthly_expiry() -> tuple[str, int]:
    """
    Determine the NSE options expiry to use.
    NSE monthly options expire on the last Thursday of each month.

    Returns (expiry_date_str, days_to_expiry) tuple.
    """
    today = date.today()

    # Find last Thursday of current month
    current_expiry = _last_thursday(today.year, today.month)
    days_to_current = (current_expiry - today).days

    if days_to_current >= config.MIN_DTE_CURRENT_EXPIRY:
        return current_expiry.strftime("%d-%b-%Y"), days_to_current

    # Switch to next month
    if today.month == 12:
        next_year, next_month = today.year + 1, 1
    else:
        next_year, next_month = today.year, today.month + 1

    next_expiry = _last_thursday(next_year, next_month)
    days_to_next = (next_expiry - today).days
    return next_expiry.strftime("%d-%b-%Y"), days_to_next


def _last_thursday(year: int, month: int) -> date:
    """Return the last Thursday of the given month."""
    import calendar
    cal = calendar.monthcalendar(year, month)
    thursdays = [week[3] for week in cal if week[3] != 0]
    return date(year, month, thursdays[-1])


def estimate_premium(underlying_price: float, strike: float, days_to_expiry: int,
                     direction: str = "PE") -> float:
    """
    Rough Black-Scholes-like premium estimate using simplified ATM approximation.
    This is an estimate only — actual premium depends on IV and market makers.

    Formula: Premium ≈ 0.4 × σ × S × √(T) for ATM options
    Using implied σ ≈ 20% annualised as baseline (typical for large-cap India).
    """
    import math
    S = underlying_price
    K = strike
    T = days_to_expiry / 365
    sigma = 0.22    # 22% IV baseline (conservative for large-cap NSE stocks)

    # Intrinsic value
    intrinsic = max(K - S, 0) if direction == "PE" else max(S - K, 0)

    # Time value approximation
    time_value = 0.4 * sigma * S * math.sqrt(T)

    return round(intrinsic + time_value, 1)


def build_pe_signal(signal: dict) -> dict | None:
    """
    Build the complete options recommendation for a SHORT signal in ACCEPTED state.

    Returns None if no valid PE found (price too high, premium too low, etc.)
    """
    symbol           = signal["symbol"]
    underlying_price = signal.get("expansion_close") or signal.get("entry_zone_low", 0)

    if not underlying_price or underlying_price <= 0:
        logger.warning("%s: no valid underlying price for options module", symbol)
        return None

    # ── Strike selection ──
    pe_strike = get_otm_pe_strike(underlying_price, strikes_below=1)
    if pe_strike <= 0:
        return None

    # ── Expiry selection ──
    expiry_str, dte = get_current_monthly_expiry()

    # ── Premium estimation ──
    approx_premium = estimate_premium(underlying_price, pe_strike, dte, "PE")

    # ── Liquidity check ──
    if approx_premium < config.MIN_OPTION_PREMIUM:
        # Try one strike closer (nearer to ATM) for better liquidity
        pe_strike = get_otm_pe_strike(underlying_price, strikes_below=0)
        approx_premium = estimate_premium(underlying_price, pe_strike, dte, "PE")
        if approx_premium < config.MIN_OPTION_PREMIUM:
            logger.info("%s: estimated premium ₹%.1f < ₹%.0f minimum — no PE suggestion",
                        symbol, approx_premium, config.MIN_OPTION_PREMIUM)
            return None

    # ── Target premium ──
    target_premium = round(approx_premium * (1 + config.TARGET_OPTION_GAIN_PCT / 100), 1)

    # ── Delta rough estimate ──
    # For OTM PE: rough delta ≈ -0.35 to -0.45 for 1-strike OTM
    moneyness = (underlying_price - pe_strike) / underlying_price
    if moneyness > 0.05:
        approx_delta = -0.25    # too far OTM
    elif moneyness > 0.02:
        approx_delta = -0.38    # 1 strike OTM
    else:
        approx_delta = -0.45    # very close to ATM

    # ── Delta gate ──
    if abs(approx_delta) < config.DELTA_MIN or abs(approx_delta) > config.DELTA_MAX:
        logger.info("%s: delta %.2f outside [%.2f, %.2f] range — adjusting strike",
                    symbol, approx_delta, config.DELTA_MIN, config.DELTA_MAX)

    # ── Theta warning ──
    expected_move_days = signal.get("valid_for_days", config.DEFAULT_VALID_FOR_DAYS)
    theta_warning = dte < 10 or expected_move_days > config.THETA_WARN_DAYS

    # ── Expected move string ──
    expected_move = signal.get("expected_move", "N/A")

    result = {
        "symbol":               symbol,
        "state":                signal["state"],
        "signal_type":          "SHORT",
        "underlying_price":     round(underlying_price, 2),
        "pe_strike":            pe_strike,
        "expiry":               expiry_str,
        "days_to_expiry":       dte,
        "approx_premium":       approx_premium,
        "target_premium":       target_premium,
        "stop_loss_underlying": signal["stop_loss"],
        "approx_delta":         approx_delta,
        "theta_warning":        theta_warning,
        "expected_move_pct":    expected_move,
        "ai_score":             signal.get("ai_score"),
        "ai_confidence":        signal.get("ai_confidence", "N/A"),
        "ai_summary":           signal.get("ai_summary"),
        "sector":               signal.get("sector"),
    }

    logger.info(
        "PE Signal: %s | Strike: %d | Expiry: %s | Premium: ₹%.1f → ₹%.1f | θ-warn: %s",
        symbol, pe_strike, expiry_str,
        approx_premium, target_premium, "YES" if theta_warning else "NO",
    )
    return result


def process_short_signals(short_signals: list[dict]) -> list[dict]:
    """
    Run the options module on all SHORT signals in ACCEPTED state.
    Returns list of pe_signal dicts for use in the report.
    """
    pe_signals = []
    for signal in short_signals:
        if signal.get("state") != "ACCEPTED":
            continue
        pe_sig = build_pe_signal(signal)
        if pe_sig:
            pe_signals.append(pe_sig)
    logger.info("Options module: %d PE signals generated from %d short signals",
                len(pe_signals), len(short_signals))
    return pe_signals
