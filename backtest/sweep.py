"""
Parameter sweep runner.

Runs a Cartesian product of parameter combinations through BacktestEngine
and returns results sorted by total_return_pct descending.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import product
from typing import Callable, Any, Optional

import pandas as pd

from backtest.engine import BacktestEngine, BacktestResult
from backtest.metrics import BacktestMetrics
from backtest.strategies import ma_crossover_signals


@dataclass
class SweepResult:
    params: dict
    metrics: BacktestMetrics
    result: BacktestResult      # full result for drill-down


def run_parameter_sweep(
    df: pd.DataFrame,
    param_grid: dict,
    strategy_fn: Callable = ma_crossover_signals,
    initial_equity: float = 1000.0,
    max_position_usd: float = 200.0,
    max_drawdown_percent: float = 25.0,
    min_trade_size_usd: float = 1.0,
    max_trade_size_usd: float = 20.0,
    size_spread_min_pct: float = 0.1,
    size_spread_max_pct: float = 0.5,
) -> list:
    """
    Run BacktestEngine for every combination in param_grid.

    Args:
        df: Historical OHLCV DataFrame.
        param_grid: Dict mapping param names to lists of values to try.
                    e.g. {"fast": [8, 12], "slow": [21, 26], "signal_period": [9]}
                    Produces 2 × 2 × 1 = 4 combinations.
        strategy_fn: Strategy function to test (default: ma_crossover_strategy).
        **engine_defaults: Passed through to BacktestEngine constructor.

    Returns:
        List of SweepResult sorted by total_return_pct descending.
        Invalid combinations (e.g. short_period >= long_period) are silently skipped.
    """
    keys = list(param_grid.keys())
    values = list(param_grid.values())
    results: list[SweepResult] = []

    combos = list(product(*values))
    print(f"[sweep] Running {len(combos)} combinations...")

    for combo in combos:
        params = dict(zip(keys, combo))

        # Skip invalid MA combinations
        if "short_period" in params and "long_period" in params:
            if params["short_period"] >= params["long_period"]:
                continue
        if "fast" in params and "slow" in params:
            if params["fast"] >= params["slow"]:
                continue
        if "ema_short" in params and "ema_long" in params:
            if params["ema_short"] >= params["ema_long"]:
                continue

        try:
            engine = BacktestEngine(
                df=df,
                strategy_fn=strategy_fn,
                params=params,
                initial_equity=initial_equity,
                max_position_usd=max_position_usd,
                max_drawdown_percent=max_drawdown_percent,
                min_trade_size_usd=min_trade_size_usd,
                max_trade_size_usd=max_trade_size_usd,
                size_spread_min_pct=size_spread_min_pct,
                size_spread_max_pct=size_spread_max_pct,
            )
            result = engine.run()
            results.append(SweepResult(params=params, metrics=result.metrics, result=result))
        except Exception as exc:
            print(f"[sweep] Skipping {params}: {exc}")
            continue

    results.sort(key=lambda r: r.metrics.total_return_pct, reverse=True)
    print(f"[sweep] Done. {len(results)} valid results.")
    return results


def sweep_to_dataframe(sweep_results: list) -> pd.DataFrame:
    """
    Convert sweep results to a pandas DataFrame for display.
    Columns: all param keys + key BacktestMetrics fields.
    Sorted by total_return_pct descending.
    """
    if not sweep_results:
        return pd.DataFrame()

    rows = []
    for sr in sweep_results:
        row = dict(sr.params)
        m = sr.metrics
        row.update({
            "return_%":        m.total_return_pct,
            "sharpe":          m.sharpe_ratio,
            "max_dd_%":        m.max_drawdown_pct,
            "win_rate_%":      m.win_rate_pct,
            "profit_factor":   m.profit_factor,
            "total_trades":    m.total_trades,
            "trades/mo":       m.trades_per_month,
            "net_pnl_$":       m.net_pnl_usd,
            "fees_$":          m.total_fees_usd,
            "stopped_early":   sr.result.stopped_early,
        })
        rows.append(row)

    return pd.DataFrame(rows)
