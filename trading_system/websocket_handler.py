"""
websocket_handler.py — Upstox WebSocket Client for Intraday Mode.

Subscribes to live market data for the filtered universe during trading hours.
On each 15-min candle close, triggers the scanner/validator/killer pipeline.

Upstox WebSocket: wss://api.upstox.com/v2/feed/market-data-feed
Subscribes to LTPC (Last Traded Price + Close) or Full mode.

Usage: called by main.py when --mode intraday is set.
"""

import json
import logging
import threading
import time
from datetime import date, datetime, time as dtime, timedelta
from typing import Callable, Optional

import config
import database as db

logger = logging.getLogger(__name__)

# Market hours (IST)
MARKET_OPEN  = dtime(9, 15)
MARKET_CLOSE = dtime(15, 30)

# 15-min candle accumulation per symbol
# { symbol: { "open": x, "high": x, "low": x, "close": x, "volume": x, "start_dt": dt } }
_candle_buffer: dict[str, dict] = {}
_candle_lock = threading.Lock()


class UpstoxWebSocketClient:
    """
    Connects to Upstox Market Feed WebSocket and accumulates 15-min candles.
    Calls on_candle_close(symbol, candle) whenever a 15-min bar closes.
    """

    WS_URL = "wss://api.upstox.com/v2/feed/market-data-feed"

    def __init__(self, instrument_keys: list[str], on_candle_close: Callable):
        self.instrument_keys = instrument_keys
        self.on_candle_close = on_candle_close
        self._ws = None
        self._running = False

    def start(self):
        """Start the WebSocket connection in a background thread."""
        try:
            import websocket  # pip install websocket-client
        except ImportError:
            logger.error("websocket-client not installed. Run: pip install websocket-client")
            return

        def on_open(ws):
            logger.info("[WS] Connected to Upstox Market Feed")
            # Subscribe to all instruments in FULL mode
            sub_payload = {
                "guid":   "intraday_feed",
                "method": "sub",
                "data": {
                    "mode":           "full",
                    "instrumentKeys": self.instrument_keys,
                }
            }
            ws.send(json.dumps(sub_payload))
            logger.info("[WS] Subscribed to %d instruments", len(self.instrument_keys))

        def on_message(ws, raw_message):
            try:
                # Upstox sends binary protobuf — decode if needed
                # For simplicity, we treat as JSON (works for text feed)
                msg = json.loads(raw_message)
                self._process_tick(msg)
            except Exception as e:
                logger.debug("[WS] Tick parse error: %s", e)

        def on_error(ws, error):
            logger.error("[WS] WebSocket error: %s", error)

        def on_close(ws, code, msg):
            logger.info("[WS] Connection closed (code=%s): %s", code, msg)
            self._running = False

        self._running = True
        self._ws = websocket.WebSocketApp(
            self.WS_URL,
            header={"Authorization": f"Bearer {config.UPSTOX_ACCESS_TOKEN}"},
            on_open=on_open,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
        )
        ws_thread = threading.Thread(target=self._ws.run_forever, daemon=True)
        ws_thread.start()
        logger.info("[WS] WebSocket thread started")

    def stop(self):
        self._running = False
        if self._ws:
            self._ws.close()

    def _process_tick(self, msg: dict):
        """
        Process an incoming tick message.
        Accumulate into 15-min candles; fire on_candle_close when bar closes.
        """
        feeds = msg.get("feeds", {})
        for instrument_key, feed in feeds.items():
            ltpc = feed.get("ltpc", {})
            if not ltpc:
                continue

            ltp = ltpc.get("ltp")
            cp  = ltpc.get("cp")    # close price (prev day)
            ts  = ltpc.get("ts")    # ISO timestamp
            if not ltp or not ts:
                continue

            # Resolve symbol from instrument_key
            symbol = instrument_key.split("|")[-1]

            now = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            now_ist = now + timedelta(hours=5, minutes=30)

            if now_ist.time() > MARKET_CLOSE:
                return   # Don't process after close

            # Compute which 15-min bar this tick belongs to
            bar_start = _floor_to_15min(now_ist)

            with _candle_lock:
                buf = _candle_buffer.get(symbol)
                if buf is None or buf["start_dt"] != bar_start:
                    # Previous bar has closed — fire callback
                    if buf is not None:
                        closed_candle = {**buf}
                        self.on_candle_close(symbol, closed_candle)
                        # Persist to DB
                        db.upsert_intraday(
                            symbol, buf["start_dt"].isoformat(),
                            buf["open"], buf["high"], buf["low"],
                            buf["close"], buf["volume"]
                        )

                    # Start new bar
                    _candle_buffer[symbol] = {
                        "start_dt": bar_start,
                        "open":     ltp,
                        "high":     ltp,
                        "low":      ltp,
                        "close":    ltp,
                        "volume":   0,
                    }
                else:
                    # Update running bar
                    buf["high"]  = max(buf["high"], ltp)
                    buf["low"]   = min(buf["low"],  ltp)
                    buf["close"] = ltp
                    # Volume not available per-tick via LTPC — use a counter
                    buf["volume"] += 1


def _floor_to_15min(dt: datetime) -> datetime:
    """Floor a datetime to the nearest 15-min interval."""
    m = (dt.minute // 15) * 15
    return dt.replace(minute=m, second=0, microsecond=0)


def run_intraday(filtered_stocks: list[dict], signal_callback: Callable):
    """
    Start the intraday WebSocket session.

    signal_callback(symbol, candle) is called whenever a 15-min bar closes.
    The caller (main.py) handles running the scanner/validator/killer on each close.
    """
    if not config.UPSTOX_ACCESS_TOKEN:
        logger.error("UPSTOX_ACCESS_TOKEN not set — cannot start WebSocket")
        return

    keys = [s["instrument_key"] for s in filtered_stocks if "NSE_EQ" in s.get("instrument_key", "")]
    logger.info("[WS] Starting intraday mode for %d instruments …", len(keys))

    client = UpstoxWebSocketClient(keys, signal_callback)
    client.start()

    # Keep alive until market close
    now = datetime.now().time()
    while now < MARKET_CLOSE:
        time.sleep(60)
        now = datetime.now().time()

    logger.info("[WS] Market closed — stopping WebSocket")
    client.stop()
