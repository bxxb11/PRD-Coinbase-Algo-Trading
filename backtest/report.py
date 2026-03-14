"""
Output formatting for backtest results.
Console summary, sweep tables, and CSV file export.
No business logic — presentation only.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Optional

import pandas as pd

from backtest.engine import BacktestResult
from backtest.metrics import BacktestMetrics


def print_summary(result: BacktestResult, strategy_name: str = "") -> None:
    """
    Print a formatted summary table to stdout.

    Example output:
    ╔══════════════════════════════════════════════════╗
    │  BACKTEST SUMMARY — MACD (fast=12, slow=26)     │
    │  BTC/USD 1h  |  2024-03-01 → 2025-03-01         │
    ╠══════════════════════════════════════════════════╣
    │  Total Return      +12.40%                       │
    │  Sharpe Ratio       0.8700                       │
    │  Max Drawdown      -8.30%                        │
    │  Win Rate          58.30%                        │
    │  Profit Factor      1.4200                       │
    │  Total Trades       24  (2.00/mo)                │
    │  Net PnL           +$124.00                      │
    │  Total Fees         $18.72                       │
    │  Final Equity    $1,124.00                       │
    │  Stopped Early?     No                           │
    ╚══════════════════════════════════════════════════╝
    """
    m = result.metrics
    params_str = _format_params(result.params)
    label = f"{strategy_name}  {params_str}".strip()

    ts_start = _fmt_ts(result.equity_timestamps[0]) if result.equity_timestamps else "—"
    ts_end = _fmt_ts(result.equity_timestamps[-1]) if result.equity_timestamps else "—"

    ret_sign = "+" if m.total_return_pct >= 0 else ""
    pnl_sign = "+" if m.net_pnl_usd >= 0 else ""

    sharpe_str = f"{m.sharpe_ratio:.4f}" if m.sharpe_ratio is not None else "N/A"
    pf_str = f"{m.profit_factor:.4f}" if m.profit_factor != float("inf") else "∞"
    stopped = f"YES — {result.stop_reason}" if result.stopped_early else "No"

    width = 52
    bar = "═" * width

    lines = [
        f"╔{bar}╗",
        f"│  {'BACKTEST SUMMARY':<{width - 2}}│",
        f"│  {label:<{width - 2}}│",
        f"│  {ts_start} → {ts_end:<{width - 4 - len(ts_start)}}│",
        f"╠{bar}╣",
        f"│  {'Total Return':<22}{ret_sign}{m.total_return_pct:.2f}%{'':<{width - 31}}│",
        f"│  {'Sharpe Ratio':<22}{sharpe_str:<{width - 24}}│",
        f"│  {'Max Drawdown':<22}-{m.max_drawdown_pct:.2f}%{'':<{width - 31}}│",
        f"│  {'Win Rate':<22}{m.win_rate_pct:.2f}%{'':<{width - 29}}│",
        f"│  {'Profit Factor':<22}{pf_str:<{width - 24}}│",
        f"│  {'Total Trades':<22}{m.total_trades}  ({m.trades_per_month:.2f}/mo){'':<{width - 36 - len(str(m.total_trades))}}│",
        f"│  {'Net PnL':<22}{pnl_sign}${m.net_pnl_usd:,.2f}{'':<{width - 28 - len(f'{m.net_pnl_usd:,.2f}')}}│",
        f"│  {'Total Fees':<22}${m.total_fees_usd:,.2f}{'':<{width - 27 - len(f'{m.total_fees_usd:,.2f}')}}│",
        f"│  {'Final Equity':<22}${result.final_equity:,.2f}{'':<{width - 27 - len(f'{result.final_equity:,.2f}')}}│",
        f"│  {'Stopped Early?':<22}{stopped:<{width - 24}}│",
        f"╚{bar}╝",
    ]

    print("\n".join(lines))


def print_sweep_table(sweep_results: list, top_n: int = 20) -> None:
    """
    Print the top_n sweep results as a formatted table.
    Uses pandas .to_string() for alignment.
    """
    from backtest.sweep import sweep_to_dataframe
    df = sweep_to_dataframe(sweep_results)
    if df.empty:
        print("[sweep] No results to display.")
        return

    df = df.head(top_n)
    print(f"\n{'─' * 80}")
    print(f"  SWEEP RESULTS (top {min(top_n, len(df))} of {len(sweep_results)})")
    print(f"{'─' * 80}")
    print(df.to_string(index=True, float_format="{:.2f}".format))
    print(f"{'─' * 80}\n")


def save_equity_curve_csv(result: BacktestResult, output_path: str) -> None:
    """
    Write equity curve to CSV.
    Columns: timestamp, equity_usd
    """
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    rows = [
        {"timestamp": str(ts), "equity_usd": eq}
        for ts, eq in zip(result.equity_timestamps, result.equity_curve)
    ]
    _write_csv(rows, str(path))
    print(f"[report] Equity curve saved → {path}  ({len(rows)} rows)")


def save_trade_log_csv(result: BacktestResult, output_path: str) -> None:
    """
    Write full trade log to CSV.
    Columns: bar_index, timestamp, side, size_usd, size_qty, fill_price,
             fee_usd, equity_after, position_usd_after
    """
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    rows = []
    for t in result.trades:
        rows.append({
            "bar_index":        t.bar_index,
            "timestamp":        str(t.timestamp),
            "side":             t.side,
            "size_usd":         t.size_usd,
            "size_qty":         t.size_qty,
            "fill_price":       t.fill_price,
            "fee_usd":          t.fee_usd,
            "equity_after":     t.equity_after,
            "position_usd_after": t.position_usd_after,
        })
    _write_csv(rows, str(path))
    print(f"[report] Trade log saved → {path}  ({len(rows)} trades)")


# ── Private helpers ───────────────────────────────────────────────────────────

def _write_csv(rows: list, path: str) -> None:
    if not rows:
        Path(path).write_text("")
        return
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def _fmt_ts(ts) -> str:
    try:
        return str(ts)[:10]
    except Exception:
        return str(ts)


def _format_params(params: dict) -> str:
    """Compact param representation for display headers."""
    parts = []
    for k, v in params.items():
        short_k = k.replace("_period", "").replace("_", "")
        parts.append(f"{short_k}={v}")
    return "(" + ", ".join(parts) + ")" if parts else ""
