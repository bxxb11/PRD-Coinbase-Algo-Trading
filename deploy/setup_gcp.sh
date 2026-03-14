#!/usr/bin/env bash
# =============================================================================
# setup_gcp.sh — One-time setup for Coinbase Algo Trading Bot on GCP (Debian 12)
#
# Run this once after SSH-ing into a fresh VM:
#   chmod +x setup_gcp.sh && sudo bash setup_gcp.sh
#
# After it completes:
#   1. Edit the .env file:          sudo nano /opt/coinbase-bot/local-trading-bot/.env
#   2. Enable and start the bot:    sudo systemctl enable --now coinbase-bot
#   3. Tail live logs:              journalctl -u coinbase-bot -f
# =============================================================================
set -euo pipefail

REPO_URL="https://github.com/bxxb11/PRD-Coinbase-Algo-Trading.git"
INSTALL_DIR="/opt/coinbase-bot"
SERVICE_USER="botrunner"
PYTHON_MIN="3.11"

echo "============================================================"
echo " Coinbase Algo Bot — GCP VM Setup"
echo "============================================================"

# ── 1. System packages ───────────────────────────────────────────────────────
echo "[1/7] Installing system packages..."
apt-get update -qq
apt-get install -y -qq \
    python3 python3-pip python3-venv python3-dev \
    git curl build-essential sqlite3

PYTHON_BIN=$(which python3)
PYTHON_VER=$("$PYTHON_BIN" --version 2>&1 | awk '{print $2}')
echo "      Python: $PYTHON_VER  (at $PYTHON_BIN)"

# ── 2. Create non-root service user ─────────────────────────────────────────
echo "[2/7] Creating service user '$SERVICE_USER'..."
if ! id "$SERVICE_USER" &>/dev/null; then
    useradd --system --no-create-home --shell /usr/sbin/nologin "$SERVICE_USER"
    echo "      User created."
else
    echo "      User already exists — skipping."
fi

# ── 3. Clone repo ────────────────────────────────────────────────────────────
echo "[3/7] Cloning repository..."
if [ -d "$INSTALL_DIR/.git" ]; then
    echo "      Repo already present — pulling latest..."
    git -C "$INSTALL_DIR" pull --ff-only
else
    git clone "$REPO_URL" "$INSTALL_DIR"
fi

BOT_DIR="$INSTALL_DIR/local-trading-bot"

# ── 4. Python virtual environment ────────────────────────────────────────────
echo "[4/7] Creating Python virtual environment..."
"$PYTHON_BIN" -m venv "$BOT_DIR/.venv"
"$BOT_DIR/.venv/bin/pip" install --quiet --upgrade pip
"$BOT_DIR/.venv/bin/pip" install --quiet -r "$BOT_DIR/requirements.txt"
echo "      Dependencies installed."

# ── 5. Runtime directories ───────────────────────────────────────────────────
echo "[5/7] Creating runtime directories..."
mkdir -p "$BOT_DIR/state" "$BOT_DIR/logs" "$BOT_DIR/data"

# ── 6. .env file (from template, credentials NOT included) ───────────────────
echo "[6/7] Setting up .env file..."
if [ ! -f "$BOT_DIR/.env" ]; then
    cp "$BOT_DIR/.env.example" "$BOT_DIR/.env"
    echo ""
    echo "  !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
    echo "  !!  ACTION REQUIRED: Fill in your API credentials:      !!"
    echo "  !!    sudo nano $BOT_DIR/.env                           !!"
    echo "  !!  Set COINBASE_API_KEY and COINBASE_API_SECRET.       !!"
    echo "  !!  Leave PAPER_TRADING=True until you have tested.     !!"
    echo "  !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
    echo ""
else
    echo "      .env already exists — skipping (credentials preserved)."
fi

# ── 7. File permissions ───────────────────────────────────────────────────────
echo "[7/7] Applying file permissions..."
chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"
chmod 600 "$BOT_DIR/.env"   # owner-read-only — protects API keys

# ── Install systemd service ───────────────────────────────────────────────────
echo ""
echo "Installing systemd service..."
cat > /etc/systemd/system/coinbase-bot.service <<EOF
[Unit]
Description=Coinbase Algo Trading Bot
Documentation=https://github.com/bxxb11/PRD-Coinbase-Algo-Trading
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$SERVICE_USER
WorkingDirectory=$BOT_DIR
EnvironmentFile=$BOT_DIR/.env
ExecStart=$BOT_DIR/.venv/bin/python main.py
Restart=on-failure
RestartSec=30
StartLimitIntervalSec=300
StartLimitBurst=5

# Logging — goes to journald (query with: journalctl -u coinbase-bot)
StandardOutput=journal
StandardError=journal
SyslogIdentifier=coinbase-bot

# Hardening — bot only needs network + local files
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ReadWritePaths=$BOT_DIR/state $BOT_DIR/logs $BOT_DIR/data

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
echo "      Service file installed at /etc/systemd/system/coinbase-bot.service"

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "============================================================"
echo " Setup complete!  Next steps:"
echo "============================================================"
echo ""
echo "  1. Fill in API credentials (leave PAPER_TRADING=True first!):"
echo "     sudo nano $BOT_DIR/.env"
echo ""
echo "  2. Start + enable the bot (auto-starts on reboot):"
echo "     sudo systemctl enable --now coinbase-bot"
echo ""
echo "  3. Check status:"
echo "     sudo systemctl status coinbase-bot"
echo ""
echo "  4. Tail live logs:"
echo "     journalctl -u coinbase-bot -f"
echo ""
echo "  5. To update the bot later:"
echo "     sudo bash $INSTALL_DIR/deploy/update.sh"
echo ""
