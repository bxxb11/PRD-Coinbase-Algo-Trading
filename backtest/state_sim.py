"""
In-memory state simulation for backtesting.

Mirrors the exact schema of the bot_state SQLite table and the
logic of execution.py → _apply_state_updates(). All helpers are
immutable — they return a NEW dict rather than mutating the input.
"""

from datetime import datetime, timezone


def make_initial_state(initial_equity_usd: float) -> dict:
    """
    Create a fresh state dict with the same fields as bot_state (id=1 row).
    """
    now = datetime.now(timezone.utc).isoformat()
    return {
        "status": "RUNNING",
        "last_signal": "NONE",
        "position_usd": 0.0,
        "position_qty": 0.0,
        "initial_equity_usd": round(initial_equity_usd, 2),
        "current_equity_usd": round(initial_equity_usd, 2),
        "peak_equity_usd": round(initial_equity_usd, 2),
        "total_trades": 0,
        "last_updated_at": now,
    }


def apply_buy(state: dict, size_usd: float, size_qty: float, fee_rate: float = 0.006) -> dict:
    """
    Return a new state dict after a BUY fill.
    Deducts fee from equity; adds to position.
    """
    fee_usd = round(size_usd * fee_rate, 2)
    new_state = dict(state)
    new_state["position_usd"] = round(state["position_usd"] + size_usd, 2)
    new_state["position_qty"] = round(state["position_qty"] + size_qty, 8)
    new_state["current_equity_usd"] = round(state["current_equity_usd"] - fee_usd, 2)
    new_state["total_trades"] = state["total_trades"] + 1
    new_state["last_signal"] = "BUY"
    new_state["last_updated_at"] = datetime.now(timezone.utc).isoformat()
    return new_state


def apply_sell(state: dict, size_usd: float, size_qty: float, fee_rate: float = 0.006) -> dict:
    """
    Return a new state dict after a SELL fill.
    Deducts fee from equity; reduces position (floored at 0).
    """
    fee_usd = round(size_usd * fee_rate, 2)
    new_state = dict(state)
    new_state["position_usd"] = round(max(0.0, state["position_usd"] - size_usd), 2)
    new_state["position_qty"] = round(max(0.0, state["position_qty"] - size_qty), 8)
    new_state["current_equity_usd"] = round(state["current_equity_usd"] - fee_usd, 2)
    new_state["total_trades"] = state["total_trades"] + 1
    new_state["last_signal"] = "SELL"
    new_state["last_updated_at"] = datetime.now(timezone.utc).isoformat()
    return new_state


def update_peak_equity(state: dict) -> dict:
    """
    If current_equity_usd > peak_equity_usd, update peak. Returns new dict.
    """
    if state["current_equity_usd"] > state["peak_equity_usd"]:
        new_state = dict(state)
        new_state["peak_equity_usd"] = state["current_equity_usd"]
        return new_state
    return state


def apply_paused(state: dict, reason: str = "") -> dict:
    """Set status to PAUSED. Returns new dict."""
    new_state = dict(state)
    new_state["status"] = "PAUSED"
    new_state["last_updated_at"] = datetime.now(timezone.utc).isoformat()
    return new_state
