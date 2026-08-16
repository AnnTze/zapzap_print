# Deploying the Telegram Print Bot

## Requirements

- macOS (any recent version)
- **Python 3.9–3.13** (3.12 recommended). **Not 3.14** — python-telegram-bot
  21.6 calls `asyncio.get_event_loop()`, removed in 3.14, and the bots crash on
  startup. `setup.sh` refuses 3.14+. A new Mac may ship a newer Python than
  this, so check with `python3 --version` before you start.
- Mitsubishi CP-D90DW connected via USB
- CP-D90DW driver installed ([download from Mitsubishi](https://www.mitsubishielectric.com/printer))
- Three Telegram bots created via [@BotFather](https://t.me/BotFather)
- A private Telegram channel for the gallery

---

## First-time setup on a new Mac

### 1. Get the project

The repository is public, so no GitHub login is needed:

```bash
git clone https://github.com/AnnTze/zapzap_print.git
cd zapzap_print
git checkout hub-dashboard
```

`hub-dashboard` is the current branch — it carries the Windows fixes, the
supply auto-pause, and the hub telemetry. `main` is older.

*(Copying the folder by hand also works, but then you have no way to pull
updates.)*

### 2. Install the printer driver

1. Download the CP-D90DW driver from the [Mitsubishi printer page](https://www.mitsubishielectric.com/printer).
2. Open the downloaded `.dmg` or installer. macOS may block it — right-click the installer and choose **Open** to bypass Gatekeeper.
3. Follow the installer prompts.
4. Plug in the printer via USB, then verify it's detected:
   ```bash
   lpstat -p
   ```
   You should see a line containing `MITSUBISHI_CPD90D`.

### 3. Run setup

```bash
chmod +x setup.sh
./setup.sh
```

The script will create a virtual environment, install dependencies, check the printer, and create `.env` from the template.

### 4. Configure `.env`

Open `.env` in your editor and fill in the values:

| Variable | What it is | How to get it |
|---|---|---|
| `BOT_TOKEN` | Print bot token | @BotFather → `/newbot` |
| `MONITOR_BOT_TOKEN` | Monitor bot token | @BotFather → `/newbot` |
| `GALLERY_BOT_TOKEN` | Gallery bot token | @BotFather → `/newbot` |
| `PRINT_BOT_TOKEN` | Same as `BOT_TOKEN` | Copy from above |
| `GALLERY_CHANNEL_ID` | Your channel's chat ID | Forward msg to @userinfobot |
| `MONITOR_PASSWORD` | Admin password | Choose any password |
| `PRINTER_NAME` | Exact printer name | From `lpstat -p` output |

> **Rotating a token?** If you're replacing a bot token (for example because
> the old one leaked), do it in @BotFather → `/mybots` → the bot → **API Token**
> → **Revoke current token**, then put the new value here. Revoking invalidates
> the old token immediately.

### 5. Set up the gallery channel

1. In Telegram: **New Channel → Private** → name it (e.g. "Photobooth Gallery").
2. Open the channel → **Manage Channel → Administrators → Add Admin** → choose the gallery bot → grant **Post Messages** permission.
3. Send any test message in the channel → forward it to [@userinfobot](https://t.me/userinfobot) → it returns the channel ID (a negative number like `-1001234567890`).
4. Paste the ID into `.env` as `GALLERY_CHANNEL_ID`.

### 6. Start the bots

```bash
./run.sh
```

### 7. Verify everything works

```bash
./status.sh
```

All three bots should show `RUNNING`. Send a test photo to the print bot in Telegram — it should print and reply "Done!".

---

### 8. Connect it to the hub (optional)

To have this booth appear on the monitoring dashboard and receive watermarks
from the hub, follow **Part 2** of
[BOOTH_SETUP_CHECKLIST.md](BOOTH_SETUP_CHECKLIST.md) — it covers Tailscale and
the two extra `.env` lines.

Skip it and the booth works exactly as described above, standalone.

---

## Daily use

| Action | Command |
|---|---|
| Start bots | `./run.sh` |
| Stop bots | `./stop.sh` |
| Check status | `./status.sh` |
| Tail print bot log | `tail -f logs/bot.log` |
| Tail monitor log | `tail -f logs/monitor.log` |
| Tail gallery log | `tail -f logs/gallery.log` |

---

## Troubleshooting

| Issue | Fix |
|---|---|
| Printer not detected | Check USB cable, run `lpstat -p`, reinstall driver from Mitsubishi |
| Bot token invalid | Regenerate from @BotFather: `/mybots → API Token → Revoke` |
| Permission denied on scripts | `chmod +x *.sh` |
| Photos not printing | Check `logs/bot.log` for the actual error |
| Bot paused | Send `/resume` to monitor bot |
| Queue stuck | Send `/queue` to monitor bot, check `logs/bot.log` |
| `Conflict: terminated by other getUpdates request` | Another instance of the same bot is running elsewhere — stop it |

---

## Files created at runtime (do not delete)

| File | Purpose |
|---|---|
| `.venv/` | Python virtual environment |
| `.pids` | Running process IDs |
| `print_log.jsonl` | Print history |
| `gallery_log.jsonl` | Gallery archive |
| `queue.jsonl` | Active print queue |
| `logs/` | All bot log files |
| `.sessions` | Monitor bot auth sessions |
| `.gallery_sessions` | Gallery bot auth sessions |
| `.ink_alerted` | Ink alert flag |
| `.bot_paused` | Pause state flag |

---

## Updating the bot

```bash
./stop.sh
# copy new files (or git pull)
./run.sh
```
