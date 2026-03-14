"""
CLI entry point for the backtesting system.

Usage examples:
    # Single run — default MA20/50, 12 months BTC/USD
    python run_backtest.py

    # MACD strategy, 24 months
    python run_backtest.py --strategy macd --months 24

    # EMA+RSI strategy
    python run_backtest.py --strategy ema_rsi

    # Load from local CSV instead of fetching
    python run_backtest.py --csv data/BTC_USD_1h_12m.csv

    # Parameter sweep — MACD fast/slow grid
    python run_backtest.py --strategy macd --sweep \\
        --fast 8 12 --slow 21 26 --signal-period 9

    # MA crossover sweep
    python run_backtest.py --strategy ma_crossover --sweep \\
        --short 10 20 50 --long 30 50 200

    # EMA+RSI sweep
    python run_backtest.py --strategy ema_rsi --sweep \\
        --ema-short 13 21 --ema-long 34 55 --rsi-period 14

    # Save outputs to directory
    python run_backtest.py --strategy macd --out-dir results/

    # Skip cache and re-fetch from exchange
    python run_backtest.py --no-cache
"""

import argparse
import os
import sys
from pathlib import Path

# Ensure project root is on path when run from any directory
sys.path.insert(0, str(Path(__file__).parent))

from backtest.strategies import STRATEGIES, DEFAULT_PARAMS
from backtest.engine import BacktestEngine
from backtest.sweep import run_parameter_sweep
from backtest.report import (
    print_summary,
    print_sweep_table,
    save_equity_curve_csv,
    save_trade_log_csv,
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Coinbase Algo Trading — Backtester",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # Data source
    p.add_argument("--pair", default="BTC/USD", help="Trading pair (default: BTC/USD)")
    p.add_argument("--timeframe", default="1h", help="OHLCV timeframe (default: 1h)")
    p.add_argument("--months", type=int, default=12, help="Months of history (default: 12)")
    p.add_argument("--csv", default=None, help="Path to local OHLCV CSV (skips exchange fetch)")
    p.add_argument("--no-cache", action="store_true", help="Re-fetch from exchange (ignore CSV cache)")

    # Strategy
    p.add_argument(
        "--strategy", default="ma_crossover",
        choices=list(STRATEGIES.keys()),
        help="Strategy to backtest (default: ma_crossover)",
    )

    # Sweep mode
    p.add_argument("--sweep", action="store_true", help="Run parameter sweep")

    # MA crossover params
    p.add_argument("--short", type=int, nargs="+", default=None,
                   help="MA short period(s) — single value or sweep list")
    p.add_argument("--long", type=int, nargs="+", default=None,
                   help="MA long period(s) — single value or sweep list")

    # MACD params
    p.add_argument("--fast", type=int, nargs="+", default=None,
                   help="MACD fast EMA period(s)")
    p.add_argument("--slow", type=int, nargs="+", default=None,
                   help="MACD slow EMA period(s)")
    p.add_argument("--signal-period", type=int, nargs="+", default=None,
                   help="MACD signal EMA period(s)")
    p.add_argument("--no-zero-filter", action="store_true",
                   help="Disable MACD zero-line filter")

    # EMA+RSI params
    p.add_argument("--ema-short", type=int, nargs="+", default=None,
                   help="EMA+RSI fast EMA period(s)")
    p.add_argument("--ema-long", type=int, nargs="+", default=None,
                   help="EMA+RSI slow EMA period(s)")
    p.add_argument("--rsi-period", type=int, nargs="+", default=None,
                   help="RSI period(s)")
    p.add_argument("--rsi-buy", type=float, default=None,
                   help="RSI buy threshold (default: 45)")
    p.add_argument("--rsi-sell", type=float, default=None,
                   help="RSI sell threshold (default: 55)")

    # Engine config
    p.add_argument("--initial", type=float, default=1000.0,
                   help="Initial equity USD (default: 1000)")
    p.add_argument("--max-position", type=float, default=200.0,
                   help="Max position USD (default: 200)")
    p.add_argument("--max-drawdown", type=float, default=25.0,
                   help="Max drawdown %% before PAUSED (default: 25)")

    # Output
    p.add_argument("--out-dir", default=None,
                   help="Directory to save CSV outputs (equity curve + trade log)")
    p.add_argument("--top", type=int, default=20,
                   help="Number of sweep results to show (default: 20)")

    return p.parse_args()


def _load_data(args):
    """Load OHLCV data from CSV or exchange."""
    if args.csv:
        from backtest.data_fetcher import load_ohlcv_from_csv
        print(f"[run_backtest] Loading data from: {args.csv}")
        return load_ohlcv_from_csv(args.csv)

    # Need exchange connection
    try:
        from bot.market_data import create_exchange
        from bot.config import load_config
        config = load_config()
        exchange = create_exchange(config)
    except Exception:
        # Fallback: create exchange without credentials (public data only)
        import ccxt
        exchange = ccxt.coinbaseadvanced({"enableRateLimit": True})

    from backtest.data_fetcher import fetch_historical_ohlcv
    return fetch_historical_ohlcv(
        exchange=exchange,
        pair=args.pair,
        timeframe=args.timeframe,
        months=args.months,
        no_cache=args.no_cache,
    )


def _build_params(args) -> dict:
    """Build strategy params dict from CLI args, filling defaults."""
    strategy = args.strategy
    params = dict(DEFAULT_PARAMS[strategy])  # start with defaults

    if strategy == "ma_crossover":
        if args.short and not args.sweep:
            params["short_period"] = args.short[0]
        if args.long and not args.sweep:
            params["long_period"] = args.long[0]

    elif strategy == "macd":
        if args.fast and not args.sweep:
            params["fast"] = args.fast[0]
        if args.slow and not args.sweep:
            params["slow"] = args.slow[0]
        if args.signal_period and not args.sweep:
            params["signal_period"] = args.signal_period[0]
        if args.no_zero_filter:
            params["zero_filter"] = False

    elif strategy == "ema_rsi":
        if args.ema_short and not args.sweep:
            params["ema_short"] = args.ema_short[0]
        if args.ema_long and not args.sweep:
            params["ema_long"] = args.ema_long[0]
        if args.rsi_period and not args.sweep:
            params["rsi_period"] = args.rsi_period[0]
        if args.rsi_buy is not None:
            params["rsi_buy_thresh"] = args.rsi_buy
        if args.rsi_sell is not None:
            params["rsi_sell_thresh"] = args.rsi_sell

    return params


def _build_param_grid(args) -> dict:
    """Build param_grid dict for sweep mode."""
    strategy = args.strategy
    grid = {}

    if strategy == "ma_crossover":
        if args.short:
            grid["short_period"] = args.short
        if args.long:
            grid["long_period"] = args.long
        # Default grid if none provided
        if not grid:
            grid = {"short_period": [10, 20, 50], "long_period": [30, 50, 200]}

    elif strategy == "macd":
        if args.fast:
            grid["fast"] = args.fast
        if args.slow:
            grid["slow"] = args.slow
        if args.signal_period:
            grid["signal_period"] = args.signal_period
        if not grid:
            grid = {"fast": [8, 12], "slow": [21, 26], "signal_period": [9]}

    elif strategy == "ema_rsi":
        if args.ema_short:
            grid["ema_short"] = args.ema_short
        if args.ema_long:
            grid["ema_long"] = args.ema_long
        if args.rsi_period:
            grid["rsi_period"] = args.rsi_period
        if not grid:
            grid = {"ema_short": [13, 21], "ema_long": [34, 55], "rsi_period": [14]}

    return grid


def _engine_kwargs(args) -> dict:
    return {
        "initial_equity": args.initial,
        "max_position_usd": args.max_position,
        "max_drawdown_percent": args.max_drawdown,
    }


def main() -> None:
    args = parse_args()

    # ── Load data ──────────────────────────────────────────────────────────────
    df = _load_data(args)
    print(f"[run_backtest] Loaded {len(df)} candles  "
          f"({df['timestamp'].iloc[0]} → {df['timestamp'].iloc[-1]})")

    strategy_fn = STRATEGIES[args.strategy]
    engine_kw = _engine_kwargs(args)

    # ── Sweep mode ─────────────────────────────────────────────────────────────
    if args.sweep:
        param_grid = _build_param_grid(args)
        print(f"[run_backtest] Sweep grid: {param_grid}")

        sweep_results = run_parameter_sweep(
            df=df,
            param_grid=param_grid,
            strategy_fn=strategy_fn,
            **engine_kw,
        )

        print_sweep_table(sweep_results, top_n=args.top)

        if args.out_dir and sweep_results:
            # Save best result's details
            best = sweep_results[0]
            out = Path(args.out_dir)
            save_equity_curve_csv(best.result, str(out / "sweep_best_equity.csv"))
            save_trade_log_csv(best.result, str(out / "sweep_best_trades.csv"))
            print(f"[run_backtest] Best params: {best.params}")
            print_summary(best.result, strategy_name=args.strategy)

        return

    # ── Single run ─────────────────────────────────────────────────────────────
    params = _build_params(args)
    print(f"[run_backtest] Strategy: {args.strategy}  Params: {params}")

    engine = BacktestEngine(df=df, strategy_fn=strategy_fn, params=params, **engine_kw)
    result = engine.run()

    print_summary(result, strategy_name=args.strategy)

    if args.out_dir:
        out = Path(args.out_dir)
        save_equity_curve_csv(result, str(out / "equity_curve.csv"))
        save_trade_log_csv(result, str(out / "trade_log.csv"))


if __name__ == "__main__":
    main()
