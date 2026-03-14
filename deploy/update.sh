#!/usr/bin/env bash
# =============================================================================
# update.sh — Pull latest code and restart the bot (zero-credential-loss)
#
# Usage:  sudo bash /opt/coinbase-bot/deploy/update.sh
# =============================================================================
set -euo pipefail

INSTALL_DIR="/opt/coinbase-bot"
BOT_DIR="$INSTALL_DIR/local-trading-bot"
SERVICE_USER="botrunner"

echo "[1/4] Stopping coinbase-bot service..."
systemctl stop coinbase-bot

echo "[2/4] Pulling latest code..."
git -C "$INSTALL_DIR" pull --ff-only

echo "[3/4] Updating Python dependencies..."
"$BOT_DIR/.venv/bin/pip" install --quiet --upgrade pip
"$BOT_DIR/.venv/bin/pip" install --quiet -r "$BOT_DIR/requirements.txt"

# Fix permissions in case new files were added
chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"

echo "[4/4] Restarting coinbase-bot service..."
systemctl daemon-reload
systemctl start coinbase-bot
systemctl status coinbase-bot --no-pager

echo ""
echo "Update complete. Tailing logs for 10s..."
journalctl -u coinbase-bot -n 20 --no-pager
