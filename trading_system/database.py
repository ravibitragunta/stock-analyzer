"""
database.py — SQLite schema setup and ALL database operations.
Single source of truth for reads/writes. All other modules call this, never sqlite3 directly.
"""

import sqlite3
import logging
from datetime import datetime, date
from pathlib import Path
from contextlib import contextmanager
from typing import Optional

import config

logger = logging.getLogger(__name__)


@contextmanager
def get_conn():
    """Context manager for safe, auto-committing DB connections."""
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row          # rows accessible as dicts
    conn.execute("PRAGMA journal_mode=WAL") # better concurrent read performance
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """
    Create all tables if they don't exist.
    Safe to call on every startup — idempotent.
    """
    with get_conn() as conn:
        conn.executescript("""
        -- ─────────────────────────────────────────────
        -- STOCKS: Nifty 200 universe master list
        -- ─────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS stocks (
            symbol          TEXT PRIMARY KEY,
            name            TEXT NOT NULL,
            sector          TEXT,
            instrument_key  TEXT NOT NULL,   -- Upstox key e.g. "NSE_EQ|INE009A01021"
            isin            TEXT,
            listed_on       TEXT DEFAULT 'NIFTY_200',
            is_active       INTEGER DEFAULT 1,
            created_at      TEXT DEFAULT (datetime('now'))
        );

        -- ─────────────────────────────────────────────
        -- OHLCV DAILY: EOD candle data
        -- ─────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS ohlcv_daily (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol          TEXT NOT NULL,
            date            TEXT NOT NULL,   -- YYYY-MM-DD
            open            REAL NOT NULL,
            high            REAL NOT NULL,
            low             REAL NOT NULL,
            close           REAL NOT NULL,
            volume          INTEGER NOT NULL,
            delivery_pct    REAL,            -- from NSE bhavcopy, nullable
            FOREIGN KEY (symbol) REFERENCES stocks(symbol),
            UNIQUE (symbol, date)
        );
        CREATE INDEX IF NOT EXISTS idx_ohlcv_symbol_date ON ohlcv_daily(symbol, date);

        -- ─────────────────────────────────────────────
        -- OHLCV INTRADAY: 15-min candles for WebSocket mode
        -- ─────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS ohlcv_intraday (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol          TEXT NOT NULL,
            datetime        TEXT NOT NULL,   -- YYYY-MM-DD HH:MM:SS
            open            REAL NOT NULL,
            high            REAL NOT NULL,
            low             REAL NOT NULL,
            close           REAL NOT NULL,
            volume          INTEGER NOT NULL,
            FOREIGN KEY (symbol) REFERENCES stocks(symbol),
            UNIQUE (symbol, datetime)
        );
        CREATE INDEX IF NOT EXISTS idx_intraday_symbol ON ohlcv_intraday(symbol, datetime);

        -- ─────────────────────────────────────────────
        -- SIGNALS: One row per signal generated
        -- ─────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS signals (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol          TEXT NOT NULL,
            date            TEXT NOT NULL,   -- Date signal was first generated
            signal_type     TEXT NOT NULL,   -- LONG | SHORT
            state           TEXT NOT NULL,   -- COMPRESSION_DETECTED | EXPANSION_FIRED | ACCEPTED | INVALIDATED | TARGET_HIT
            entry_zone_low  REAL,
            entry_zone_high REAL,
            stop_loss       REAL,
            risk_pct        REAL,            -- (entry - stop) / entry * 100
            expected_move   TEXT,            -- e.g. "+5 to +8%"
            valid_for_days  INTEGER DEFAULT 5,
            compression_days INTEGER,        -- how many days stock was in compression
            expansion_candle_date TEXT,      -- date of the expansion candle
            expansion_high  REAL,
            expansion_low   REAL,
            expansion_close REAL,
            ai_score        REAL,            -- composite AI confidence 0-1, NULL if AI disabled
            ai_confidence   TEXT,            -- HIGH | MEDIUM | LOW | N/A
            ai_summary      TEXT,            -- one-line AI explanation
            created_at      TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (symbol) REFERENCES stocks(symbol)
        );
        CREATE INDEX IF NOT EXISTS idx_signals_date ON signals(date);
        CREATE INDEX IF NOT EXISTS idx_signals_state ON signals(state);

        -- ─────────────────────────────────────────────
        -- SIGNAL TRACKER: Daily state updates per signal
        -- ─────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS signal_tracker (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_id       INTEGER NOT NULL,
            date            TEXT NOT NULL,
            outcome         TEXT NOT NULL,   -- ACTIVE | INVALIDATED | TARGET_HIT
            close_price     REAL,
            notes           TEXT,
            FOREIGN KEY (signal_id) REFERENCES signals(id)
        );

        -- ─────────────────────────────────────────────
        -- MARKET BREADTH: Daily market gate results
        -- ─────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS market_breadth (
            date                TEXT PRIMARY KEY,
            nifty_close         REAL,
            nifty_20ema         REAL,
            nifty_above_20ema   INTEGER,     -- 1/0 boolean
            vix_level           REAL,
            pcr                 REAL,
            advance_decline     REAL,
            long_allowed        INTEGER,
            short_allowed       INTEGER,
            market_regime       TEXT,        -- BULLISH | BEARISH | NEUTRAL | VOLATILE
            global_macro_score  REAL,
            created_at          TEXT DEFAULT (datetime('now'))
        );

        -- ─────────────────────────────────────────────
        -- AI SENTIMENT: Cached per-stock AI results
        -- ─────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS ai_sentiment (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol          TEXT NOT NULL,
            date            TEXT NOT NULL,
            dimension       TEXT NOT NULL,   -- sector | macro | value_chain | event_risk | institutional
            sentiment       TEXT,            -- BULLISH | BEARISH | NEUTRAL
            confidence      REAL,
            key_insight     TEXT,
            raw_response    TEXT,
            model_used      TEXT,
            tokens_used     INTEGER,
            created_at      TEXT DEFAULT (datetime('now')),
            UNIQUE (symbol, date, dimension)
        );

        -- ─────────────────────────────────────────────
        -- BACKTEST RESULTS
        -- ─────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS backtest_results (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_id       INTEGER,
            symbol          TEXT NOT NULL,
            signal_type     TEXT NOT NULL,
            entry_date      TEXT,
            entry_price     REAL,
            exit_date       TEXT,
            exit_price      REAL,
            outcome         TEXT,            -- WIN | LOSS | EXPIRED
            pnl_pct         REAL,
            r_multiple      REAL,
            notes           TEXT
        );

        -- ─────────────────────────────────────────────
        -- ALERT LOG: One Telegram alert per stock per day
        -- ─────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS alert_log (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol          TEXT NOT NULL,
            date            TEXT NOT NULL,   -- YYYY-MM-DD
            alert_type      TEXT NOT NULL,   -- PE_ALERT | EOD_WATCHLIST | SYSTEM
            sent_at         TEXT DEFAULT (datetime('now')),
            UNIQUE (symbol, date, alert_type)
        );
        CREATE INDEX IF NOT EXISTS idx_alert_log_date ON alert_log(date, symbol);

        -- ─────────────────────────────────────────────
        -- PE WATCHLIST: Tonight's candidates for tomorrow's PE runner
        -- ─────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS pe_watchlist (
            symbol          TEXT NOT NULL,
            date            TEXT NOT NULL,   -- YYYY-MM-DD (the EOD run date)
            PRIMARY KEY (symbol, date)
        );
        """)
    logger.info("Database initialised at %s", config.DB_PATH)


# ─────────────────────────────────────────────
# STOCKS
# ─────────────────────────────────────────────

def upsert_stock(symbol: str, name: str, sector: str, instrument_key: str, isin: str = None):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO stocks (symbol, name, sector, instrument_key, isin)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(symbol) DO UPDATE SET
                name=excluded.name,
                sector=excluded.sector,
                instrument_key=excluded.instrument_key,
                isin=excluded.isin
        """, (symbol, name, sector, instrument_key, isin))


def get_all_stocks() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM stocks WHERE is_active=1 ORDER BY symbol"
        ).fetchall()
    return [dict(r) for r in rows]


def get_stock(symbol: str) -> Optional[dict]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM stocks WHERE symbol=?", (symbol,)).fetchone()
    return dict(row) if row else None


# ─────────────────────────────────────────────
# OHLCV DAILY
# ─────────────────────────────────────────────

def bulk_insert_ohlcv(rows: list[dict]):
    """
    rows: list of dicts with keys: symbol, date, open, high, low, close, volume
    Uses INSERT OR IGNORE to skip duplicates safely.
    """
    if not rows:
        return
    with get_conn() as conn:
        conn.executemany("""
            INSERT OR IGNORE INTO ohlcv_daily
                (symbol, date, open, high, low, close, volume, delivery_pct)
            VALUES
                (:symbol, :date, :open, :high, :low, :close, :volume, :delivery_pct)
        """, [{**r, "delivery_pct": r.get("delivery_pct")} for r in rows])


def get_ohlcv(symbol: str, days: int = 365) -> list[dict]:
    """Return up to `days` most recent EOD candles for a symbol, ascending by date."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT * FROM ohlcv_daily
            WHERE symbol=?
            ORDER BY date DESC
            LIMIT ?
        """, (symbol, days)).fetchall()
    return [dict(r) for r in reversed(rows)]


def get_latest_date(symbol: str) -> Optional[str]:
    """Return the most recent date we have data for a given symbol."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT MAX(date) as d FROM ohlcv_daily WHERE symbol=?", (symbol,)
        ).fetchone()
    return row["d"] if row else None


def get_ohlcv_range(symbol: str, start: str, end: str) -> list[dict]:
    """Return EOD candles between start and end dates (inclusive)."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT * FROM ohlcv_daily
            WHERE symbol=? AND date BETWEEN ? AND ?
            ORDER BY date ASC
        """, (symbol, start, end)).fetchall()
    return [dict(r) for r in rows]


# ─────────────────────────────────────────────
# OHLCV INTRADAY
# ─────────────────────────────────────────────

def upsert_intraday(symbol: str, dt: str, o: float, h: float, l: float, c: float, v: int):
    with get_conn() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO ohlcv_intraday
                (symbol, datetime, open, high, low, close, volume)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (symbol, dt, o, h, l, c, v))


def get_intraday_today(symbol: str) -> list[dict]:
    today = date.today().isoformat()
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT * FROM ohlcv_intraday
            WHERE symbol=? AND datetime LIKE ?
            ORDER BY datetime ASC
        """, (symbol, f"{today}%")).fetchall()
    return [dict(r) for r in rows]


# ─────────────────────────────────────────────
# SIGNALS
# ─────────────────────────────────────────────

def insert_signal(signal: dict) -> int:
    """Insert a new signal. Returns the new signal ID."""
    with get_conn() as conn:
        cur = conn.execute("""
            INSERT INTO signals (
                symbol, date, signal_type, state,
                entry_zone_low, entry_zone_high, stop_loss, risk_pct,
                expected_move, valid_for_days, compression_days,
                expansion_candle_date, expansion_high, expansion_low, expansion_close,
                ai_score, ai_confidence, ai_summary
            ) VALUES (
                :symbol, :date, :signal_type, :state,
                :entry_zone_low, :entry_zone_high, :stop_loss, :risk_pct,
                :expected_move, :valid_for_days, :compression_days,
                :expansion_candle_date, :expansion_high, :expansion_low, :expansion_close,
                :ai_score, :ai_confidence, :ai_summary
            )
        """, signal)
    return cur.lastrowid


def update_signal_state(signal_id: int, state: str):
    with get_conn() as conn:
        conn.execute(
            "UPDATE signals SET state=? WHERE id=?", (state, signal_id)
        )


def update_signal_ai(signal_id: int, ai_score: float, ai_confidence: str, ai_summary: str):
    with get_conn() as conn:
        conn.execute("""
            UPDATE signals SET ai_score=?, ai_confidence=?, ai_summary=?
            WHERE id=?
        """, (ai_score, ai_confidence, ai_summary, signal_id))


def get_active_signals(as_of_date: str = None) -> list[dict]:
    """Return all signals that are not INVALIDATED or TARGET_HIT."""
    q_date = as_of_date or date.today().isoformat()
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT s.*, st.name, st.sector
            FROM signals s
            JOIN stocks st ON s.symbol = st.symbol
            WHERE s.state NOT IN ('INVALIDATED', 'TARGET_HIT')
              AND s.date <= ?
            ORDER BY s.date DESC
        """, (q_date,)).fetchall()
    return [dict(r) for r in rows]


def get_signals_by_state(state: str, date_from: str = None) -> list[dict]:
    date_from = date_from or "2020-01-01"
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT s.*, st.name, st.sector
            FROM signals s
            JOIN stocks st ON s.symbol = st.symbol
            WHERE s.state=? AND s.date >= ?
            ORDER BY s.date DESC
        """, (state, date_from)).fetchall()
    return [dict(r) for r in rows]


def get_signals_for_date(target_date: str) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT s.*, st.name, st.sector
            FROM signals s
            JOIN stocks st ON s.symbol = st.symbol
            WHERE s.date=?
            ORDER BY s.signal_type, s.ai_score DESC
        """, (target_date,)).fetchall()
    return [dict(r) for r in rows]


def symbol_has_active_signal(symbol: str) -> bool:
    with get_conn() as conn:
        row = conn.execute("""
            SELECT COUNT(*) as cnt FROM signals
            WHERE symbol=? AND state NOT IN ('INVALIDATED', 'TARGET_HIT')
        """, (symbol,)).fetchone()
    return row["cnt"] > 0


# ─────────────────────────────────────────────
# SIGNAL TRACKER
# ─────────────────────────────────────────────

def log_signal_outcome(signal_id: int, outcome: str, close_price: float = None, notes: str = None):
    today = date.today().isoformat()
    with get_conn() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO signal_tracker
                (signal_id, date, outcome, close_price, notes)
            VALUES (?, ?, ?, ?, ?)
        """, (signal_id, today, outcome, close_price, notes))


# ─────────────────────────────────────────────
# MARKET BREADTH
# ─────────────────────────────────────────────

def save_market_gate(gate: dict):
    with get_conn() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO market_breadth (
                date, nifty_close, nifty_20ema, nifty_above_20ema,
                vix_level, pcr, advance_decline, long_allowed, short_allowed,
                market_regime, global_macro_score
            ) VALUES (
                :date, :nifty_close, :nifty_20ema, :nifty_above_20ema,
                :vix_level, :pcr, :advance_decline, :long_allowed, :short_allowed,
                :market_regime, :global_macro_score
            )
        """, gate)


def get_market_gate(target_date: str = None) -> Optional[dict]:
    target_date = target_date or date.today().isoformat()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM market_breadth WHERE date=?", (target_date,)
        ).fetchone()
    return dict(row) if row else None


# ─────────────────────────────────────────────
# AI SENTIMENT
# ─────────────────────────────────────────────

def save_ai_sentiment(symbol: str, dimension: str, data: dict):
    today = date.today().isoformat()
    with get_conn() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO ai_sentiment
                (symbol, date, dimension, sentiment, confidence, key_insight,
                 raw_response, model_used, tokens_used)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            symbol, today, dimension,
            data.get("sentiment"), data.get("confidence"),
            data.get("key_insight"), data.get("raw_response"),
            data.get("model_used"), data.get("tokens_used"),
        ))


def get_ai_sentiment_today(symbol: str) -> dict:
    """Return all AI dimensions for a symbol for today, as a dict keyed by dimension."""
    today = date.today().isoformat()
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT * FROM ai_sentiment WHERE symbol=? AND date=?
        """, (symbol, today)).fetchall()
    return {row["dimension"]: dict(row) for row in rows}


# ─────────────────────────────────────────────
# BACKTEST
# ─────────────────────────────────────────────

def insert_backtest_result(result: dict):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO backtest_results
                (signal_id, symbol, signal_type, entry_date, entry_price,
                 exit_date, exit_price, outcome, pnl_pct, r_multiple, notes)
            VALUES
                (:signal_id, :symbol, :signal_type, :entry_date, :entry_price,
                 :exit_date, :exit_price, :outcome, :pnl_pct, :r_multiple, :notes)
        """, result)


def get_backtest_results(start: str = None, end: str = None) -> list[dict]:
    start = start or "2020-01-01"
    end = end or date.today().isoformat()
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT * FROM backtest_results
            WHERE entry_date BETWEEN ? AND ?
            ORDER BY entry_date ASC
        """, (start, end)).fetchall()
    return [dict(r) for r in rows]


def clear_backtest_results():
    with get_conn() as conn:
        conn.execute("DELETE FROM backtest_results")


# ─────────────────────────────────────────────
# ALERT LOG (Telegram deduplication)
# ─────────────────────────────────────────────

def alert_sent_today(symbol: str, alert_type: str) -> bool:
    """Check if a Telegram alert was already sent today for this symbol + type."""
    today = date.today().isoformat()
    with get_conn() as conn:
        row = conn.execute("""
            SELECT COUNT(*) as cnt FROM alert_log
            WHERE symbol=? AND date=? AND alert_type=?
        """, (symbol, today, alert_type)).fetchone()
    return row["cnt"] > 0


def save_alert_log(symbol: str, alert_type: str):
    """Record that a Telegram alert was sent today."""
    today = date.today().isoformat()
    with get_conn() as conn:
        conn.execute("""
            INSERT OR IGNORE INTO alert_log (symbol, date, alert_type)
            VALUES (?, ?, ?)
        """, (symbol, today, alert_type))


# ─────────────────────────────────────────────
# PE WATCHLIST (persisted across EOD → intraday)
# ─────────────────────────────────────────────

def save_pe_watchlist(symbols: list[str]):
    """Save tonight's PE watchlist (for tomorrow's intraday PE engine)."""
    today = date.today().isoformat()
    with get_conn() as conn:
        conn.execute("DELETE FROM pe_watchlist WHERE date=?", (today,))
        conn.executemany(
            "INSERT OR IGNORE INTO pe_watchlist (symbol, date) VALUES (?, ?)",
            [(s, today) for s in symbols]
        )


def get_pe_watchlist(for_date: str = None) -> list[str]:
    """Get the most recent PE watchlist (today's or yesterday's EOD run)."""
    with get_conn() as conn:
        if for_date:
            rows = conn.execute(
                "SELECT symbol FROM pe_watchlist WHERE date=?", (for_date,)
            ).fetchall()
        else:
            # Get the most recent date's watchlist
            rows = conn.execute("""
                SELECT symbol FROM pe_watchlist
                WHERE date = (SELECT MAX(date) FROM pe_watchlist)
            """).fetchall()
    return [r["symbol"] for r in rows]
