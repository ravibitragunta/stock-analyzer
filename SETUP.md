# Setup and Installation Guide

Follow these steps to configure, install, and run the Nifty 200 Swing Trading System.

## Prerequisites

1. **Python 3.10+**: Ensure you have Python installed. Operating system can be Linux, macOS, or Windows (WSL recommended).
2. **Upstox API Account**: You need an active Upstox trading account with Developer API access enabled to fetch market data.
3. **Telegram Account**: To receive automated alerts, you require a Telegram bot token.

---

## 1. Installation

Open your terminal and clone the repository (if you haven't already):
```bash
git clone git@github.com:ravibitragunta/stock-analyzer.git
cd stock-analyzer
```

Create a virtual environment and activate it:
```bash
python3 -m venv venv
source venv/bin/activate
# Windows users: venv\Scripts\activate
```

Install the dependencies:
```bash
cd trading_system
pip install -r requirements.txt
```

---

## 2. Environment Configuration

The system requires several API keys to function. While a few fallbacks exist inside `config.py`, it is highly recommended to set these up via your OS Environment Variables.

### Upstox API
1. Create an API App on the Upstox Developer Portal.
2. Provide a redirect URI (e.g., `https://127.0.0.1:5000/callback`).
3. Obtain your `API_KEY` and `API_SECRET`.

### Telegram Alerts (Optional)
1. Open Telegram and search for `@BotFather`.
2. Create a new bot and copy the `BOT_TOKEN`.
3. Create a public/private channel and add your bot as an admin.
4. Retrieve the `CHAT_ID` (Using roughly the ID format `-100XXXXXXXXXX`).

### Setting Environment Variables (Linux/Mac)
You can export these directly in your terminal before running the script:
```bash
export UPSTOX_API_KEY="your-api-key"
export TELEGRAM_BOT_TOKEN="your-telegram-bot-token"
export TELEGRAM_CHAT_ID="your-chat-id"

# Optional AI Features:
export CLAUDE_API_KEY="your-anthropic-key"
export GEMINI_API_KEY="your-google-key"
```

---

## 3. Account Authentication (Upstox)

Before running the main pipelines, you must authenticate to obtain a daily Access Token.
```bash
python auth.py
```
This script will:
1. Generate an Upstox login URL.
2. Ask you to open it in your browser and log in with your credentials/OTP.
3. Give you a `code` in the redirect URL which you paste back into the terminal.
4. Automatically save the token for the day.

*You must do this once a day before running the intraday or EOD scripts.*

---

## 4. Running the System

### Initial Run
The very first time you run the system, it needs to populate the SQLite database and fetch 1 year of historical data for 200 stocks. **This may take 10-15 minutes.**
```bash
python main.py
```

### End-Of-Day (EOD) Mode
Run this every day after 3:30 PM (e.g., via a Cron Job at 9:30 PM). It will perform incremental data updates (takes <2 mins) and generate the HTML report + Telegram swing signals.
```bash
python main.py --mode eod
```

### Intraday Mode (WebSocket / PE Options)
Run this during live market hours (10:30 AM – 2:00 PM). It subscribes to a live tick stream and will alert your Telegram channel instantaneously if an options breakdown occurs.
```bash
python main.py --mode intraday
```

### Backtest Mode
Check historical performance over a specified time window.
```bash
python main.py --mode backtest --start 2024-01-01 --end 2024-12-31
```

---

## 5. Automation (Cron Job)

To fully automate the EOD run every night at 9:30 PM on Linux/Mac, edit your crontab (`crontab -e`):

```bash
30 21 * * 1-5 cd /home/ravi/Documents/work/stock-scraper/trading_system && /home/ravi/Documents/work/stock-scraper/venv/bin/python main.py --mode eod >> cron_log.txt 2>&1
```
*(Ensure you map the path to your exact python binary inside your `venv`)*
