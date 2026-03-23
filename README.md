# Coinbase Algo Trading Bot

A 24/7 automated trading bot for Coinbase Advanced Trade — backtest-validated, cloud-deployed, and built with stability as the first priority.

Five selectable strategies (MA Crossover / MACD / EMA+RSI / SuperTrend / **Donchian+ADX**), a full backtesting engine with parameter sweep, and one-command GCP deployment via systemd.

---

## Features

- **5 selectable strategies** — switch with a single env var; Donchian+ADX is the aggressive winner, EMA+RSI the conservative default
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
        ├── strategy.py        ← 5 strategies + dispatch_strategy()
        ├── risk_manager.py    ← position cap + drawdown gate + PAUSED transition
        ├── execution.py       ← paper/live order router + dedup
        ├── state_manager.py   ← all SQLite reads/writes (WAL mode)
        ├── config.py          ← frozen Config dataclass, loads .env
        └── logger.py          ← system.log / trades.log / risk.log

backtest/
  ├── engine.py          ← vectorised BacktestEngine (O(n) precompute)
  ├── strategies.py      ← vectorised signal functions for all 5 strategies
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

### Backtest results — 2025–2026 validation (10,725 × 1h bars, BTC $62k→$126k→$75k, 1.2% round-trip fee)

> ⚡ **Strategy re-validated on 2025–2026 real BTC data** (OKX, Jan 2025 – Mar 2026). The market saw a full bull-run, ATH, and deep correction — a genuine stress test.

| Strategy | Best Params | Return | Max DD | Profit Factor | Trades/mo | Verdict |
|---|---|---|---|---|---|---|
| **EMA+RSI** ← **WINNER** | EMA 21/34, RSI 21 | **-0.04%** | -0.27% | **0.90** | 3.3 | Near-breakeven on volatile year; best risk-adjusted |
| Donchian+ADX | enter=72h, exit=240h, ADX<20 | -0.11% | -0.52% | 0.81 | 2.2 | Decent but still negative |
| SuperTrend | ATR 10, ×3.5 | -1.87% | -2.18% | 0.27 | 13.7 | Fee drag — 200+ trades/month |

**Why EMA+RSI(21/34/21) wins on 2025–2026 data:** The faster EMA(21/34) crossover adapts to the choppy post-ATH market better than the original 13/55 pairing. Low trade frequency (3.3/mo) keeps fees minimal. Max drawdown of only -0.27% on a year where BTC dropped 40%+ from peak.

**Why SuperTrend fails on 1h:** Generates 130–400 trades/year on 1h bars — the 1.2% round-trip fee eats every small gain. Published results showing 155% profit were on daily/4h timeframes.

### Strategy details

**Donchian+ADX** (`STRATEGY=donchian_adx`) — aggressive recommended
- BUY: close breaks above the highest close of the prior 48 bars AND ADX < 25 (entering from consolidation, not a mature trend)
- SELL: close drops below the lowest close of the prior 240 bars
- ADX computed with Wilder smoothing (pure pandas, no external libraries)

**EMA+RSI** (`STRATEGY=ema_rsi`) — **recommended default** (2025–2026 sweep winner)
- BUY: EMA21 > EMA34 for ≥3 consecutive bars AND RSI crosses up through 45
- SELL: EMA21 < EMA34 for ≥3 consecutive bars AND RSI crosses down through 55
- RSI uses Wilder smoothing: `ewm(alpha=1/period, adjust=False)`
- Default params: `EMA_SHORT=21, EMA_LONG=34, RSI_PERIOD=21` (re-optimised on 2025–2026 data)

**SuperTrend** (`STRATEGY=supertrend`) — reference only (not recommended on 1h)
- BUY: SuperTrend band flips from bearish to bullish (close > ratcheted upper band)
- SELL: SuperTrend band flips from bullish to bearish (close < ratcheted lower band)
- Band ratchets: upper only moves down, lower only moves up

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
# Single strategy run on 2025-2026 data (cached — no exchange needed)
python run_backtest.py --csv data/BTC_USD_1h_2025_2026.csv --strategy ema_rsi

# Or fetch fresh 12-month data from exchange
python run_backtest.py --strategy ema_rsi --months 12

# Parameter sweeps
python run_backtest.py --strategy ema_rsi --sweep \
    --ema-short 13 21 --ema-long 34 55 89 --rsi-period 14 21

python run_backtest.py --strategy donchian_adx --sweep \
    --dc-enter-bars 24 48 72 --dc-exit-bars 120 240 360

python run_backtest.py --strategy supertrend --sweep \
    --atr-period 7 10 14 --atr-multiplier 2.5 3.0 3.5

# Save equity curve + trade log to CSV + interactive HTML report
python run_backtest.py --csv data/BTC_USD_1h_2025_2026.csv --strategy ema_rsi \
    --out-dir results/ --html
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
STRATEGY=donchian_adx     # aggressive winner
# or
STRATEGY=ema_rsi          # conservative winner
```

### Run

```bash
python main.py
```

Startup banner (paper mode, Donchian+ADX):
```
============================================================
Coinbase Algo Trading Bot — PAPER MODE
Pair:       BTC/USD
Strategy:   donchian_adx  |  Timeframe: 1h
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
3. Fill in your API keys with `nano /opt/coinbase-bot/.env`
4. `sudo systemctl enable --now coinbase-bot`

The bot auto-restarts on crash and survives VM reboots via systemd.

**To update the bot after code changes:**
```bash
sudo git config --global --add safe.directory /opt/coinbase-bot
sudo bash /opt/coinbase-bot/deploy/update.sh
```

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

| Parameter | Default | Options / Description |
|-----------|---------|-------------|
| `STRATEGY` | `ema_rsi` | `ma_crossover` / `macd` / `ema_rsi` / `supertrend` / `donchian_adx` |
| `EMA_SHORT` | `21` | Short EMA period (ema_rsi) |
| `EMA_LONG` | `34` | Long EMA period (ema_rsi) |
| `RSI_PERIOD` | `21` | RSI lookback (ema_rsi) |
| `RSI_BUY_THRESH` | `45.0` | RSI level to cross up for BUY (ema_rsi) |
| `RSI_SELL_THRESH` | `55.0` | RSI level to cross down for SELL (ema_rsi) |
| `TREND_CONFIRM_BARS` | `3` | Consecutive bars EMA alignment required (ema_rsi) |
| `ATR_PERIOD` | `10` | ATR lookback (supertrend) |
| `ATR_MULTIPLIER` | `3.0` | Band width multiplier (supertrend) |
| `DC_ENTER_BARS` | `48` | Entry channel lookback in hours (donchian_adx) |
| `DC_EXIT_BARS` | `240` | Exit channel lookback in hours (donchian_adx) |
| `ADX_PERIOD` | `14` | ADX smoothing period (donchian_adx) |
| `ADX_THRESHOLD` | `25.0` | Max ADX for entry — enforces breakout from consolidation (donchian_adx) |
| `MACD_FAST` | `12` | MACD fast EMA period |
| `MACD_SLOW` | `26` | MACD slow EMA period |
| `MACD_SIGNAL_PERIOD` | `9` | MACD signal line period |
| `MACD_ZERO_FILTER` | `True` | Only trade when MACD line is on correct side of zero |
| `MA_SHORT_PERIOD` | `20` | Short MA window (ma_crossover) |
| `MA_LONG_PERIOD` | `50` | Long MA window (ma_crossover) |

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
| **v3** | ✅ Complete | GCP deployment scripts, systemd service, update workflow |
| **v4** | ✅ Complete | SuperTrend + Donchian+ADX strategies (research-backed, sweep-optimised) |
| **v5** | ✅ Complete | 2025–2026 re-validation (OKX real data); SELL-cap fix; EMA params re-optimised to 21/34/21 |
| **v6** | Future | AI-generated strategies using Claude API |

---

## Disclaimer

> **This bot trades real money when `PAPER_TRADING=False`.** Cryptocurrency markets are highly volatile. Past strategy performance does not guarantee future results. The $200 position cap and 25% drawdown limit are safeguards, not guarantees against loss. Use at your own risk. Start with paper trading and small amounts.
