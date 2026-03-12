"""
execution.py — place or simulate trade orders.

Paper mode: simulate fills at current price, no CCXT calls.
Live mode:  submit CCXT market orders with client_order_id for deduplication.

Both paths record the trade via state_manager and update bot_state.
"""

import sqlite3
import uuid

import ccxt

from bot.logger import get_system_logger, get_trade_logger
from bot import state_manager

# Coinbase Advanced Trade taker fee (0.6%)
_TAKER_FEE_RATE = 0.006


class OrderExecutionError(Exception):
    pass


def _build_client_order_id(mode: str) -> str:
    return f"{mode.lower()}-{uuid.uuid4()}"


def _apply_state_updates(
    conn: sqlite3.Connection,
    side: str,
    size_usd: float,
    size_qty: float,
    state: dict,
) -> None:
    """Update position and equity in bot_state after a fill."""
    fee_usd = size_usd * _TAKER_FEE_RATE

    if side == "BUY":
        new_position_usd = state["position_usd"] + size_usd
        new_position_qty = state["position_qty"] + size_qty
    else:  # SELL
        new_position_usd = max(0.0, state["position_usd"] - size_usd)
        new_position_qty = max(0.0, state["position_qty"] - size_qty)

    new_equity = state["current_equity_usd"] - fee_usd

    state_manager.update_bot_state(
        conn,
        position_usd=round(new_position_usd, 8),
        position_qty=round(new_position_qty, 8),
        current_equity_usd=round(new_equity, 2),
        total_trades=state["total_trades"] + 1,
        last_signal=side,
    )


def execute_paper_order(
    side: str,
    pair: str,
    size_usd: float,
    current_price: float,
    conn: sqlite3.Connection,
    config,
) -> dict:
    """
    Simulate a fill at current_price. No real order is placed.
    Records the trade, updates bot_state, returns the trade record.
    """
    trade_log = get_trade_logger()
    state = state_manager.get_bot_state(conn)

    size_qty = round(size_usd / current_price, 8) if current_price > 0 else 0.0
    client_order_id = _build_client_order_id("PAPER")

    trade_record = {
        "client_order_id": client_order_id,
        "side": side,
        "pair": pair,
        "size_usd": size_usd,
        "size_qty": size_qty,
        "fill_price": current_price,
        "mode": "PAPER",
        "status": "FILLED",
        "exchange_order_id": None,
        "raw_response": None,
    }

    row_id = state_manager.record_trade(conn, trade_record)
    if row_id == -1:
        get_system_logger().warning(f"Duplicate paper order skipped: {client_order_id}")
        trade_record["status"] = "DEDUP_SKIP"
        return trade_record

    _apply_state_updates(conn, side, size_usd, size_qty, state)

    trade_log.info(
        f"PAPER {side} | {pair} | size=${size_usd:.2f} | qty={size_qty:.8f} "
        f"| price=${current_price:,.2f} | fee=${size_usd * _TAKER_FEE_RATE:.4f} "
        f"| order_id={client_order_id}"
    )
    return trade_record


def execute_live_order(
    side: str,
    pair: str,
    size_usd: float,
    exchange: ccxt.Exchange,
    conn: sqlite3.Connection,
    config,
) -> dict:
    """
    Submit a real market order via CCXT with client_order_id deduplication.
    Parses fill price and quantity from the CCXT response.
    Raises OrderExecutionError on CCXT exchange errors.
    """
    trade_log = get_trade_logger()
    sys_log = get_system_logger()
    state = state_manager.get_bot_state(conn)

    client_order_id = _build_client_order_id("LIVE")

    try:
        response = exchange.create_market_order(
            symbol=pair,
            side=side.lower(),
            amount=size_usd,
            params={"client_order_id": client_order_id},
        )
    except ccxt.BaseError as e:
        raise OrderExecutionError(f"CCXT order failed: {e}") from e

    fill_price = float(response.get("average") or response.get("price") or 0)
    size_qty = float(response.get("filled") or 0)
    exchange_order_id = str(response.get("id", ""))

    if fill_price <= 0:
        sys_log.error(f"Live order response missing fill price: {response}")

    trade_record = {
        "client_order_id": client_order_id,
        "side": side,
        "pair": pair,
        "size_usd": size_usd,
        "size_qty": size_qty,
        "fill_price": fill_price,
        "mode": "LIVE",
        "status": "FILLED",
        "exchange_order_id": exchange_order_id,
        "raw_response": response,
    }

    row_id = state_manager.record_trade(conn, trade_record)
    if row_id == -1:
        sys_log.warning(f"Duplicate live order skipped: {client_order_id}")
        trade_record["status"] = "DEDUP_SKIP"
        return trade_record

    _apply_state_updates(conn, side, size_usd, size_qty, state)

    trade_log.info(
        f"LIVE {side} | {pair} | size=${size_usd:.2f} | qty={size_qty:.8f} "
        f"| price=${fill_price:,.2f} | exchange_id={exchange_order_id} "
        f"| order_id={client_order_id}"
    )
    return trade_record


def execute_order(
    side: str,
    trade_size_usd: float,
    exchange: ccxt.Exchange,
    conn: sqlite3.Connection,
    config,
    current_price: float,
) -> dict:
    """
    Router: dispatches to paper or live execution based on config.paper_trading.
    This is the only function called by trading_loop.
    """
    if config.paper_trading:
        return execute_paper_order(side, config.trading_pair, trade_size_usd, current_price, conn, config)
    else:
        return execute_live_order(side, config.trading_pair, trade_size_usd, exchange, conn, config)
