"""
logger.py — centralized structured logging with three rotating streams:
  system.log  — all events (also to stdout)
  trades.log  — order lifecycle events
  risk.log    — drawdown checks, state transitions
"""

import logging
import os
from logging.handlers import RotatingFileHandler

_FMT = "%(asctime)s | %(name)-6s | %(levelname)-8s | %(module)s | %(message)s"
_DATE_FMT = "%Y-%m-%dT%H:%M:%S"
_MAX_BYTES = 10 * 1024 * 1024  # 10 MB
_BACKUP_COUNT = 5

_initialized = False


def setup_logging(log_dir: str, log_level: str = "INFO") -> None:
    """
    Create rotating file handlers for system/trades/risk loggers.
    Adds a stdout StreamHandler to the system logger only.
    Idempotent — safe to call multiple times (only configures once).
    """
    global _initialized
    if _initialized:
        return

    os.makedirs(log_dir, exist_ok=True)
    level = getattr(logging, log_level.upper(), logging.INFO)
    formatter = logging.Formatter(_FMT, datefmt=_DATE_FMT)

    def _make_logger(name: str, filename: str, stdout: bool = False) -> logging.Logger:
        logger = logging.getLogger(name)
        logger.setLevel(level)
        logger.propagate = False

        fh = RotatingFileHandler(
            os.path.join(log_dir, filename),
            maxBytes=_MAX_BYTES,
            backupCount=_BACKUP_COUNT,
            encoding="utf-8",
        )
        fh.setFormatter(formatter)
        logger.addHandler(fh)

        if stdout:
            sh = logging.StreamHandler()
            sh.setFormatter(formatter)
            logger.addHandler(sh)

        return logger

    _make_logger("system", "system.log", stdout=True)
    _make_logger("trades", "trades.log", stdout=False)
    _make_logger("risk", "risk.log", stdout=False)

    _initialized = True


def get_system_logger() -> logging.Logger:
    return logging.getLogger("system")


def get_trade_logger() -> logging.Logger:
    return logging.getLogger("trades")


def get_risk_logger() -> logging.Logger:
    return logging.getLogger("risk")
