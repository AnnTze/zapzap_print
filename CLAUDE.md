# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> **Resuming work?** Read [HANDOFF.md](HANDOFF.md) first — it has the current
> state of the deployment, what runs on which machine, the ranked next steps,
> and the findings from the Apple HIG and LumaBooth research that only exist
> there.

## What this is

A three-bot Telegram photobooth print system running on macOS:

- **`bot.py`** — public-facing print bot. Receives single photos and multi-photo albums from users, prints on a Mitsubishi CP-D90DW via `lpr` on 10×15 cm (ME_10x15) paper. Posts every successful print to a private Telegram channel and logs to both `print_log.jsonl` and `gallery_log.jsonl`.
- **`monitor.py`** — private admin bot. Password-protected. Polls `print_log.jsonl` every 10 s, provides stats/alerts/ink tracking/queue visibility.
- **`gallery.py`** — private gallery query bot. Password-protected. Reads `gallery_log.jsonl` to answer search queries (`/latest`, `/gallery`, `/photos`, `/count`) and resends photos.

Dependencies: `python-telegram-bot==21.6`, `pillow>=10.0`, `python-dotenv>=1.0`.

**Mixed Mac/Windows fleets**: never use strftime's `%-d` (or any `%-` flag) —
it is a glibc/BSD extension that raises `ValueError` on Windows, where the
spelling is `%#d`. No single format string works on both, so use
`datefmt.fmt_date` / `datefmt.fmt_datetime`, which build the day number in
Python and only ask strftime for `%b`/`%Y`. Same rule for file locking: use
`supply_lock.locked()`, never `fcntl` directly.

**Python version**: 3.9–3.13 (3.12 recommended). **Python 3.14 is not supported** — `python-telegram-bot 21.6` calls `asyncio.get_event_loop()` which was removed in 3.14, causing the bots to crash on startup. `setup.sh` enforces this.

## File structure

```
.
├── bot.py / monitor.py / gallery.py   # The three bots
├── requirements.txt
├── .env / .env.example                # Runtime config (.env never committed)
│
├── setup.sh                           # One-time setup on a new Mac
├── run.sh / stop.sh / status.sh       # Manual lifecycle for development / events
├── install-autostart.sh               # Register launchd agents (production)
├── uninstall-autostart.sh             # Remove launchd agents
│
├── DEPLOY.md                          # Step-by-step new-Mac guide
├── HUB_SETUP.md                       # Tailscale + hub + booth telemetry setup
├── USER_GUIDE.md                      # End-user "how to send a photo" guide
│
├── logs/                              # Bot stdout/stderr + monthly archived print logs
│   ├── bot.log / monitor.log / gallery.log
│   └── print_log_YYYY_MM.jsonl
├── print_log.jsonl                    # One JSON line per print attempt
├── gallery_log.jsonl                  # One JSON line per successful print
├── .pids                              # PIDs from ./run.sh (auto-managed)
├── supply_lock.py                     # Cross-platform flock/msvcrt guard for .supply_state
├── .sessions / .gallery_sessions      # Persisted bot auth sessions
├── .bot_paused                        # Present = paused; contents = reason
├── .bot_paused_auto                   # Present = the pause was automatic (empty supply)
├── .supply_state / .supply_lock       # Ribbon + paper counters, and their lock file
└── .ink_alerted                       # Legacy flag, only cleared by log rotation
```

## Commands

```bash
# Initial setup (one-time per Mac)
./setup.sh

# Manual lifecycle
./run.sh           # start all three bots, save PIDs to .pids
./stop.sh          # SIGTERM with 5s timeout, escalate to SIGKILL
./status.sh        # per-bot RUNNING/STOPPED, printer state, queue, log sizes, uptime

# Production auto-start (launchd)
./install-autostart.sh    # writes ~/Library/LaunchAgents/com.local.zapzap.{print,monitor,gallery}_bot.plist
./uninstall-autostart.sh

# Direct launchd commands
launchctl list | grep zapzap

# Printer / queue
lpstat -p          # list configured printers
lpstat -o          # show current print queue
cancel -a          # cancel all queued jobs

# Logs
tail -f logs/bot.log
tail -f logs/monitor.log
tail -f logs/gallery.log
```

> Don't mix `./run.sh` with `./install-autostart.sh` — both starting the same bot triggers Telegram's "Conflict: terminated by other getUpdates request" error. `status.sh` detects bots managed either way.

## Configuration

All runtime config lives in `.env`. Copy `.env.example` to `.env` (or run `./setup.sh`) and fill in values.

| Variable | Default | Where it's used |
|---|---|---|
| `PRINT_BOT_TOKEN` | — | `bot.py` (loaded as `BOT_TOKEN`), `monitor.py` (for `/lastphoto`) |
| `MONITOR_BOT_TOKEN` | — | `monitor.py` |
| `GALLERY_BOT_TOKEN` | — | `gallery.py` and `bot.py` (for posting to channel) |
| `MONITOR_PASSWORD` | `changeme` | shared by `monitor.py` and `gallery.py` |
| `GALLERY_CHANNEL_ID` | — | `bot.py` (negative chat_id, e.g. `-1001234567890`) |
| `RIBBON_CAPACITY` | `700` | `monitor.py` |
| `INK_ALERT_THRESHOLD` | `100` | `monitor.py` |
| `LOG_FILE` | `print_log.jsonl` | `bot.py`, `monitor.py` |
| `LOG_ARCHIVE_DIR` | `logs` | `monitor.py` |
| `GALLERY_LOG_FILE` | `gallery_log.jsonl` | `bot.py`, `gallery.py` |

Hardcoded in `bot.py` (edit the file to change):

| Constant | Value | Purpose |
|---|---|---|
| `PRINTER_NAME` | `"MITSUBISHI_CPD90D"` | Target CUPS printer (`None` = system default) |
| `PAPER_W_PX` / `PAPER_H_PX` | `1772` / `1181` | Canvas at 300 DPI for 10×15 cm |
| `MAX_COPIES` | = `MAX_PRINTS_PER_MESSAGE` | Cap on copies per photo |
| `RIBBON_CAPACITY` / `DEFAULT_PAPER_LOAD` | `700` / `50` | Assumed supply when `.supply_state` doesn't exist yet (must match `monitor.py`) |

## Architecture

### bot.py — routing and image pipeline

**Entry point**: `handle_image(update, context)`
- `message.document` → `process_single_photo` immediately
- `message.photo` with no `media_group_id` → `process_single_photo` immediately
- `message.photo` with `media_group_id` → buffered in `album_buffer`, timer reset to 1.5 s, fires `process_album` after last photo arrives

**`process_single_photo(update, context)`**
Handles one photo or document: download → `fix_exif_rotation` → `fit_to_paper` → save temp JPEG at 300 DPI → `send_to_printer` → `append_print_log` → `post_to_gallery_channel`.

**`process_album(media_group_id, context)`**
Fires after 1.5 s timer. Finds caption from any message in group, calls `parse_copy_list(caption, photo_count)` to get per-photo copy list. If mismatch → replies with error, aborts. Otherwise loops through each photo with `process_single_photo`-equivalent logic.

**`fit_to_paper(img)`**: picks landscape or portrait canvas by aspect ratio, scales to **fill** (centre-crop, no white borders), LANCZOS resize.

**`fix_exif_rotation(img)`**: `ImageOps.exif_transpose`, called **before** `convert()`. Never reintroduce `img._getexif()` — it only exists on `JpegImageFile` and silently no-ops after a convert.

### Pause and supply state

The bots share state through files in the working directory:

- **`.bot_paused`** — present = print bot refuses photos; contents are the reason shown to users.
- **`.bot_paused_auto`** — present = `bot.py` paused *itself* because a supply hit zero. `/newribbon` and `/newpaper` lift an automatic pause once both supplies are stocked; they never lift a manual one. `/resume` refuses to lift an automatic pause while a supply still reads empty.
- **`.supply_state`** — JSON ribbon/paper counters plus `alerts_sent` threshold keys.
- **`.supply_lock`** — locked via `supply_lock.locked()` (`fcntl` on macOS, `msvcrt` on Windows). Use the OS lock, never an exists-check: the kernel releases it if a bot is killed mid-update, so a stale lock file can't wedge every later print.

`bot.py` calls `print_blocked()` before every photo **and between photos in an album**, so a supply running out mid-album stops the rest of it.

**`send_to_printer(jpeg_path, copies)`**: `lpr -# <copies> -o media=ME_10x15 -o fit-to-page -P MITSUBISHI_CPD90D <file>`. Raises `RuntimeError` on non-zero exit.

**Caption parsing**:
- `parse_copy_list(caption, photo_count) -> list[int] | str` — handles `None`, single number, comma-separated. Returns error string on mismatch.
- `parse_copies(caption) -> int` — single-photo wrapper, delegates to `parse_copy_list(caption, 1)[0]`.

**Gallery posting**: `post_to_channel(file_bytes, user_name, copies)` uploads raw bytes (not file_id — file IDs are bot-specific) via gallery bot to the channel. Returns the gallery bot's `file_id` from the sent message.

**Helpers**:
- `append_print_log(user, file_id, copies, status, error)` — wraps `write_log_entry`
- `post_to_gallery_channel(file_bytes, user, copies)` — wraps `post_to_channel` + `write_gallery_log_entry`

### monitor.py — background tasks

- **`poll_log`**: every 10 s, reads new lines from `print_log.jsonl`. `failed` → alert all sessions. `success` → check ribbon/paper against `RIBBON_THRESHOLDS`/`PAPER_THRESHOLDS`, alerting once per threshold (recorded in `alerts_sent`). Thresholds at or above a full load are skipped so a fresh reload doesn't alert on its first print. Also calls `check_auto_pause` so admins hear when `bot.py` pauses itself.
- **`daily_rotation_task`**: at midnight, if first entry is from a prior month → `os.replace` to `logs/print_log_YYYY_MM.jsonl` (appending if that archive exists), recreate the live log, reset `last_line_count`, delete `.ink_alerted`. Never copy-then-truncate — that loses any print landing in between.
- **Session auth**: password-based, persisted to `.sessions`.

### gallery.py — query bot

- Reads `gallery_log.jsonl` only (no archived logs — gallery is current ribbon only).
- Resends photos using `GALLERY_BOT_TOKEN` + stored `file_id` (gallery bot's own IDs, always reusable).
- Pagination: batches of 20, `/more` continues. State in memory per chat_id, reset on new command.

### Deployment scripts

- **`setup.sh`** — bootstraps a fresh Mac: checks macOS + Python 3.9–3.13, creates `.venv`, installs `requirements.txt`, looks for the Mitsubishi printer via `lpstat -p`, creates `.env` from `.env.example`, syntax-checks the three bot files. Refuses Python 3.14+.
- **`run.sh`** — starts each bot via `nohup .venv/bin/python <bot>.py >> logs/<bot>.log 2>&1 &` if not already running; saves PIDs (one per line, `name=PID` format) to `.pids`. Reads `.env` to verify configuration before starting.
- **`stop.sh`** — reads `.pids`, sends SIGTERM, waits up to 5 s, escalates to SIGKILL.
- **`status.sh`** — per-bot RUNNING/STOPPED with last 3 log lines; printer state via `lpstat -p`; queue depth from `queue.jsonl` if present; sizes of `print_log.jsonl` / `gallery_log.jsonl` / `queue.jsonl`; uptime from `.pids` mtime. **Detects launchd-managed bots too** by parsing `launchctl list | grep zapzap`.
- **`install-autostart.sh`** — writes three plists to `~/Library/LaunchAgents/com.local.zapzap.{print,monitor,gallery}_bot.plist` with `RunAtLoad=true` and `KeepAlive=true`, then `launchctl load`s them. Stops manual bots first.
- **`uninstall-autostart.sh`** — `launchctl unload`s and removes the three plists.

### Log formats

**`print_log.jsonl`** — one entry per print attempt:
```json
{
  "timestamp": "2026-04-25T06:32:11.123456+00:00",
  "user_id": 123456789,
  "user_name": "Alice",
  "username": "alice_tg",
  "copies": 2,
  "status": "success",
  "error": null,
  "photo_file_id": "AgACAgIAAxk..."
}
```

**`gallery_log.jsonl`** — one entry per successful print (gallery bot's file_id):
```json
{
  "timestamp": "2026-04-25T06:32:11.123456+00:00",
  "user_id": 123456789,
  "user_name": "Alice",
  "username": "alice_tg",
  "photo_file_id": "AgACAgIAAxk...",
  "copies": 2
}
```

`monitor.py` `/stats` and `/users` read current + all archived print logs. `/today` and `/history` read current only. `/ink` counts current log only (resets on rotation).

## Hub and dashboard (optional)

A booth with no `HUB_URL` set behaves exactly as it always has — the hub is
never in the print path. When configured, `monitor.py` posts a snapshot of this
booth to the hub every 10 s and the hub renders a dashboard.

```bash
# On the hub machine
python -m hub.main add-printer "Booth A"   # prints PRINTER_ID + PRINTER_API_KEY
python -m hub.main add-event "Sarah & Tom"
python -m hub.main assign <printer_id> <event_id>
python -m hub.main serve                   # dashboard at http://HUB_BIND:HUB_PORT
python -m hub.main printers                # status from the terminal

# On each booth Mac, in .env
HUB_URL=http://<hub-tailscale-address>:8080
PRINTER_API_KEY=<key printed by add-printer>
```

| Piece | Role |
|---|---|
| `hub/` | Server. sqlite + aiohttp + dashboard. **Never imports PIL or `printing/`** |
| `hubclient/` | Booth side, imported by `monitor.py`. **Never imports telegram**. Uses httpx (already a PTB dependency) |
| `tests/test_boundaries.py` | Enforces both rules above — run it after touching either package |

Telemetry is **snapshot, not event stream**: each heartbeat carries current
truth, so a missed beat costs nothing and needs no spool. Keep it that way when
adding fields — anything cumulative (e.g. shipping individual print records)
needs store-and-forward to survive a wifi drop.

`HUB_BIND` defaults to `127.0.0.1`. Set it to the hub's Tailscale address to
reach the dashboard from a phone without exposing it on venue wifi.
