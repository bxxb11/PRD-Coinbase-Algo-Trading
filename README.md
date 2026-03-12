# Coinbase Algo Trading Bot

A local automated trading bot for Coinbase Advanced Trade, built around an MA20/MA50 crossover strategy. Designed with **stability and risk control as the first priority** — the system prioritises not losing money over making money, especially in v1.

---

## Features

- **Paper & live trading** — single `PAPER_TRADING` flag; safe default is `True`
- **MA20/MA50 crossover strategy** on 1-hour candles (golden cross → BUY, death cross → SELL)
- **Dynamic trade sizing** — $1–$20 per trade, scaled by MA spread strength (weak signal = small size)
- **Hard risk limits** — $200 max position cap; 25% max drawdown auto-pauses the bot
- **Crash-safe state** — SQLite with WAL mode; survives hard kills without data loss
- **Full audit trail** — append-only trade ledger, three rotating log streams
- **Zero duplicate orders** — every order carries a unique `client_order_id`; restarts are safe

---

## Project Goals (from PRD)

| Goal | Target |
|------|--------|
| Continuous runtime | ≥ 24 hours without crash or trading error |
| Duplicate orders | 0 |
| Max open position | ≤ $200 |
| Max account loss | ≤ 25% of initial equity |
| Trade record completeness | 100% |

---

## Architecture

```
main.py
  └── trading_loop.py          ← orchestrates every tick (60-min interval)
        ├── market_data.py     ← CCXT: fetch_ohlcv + fetch_ticker_price
        ├── strategy.py        ← pure MA crossover signal + trade sizing
        ├── risk_manager.py    ← position cap + drawdown gate + PAUSED transition
        ├── execution.py       ← paper/live order router + dedup
        ├── state_manager.py   ← all SQLite reads/writes (WAL mode)
        ├── config.py          ← frozen Config dataclass, loads .env
        └── logger.py          ← system.log / trades.log / risk.log

state/
  └── trading.db              ← bot_state (single row) + trades (ledger)

logs/
  ├── system.log              ← all events, also stdout
  ├── trades.log              ← order fills only
  └── risk.log                ← drawdown checks, PAUSED transitions
```

**Data flow per tick:**
```
fetch OHLCV → compute MA20/MA50 → detect crossover → size trade
    → risk check → execute order (paper or live) → update state
```

---

## Strategy

### Signal generation

The bot fetches 100 hourly candles from Coinbase and computes two simple moving averages:

| Signal | Condition | Label |
|--------|-----------|-------|
| **BUY** | MA20 crosses **above** MA50 | Golden cross |
| **SELL** | MA20 crosses **below** MA50 | Death cross |
| **HOLD** | No new crossover | — |

Signals are deduplicated: if the last executed trade was already a BUY, a new BUY signal is ignored until a SELL crossover occurs first.

### Dynamic trade sizing

Trade size scales linearly with the MA spread (how far apart the two MAs are):

```
spread % = abs(MA20 - MA50) / MA50 × 100

spread < 0.1%  →  $1.00   (barely crossing — minimum confidence)
spread = 0.3%  →  $10.50  (moderate trend)
spread ≥ 0.5%  →  $20.00  (strong trend divergence — maximum size)
```

This means the bot risks less capital on weak signals and scales up only when the trend is well-established.

---

## Risk Management

```
Every proposed trade passes through three gates in order:
  1. Bot status      → PAUSED/STOPPED state blocks all trading
  2. Drawdown check  → if equity dropped ≥ 25% from peak, bot enters PAUSED
  3. Position cap    → BUY rejected if it would push position above $200
                       (SELL always allowed — exits are never blocked)
```

| Rule | Threshold | Action |
|------|-----------|--------|
| Position cap | > $200 | Trade `REJECTED` |
| Drawdown warning | ≥ 22.5% (90% of limit) | `WARNING` logged to risk.log |
| Max drawdown | ≥ 25% | Bot enters `PAUSED` — DB updated atomically |

When `PAUSED`, the bot continues running (loop keeps ticking) but skips all order execution. Re-enabling requires manually setting `status = 'RUNNING'` in the database after reviewing the situation.

---

## Quickstart

### Prerequisites
- Python 3.11+
- Coinbase Advanced Trade API key with **View** and **Trade** scopes

### Setup

```bash
git clone https://github.com/bxxb11/PRD-Coinbase-Algo-Trading.git
cd PRD-Coinbase-Algo-Trading
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` — at minimum set your API credentials and mode:

```env
PAPER_TRADING=True          # keep True until you're ready for real money
COINBASE_API_KEY=your_key
COINBASE_API_SECRET=your_secret
```

### Run

```bash
python main.py
```

The bot prints a startup banner and begins ticking immediately:

```
============================================================
Coinbase Algo Trading Bot — PAPER MODE
Pair:       BTC/USD
Timeframe:  1h  |  MA20/MA50
Trade size: $1.0–$20.0 (spread-sized)
Max pos:    $200.0  |  Max drawdown: 25.0%
============================================================
Tick #0: HOLD | MA20=70093.13 MA50=70183.04 | price=$69,423.80
```

Stop with `Ctrl+C` — shuts down cleanly and closes the database.

---

## Configuration Reference

All settings live in `.env`. Copy `.env.example` to get started.

| Parameter | Default | Description |
|-----------|---------|-------------|
| `PAPER_TRADING` | `True` | `True` = simulate only. `False` = real orders. |
| `COINBASE_API_KEY` | — | Required when `PAPER_TRADING=False` |
| `COINBASE_API_SECRET` | — | Required when `PAPER_TRADING=False` |
| `TRADING_PAIR` | `BTC/USD` | Market to trade |
| `TIMEFRAME` | `1h` | OHLCV candle timeframe |
| `MA_SHORT_PERIOD` | `20` | Short moving average window |
| `MA_LONG_PERIOD` | `50` | Long moving average window |
| `CANDLES_REQUIRED` | `100` | OHLCV history to fetch per tick |
| `LOOP_INTERVAL_SECONDS` | `3600` | Seconds between ticks (3600 = 1 hour) |
| `MIN_TRADE_SIZE_USD` | `1.0` | Minimum trade size (weak signal) |
| `MAX_TRADE_SIZE_USD` | `20.0` | Maximum trade size (strong signal) |
| `SIZE_SPREAD_MIN_PCT` | `0.1` | MA spread % that maps to `MIN_TRADE_SIZE_USD` |
| `SIZE_SPREAD_MAX_PCT` | `0.5` | MA spread % that maps to `MAX_TRADE_SIZE_USD` |
| `MAX_POSITION_USD` | `200.0` | Hard cap on total open position |
| `MAX_DRAWDOWN_PERCENT` | `25.0` | Drawdown % that triggers PAUSED state |
| `INITIAL_EQUITY_USD` | `1000.0` | Starting equity (paper mode baseline) |
| `DB_PATH` | `state/trading.db` | SQLite database path |
| `LOG_DIR` | `logs/` | Directory for log files |
| `LOG_LEVEL` | `INFO` | Logging level (`DEBUG`, `INFO`, `WARNING`) |

---

## Monitoring

### Watch logs live

```powershell
# Windows PowerShell
Get-Content -Wait "logs\system.log"
```

```bash
# macOS / Linux
tail -f logs/system.log
```

### Check bot state

```bash
sqlite3 state/trading.db "SELECT * FROM bot_state;"
```

### View trade history

```bash
sqlite3 state/trading.db "SELECT side, size_usd, fill_price, mode, status, created_at FROM trades ORDER BY created_at DESC LIMIT 20;"
```

### Log files

| File | Contents |
|------|----------|
| `logs/system.log` | Every tick result — HOLD, signal detected, order outcome |
| `logs/trades.log` | Order fills only — side, size, price, fee, order ID |
| `logs/risk.log` | Drawdown checks, warnings, PAUSED transitions |

All logs rotate at 10 MB, keeping 5 backups.

---

## Roadmap

| Version | Status | Scope |
|---------|--------|-------|
| **v1** | ✅ Complete | Stable loop, MA crossover strategy, risk controls, paper + live trading |
| **v2** | Planned | Backtesting on historical data — Sharpe ratio, max drawdown, win rate |
| **v3** | Future | AI-generated strategies using Claude API |

---

## Disclaimer

> **This bot trades real money when `PAPER_TRADING=False`.** Cryptocurrency markets are highly volatile. Past strategy performance does not guarantee future results. The $200 position cap and 25% drawdown limit are safeguards, not guarantees against loss. Use at your own risk. Start with paper trading and small amounts.
