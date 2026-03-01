# Nifty 200 Swing Trading & Intraday Options System

Welcome to the **Nifty 200 Swing Trading System**. This repository contains a fully automated algorithm to screen, analyze, and trade stocks from the Nifty 200 universe, primarily focusing on **Compression, Expansion, and Acceptance (CEA)** patterns. It includes End-of-Day (EOD) swing trading alerts (Long/Short) and real-time Intraday Put Option (PE) alerts for market hedging and short-side momentum.

## What is this repository about?

This system acts as an "always-on" quantitative analyst. It handles:
- **Data Acquisition**: Connects to the Upstox API to fetch historical (OHLCV) and real-time market data.
- **Universe Filtering**: Narrows down the 200 NSE stocks into highly liquid and tradeable candidates.
- **Pattern Recognition**: Automatically scans for compression (consolidation) and trade breakouts (expansion).
- **AI Sentiment Analysis**: Optionally uses Large Language Models (Claude / Gemini) to digest market context and score trade setups.
- **Options Engine**: Generates real-time PE Option buying alerts when intraday structural breakdowns occur.
- **Real-time Notifications**: Broadcasts EOD reports and live Intraday alerts via Telegram.

## Key Features

1. **Market Gate**: Prevents trading in adverse market conditions by parsing the `India VIX`, Put-Call Ratio (PCR), and Nifty 50 moving averages.
2. **CEA Strategy**: Pure price-action algorithmic scanner detecting low-risk, high-reward setups. 
3. **Intraday PE Runner**: Real-time WebSocket monitoring to find immediate short-covering/breakdown opportunities in Put Options. 
4. **Automated Risk Management**: Built-in ATR (Average True Range) calculations strictly define Entry Zones and Stop Losses.
5. **Backtesting Engine**: Capability to run historical performance evaluations of the CEA logic.

## Documentation

For a comprehensive guide on how the system is put together, how the trading logic operates, and how you can tweak it to match your own risk appetite, please refer to the documentation files:

- **[ARCHITECTURE.md](./ARCHITECTURE.md)**: Detailed breakdown of the trading logic, algorithms, options handling, and instructions to fine-tune the parameters.
- **[SETUP.md](./SETUP.md)**: Step-by-step instructions to install, configure, and launch the trading system on your local machine or server.

## Pipeline Overview

The entry point of the application is `trading_system/main.py`, which supports multiple modes:
- **EOD Mode (`--mode eod`)**: Run after market close to generate swing trade candidates for the next day.
- **Intraday Mode (`--mode intraday`)**: Run during active market hours to receive live PE (Put Option) trade signals via WebSockets.
- **Backtest Mode (`--mode backtest`)**: Simulate the algorithms over past historical data to evaluate potential profitability.

*Note: This system provides trading signals and analysis. It does not automatically execute orders on your brokerage account. Always perform your own due diligence before risking real capital.*
