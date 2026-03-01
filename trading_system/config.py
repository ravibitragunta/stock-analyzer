"""
config.py — Central configuration for the Nifty 200 Swing Trading System.
ALL thresholds and constants live here. Never hardcode values in logic files.
"""

import os
from pathlib import Path

# ─────────────────────────────────────────────
# PROJECT PATHS
# ─────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "trading.db"
LOG_DIR = BASE_DIR
ERRORS_LOG = LOG_DIR / "errors.log"
OUTPUT_DIR = BASE_DIR
INSTRUMENTS_FILE = BASE_DIR.parent / "NSE_instruments.json"

# ─────────────────────────────────────────────
# UPSTOX API
# ─────────────────────────────────────────────
UPSTOX_BASE_URL = "https://api.upstox.com/v2"
UPSTOX_API_KEY = os.getenv("UPSTOX_API_KEY", "")          # Set via env or edit here
UPSTOX_ACCESS_TOKEN = os.getenv("UPSTOX_ACCESS_TOKEN", 
"") # OAuth access token

# Rate limiting — exponential backoff
RATE_LIMIT_RETRIES = 3
RATE_LIMIT_BASE_DELAY = 2   # seconds; doubles on each retry (2 → 4 → 8)
HTTP_TIMEOUT = 30            # seconds per request

# Instruments file — refreshed daily at startup
INSTRUMENTS_URL = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"
INSTRUMENTS_REFRESH_HRS = 24

# ─────────────────────────────────────────────
# CONFIRMED INSTRUMENT KEYS (from NSE master)
# ─────────────────────────────────────────────
INDIA_VIX_KEY  = "NSE_INDEX|India VIX"   # exchange_token: 26017
NIFTY_50_KEY   = "NSE_INDEX|Nifty 50"    # exchange_token: 26000
NIFTY_200_KEY  = "NSE_INDEX|Nifty 200"   # exchange_token: 26033
NIFTY_100_KEY  = "NSE_INDEX|Nifty 100"   # exchange_token: 26012
BANK_NIFTY_KEY = "NSE_INDEX|Nifty Bank"  # exchange_token: 26009

# Sector indices — used by market gate and AI sentiment
SECTOR_INDICES = {
    "IT":         "NSE_INDEX|Nifty IT",
    "Pharma":     "NSE_INDEX|Nifty Pharma",
    "Auto":       "NSE_INDEX|Nifty Auto",
    "FMCG":       "NSE_INDEX|Nifty FMCG",
    "Metal":      "NSE_INDEX|Nifty Metal",
    "Energy":     "NSE_INDEX|Nifty Energy",
    "Realty":     "NSE_INDEX|Nifty Realty",
    "Infra":      "NSE_INDEX|Nifty Infra",
    "Media":      "NSE_INDEX|Nifty Media",
    "PSU Bank":   "NSE_INDEX|Nifty PSU Bank",
    "Finance":    "NSE_INDEX|Nifty Fin Service",
    "Healthcare": "NSE_INDEX|NIFTY HEALTHCARE",
}

# ─────────────────────────────────────────────
# DATA LAYER
# ─────────────────────────────────────────────
HISTORICAL_DAYS = 365          # Initial fetch: 1 year of EOD data
INTRADAY_CANDLE_MINUTES = 15   # WebSocket candle interval

# ─────────────────────────────────────────────
# UNIVERSE FILTER
# ─────────────────────────────────────────────
MIN_TRADED_VALUE_CR = 75       # Avg daily traded value > ₹75 Cr
MIN_PRICE = 150                # Stock price > ₹150
SECTOR_FILTER_ENABLED = True   # Filter out stocks if their sector is bearish

# ─────────────────────────────────────────────
# MARKET GATE
# ─────────────────────────────────────────────
VIX_WARN_THRESHOLD  = 15.0     # Warn if VIX in [15, 20)
VIX_BLOCK_THRESHOLD = 20.0     # Block longs if VIX >= 20
VIX_SHORT_THRESHOLD = 18.0     # Allow shorts if VIX >= 18
PCR_BULL_MIN        = 0.7      # PCR must be >= 0.7 for longs (market not too bearish-hedged)
PCR_BEAR_MAX        = 0.7      # PCR < 0.7 = allow shorts
AD_RATIO_MIN        = 0.4      # Advance-Decline ratio must be > 0.4 for longs
EMA_PERIOD          = 20       # EMA period for Nifty 50 gate check

# NSE holidays for 2025 and 2026 (hardcoded, update annually)
NSE_HOLIDAYS = {
    # 2025
    "2025-01-26", "2025-02-26", "2025-03-14", "2025-03-31",
    "2025-04-10", "2025-04-14", "2025-04-18", "2025-05-01",
    "2025-08-15", "2025-08-27", "2025-10-02", "2025-10-02",
    "2025-10-20", "2025-10-23", "2025-11-05", "2025-12-25",
    # 2026
    "2026-01-26", "2026-03-02", "2026-03-19", "2026-03-20",
    "2026-04-02", "2026-04-03", "2026-04-14", "2026-05-01",
    "2026-08-15", "2026-10-02", "2026-10-22", "2026-11-14",
    "2026-12-25",
}

# ─────────────────────────────────────────────
# CEA STRATEGY — COMPRESSION
# ─────────────────────────────────────────────
MAX_CANDLE_RANGE_PCT   = 4.5   # (high-low)/close < 4.5% on average
ATR_PERCENTILE_MAX     = 60    # ATR(14) must be in lowest 60th percentile of last 6 months
COMPRESSION_BAND_PCT   = 6.0   # Price must stay within a 6% band
COMPRESSION_LOOKBACK   = 10    # Days to look back for compression
COMPRESSION_MIN_DAYS   = 4     # Must be compressed for at least 4 consecutive days
BB_WIDTH_PERCENTILE    = 50    # Bollinger Band width in lowest 50th percentile

# ─────────────────────────────────────────────
# CEA STRATEGY — EXPANSION
# ─────────────────────────────────────────────
EXPANSION_RANGE_MULT   = 2.0   # Expansion candle range >= 2x avg 10-day range
EXPANSION_CLOSE_PCT    = 0.25  # Close must be in top/bottom 25% of candle
EXPANSION_VOL_MULT     = 1.5   # Volume >= 1.5x 20-day average
MAX_UPPER_WICK_PCT     = 0.20  # Upper wick < 20% of total candle range (longs)
EXPANSION_LOOKBACK     = 10    # Days for average range calculation

# ─────────────────────────────────────────────
# CEA STRATEGY — ACCEPTANCE
# ─────────────────────────────────────────────
MAX_RETRACEMENT_PCT    = 0.40  # Pullback must be <= 40% of expansion candle body
ACCEPTANCE_MAX_DAYS    = 2     # Check acceptance over next 2 days

# ─────────────────────────────────────────────
# STOP LOSS & ENTRY
# ─────────────────────────────────────────────
ATR_MULTIPLIER          = 2.0   # Stop = entry - (ATR_MULTIPLIER * ATR14)
MAX_STOP_FROM_ENTRY_PCT = 3.5   # If stop > 3.5% from entry → log warning but still emit signal
ENTRY_ZONE_BUFFER_PCT   = 0.5   # Entry zone = expansion close to close + 0.5%
RISK_PER_TRADE_PCT      = 1.0   # Max % of capital to risk per trade (for position sizing)

# ─────────────────────────────────────────────
# SIGNAL TRACKING
# ─────────────────────────────────────────────
DEFAULT_VALID_FOR_DAYS = 5      # Signal auto-invalidates after this many days
TARGET_MOVE_PCT        = 20.0   # Target premium gain for options (and TARGET_HIT state)

# ─────────────────────────────────────────────
# OPTIONS MODULE
# ─────────────────────────────────────────────
MIN_OPTION_PREMIUM     = 5.0    # Reject strikes with premium < ₹5 (illiquid)
TARGET_OPTION_GAIN_PCT = 20.0   # Target: 20% gain on premium paid
THETA_WARN_DAYS        = 5      # Warn if expected move window > 5 days
MIN_DTE_CURRENT_EXPIRY = 7      # Use current expiry only if > 7 days remaining
DELTA_MIN              = 0.30   # PE delta min (absolute) — not too far OTM
DELTA_MAX              = 0.50   # PE delta max (absolute)
IV_PERCENTILE_MAX      = 50     # Don't buy PE if IV > 50th percentile

# NSE strike intervals by price range
STRIKE_INTERVALS = [
    (0,     100,   5),
    (100,   250,   5),
    (250,   500,   10),
    (500,   1000,  20),
    (1000,  2500,  50),
    (2500,  5000,  100),
    (5000,  10000, 100),
    (10000, 99999, 200),
]

# ─────────────────────────────────────────────
# AI SENTIMENT LAYER
# ─────────────────────────────────────────────
AI_SENTIMENT_ENABLED   = True           # Master switch — set False to skip entirely
AI_PROVIDER            = "claude"       # "claude" | "gemini" | "both"
AI_FALLBACK_ON_ERROR   = True           # If True: on AI failure, skip gracefully
AI_MAX_CALLS_PER_RUN   = 30            # Hard cap on total AI calls per run (EOD batch)
CLAUDE_API_KEY         = os.getenv("CLAUDE_API_KEY", "")
GEMINI_API_KEY         = os.getenv("GEMINI_API_KEY", "")

# Claude model selection (Haiku = fast+cheap, Sonnet = accurate but 10× cost)
CLAUDE_HAIKU_MODEL     = "claude-3-5-haiku-20241022"   # $0.80/MTok in | $4/MTok out
CLAUDE_SONNET_MODEL    = "claude-3-5-sonnet-20241022"  # $3/MTok in    | $15/MTok out

# Cost optimization — Claude API features
CLAUDE_USE_PROMPT_CACHE = True    # Cache system prompts (saves ~85% on input tokens)
CLAUDE_USE_BATCH_API    = True    # Use Batch API for EOD run (50% discount)
CLAUDE_BATCH_POLL_SEC   = 10      # Seconds between batch status polls
CLAUDE_BATCH_TIMEOUT    = 300     # Max seconds to wait for batch results

# Dimension weights for composite AI score
AI_WEIGHTS = {
    "sector":        0.25,
    "macro":         0.20,
    "value_chain":   0.20,
    "event_risk":    0.20,
    "institutional": 0.15,
}

# Score thresholds
AI_HIGH_CONFIDENCE     = 0.75
AI_LOW_CONFIDENCE      = 0.30

# ─────────────────────────────────────────────
# TELEGRAM
# ─────────────────────────────────────────────
TELEGRAM_BOT_TOKEN     = os.getenv("TELEGRAM_BOT_TOKEN", "")   # From @BotFather
TELEGRAM_CHAT_ID       = os.getenv("TELEGRAM_CHAT_ID", "-1003790683291") # "Trade-ideas-rrb" channel ID
TELEGRAM_ENABLED       = bool(TELEGRAM_BOT_TOKEN)   # Auto-disables if token is empty

# ─────────────────────────────────────────────
# SCHEDULER
# ─────────────────────────────────────────────
EOD_RUN_TIME_IST       = "21:30"   # 9:30 PM IST daily
INTRADAY_START_IST     = "10:30"   # Don't alert before 10:30 AM (avoid opening noise)
INTRADAY_END_IST       = "14:00"   # Stop PE alerts after 2 PM (theta/time risk)

# ─────────────────────────────────────────────
# INTRADAY PE ALERT ENGINE
# ─────────────────────────────────────────────
# VIX gate for PE buying (sweet spot: not too cheap, not panic-priced)
PE_VIX_MIN             = 13.0     # Don't buy PE if VIX < 13 (too cheap = no IV)
PE_VIX_MAX             = 22.0     # Don't buy PE if VIX > 22 (too expensive)

# PCR gate — below this = market complacent = shorts can work
PE_PCR_MAX             = 0.85     # Alert only when PCR < 0.85

# OI buildup confirmation — PE OI must be rising on the strike
PE_OI_CHANGE_MIN_PCT   = 10.0     # PE OI on target strike rose >= 10% vs prev day

# IV percentile gate — don't buy expensive options
PE_IV_PERCENTILE_MAX   = 55       # Skip if IV > 55th percentile

# Intraday EMA break — underlying must break below this for PE alert
PE_EMA_PERIOD          = 20       # 20-EMA on intraday (15-min bars)

# Price already moved too much — don't chase
PE_PREMIUM_CHASE_MAX   = 35       # Skip if PE premium already up >35% from prev close

# REST poll interval during intraday session (VIX + option chain)
OPTION_CHAIN_POLL_SEC  = 180      # Poll every 3 minutes
VIX_POLL_SEC           = 120      # Poll VIX every 2 minutes

# ─────────────────────────────────────────────
# BACKTESTING
# ─────────────────────────────────────────────
BACKTEST_START_DATE    = "2024-01-01"
BACKTEST_END_DATE      = "2024-12-31"
BACKTEST_SLIPPAGE_PCT  = 0.15
MONTE_CARLO_RUNS       = 1000

# ─────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────
LOG_LEVEL = "INFO"   # DEBUG | INFO | WARNING | ERROR
