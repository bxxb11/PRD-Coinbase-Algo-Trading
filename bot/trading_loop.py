"""
trading_loop.py — orchestrates all modules in a 60-second loop.
The outermost exception handler ensures the bot NEVER crashes silently.
All tick-level failures are logged and the loop continues.
"""

import sqlite3
import time
import traceback

import ccxt

from bot import execution, market_data, risk_manager, state_manager, strategy
from bot.config import Config
from bot.logger import get_system_logger, get_risk_logger
from bot.risk_manager import RiskDecision
from bot.strategy import Signal


def initialize_bot(config: Config) -> tuple:
    """
    Set up logging, database, and exchange connection.
    Seeds bot_state with initial equity on first run.
    Returns (exchange, conn).
    """
    from bot.logger import setup_logging
    setup_logging(config.log_dir, config.log_level)
    log = get_system_logger()

    conn = state_manager.init_db(config.db_path)
    exchange = market_data.create_exchange(config)

    # Seed initial equity on first run (when it's still 0.0)
    current_state = state_manager.get_bot_state(conn)
    if current_state["initial_equity_usd"] == 0.0:
        state_manager.update_bot_state(
            conn,
            initial_equity_usd=config.initial_equity_usd,
            current_equity_usd=config.initial_equity_usd,
            peak_equity_usd=config.initial_equity_usd,
        )

    mode = "PAPER" if config.paper_trading else "LIVE"
    log.info("=" * 60)
    log.info(f"Coinbase Algo Trading Bot — {mode} MODE")
    log.info(f"Pair:       {config.trading_pair}")
    log.info(f"Strategy:   {config.strategy_name}  |  Timeframe: {config.timeframe}")
    log.info(f"Trade size: ${config.min_trade_size_usd}–${config.max_trade_size_usd} (spread-sized)")
    log.info(f"Max pos:    ${config.max_position_usd}  |  Max drawdown: {config.max_drawdown_percent}%")
    log.info(f"Equity:     ${config.initial_equity_usd}")
    log.info(f"DB:         {config.db_path}")
    log.info("=" * 60)

    return exchange, conn


def run_tick(
    exchange: ccxt.Exchange,
    conn: sqlite3.Connection,
    config: Config,
    tick_number: int,
) -> None:
    """
    Execute a single trading loop iteration.
    Designed to never raise — all exceptions are caught, logged, and swallowed.
    """
    log = get_system_logger()

    try:
        log.debug(f"Tick #{tick_number} started")

        # ── Step 1: Read state ──────────────────────────────────────
        try:
            state = state_manager.get_bot_state(conn)
        except Exception as e:
            log.error(f"Tick #{tick_number}: state read failed — {e}")
            return

        # ── Step 2: Status gate ─────────────────────────────────────
        if state["status"] in ("PAUSED", "STOPPED"):
            log.info(f"Bot is {state['status']} — skipping tick #{tick_number}")
            return

        # ── Step 3: Fetch OHLCV ─────────────────────────────────────
        try:
            df = market_data.fetch_ohlcv(
                exchange,
                config.trading_pair,
                config.timeframe,
                config.candles_required,
            )
        except (market_data.InsufficientDataError, ccxt.NetworkError, ccxt.ExchangeError) as e:
            log.warning(f"Tick #{tick_number}: OHLCV fetch failed — {e}")
            return

        # ── Step 4: Compute signal via configured strategy ──────────
        try:
            signal = strategy.dispatch_strategy(df, config)
        except Exception as e:
            log.error(f"Tick #{tick_number}: signal computation failed — {e}")
            return

        log.debug(f"Tick #{tick_number}: signal={signal.value}")

        # ── Step 5: Signal deduplication ────────────────────────────
        latest = df.iloc[-1]
        price_info = f"price=${latest['close']:,.2f}"

        if signal == Signal.HOLD:
            log.info(f"Tick #{tick_number}: HOLD | {config.strategy_name} | {price_info}")
            return

        if signal.value == state["last_signal"]:
            log.info(
                f"Tick #{tick_number}: {signal.value} (already acted on) | {config.strategy_name}"
            )
            return

        # ── Step 3b: Compute opportunity-sized trade amount ─────────
        # Always uses MA20/50 spread for sizing regardless of strategy
        try:
            df_with_ma = strategy.compute_moving_averages(
                df, config.ma_short_period, config.ma_long_period
            )
        except ValueError:
            df_with_ma = df
        trade_size_usd = strategy.compute_trade_size(
            df_with_ma,
            config.min_trade_size_usd,
            config.max_trade_size_usd,
            config.size_spread_min_pct,
            config.size_spread_max_pct,
        )
        log.info(
            f"Tick #{tick_number}: {signal.value} signal | trade size=${trade_size_usd:.2f}"
        )

        # ── Step 6: Fetch current price (for paper fills) ───────────
        try:
            current_price = market_data.fetch_ticker_price(exchange, config.trading_pair)
        except market_data.PriceFetchError as e:
            log.warning(f"Tick #{tick_number}: ticker fetch failed — {e}")
            return

        # ── Step 7: Risk evaluation ─────────────────────────────────
        risk_result = risk_manager.evaluate_trade(
            signal, state, config, conn, trade_size_usd
        )

        if risk_result.decision == RiskDecision.PAUSED:
            get_risk_logger().critical(
                f"Tick #{tick_number}: bot PAUSED. {risk_result.reason}"
            )
            return

        if risk_result.decision == RiskDecision.REJECTED:
            log.warning(f"Tick #{tick_number}: trade REJECTED — {risk_result.reason}")
            return

        # ── Step 8: Execute order ───────────────────────────────────
        try:
            trade_record = execution.execute_order(
                side=signal.value,
                trade_size_usd=trade_size_usd,
                exchange=exchange,
                conn=conn,
                config=config,
                current_price=current_price,
            )
            log.info(
                f"Tick #{tick_number}: order {trade_record['status']} "
                f"| {trade_record['mode']} {trade_record['side']} "
                f"${trade_record['size_usd']:.2f} @ ${trade_record['fill_price']:,.2f}"
            )
        except execution.OrderExecutionError as e:
            log.error(f"Tick #{tick_number}: order execution failed — {e}")
            return

        # ── Step 9: Update peak equity ──────────────────────────────
        state_after = state_manager.get_bot_state(conn)
        if state_after["current_equity_usd"] > state_after["peak_equity_usd"]:
            state_manager.update_bot_state(
                conn, peak_equity_usd=state_after["current_equity_usd"]
            )

        log.info(
            f"Tick #{tick_number} complete | "
            f"position=${state_after['position_usd']:.2f} | "
            f"equity=${state_after['current_equity_usd']:.2f}"
        )

    except Exception:
        # Outermost catch-all: log and continue — loop never dies
        log.critical(
            f"Tick #{tick_number}: unhandled exception\n{traceback.format_exc()}"
        )


def run_loop(config: Config) -> None:
    """
    Initialize the bot, then run the trading loop forever.
    Sleeps to maintain the configured tick interval.
    Exits cleanly on KeyboardInterrupt.
    """
    log_uninit = get_system_logger()

    try:
        exchange, conn = initialize_bot(config)
    except Exception as e:
        log_uninit.critical(f"Bot initialization failed: {e}\n{traceback.format_exc()}")
        raise

    log = get_system_logger()
    tick_number = 0

    try:
        while True:
            tick_start = time.monotonic()
            run_tick(exchange, conn, config, tick_number)
            elapsed = time.monotonic() - tick_start
            sleep_time = max(0.0, config.loop_interval_seconds - elapsed)
            log.debug(f"Tick #{tick_number} took {elapsed:.2f}s — sleeping {sleep_time:.1f}s")
            time.sleep(sleep_time)
            tick_number += 1

    except KeyboardInterrupt:
        log.info("KeyboardInterrupt received — shutting down cleanly")
        conn.close()
        log.info("Database connection closed. Goodbye.")
