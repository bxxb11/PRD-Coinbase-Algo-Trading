# Coinbase Algo Trading Bot

A 24/7 automated trading bot for Coinbase Advanced Trade — backtest-validated, cloud-deployed, and built with stability as the first priority.

Three selectable strategies (MA Crossover / MACD / **EMA+RSI**), a full backtesting engine with parameter sweep, and one-command GCP deployment via systemd.

---

## Features

- **3 selectable strategies** — switch with a single env var; EMA+RSI is the backtest-validated default
- **Full backtesting system** — Sharpe, max drawdown, win rate, profit factor, parameter sweep
- **Scheduled or interval ticks** — run every N seconds, or at fixed clock times (e.g. 02:00 + 14:00 UTC)
- **Paper & live trading** — single `PAPER_TRADING` flag; safe default is `True`
- **Dynamic trade sizing** — $1–$20 per trade, scaled by MA spread strength
- **Hard risk limits** — $200 max position; 25% max drawdown auto-pauses the bot
- **Crash-safe state** — SQLite WAL mode; survives hard kills without data loss
- **Full audit trail** — append-only trade ledger, three rotating log streams
- **Zero duplicate orders** — unique `client_order_id` per order; safe to restart
- **GCP deployment** — one-command VM setup with systemd auto-restart on crash/reboot

---

## Project Goals

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
  └── bot/trading_loop.py      ← orchestrates every tick (interval or scheduled)
        ├── market_data.py     ← CCXT: fetch_ohlcv + fetch_ticker_price
        ├── strategy.py        ← MA Crossover / MACD / EMA+RSI + dispatch_strategy()
        ├── risk_manager.py    ← position cap + drawdown gate + PAUSED transition
        ├── execution.py       ← paper/live order router + dedup
        ├── state_manager.py   ← all SQLite reads/writes (WAL mode)
        ├── config.py          ← frozen Config dataclass, loads .env
        └── logger.py          ← system.log / trades.log / risk.log

backtest/
  ├── engine.py          ← vectorised BacktestEngine (O(n) precompute)
  ├── strategies.py      ← vectorised signal functions for all 3 strategies
  ├── metrics.py         ← Sharpe, drawdown, profit factor, round-trip pairing
  ├── sweep.py           ← Cartesian parameter grid sweep
  ├── data_fetcher.py    ← paginated OHLCV fetch + CSV cache
  └── report.py          ← console summary + CSV exports

state/
  └── trading.db         ← bot_state (single row) + trades (ledger)

logs/
  ├── system.log         ← all tick events
  ├── trades.log         ← order fills only
  └── risk.log           ← drawdown checks, PAUSED transitions

deploy/
  ├── setup_gcp.sh       ← one-command GCP VM provisioner
  ├── update.sh          ← pull latest code + restart bot
  └── DEPLOY.md          ← full deployment walkthrough
```

**Data flow per tick:**
```
fetch OHLCV → dispatch_strategy() → signal dedup → size trade
    → risk check → execute order (paper or live) → update SQLite state
```

---

## Strategies

### Backtest results (12-month BTC/USD, 8,760 × 1h bars, 1.2% round-trip fee)

| Strategy | Best Params | Profit Factor | Trades/yr | Verdict |
|---|---|---|---|---|
| **EMA+RSI** ← **WINNER** | EMA 13/55, RSI 21 | **1.12** | 27 | Only strategy > 1.0 |
| MA Crossover | 20/200 | 1.34 (high variance) | 64 | Fee drag at scale |
| MACD | 21/55/12 | 0.58 | 54 | Consistent loser |

**Why EMA+RSI wins:** 1.2% round-trip fee eliminates high-frequency strategies. EMA+RSI uses pullback entry (improving fill price ~0.5–1.5%), low trade frequency (27/yr minimises fee drag), and Fibonacci EMA periods (13/55) that align with BTC cycle structure.

### Strategy details

**EMA+RSI** (`STRATEGY=ema_rsi`) — recommended
- BUY: EMA13 > EMA55 for ≥3 consecutive bars AND RSI crosses up through 45
- SELL: EMA13 < EMA55 for ≥3 consecutive bars AND RSI crosses down through 55
- RSI uses Wilder smoothing: `ewm(alpha=1/period, adjust=False)`

**MACD** (`STRATEGY=macd`)
- BUY: MACD histogram crosses from negative to positive (and MACD line > 0 if zero_filter=True)
- SELL: histogram crosses from positive to negative

**MA Crossover** (`STRATEGY=ma_crossover`)
- BUY: MA20 crosses above MA50 (golden cross)
- SELL: MA20 crosses below MA50 (death cross)

### Dynamic trade sizing (all strategies)

Trade size scales with the MA20/50 spread — weak signal = smaller size:

```
spread < 0.1%  →  $1.00   (minimal confidence)
spread = 0.3%  →  $10.50  (moderate trend)
spread ≥ 0.5%  →  $20.00  (strong divergence)
```

---

## Backtesting

```bash
# Single strategy run (first run fetches ~36s from exchange, cached after)
python run_backtest.py --strategy ema_rsi --months 12

# Load from cached CSV (fast, no exchange needed)
python run_backtest.py --csv data/BTC_USD_1h_12m.csv --strategy ema_rsi

# Parameter sweep (finds best EMA/RSI combinations)
python run_backtest.py --strategy ema_rsi --sweep --ema-short 8 13 21 --ema-long 34 55 89

# Save equity curve + trade log to CSV files
python run_backtest.py --strategy ema_rsi --months 12 --out-dir results/
```

---

## Quickstart (local)

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

Edit `.env` — at minimum set credentials:

```env
PAPER_TRADING=True
COINBASE_API_KEY=your_key
COINBASE_API_SECRET=your_secret
STRATEGY=ema_rsi          # backtest winner
```

### Run

```bash
python main.py
```

Startup banner (paper mode, EMA+RSI, scheduled ticks off):
```
============================================================
Coinbase Algo Trading Bot — PAPER MODE
Pair:       BTC/USD
Strategy:   ema_rsi  |  Timeframe: 1h
Interval:   every 60s
Trade size: $1.0–$20.0 (spread-sized)
Max pos:    $200.0  |  Max drawdown: 25.0%
============================================================
```

Stop with `Ctrl+C` — shuts down cleanly and closes the database.

---

## GCP Deployment (24/7)

See **[deploy/DEPLOY.md](deploy/DEPLOY.md)** for the full step-by-step walkthrough.

Quick summary:
1. Create a GCP e2-micro VM (~$6/month, free tier eligible)
2. SSH in and run the one-line setup script
3. Fill in your API keys with `nano`
4. `sudo systemctl enable --now coinbase-bot`

The bot auto-restarts on crash and survives VM reboots via systemd.

---

## Configuration Reference

All settings live in `.env`. Copy `.env.example` to get started.

### Core

| Parameter | Default | Description |
|-----------|---------|-------------|
| `PAPER_TRADING` | `True` | `True` = simulate only. `False` = real orders. |
| `COINBASE_API_KEY` | — | Required when `PAPER_TRADING=False` |
| `COINBASE_API_SECRET` | — | Required when `PAPER_TRADING=False` |
| `TRADING_PAIR` | `BTC/USD` | Market to trade |
| `TIMEFRAME` | `1h` | OHLCV candle timeframe |
| `LOOP_INTERVAL_SECONDS` | `60` | Seconds between ticks (ignored if SCHEDULED_HOURS set) |
| `SCHEDULED_HOURS` | _(blank)_ | Comma-separated UTC hours to tick, e.g. `2,14` for 02:00 + 14:00 |

### Strategy

| Parameter | Default | Description |
|-----------|---------|-------------|
| `STRATEGY` | `ema_rsi` | Active strategy: `ma_crossover` / `macd` / `ema_rsi` |
| `EMA_SHORT` | `13` | Short EMA period (EMA+RSI only) |
| `EMA_LONG` | `55` | Long EMA period (EMA+RSI only) |
| `RSI_PERIOD` | `21` | RSI lookback period (EMA+RSI only) |
| `RSI_BUY_THRESH` | `45.0` | RSI level to cross up for BUY signal |
| `RSI_SELL_THRESH` | `55.0` | RSI level to cross down for SELL signal |
| `TREND_CONFIRM_BARS` | `3` | Consecutive bars EMA alignment required |
| `MACD_FAST` | `12` | MACD fast EMA period |
| `MACD_SLOW` | `26` | MACD slow EMA period |
| `MACD_SIGNAL_PERIOD` | `9` | MACD signal line period |
| `MACD_ZERO_FILTER` | `True` | Only trade when MACD line is on correct side of zero |
| `MA_SHORT_PERIOD` | `20` | Short MA window (MA crossover only) |
| `MA_LONG_PERIOD` | `50` | Long MA window (MA crossover only) |

### Sizing & Risk

| Parameter | Default | Description |
|-----------|---------|-------------|
| `MIN_TRADE_SIZE_USD` | `1.0` | Minimum trade size (weak signal) |
| `MAX_TRADE_SIZE_USD` | `20.0` | Maximum trade size (strong signal) |
| `SIZE_SPREAD_MIN_PCT` | `0.1` | MA spread % that maps to MIN size |
| `SIZE_SPREAD_MAX_PCT` | `0.5` | MA spread % that maps to MAX size |
| `MAX_POSITION_USD` | `200.0` | Hard cap on total open position |
| `MAX_DRAWDOWN_PERCENT` | `25.0` | Drawdown % that triggers PAUSED state |
| `INITIAL_EQUITY_USD` | `1000.0` | Starting equity (paper mode baseline) |

### Persistence & Logging

| Parameter | Default | Description |
|-----------|---------|-------------|
| `CANDLES_REQUIRED` | `100` | OHLCV history to fetch per tick |
| `DB_PATH` | `state/trading.db` | SQLite database path |
| `LOG_DIR` | `logs/` | Directory for log files |
| `LOG_LEVEL` | `INFO` | Logging verbosity (`DEBUG`, `INFO`, `WARNING`) |

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

When `PAUSED`, the loop keeps ticking but skips all order execution. Re-enable by setting `status = 'RUNNING'` in the database after reviewing the situation.

---

## Monitoring

### Local (Windows)
```powershell
Get-Content -Wait "logs\system.log"
```

### Local (macOS / Linux) or GCP VM
```bash
tail -f logs/system.log

# On GCP VM — systemd journal (richer output)
journalctl -u coinbase-bot -f
```

### Check bot state
```bash
sqlite3 state/trading.db "SELECT status, current_equity_usd, position_usd, last_signal FROM bot_state;"
```

### View trade history
```bash
sqlite3 state/trading.db \
  "SELECT side, size_usd, fill_price, mode, status, created_at FROM trades ORDER BY created_at DESC LIMIT 20;"
```

### Log files

| File | Contents |
|------|----------|
| `logs/system.log` | Every tick — HOLD, signal detected, order outcome, next tick time |
| `logs/trades.log` | Order fills only — side, size, price, fee, order ID |
| `logs/risk.log` | Drawdown checks, warnings, PAUSED transitions |

All logs rotate at 10 MB, keeping 5 backups.

---

## Roadmap

| Version | Status | Scope |
|---------|--------|-------|
| **v1** | ✅ Complete | Stable loop, MA crossover strategy, risk controls, paper + live trading |
| **v2** | ✅ Complete | Backtesting engine, MACD + EMA+RSI strategies, parameter sweep, GCP deployment, scheduled ticks |
| **v3** | Future | AI-generated strategies using Claude API |

---

## Disclaimer

> **This bot trades real money when `PAPER_TRADING=False`.** Cryptocurrency markets are highly volatile. Past strategy performance does not guarantee future results. The $200 position cap and 25% drawdown limit are safeguards, not guarantees against loss. Use at your own risk. Start with paper trading and small amounts.
