# Architecture and Trading Logic

This document explains the internal mechanics of the trading system, how the individual modules interact, the core trading logic, and how developers/traders can fine-tune the parameters.

---

## 1. System Flow & Architecture

The system operates in two main modes:

### EOD Pipeline (End of Day)
Runs after market hours (e.g., 9:30 PM).
1. **Data Fetching**: Updates historical daily candles for all Nifty 200 stocks via the Upstox API.
2. **Market Gate**: Checks macro conditions (`India VIX`, PCR, Advance/Decline ratio). Blocks Long or Short trades if the broader market is deemed unfavorable.
3. **Universe Filter**: Removes penny stocks or highly illiquid stocks.
4. **Scanner (Compression)**: Finds stocks that are accumulating in a tight range with dropping volume.
5. **Validator (Expansion & Acceptance)**: Checks previously compressed stocks to see if they "broke out" today with high volume. Validates if older breakouts are still holding.
6. **Killer**: Auto-invalidates past signals if they hit stop loss or have expired.
7. **AI Sentiment** (Optional): Feeds the signals to Claude/Gemini to get a fundamental/news perspective score.
8. **Options Module**: Suggests optimal Put Options (PE) to buy for any valid SHORT signals.
9. **Reporter**: Publishes an HTML report and sends JSON data/Telegram messages.

### Intraday Pipeline
Runs during active market hours (10:30 AM to 2:00 PM).
1. **WebSocket Client**: Subscribes to live feed for stocks that were identified as SHORT candidates the previous night.
2. **Option Chain Poller**: Every 3 minutes, hits the NSE to get real-time OI (Open Interest), PCR, and IV (Implied Volatility).
3. **Trigger**: If the underlying price breaks its 15-min 20-EMA, VIX is favorable, and Option OI is rising, it triggers a live "Buy PE" alert via Telegram.

---

## 2. Core Logic Explained

### CEA (Compression, Expansion, Acceptance) Strategy

Used primarily for Stock selection (Swing Trading).

**1. Compression (The Setup)**
We look for a "spring coiling."
- **Price Range**: Moving in a very tight band (e.g., max 6% difference between highs and lows over 10 days).
- **Volatility Drop**: The Average True Range (ATR) must be at its lowest 60th percentile over the last 6 months. Bollinger Bands must be narrow.
- **Volume**: On-Balance Volume (OBV) trend must be flat or declining.

**2. Expansion (The Breakout)**
The spring releases.
- **Price Action**: A single day's candle range must be 2x the recent average range.
- **Volume Action**: Volume must be 1.5x the 20-day average. 
- **Direction**: For a Long, it must close near the top 25% of the candle and break out of the compression band.

**3. Acceptance (The Confirmation)**
We don't buy the breakout immediately. We wait 1-2 days.
- **Retracement**: Pullback must not exceed 40% of the expansion candle body.
- **EMA Hold**: Price must strictly hold above the 20-EMA (for longs).
- **Volume Fade**: The pullback days must be low volume.

### Options Logic (PE Selection)

When a stock gives a SHORT signal, the system recommends a Put Option (PE) instead of shorting the equity directly.

1. **Strike Selection**: Picks the nearest Out-Of-The-Money (OTM) strike. 
2. **Delta Rule**: Targets a Delta of -0.30 to -0.50 (It shouldn't be too far OTM where it won't move, and not ITM which is too expensive).
3. **Liquidity Check**: Ignores strikes with a premium under ₹5.
4. **Expiry Check**: Always uses the current month's expiry unless there's less than 7 days left, at which point it defaults to next month's expiry.
5. **Intraday Criteria**:
   - VIX must be between 13.0 and 22.0.
   - Strike Open Interest (OI) must be up at least 10% on the day.

---

## 3. Fine-tuning the Logic (For Beginners)

All trading configurations are strictly handled in `trading_system/config.py`. **Never edit logic inside the python files directly if you want to change parameters.** 

### A) Adjusting the Stock Universe Constraints
If you want to trade smaller cap stocks or want stricter liquidity, look here:
```python
MIN_TRADED_VALUE_CR = 75   # Minimum Daily Volume turnover in Crores
MIN_PRICE = 150            # Reject penny stocks below this price
```

### B) Making the Swing Strategy More Aggressive / Conservative
If you want **more signals** (Aggressive):
1. **Reduce Compression strictness:**
   ```python
   MAX_CANDLE_RANGE_PCT = 5.0    # Was 4.5. Allows wider candles.
   COMPRESSION_MIN_DAYS = 3      # Was 4. Faster trigger.
   ```
2. **Reduce Volume requirements:**
   ```python
   EXPANSION_VOL_MULT = 1.2      # Was 1.5. Breakout needs less volume to qualify.
   ```

If you want **higher accuracy but fewer signals** (Conservative):
1. **Tighten market gates:**
   ```python
   VIX_BLOCK_THRESHOLD = 18.0    # Stop trading longs entirely if VIX crosses 18.
   ```
2. **Require stronger breakouts:**
   ```python
   EXPANSION_RANGE_MULT = 2.5    # Breakout candle must be 2.5x bigger than usual.
   ```

### C) Fine-Tuning Option (PE) Alerts
Do you want options to yield higher potential rewards but with more risk?
1. **Change Delta (Moneyness):**
   ```python
   # To buy significantly cheaper, further OTM Puts:
   DELTA_MIN = 0.15
   DELTA_MAX = 0.30
   ```
2. **Adjust VIX Gates:**
   If you aren't getting Intraday PE alerts because the market is too quiet, you can relax the VIX check:
   ```python
   PE_VIX_MIN = 11.0             # Allow PE buying even in low volatility
   ```
3. **Adjust Profit Targets:**
   ```python
   TARGET_OPTION_GAIN_PCT = 30.0 # Wait for a 30% jump in Premium instead of 20%
   ```

### D) Turning off AI Sentiment
If you don't want to pay API costs for Claude/Gemini, disable it easily:
```python
AI_SENTIMENT_ENABLED = False     # Automatically falls back to pure math/price-action logic.
```
