# GCP Deployment Guide — Coinbase Algo Trading Bot

Deploy the bot to a Google Cloud VM so it runs 24/7 without needing your laptop.

---

## ⚠️ Read Before Deploying

| Concern | Detail |
|---|---|
| **API key security** | Keys live only in `.env` on the VM — never in the repo (`.gitignore` blocks it). Use **Trade Only** permissions on Coinbase — no withdrawal access. |
| **Paper trading first** | `PAPER_TRADING=True` is the default. Run paper mode for ≥2 weeks before flipping to `False`. |
| **VM cost** | e2-micro: ~**$5–7/month** (or free if you haven't used the GCP free tier). 10 GB disk, 1 GB RAM is plenty. |
| **No inbound ports needed** | The bot is outbound-only (connects to Coinbase API). Leave the default firewall — do not open any ports. |
| **SQLite is single-process** | Do **not** run multiple bot instances pointing at the same `state/trading.db`. |

---

## Step 1 — Create the GCP VM

### Option A: Google Cloud Console (UI)
1. Go to [console.cloud.google.com](https://console.cloud.google.com) → **Compute Engine → VM Instances → Create Instance**
2. Settings:
   - **Name**: `coinbase-algo-bot`
   - **Region**: `us-central1` (or `us-west1`) — low latency to Coinbase US
   - **Machine type**: `e2-micro` (2 vCPU shared, 1 GB RAM)
   - **Boot disk**: Debian 12 "Bookworm", 10 GB Standard persistent disk
   - **Firewall**: Leave defaults (no HTTP/HTTPS needed)
3. Click **Create**

### Option B: gcloud CLI (faster)
```bash
gcloud compute instances create coinbase-algo-bot \
  --zone=us-central1-a \
  --machine-type=e2-micro \
  --image-family=debian-12 \
  --image-project=debian-cloud \
  --boot-disk-size=10GB \
  --boot-disk-type=pd-standard \
  --tags=no-ip-forward
```

---

## Step 2 — SSH into the VM

```bash
# Via gcloud (recommended — no key management needed)
gcloud compute ssh coinbase-algo-bot --zone=us-central1-a

# Or via the Console: Compute Engine → VM Instances → SSH button
```

---

## Step 3 — Run the Setup Script

```bash
# Download and run the one-time setup script
curl -fsSL https://raw.githubusercontent.com/bxxb11/PRD-Coinbase-Algo-Trading/main/local-trading-bot/deploy/setup_gcp.sh -o setup_gcp.sh
chmod +x setup_gcp.sh
sudo bash setup_gcp.sh
```

This will:
- Install Python 3, git, sqlite3
- Create a locked-down `botrunner` service user (no shell, no login)
- Clone the repo to `/opt/coinbase-bot/`
- Create a Python venv and install all requirements
- Create `state/`, `logs/`, `data/` directories
- Copy `.env.example` → `.env` (credentials NOT included)
- Install the systemd service

---

## Step 4 — Configure Credentials

```bash
sudo nano /opt/coinbase-bot/local-trading-bot/.env
```

Required changes:
```ini
COINBASE_API_KEY=your_actual_api_key
COINBASE_API_SECRET=your_actual_api_secret
PAPER_TRADING=True          # keep True until you've tested
STRATEGY=ema_rsi            # backtest-validated winner
```

Save: `Ctrl+O → Enter → Ctrl+X`

The `.env` file is `chmod 600` (owner-read-only) — other OS users cannot read your keys.

---

## Step 5 — Start the Bot

```bash
# Enable (auto-start on reboot) and start now
sudo systemctl enable --now coinbase-bot

# Check it's running
sudo systemctl status coinbase-bot
```

Expected output:
```
● coinbase-bot.service - Coinbase Algo Trading Bot
     Loaded: loaded (/etc/systemd/system/coinbase-bot.service; enabled)
     Active: active (running) since ...
```

---

## Step 6 — Monitor

### Live log stream (best for initial check)
```bash
journalctl -u coinbase-bot -f
```

### Last 50 log entries
```bash
journalctl -u coinbase-bot -n 50 --no-pager
```

### Bot's own rotating log files
```bash
tail -f /opt/coinbase-bot/local-trading-bot/logs/system.log
tail -f /opt/coinbase-bot/local-trading-bot/logs/trades.log
tail -f /opt/coinbase-bot/local-trading-bot/logs/risk.log
```

### Check state (live position/equity)
```bash
sqlite3 /opt/coinbase-bot/local-trading-bot/state/trading.db \
  "SELECT status, last_signal, position_usd, current_equity_usd, peak_equity_usd FROM bot_state;"
```

### Recent trades
```bash
sqlite3 /opt/coinbase-bot/local-trading-bot/state/trading.db \
  "SELECT created_at, side, size_usd, fill_price, mode FROM trades ORDER BY created_at DESC LIMIT 10;"
```

---

## Updating the Bot

When new code is pushed to GitHub:
```bash
gcloud compute ssh coinbase-algo-bot --zone=us-central1-a
sudo bash /opt/coinbase-bot/deploy/update.sh
```

This stops the service, pulls latest code, upgrades dependencies, and restarts.

---

## Start/Stop/Restart

```bash
sudo systemctl stop coinbase-bot      # graceful stop
sudo systemctl start coinbase-bot     # start
sudo systemctl restart coinbase-bot   # restart
sudo systemctl disable coinbase-bot   # stop auto-start on reboot
```

---

## Switching to Live Trading

Only do this after ≥2 weeks of paper results you're comfortable with:

```bash
sudo nano /opt/coinbase-bot/local-trading-bot/.env
# Change:  PAPER_TRADING=True  →  PAPER_TRADING=False
sudo systemctl restart coinbase-bot
journalctl -u coinbase-bot -f   # watch startup banner confirm LIVE MODE
```

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Active: failed` | `journalctl -u coinbase-bot -n 30` to see the error |
| `ModuleNotFoundError` | `sudo bash /opt/coinbase-bot/deploy/update.sh` to reinstall deps |
| Bot PAUSED in logs | Drawdown limit hit — check `risk.log`. Reset equity via SQLite or restart paper |
| High CPU / slow ticks | Normal at startup (CCXT initialisation). Settles to near-zero in steady state |
| VM rebooted, bot offline | If `systemctl enable` was run, it auto-restarts. Check `systemctl status coinbase-bot` |

---

## Cost Summary

| Resource | Spec | Est. Monthly Cost |
|---|---|---|
| e2-micro VM | 2 shared vCPU, 1 GB RAM | ~$6/mo (free tier: 1 free/mo) |
| 10 GB Standard disk | Persistent HDD | ~$0.40/mo |
| Network egress | API calls ~few MB/day | ~$0 |
| **Total** | | **~$6–7/mo** (or free first month) |

Free tier eligibility: [cloud.google.com/free](https://cloud.google.com/free)
