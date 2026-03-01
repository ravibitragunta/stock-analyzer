"""
data_fetcher.py — Upstox API v2 REST client.

Responsibilities:
  - Refresh the NSE instruments master file daily
  - Fetch 1 year of EOD historical data on first run
  - Fetch incremental (latest candle only) on subsequent runs
  - Fetch index data (Nifty 50, VIX) for market gate
  - Handle 429 rate limits with exponential backoff
  - Log all API errors to errors.log
"""

import gzip
import json
import logging
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import requests
import urllib.parse

import config
import database as db

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# SESSION SETUP
# A single requests.Session with auth headers is reused throughout
# ─────────────────────────────────────────────

def _build_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "Authorization": f"Bearer {config.UPSTOX_ACCESS_TOKEN}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    })
    return session


_session: Optional[requests.Session] = None


def get_session() -> requests.Session:
    global _session
    if _session is None:
        _session = _build_session()
    return _session


# ─────────────────────────────────────────────
# RATE-LIMITED API CALL (exponential backoff)
# ─────────────────────────────────────────────

def api_get(endpoint: str, params: dict = None) -> Optional[dict]:
    """
    Make a GET request to the Upstox API with exponential backoff on 429.
    Returns parsed JSON on success, None on final failure (after max retries).
    """
    url = f"{config.UPSTOX_BASE_URL}{endpoint}"
    delay = config.RATE_LIMIT_BASE_DELAY

    for attempt in range(1, config.RATE_LIMIT_RETRIES + 2):  # +2 for initial + retries
        try:
            resp = get_session().get(url, params=params, timeout=config.HTTP_TIMEOUT)

            if resp.status_code == 200:
                return resp.json()

            elif resp.status_code == 429:
                # Rate limited — back off and retry
                wait = delay * (2 ** (attempt - 1))
                logger.warning("[429] Rate limited on %s — waiting %.0fs (attempt %d/%d)",
                               endpoint, wait, attempt, config.RATE_LIMIT_RETRIES + 1)
                time.sleep(wait)
                continue

            elif resp.status_code == 401:
                logger.error("[401] Unauthorized — check UPSTOX_ACCESS_TOKEN in config.py")
                _log_error(f"401 Unauthorized: {url}")
                return None

            elif resp.status_code == 404:
                logger.warning("[404] Not found: %s | params=%s", url, params)
                return None

            else:
                logger.error("[%d] Unexpected status for %s: %s",
                             resp.status_code, url, resp.text[:200])
                _log_error(f"HTTP {resp.status_code}: {url} → {resp.text[:200]}")
                return None

        except requests.exceptions.Timeout:
            logger.warning("Timeout on %s (attempt %d)", url, attempt)
            _log_error(f"Timeout: {url}")
            time.sleep(delay)

        except requests.exceptions.RequestException as e:
            logger.error("Request error on %s: %s", url, e)
            _log_error(f"RequestException: {url} → {e}")
            time.sleep(delay)

    logger.error("All retries exhausted for %s", url)
    _log_error(f"Exhausted retries: {url}")
    return None


def _log_error(message: str):
    """Append to errors.log with timestamp."""
    with open(config.ERRORS_LOG, "a") as f:
        f.write(f"[{datetime.now().isoformat()}] {message}\n")


# ─────────────────────────────────────────────
# INSTRUMENTS FILE
# ─────────────────────────────────────────────

def refresh_instruments_if_stale() -> bool:
    """
    Download the Upstox NSE instruments JSON if older than INSTRUMENTS_REFRESH_HRS.
    Returns True if file was refreshed, False if still fresh.
    """
    path = config.INSTRUMENTS_FILE
    if path.exists():
        age_hours = (time.time() - path.stat().st_mtime) / 3600
        if age_hours < config.INSTRUMENTS_REFRESH_HRS:
            logger.info("Instruments file is fresh (%.1f hrs old) — skipping download", age_hours)
            return False

    logger.info("Downloading NSE instruments file …")
    try:
        resp = requests.get(config.INSTRUMENTS_URL, timeout=60, stream=True)
        resp.raise_for_status()

        gz_path = path.with_suffix(".json.gz")
        with open(gz_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)

        # Decompress
        with gzip.open(gz_path, "rb") as gz_f, open(path, "wb") as out_f:
            out_f.write(gz_f.read())

        gz_path.unlink(missing_ok=True)
        logger.info("Instruments file saved to %s (%.1f MB)", path, path.stat().st_size / 1_000_000)
        return True

    except Exception as e:
        logger.error("Failed to download instruments file: %s", e)
        _log_error(f"Instruments download failed: {e}")
        return False


# ─────────────────────────────────────────────
# HISTORICAL OHLCV FETCH
# ─────────────────────────────────────────────

def fetch_historical_ohlcv(instrument_key: str, symbol: str,
                            from_date: str, to_date: str) -> list[dict]:
    """
    Fetch EOD historical candles via Upstox REST.
    Endpoint: GET /historical-candle/{instrument_key}/day/{to_date}/{from_date}
    """
    to_date   = to_date or datetime.today().strftime("%Y-%m-%d")
    from_date = from_date or (datetime.today() - timedelta(days=365)).strftime("%Y-%m-%d")
    
    encoded_key = urllib.parse.quote(instrument_key)
    endpoint = f"/historical-candle/{encoded_key}/day/{to_date}/{from_date}"

    data = api_get(endpoint)
    if not data or "data" not in data:
        logger.warning("No OHLCV data returned for %s (%s → %s)", symbol, from_date, to_date)
        return []

    candles = data["data"].get("candles", [])
    rows = []
    for candle in candles:
        # Upstox candle format: [timestamp, open, high, low, close, volume, oi]
        try:
            ts, o, h, l, c, v = candle[0], candle[1], candle[2], candle[3], candle[4], candle[5]
            candle_date = ts[:10]   # Extract YYYY-MM-DD from ISO timestamp
            rows.append({
                "symbol":       symbol,
                "date":         candle_date,
                "open":         float(o),
                "high":         float(h),
                "low":          float(l),
                "close":        float(c),
                "volume":       int(v),
                "delivery_pct": None,
            })
        except (IndexError, ValueError) as e:
            logger.warning("Bad candle format for %s: %s — %s", symbol, candle, e)
            continue

    return rows


def fetch_all_historical(stocks: list[dict], force: bool = False):
    """
    For each stock, fetch 1 year of EOD data if we have none (or force=True).
    Uses incremental logic: only fetches from the last known date.
    Adds a small delay between symbols to avoid rate limits.
    """
    to_date = date.today().isoformat()
    from_date_full = (date.today() - timedelta(days=config.HISTORICAL_DAYS)).isoformat()

    total = len(stocks)
    logger.info("Fetching EOD data for %d stocks …", total)

    for i, stock in enumerate(stocks, 1):
        symbol = stock["symbol"]
        instrument_key = stock["instrument_key"]

        if instrument_key == f"NSE_EQ|{symbol}":
            logger.debug("Placeholder key for %s — skipping", symbol)
            continue

        latest = db.get_latest_date(symbol)
        if latest and not force:
            # Incremental: only fetch from last known date
            from_date = (datetime.strptime(latest, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
            if from_date > to_date:
                logger.debug("%s is up to date (latest: %s)", symbol, latest)
                continue
        else:
            from_date = from_date_full

        logger.info("[%d/%d] Fetching %s from %s …", i, total, symbol, from_date)
        rows = fetch_historical_ohlcv(instrument_key, symbol, from_date, to_date)

        if rows:
            db.bulk_insert_ohlcv(rows)
            logger.debug("Inserted %d rows for %s", len(rows), symbol)
        else:
            logger.warning("No data for %s (check instrument key: %s)", symbol, instrument_key)

        # Polite delay: ~3 stocks/second to stay within Upstox rate limits
        time.sleep(0.35)

    logger.info("Historical OHLCV fetch complete.")


def fetch_incremental_update(stocks: list[dict]):
    """
    Fast incremental update: only fetch today's (or latest missing) candle.
    Used on subsequent EOD runs. Typically < 2 minutes for 200 stocks.
    """
    to_date = date.today().isoformat()
    for stock in stocks:
        symbol = stock["symbol"]
        instrument_key = stock["instrument_key"]
        
        if instrument_key == f"NSE_EQ|{symbol}":
            continue

        latest = db.get_latest_date(symbol)
        if latest == to_date:
            continue  # Already have today's data

        from_date = (
            (datetime.strptime(latest, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
            if latest else to_date
        )
        rows = fetch_historical_ohlcv(stock["instrument_key"], symbol, from_date, to_date)
        if rows:
            db.bulk_insert_ohlcv(rows)
        time.sleep(0.35)

    logger.info("Incremental OHLCV update complete.")


# ─────────────────────────────────────────────
# INDEX DATA (for market gate)
# ─────────────────────────────────────────────

def fetch_index_ohlcv(instrument_key: str, days: int = 50) -> list[dict]:
    """
    Fetch EOD candles for an index (Nifty 50, VIX, etc.)
    Returns list of {date, open, high, low, close, volume} dicts.
    """
    to_date = date.today().isoformat()
    from_date = (date.today() - timedelta(days=days)).isoformat()
    encoded_key = instrument_key.replace("|", "%7C")
    endpoint = f"/historical-candle/{encoded_key}/day/{to_date}/{from_date}"

    data = api_get(endpoint)
    if not data or "data" not in data:
        return []

    candles = data["data"].get("candles", [])
    rows = []
    for candle in candles:
        try:
            ts, o, h, l, c, v = candle[0], candle[1], candle[2], candle[3], candle[4], candle[5]
            rows.append({
                "date":   ts[:10],
                "open":   float(o),
                "high":   float(h),
                "low":    float(l),
                "close":  float(c),
                "volume": int(v),
            })
        except (IndexError, ValueError):
            continue
    return sorted(rows, key=lambda r: r["date"])


def fetch_ltp(instrument_keys: list[str]) -> dict[str, float]:
    """
    Fetch latest traded price for a batch of instrument keys.
    Returns {instrument_key: ltp} dict.
    Uses GET /market-quote/ltp
    """
    if not instrument_keys:
        return {}

    # Upstox accepts comma-separated instrument_key query param
    params = {"instrument_key": ",".join(instrument_keys)}
    data = api_get("/market-quote/ltp", params=params)

    if not data or "data" not in data:
        return {}

    result = {}
    for key, quote in data["data"].items():
        result[key] = float(quote.get("last_price", 0))
    return result


def fetch_full_quotes(instrument_keys: list[str]) -> dict[str, dict]:
    """
    Fetch full OHLCV snapshot for a batch of instrument keys.
    Returns {instrument_key: {open, high, low, close, volume, last_price}} dict.
    Uses GET /market-quote/quotes
    """
    if not instrument_keys:
        return {}

    params = {"instrument_key": ",".join(instrument_keys)}
    data = api_get("/market-quote/quotes", params=params)

    if not data or "data" not in data:
        return {}

    result = {}
    ohlc_data = data["data"]
    for key, quote in ohlc_data.items():
        ohlc = quote.get("ohlc", {})
        result[key] = {
            "open":       float(ohlc.get("open", 0)),
            "high":       float(ohlc.get("high", 0)),
            "low":        float(ohlc.get("low", 0)),
            "close":      float(ohlc.get("close", 0)),
            "volume":     int(quote.get("volume", 0)),
            "last_price": float(quote.get("last_price", 0)),
        }
    return result


# ─────────────────────────────────────────────
# NSE BHAVCOPY (delivery %, advance/decline)
# ─────────────────────────────────────────────

def fetch_nse_bhavcopy(target_date: str = None) -> dict[str, float]:
    """
    Download NSE equity bhavcopy CSV for a given date (default: today).
    Returns {symbol: delivery_pct} dict.
    NSE bhavcopy URL pattern: https://archives.nseindia.com/content/historical/EQUITIES/{YYYY}/{MON}/cm{DD}{MON}{YYYY}bhav.csv.zip
    """
    import io
    import zipfile
    import csv

    target = datetime.strptime(target_date, "%Y-%m-%d") if target_date else datetime.today()
    mon = target.strftime("%b").upper()   # e.g. FEB
    day = target.strftime("%d")            # e.g. 22
    year = target.strftime("%Y")           # e.g. 2026

    url = f"https://archives.nseindia.com/content/historical/EQUITIES/{year}/{mon}/cm{day}{mon}{year}bhav.csv.zip"

    try:
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://www.nseindia.com",
        }
        resp = requests.get(url, headers=headers, timeout=30)
        if resp.status_code != 200:
            logger.warning("NSE bhavcopy not available for %s (HTTP %d)", target_date, resp.status_code)
            return {}

        with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
            csv_name = z.namelist()[0]
            with z.open(csv_name) as csv_file:
                reader = csv.DictReader(io.TextIOWrapper(csv_file))
                result = {}
                for row in reader:
                    symbol = row.get("SYMBOL", "").strip()
                    try:
                        del_qty = float(row.get("DELIV_QTY", 0) or 0)
                        total_qty = float(row.get("TTL_TRD_QNTY", 0) or 0)
                        if total_qty > 0:
                            result[symbol] = round(del_qty / total_qty * 100, 2)
                    except (ValueError, ZeroDivisionError):
                        continue
                return result

    except Exception as e:
        logger.warning("Failed to fetch NSE bhavcopy: %s", e)
        return {}


def fetch_advance_decline() -> float:
    """
    Approximate advance/decline ratio from Nifty 200 current quotes.
    Returns ratio in [0, 1] where 1 = all advancing, 0 = all declining.
    """
    stocks = db.get_all_stocks()
    keys = [s["instrument_key"] for s in stocks if "NSE_EQ" in s.get("instrument_key", "")][:50]
    if not keys:
        return 0.5   # neutral fallback

    quotes = fetch_full_quotes(keys)
    advance = sum(1 for q in quotes.values() if q["close"] > q["open"])
    total = len(quotes)
    return round(advance / total, 3) if total > 0 else 0.5
