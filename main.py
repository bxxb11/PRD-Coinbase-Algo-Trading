"""
main.py — entry point only. No business logic here.

Usage:
  python main.py

Configure via .env (copy .env.example to .env and fill in values).
PAPER_TRADING=True by default — set to False only after paper testing.
"""

from bot.config import load_config, ConfigValidationError
from bot.trading_loop import run_loop


if __name__ == "__main__":
    try:
        config = load_config()
    except ConfigValidationError as e:
        print(f"Configuration error: {e}")
        print("Check your .env file against .env.example")
        raise SystemExit(1)

    run_loop(config)
