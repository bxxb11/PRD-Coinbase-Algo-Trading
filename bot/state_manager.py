"""
state_manager.py — all SQLite reads and writes.
Owns the database schema, WAL-mode setup, and ACID-safe upserts.
No other module writes to the database directly.
"""

import json
import sqlite3
from datetime import datetime, timezone
from typing import Optional


class StateNotFoundError(Exception):
    pass


_VALID_STATE_COLUMNS = {
    "status",
    "last_signal",
    "position_usd",
    "position_qty",
    "initial_equity_usd",
    "current_equity_usd",
    "peak_equity_usd",
    "total_trades",
    "last_updated_at",
}

_DDL = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS bot_state (
    id                  INTEGER PRIMARY KEY DEFAULT 1,
    status              TEXT    NOT NULL DEFAULT 'RUNNING',
    last_signal         TEXT    NOT NULL DEFAULT 'NONE',
    position_usd        REAL    NOT NULL DEFAULT 0.0,
    position_qty        REAL    NOT NULL DEFAULT 0.0,
    initial_equity_usd  REAL    NOT NULL DEFAULT 0.0,
    current_equity_usd  REAL    NOT NULL DEFAULT 0.0,
    peak_equity_usd     REAL    NOT NULL DEFAULT 0.0,
    total_trades        INTEGER NOT NULL DEFAULT 0,
    last_updated_at     TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS trades (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    client_order_id     TEXT    NOT NULL UNIQUE,
    side                TEXT    NOT NULL,
    pair                TEXT    NOT NULL,
    size_usd            REAL    NOT NULL,
    size_qty            REAL    NOT NULL,
    fill_price          REAL    NOT NULL,
    mode                TEXT    NOT NULL,
    status              TEXT    NOT NULL,
    exchange_order_id   TEXT,
    raw_response        TEXT,
    created_at          TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_trades_client_order_id ON trades(client_order_id);
CREATE INDEX IF NOT EXISTS idx_trades_created_at      ON trades(created_at);
"""


def init_db(db_path: str) -> sqlite3.Connection:
    """
    Open (or create) the SQLite database, apply schema DDL,
    and insert the default bot_state row if missing.
    Returns the open connection.
    """
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(_DDL)
    conn.commit()

    # Insert default state row if table is empty
    row = conn.execute("SELECT COUNT(*) FROM bot_state").fetchone()[0]
    if row == 0:
        conn.execute(
            "INSERT INTO bot_state (id) VALUES (1)"
        )
        conn.commit()

    return conn


def get_bot_state(conn: sqlite3.Connection) -> dict:
    """
    Return the single bot_state row as a plain dict.
    Raises StateNotFoundError if the row is missing.
    """
    row = conn.execute("SELECT * FROM bot_state WHERE id = 1").fetchone()
    if row is None:
        raise StateNotFoundError("bot_state row missing — was init_db() called?")
    return dict(row)


def update_bot_state(conn: sqlite3.Connection, **fields) -> None:
    """
    UPDATE bot_state SET <fields> WHERE id=1.
    Validates column names; raises ValueError on unknown fields.
    Always refreshes last_updated_at.
    """
    unknown = set(fields) - _VALID_STATE_COLUMNS
    if unknown:
        raise ValueError(f"Unknown bot_state column(s): {unknown}")

    fields["last_updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values())
    conn.execute(f"UPDATE bot_state SET {set_clause} WHERE id = 1", values)
    conn.commit()


def record_trade(conn: sqlite3.Connection, trade: dict) -> int:
    """
    INSERT a trade record. Returns the new row id on success.
    Returns -1 on UNIQUE constraint violation (deduplication path) — does not raise.
    """
    try:
        cursor = conn.execute(
            """
            INSERT INTO trades
                (client_order_id, side, pair, size_usd, size_qty,
                 fill_price, mode, status, exchange_order_id, raw_response)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trade["client_order_id"],
                trade["side"],
                trade["pair"],
                trade["size_usd"],
                trade["size_qty"],
                trade["fill_price"],
                trade["mode"],
                trade["status"],
                trade.get("exchange_order_id"),
                json.dumps(trade.get("raw_response")) if trade.get("raw_response") else None,
            ),
        )
        conn.commit()
        return cursor.lastrowid
    except sqlite3.IntegrityError:
        # UNIQUE constraint on client_order_id — dedup path
        return -1


def get_open_position(conn: sqlite3.Connection) -> dict:
    """Return {'position_usd': float, 'position_qty': float} from bot_state."""
    state = get_bot_state(conn)
    return {
        "position_usd": state["position_usd"],
        "position_qty": state["position_qty"],
    }


def get_trade_history(
    conn: sqlite3.Connection,
    limit: int = 100,
    side: Optional[str] = None,
) -> list:
    """
    Return recent trades as list of dicts, newest first.
    Optional filter by side ('BUY' or 'SELL').
    """
    if side:
        rows = conn.execute(
            "SELECT * FROM trades WHERE side = ? ORDER BY created_at DESC LIMIT ?",
            (side.upper(), limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM trades ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]
