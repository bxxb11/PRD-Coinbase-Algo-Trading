"""
Output formatting for backtest results.
Console summary, sweep tables, CSV file export, and interactive HTML reports.
No business logic — presentation only.
"""

from __future__ import annotations

import csv
import json
from datetime import datetime
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


def save_html_report(
    result: BacktestResult,
    output_path: str,
    strategy_name: str = "",
) -> None:
    """
    Write a self-contained interactive HTML report to output_path.

    Contains:
      - Six summary metric cards (return, profit factor, Sharpe, drawdown, win rate, trades)
      - Interactive equity curve chart (Plotly.js via CDN)
      - Interactive drawdown chart
      - Full colour-coded trade log table
      - Run info footer (strategy, params, period, generated timestamp)

    No extra Python dependencies — Plotly.js is loaded from CDN.
    """
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    m = result.metrics
    params_str = _format_params(result.params)
    label = f"{strategy_name} {params_str}".strip()

    ts_start = _fmt_ts(result.equity_timestamps[0]) if result.equity_timestamps else "—"
    ts_end   = _fmt_ts(result.equity_timestamps[-1]) if result.equity_timestamps else "—"

    # ── Metric card values ───────────────────────────────────────────────────
    ret_pct    = m.total_return_pct
    ret_str    = f"{'+' if ret_pct >= 0 else ''}{ret_pct:.2f}%"
    ret_colour = "#27ae60" if ret_pct >= 0 else "#e74c3c"

    pf_val  = m.profit_factor
    pf_str  = "∞" if pf_val == float("inf") else f"{pf_val:.2f}"
    pf_colour = "#27ae60" if pf_val >= 1.0 else "#e74c3c"

    sharpe_str = f"{m.sharpe_ratio:.2f}" if m.sharpe_ratio is not None else "N/A"
    sharpe_colour = "#27ae60" if (m.sharpe_ratio or 0) >= 0 else "#e74c3c"

    dd_str    = f"-{m.max_drawdown_pct:.2f}%"
    wr_str    = f"{m.win_rate_pct:.1f}%"
    wr_colour = "#27ae60" if m.win_rate_pct >= 50 else "#e74c3c"

    # ── Equity curve data ────────────────────────────────────────────────────
    eq_ts  = [str(ts)[:19] for ts in result.equity_timestamps]
    eq_val = [round(v, 2) for v in result.equity_curve]

    # ── Drawdown series (running peak → trough %) ────────────────────────────
    dd_series: list[float] = []
    running_peak = float("-inf")
    for eq in result.equity_curve:
        running_peak = max(running_peak, eq)
        dd_series.append(round((eq - running_peak) / running_peak * 100, 4))

    # ── Trade table rows ─────────────────────────────────────────────────────
    trade_rows_html = ""
    for t in result.trades:
        row_class = "buy-row" if t.side == "BUY" else "sell-row"
        trade_rows_html += (
            f'<tr class="{row_class}">'
            f"<td>{str(t.timestamp)[:19]}</td>"
            f'<td><span class="badge {"badge-buy" if t.side == "BUY" else "badge-sell"}">'
            f"{t.side}</span></td>"
            f"<td>${t.size_usd:.2f}</td>"
            f"<td>${t.fill_price:,.2f}</td>"
            f"<td>${t.fee_usd:.2f}</td>"
            f"<td>${t.equity_after:,.2f}</td>"
            f"<td>${t.position_usd_after:.2f}</td>"
            f"</tr>\n"
        )

    if not trade_rows_html:
        trade_rows_html = '<tr><td colspan="7" style="text-align:center;color:#888;">No trades executed</td></tr>'

    generated_at = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    # ── Assemble HTML ────────────────────────────────────────────────────────
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Backtest Report — {label}</title>
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js" charset="utf-8"></script>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
          background: #f0f2f5; color: #1a1a2e; }}
  header {{ background: #1a1a2e; color: #fff; padding: 24px 32px; }}
  header h1 {{ font-size: 1.4rem; font-weight: 600; margin-bottom: 4px; }}
  header p  {{ font-size: 0.85rem; color: #aab; }}
  .container {{ max-width: 1200px; margin: 0 auto; padding: 24px 20px; }}

  /* ── Metric cards ── */
  .cards {{ display: grid; grid-template-columns: repeat(3, 1fr);
            gap: 16px; margin-bottom: 28px; }}
  @media (max-width: 700px) {{ .cards {{ grid-template-columns: repeat(2, 1fr); }} }}
  .card {{ background: #fff; border-radius: 12px; padding: 20px 24px;
           box-shadow: 0 2px 8px rgba(0,0,0,.08); }}
  .card .label {{ font-size: 0.78rem; color: #888; text-transform: uppercase;
                  letter-spacing: .06em; margin-bottom: 8px; }}
  .card .value {{ font-size: 1.9rem; font-weight: 700; }}

  /* ── Charts ── */
  .chart-box {{ background: #fff; border-radius: 12px;
                box-shadow: 0 2px 8px rgba(0,0,0,.08);
                padding: 20px; margin-bottom: 24px; }}
  .chart-box h2 {{ font-size: 1rem; font-weight: 600; margin-bottom: 12px;
                   color: #444; }}

  /* ── Trade table ── */
  .table-box {{ background: #fff; border-radius: 12px;
                box-shadow: 0 2px 8px rgba(0,0,0,.08);
                padding: 20px; margin-bottom: 24px; overflow-x: auto; }}
  .table-box h2 {{ font-size: 1rem; font-weight: 600; margin-bottom: 14px; color: #444; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 0.875rem; }}
  th {{ background: #f7f8fa; padding: 10px 12px; text-align: left;
        font-weight: 600; color: #555; border-bottom: 2px solid #e8e8e8; }}
  td {{ padding: 9px 12px; border-bottom: 1px solid #f0f0f0; }}
  tr:last-child td {{ border-bottom: none; }}
  .buy-row  {{ background: #f0faf4; }}
  .sell-row {{ background: #fff5f5; }}
  .badge {{ display: inline-block; padding: 2px 10px; border-radius: 20px;
             font-size: 0.78rem; font-weight: 700; }}
  .badge-buy  {{ background: #d4f0de; color: #1a7a3e; }}
  .badge-sell {{ background: #fddede; color: #b71c1c; }}

  /* ── Footer ── */
  footer {{ text-align: center; font-size: 0.78rem; color: #999;
            padding: 20px; margin-top: 8px; }}
</style>
</head>
<body>

<header>
  <h1>Backtest Report — {label}</h1>
  <p>BTC/USD &nbsp;·&nbsp; 1h bars &nbsp;·&nbsp; {ts_start} → {ts_end}</p>
</header>

<div class="container">

  <!-- Metric cards -->
  <div class="cards">
    <div class="card">
      <div class="label">Total Return</div>
      <div class="value" style="color:{ret_colour}">{ret_str}</div>
    </div>
    <div class="card">
      <div class="label">Profit Factor</div>
      <div class="value" style="color:{pf_colour}">{pf_str}</div>
    </div>
    <div class="card">
      <div class="label">Sharpe Ratio</div>
      <div class="value" style="color:{sharpe_colour}">{sharpe_str}</div>
    </div>
    <div class="card">
      <div class="label">Max Drawdown</div>
      <div class="value" style="color:#e74c3c">{dd_str}</div>
    </div>
    <div class="card">
      <div class="label">Win Rate</div>
      <div class="value" style="color:{wr_colour}">{wr_str}</div>
    </div>
    <div class="card">
      <div class="label">Total Trades</div>
      <div class="value" style="color:#2980b9">{m.total_trades}</div>
    </div>
  </div>

  <!-- Equity curve -->
  <div class="chart-box">
    <h2>Equity Curve</h2>
    <div id="equity-chart" style="height:360px;"></div>
  </div>

  <!-- Drawdown chart -->
  <div class="chart-box">
    <h2>Drawdown (%)</h2>
    <div id="dd-chart" style="height:240px;"></div>
  </div>

  <!-- Trade log -->
  <div class="table-box">
    <h2>Trade Log &nbsp;<span style="font-weight:400;color:#999;font-size:.85rem;">({m.total_trades} executed trades)</span></h2>
    <div style="max-height:420px;overflow-y:auto;">
      <table>
        <thead>
          <tr>
            <th>Date</th><th>Side</th><th>Size USD</th>
            <th>Fill Price</th><th>Fee</th><th>Equity After</th><th>Position</th>
          </tr>
        </thead>
        <tbody>
{trade_rows_html}        </tbody>
      </table>
    </div>
  </div>

</div><!-- /container -->

<footer>
  Strategy: <strong>{label}</strong> &nbsp;·&nbsp;
  Initial equity: <strong>${result.initial_equity:,.0f}</strong> &nbsp;·&nbsp;
  Final equity: <strong>${result.final_equity:,.2f}</strong> &nbsp;·&nbsp;
  Net PnL: <strong>${m.net_pnl_usd:+,.2f}</strong> &nbsp;·&nbsp;
  Fees paid: <strong>${m.total_fees_usd:,.2f}</strong><br>
  <span style="margin-top:6px;display:inline-block;">Generated {generated_at}</span>
</footer>

<script>
(function() {{
  var eqTs  = {json.dumps(eq_ts)};
  var eqVal = {json.dumps(eq_val)};
  var ddSeries = {json.dumps(dd_series)};
  var initEq = {result.initial_equity};

  // ── Equity curve ──────────────────────────────────────────────────────────
  Plotly.newPlot('equity-chart', [
    {{
      x: eqTs, y: eqVal,
      type: 'scatter', mode: 'lines',
      line: {{color: '#2980b9', width: 2}},
      name: 'Equity',
      hovertemplate: '%{{x}}<br><b>$%{{y:,.2f}}</b><extra></extra>'
    }},
    {{
      x: [eqTs[0], eqTs[eqTs.length-1]],
      y: [initEq, initEq],
      type: 'scatter', mode: 'lines',
      line: {{color: '#aaa', width: 1, dash: 'dash'}},
      name: 'Starting equity',
      hoverinfo: 'skip'
    }}
  ], {{
    margin: {{t: 10, r: 20, b: 40, l: 70}},
    xaxis: {{showgrid: false, zeroline: false}},
    yaxis: {{title: 'Equity (USD)', tickprefix: '$', gridcolor: '#f0f0f0'}},
    paper_bgcolor: 'white', plot_bgcolor: 'white',
    showlegend: true, legend: {{x: 0, y: 1}}
  }}, {{responsive: true, displayModeBar: false}});

  // ── Drawdown chart ────────────────────────────────────────────────────────
  Plotly.newPlot('dd-chart', [
    {{
      x: eqTs, y: ddSeries,
      type: 'scatter', mode: 'lines',
      fill: 'tozeroy',
      line: {{color: '#e74c3c', width: 1.5}},
      fillcolor: 'rgba(231,76,60,0.15)',
      name: 'Drawdown %',
      hovertemplate: '%{{x}}<br><b>%{{y:.2f}}%</b><extra></extra>'
    }}
  ], {{
    margin: {{t: 10, r: 20, b: 40, l: 60}},
    xaxis: {{showgrid: false, zeroline: false}},
    yaxis: {{title: 'Drawdown (%)', ticksuffix: '%', gridcolor: '#f0f0f0'}},
    paper_bgcolor: 'white', plot_bgcolor: 'white',
    showlegend: false
  }}, {{responsive: true, displayModeBar: false}});
}})();
</script>
</body>
</html>"""

    path.write_text(html, encoding="utf-8")
    print(f"[report] HTML report saved → {path}")


def _format_params(params: dict) -> str:
    """Compact param representation for display headers."""
    parts = []
    for k, v in params.items():
        short_k = k.replace("_period", "").replace("_", "")
        parts.append(f"{short_k}={v}")
    return "(" + ", ".join(parts) + ")" if parts else ""
