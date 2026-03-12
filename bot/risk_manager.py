"""
risk_manager.py — gates every proposed trade through three checks:
  1. Bot status (RUNNING / PAUSED / STOPPED)
  2. Drawdown limit (25% max → PAUSED transition)
  3. Position size cap (BUY signals only, SELL always allowed)

Never executes trades itself. Returns typed RiskResult with a reason string.
"""

import sqlite3
from dataclasses import dataclass
from enum import Enum

from bot.logger import get_risk_logger
from bot.strategy import Signal


class RiskDecision(Enum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"  # trade-level rejection
    PAUSED = "PAUSED"      # drawdown breach → transitions bot to PAUSED


@dataclass
class RiskResult:
    decision: RiskDecision
    reason: str


def check_bot_status(status: str) -> RiskResult:
    """Reject if bot is not in RUNNING state."""
    if status in ("PAUSED", "STOPPED"):
        return RiskResult(RiskDecision.REJECTED, f"Bot is in {status} state — trading halted")
    return RiskResult(RiskDecision.APPROVED, "Bot RUNNING")


def check_drawdown(
    current_equity_usd: float,
    peak_equity_usd: float,
    max_drawdown_percent: float,
) -> RiskResult:
    """
    Calculate drawdown from peak equity.
    At 90% of the limit, emit a warning but still approve.
    At or beyond the limit, return PAUSED.
    """
    risk_log = get_risk_logger()

    if peak_equity_usd <= 0:
        return RiskResult(RiskDecision.APPROVED, "Peak equity not yet set")

    drawdown = (peak_equity_usd - current_equity_usd) / peak_equity_usd * 100

    if drawdown >= max_drawdown_percent:
        msg = f"Max drawdown breached: {drawdown:.2f}% >= {max_drawdown_percent:.1f}%"
        risk_log.critical(msg)
        return RiskResult(RiskDecision.PAUSED, msg)

    warning_threshold = max_drawdown_percent * 0.9
    if drawdown >= warning_threshold:
        risk_log.warning(
            f"Drawdown approaching limit: {drawdown:.2f}% "
            f"(warning at {warning_threshold:.1f}%, limit {max_drawdown_percent:.1f}%)"
        )

    return RiskResult(RiskDecision.APPROVED, f"Drawdown {drawdown:.2f}% within limits")


def check_position_limit(
    current_position_usd: float,
    trade_size_usd: float,
    max_position_usd: float,
) -> RiskResult:
    """
    Reject BUY trades that would push total position over the cap.
    SELL trades bypass this check (always allow exits).
    """
    projected = current_position_usd + trade_size_usd
    if projected > max_position_usd:
        return RiskResult(
            RiskDecision.REJECTED,
            f"Position cap exceeded: ${current_position_usd:.2f} + ${trade_size_usd:.2f} "
            f"= ${projected:.2f} > ${max_position_usd:.2f} max",
        )
    return RiskResult(
        RiskDecision.APPROVED,
        f"Position OK: ${projected:.2f} <= ${max_position_usd:.2f}",
    )


def evaluate_trade(
    signal: Signal,
    state: dict,
    config,
    conn: sqlite3.Connection,
    trade_size_usd: float,
) -> RiskResult:
    """
    Master risk gate. Runs checks in fail-fast order:
      1. Bot status
      2. Drawdown (may transition to PAUSED and write to DB)
      3. Position limit (BUY only)

    Returns the first non-APPROVED result, or APPROVED if all pass.
    conn is used only if a PAUSED state write is needed.
    """
    from bot import state_manager  # local import to avoid circular

    risk_log = get_risk_logger()

    # 1. Status check
    result = check_bot_status(state["status"])
    if result.decision != RiskDecision.APPROVED:
        risk_log.warning(result.reason)
        return result

    # 2. Drawdown check
    result = check_drawdown(
        state["current_equity_usd"],
        state["peak_equity_usd"],
        config.max_drawdown_percent,
    )
    if result.decision == RiskDecision.PAUSED:
        # Atomic: write PAUSED status to DB alongside the risk decision
        state_manager.update_bot_state(conn, status="PAUSED")
        risk_log.critical(f"Bot transitioned to PAUSED. {result.reason}")
        return result
    if result.decision == RiskDecision.REJECTED:
        risk_log.warning(result.reason)
        return result

    # 3. Position limit (BUY signals only — always allow SELL/exits)
    if signal == Signal.BUY:
        result = check_position_limit(
            state["position_usd"],
            trade_size_usd,
            config.max_position_usd,
        )
        if result.decision != RiskDecision.APPROVED:
            risk_log.warning(result.reason)
            return result

    risk_log.debug(f"Trade approved for {signal.value} ${trade_size_usd:.2f}")
    return RiskResult(RiskDecision.APPROVED, f"All risk checks passed for {signal.value}")
