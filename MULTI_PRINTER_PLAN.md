# Multi-Printer Hub Architecture

## Context

Today the operator deploys a **full separate stack** (bot.py + monitor.py + gallery.py, one Telegram bot token, one `.env`, one launchd install) per physical printer/Mac. That was fine for one printer but doesn't scale: every new printer means creating a new bot with @BotFather, duplicating config, and giving guests a different link depending on which printer/event they're at.

The physical constraint driving the design: the Mitsubishi CP-D90DW has no network interface — it's USB-only, so it must always be driven by a local process on the Mac it's plugged into. What *can* change is how that Mac learns what to print.

**Locked decisions from discussion with the user** (not open questions):
- Guests only ever use **one Telegram bot/link**, regardless of how many printers or simultaneous events exist.
- **No additional Telegram bot tokens**, even internal ones — ruled out a "Telegram-as-message-bus" design because avoiding polling conflicts across printers would have required one token per printer under the hood.
- A guest reaches the right event via a **deep-link start parameter** (`t.me/YourBot?start=<code>`), which the operator generates per event (e.g. as a QR code). An event can have multiple assigned printers (for concurrent throughput or multiple simultaneous events).
- Hub hosting: recommend a **small always-on cloud VM** (~$5-6/mo, DigitalOcean/Linode/Hetzner) rather than relying on one of the operator's Macs staying on 24/7 — that Mac being off/asleep would take down printing for *every* event, not just its own.
- Admin (create event/printer, assign, view status) happens via **Telegram `/admin` commands** on the same guest bot, not a separate tool — per user's explicit choice.
- Include **hub deployment scripts**, mirroring this repo's existing `setup.sh`/`run.sh`/`install-autostart.sh` pattern.

## Architecture

```
                    ┌─────────────────────────────┐
   guests  ───────► │  Hub (cloud VM)              │
   (1 bot)          │  - hub/bot.py (guest+admin)  │
                     │  - hub/api.py (aiohttp)      │
                     │  - hub.db (SQLite/WAL)       │
                     │  - gallery.py (moved here)   │
                     └─────────┬───────────┬────────┘
                        HTTPS poll   HTTPS poll
                     (bearer=api_key)
                     ┌─────────▼──┐   ┌─────▼───────┐
                     │ print_agent │   │ print_agent │  ...one per printer
                     │  Mac #1     │   │  Mac #2     │
                     │ + monitor.py│   │ + monitor.py│  (unchanged, per-Mac)
                     │ + printer   │   │ + printer   │
                     └─────────────┘   └─────────────┘
```

Each printer Mac polls *out* to the hub (no inbound networking needed on any Mac). The hub never touches Pillow/image processing or `lpr` — that stays on the printer's own Mac, same as today.

## Data model — `hub/schema.sql` (SQLite, WAL mode)

```sql
CREATE TABLE printers (
  printer_id      TEXT PRIMARY KEY,
  api_key_hash    TEXT NOT NULL UNIQUE,       -- sha256(api_key); plaintext shown once at creation
  display_name    TEXT NOT NULL,
  last_seen_at    TEXT,                        -- updated every /agent/poll (heartbeat)
  paused          INTEGER NOT NULL DEFAULT 0,
  created_at      TEXT NOT NULL,
  revoked_at      TEXT
);

CREATE TABLE events (
  event_id                TEXT PRIMARY KEY,
  deep_link_code           TEXT NOT NULL UNIQUE,   -- t.me/Bot?start=<code>
  display_name             TEXT NOT NULL,
  active                   INTEGER NOT NULL DEFAULT 1,
  max_prints_per_message   INTEGER,                -- NULL = hub default
  created_at               TEXT NOT NULL
);

CREATE TABLE event_printer_assignments (
  event_id     TEXT NOT NULL REFERENCES events(event_id),
  printer_id   TEXT NOT NULL REFERENCES printers(printer_id),
  assigned_at  TEXT NOT NULL,
  PRIMARY KEY (event_id, printer_id)
);

CREATE TABLE chat_sessions (
  chat_id   INTEGER PRIMARY KEY,     -- guest's Telegram chat_id
  event_id  TEXT REFERENCES events(event_id),
  bound_at  TEXT NOT NULL
);

CREATE TABLE jobs (
  job_id          TEXT PRIMARY KEY,   -- uuid4
  event_id        TEXT NOT NULL REFERENCES events(event_id),
  printer_id      TEXT REFERENCES printers(printer_id),   -- assigned at enqueue time
  chat_id         INTEGER NOT NULL,
  message_id      INTEGER NOT NULL,   -- reply target for preview/error
  user_id         INTEGER,
  user_name       TEXT,
  username        TEXT,
  copies          INTEGER NOT NULL,
  photo_ref       TEXT NOT NULL,      -- HUB_DATA_DIR/incoming/<job_id>.jpg
  status          TEXT NOT NULL DEFAULT 'pending',  -- pending|claimed|printing|success|failed
  created_at      TEXT NOT NULL,
  claimed_at      TEXT,
  completed_at    TEXT,
  error           TEXT,
  preview_ref     TEXT,               -- HUB_DATA_DIR/previews/<job_id>.jpg, set by agent on completion
  gallery_posted  INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_jobs_printer_status ON jobs(printer_id, status);
CREATE INDEX idx_jobs_event ON jobs(event_id);
```

No migration framework — `CREATE TABLE IF NOT EXISTS`, applied idempotently on hub startup.

**Printer selection (least-recently-assigned round robin)** among an event's online, unpaused, non-revoked printers:
```sql
SELECT p.printer_id
FROM event_printer_assignments epa
JOIN printers p ON p.printer_id = epa.printer_id
LEFT JOIN (SELECT printer_id, MAX(created_at) last_job_at FROM jobs GROUP BY printer_id) j
       ON j.printer_id = p.printer_id
WHERE epa.event_id = ? AND p.revoked_at IS NULL AND p.paused = 0
  AND p.last_seen_at > ?   -- now - PRINTER_OFFLINE_AFTER_SECONDS
ORDER BY j.last_job_at IS NOT NULL, j.last_job_at ASC
LIMIT 1;
```
If no printer qualifies, the hub rejects the photo immediately with a clear guest-facing message rather than silently queueing an orphaned job.

**Atomic job claim** (race-free even with concurrent `/agent/poll` requests): `BEGIN IMMEDIATE` → `SELECT ... WHERE printer_id=? AND status='pending' ORDER BY created_at LIMIT 1` → `UPDATE ... SET status='claimed'` in one transaction, serialized through the hub's single aiosqlite connection.

## New files

**Shared modules (extracted from `bot.py`, used by both legacy `bot.py` and the new hub/agent):**
- `image_pipeline.py` — move `fix_exif_rotation` ([bot.py:121](bot.py#L121)), `fit_to_paper` ([bot.py:140](bot.py#L140)), `get_watermark`/`apply_watermark` ([bot.py:163-199](bot.py#L163)), plus `PAPER_W_PX`/`PAPER_H_PX`/`WATERMARK_*` constants. Needs Pillow. Used by `bot.py` and `print_agent.py` only (hub never touches images).
- `caption_parsing.py` — move `parse_copy_list`, `parse_copies`, `validate_print_limit` ([bot.py:51-118](bot.py#L51)), `MAX_COPIES`/`MAX_PRINTS_PER_MESSAGE`. Pure stdlib, no Pillow. Used by `bot.py` and `hub/bot.py`.
- `local_state.py` — move pause/supply tracking ([bot.py:338-379](bot.py#L338)) and `write_log_entry`/`append_print_log` ([bot.py:219-239](bot.py#L219), [bot.py:298-307](bot.py#L298)). Used by `bot.py`, `monitor.py`, and `print_agent.py` (also removes existing duplication between `bot.py` and `monitor.py`).

`bot.py` itself keeps 100% current behavior — only its imports change (inline definitions replaced with imports from the three modules above). Operators running a single printer keep using `setup.sh`/`run.sh`/`install-autostart.sh` completely unchanged.

**Hub package (`hub/`), deployed on the cloud VM:**
- `hub/config.py` — env loading (`HUB_DB_PATH`, `HUB_DATA_DIR`, `HUB_HTTP_HOST/PORT`, `HUB_ADMIN_PASSWORD`, `PRINTER_OFFLINE_AFTER_SECONDS`, `PRINT_BOT_TOKEN`, `GALLERY_BOT_TOKEN`/`GALLERY_CHANNEL_ID`, `MAX_PRINTS_PER_MESSAGE`).
- `hub/schema.sql`, `hub/db.py` — aiosqlite connection (WAL pragma), atomic claim query, CRUD helpers for all 5 tables.
- `hub/admin_auth.py` — password-gated session pattern, ported from `monitor.py`'s `authenticated_sessions`/`load_sessions`/`save_sessions`/`is_authenticated`/`require_auth` ([monitor.py:57-74](monitor.py#L57), [monitor.py:206-213](monitor.py#L206)). **Deliberate deviation**: unlike `monitor.py:581` (any unrecognized text = password attempt), `hub/bot.py` also serves guests, so admin login must be the explicit command `/admin <password>` — free-text password matching would misfire on guest chatter.
- `hub/bot.py` — guest-facing Application on the single `PRINT_BOT_TOKEN`:
  - `/start <code>` → look up `deep_link_code`, bind `chat_sessions`, reply with instructions (reuse the guest-instruction text style from [bot.py:388-413](bot.py#L388)).
  - Photo/album handling: same `media_group_id` buffering pattern as [bot.py:579-607](bot.py#L579) (album_buffer, 1.5s timer). Per photo: check `chat_sessions` binding → `parse_copy_list`/`validate_print_limit` from `caption_parsing.py` → download bytes → run printer-selection query → write `HUB_DATA_DIR/incoming/<job_id>.jpg` → insert `jobs` row (`pending`) → reply `"Printing N copies..."`. Albums naturally spread across an event's assigned printers since each photo gets its own selection call.
  - Admin commands (after `/admin <password>`): `/newevent <name>` (generates `event_id` + `deep_link_code` via `secrets.token_urlsafe(6)`, replies with the ready `t.me/<bot>?start=<code>` link), `/newprinter <name>` (generates `printer_id` + `api_key` via `secrets.token_urlsafe(32)`, stored hashed, shown once), `/assign <event_id> <printer_id>` / `/unassign`, `/events`, `/printers` (online/offline from `last_seen_at`, paused state), `/endevent <event_id>`, `/logout`.
  - Gallery posting moves here (ported from [bot.py:275-320](bot.py#L275)) since it's now hub-side, triggered from the result-relay handler below — `gallery.py` also now runs on the hub, reading the hub's `gallery_log.jsonl`, not on a printer Mac.
- `hub/api.py` — aiohttp app, all endpoints require `Authorization: Bearer <api_key>` matched against `printers.api_key_hash`:
  - `GET /agent/poll` — updates `last_seen_at`/`paused` (heartbeat), atomically claims one pending job for this printer_id, returns `{"job": null}` or job details.
  - `GET /agent/jobs/{job_id}/photo` — streams the raw JPEG; 403 if not claimed by this printer_id.
  - `POST /agent/jobs/{job_id}/result` — multipart (`status`, optional `error`, `preview` file). Updates the job row, then directly sends the guest the preview photo + "Done!"/error text (reply-to the original message) and posts to the gallery channel on success — no separate poll loop needed since the hub bot instance lives in the same process.
- `hub/main.py` — wires `hub/bot.py`'s PTB Application and `hub/api.py`'s aiohttp server into one asyncio event loop (PTB v21 supports manual `initialize()`/`start()`/`updater.start_polling()` instead of the blocking `run_polling()`, run alongside `aiohttp.web.AppRunner`), plus graceful shutdown.

**Print agent, deployed on each printer Mac (top-level, alongside `bot.py`/`monitor.py`/`gallery.py`):**
- `print_agent.py` — poll loop: if locally paused ([local_state.py](local_state.py) `is_paused()`, same `.bot_paused` mechanism as today), sleep; else `GET /agent/poll`. On a job: fetch photo bytes, run `image_pipeline.fix_exif_rotation` → `fit_to_paper` → `apply_watermark`, save a preview buffer, print via **unchanged** `printing.get_printer_backend().print_image()` ([printing/\_\_init\_\_.py:23](printing/__init__.py#L23), same call shape as [bot.py:214](bot.py#L214)), `local_state.increment_supply_used`/`append_print_log`, then `POST /agent/jobs/{id}/result` with the outcome + preview bytes.
- No `PRINT_BOT_TOKEN` needed on printer Macs at all — the agent never talks to Telegram directly. `monitor.py` keeps running per-Mac completely unchanged (local pause/supply/ink are physical, per-Mac concerns, not hub concerns) — it already reads `print_log.jsonl`, which `print_agent.py` writes via the same `local_state.append_print_log` used by `bot.py` today.

**Printer backend addition (for local testing without hardware):**
- `printing/dummy_backend.py` — new `PrinterBackend` that logs "would have printed N copies of `<path>`" and always succeeds (or fails, via `DUMMY_PRINTER_FAIL=1`, to test the failure path).
- One additive line at the top of `get_printer_backend()` ([printing/\_\_init\_\_.py:23](printing/__init__.py#L23)): `if os.getenv("PRINTER_BACKEND") == "dummy": return DummyPrinterBackend()`. Zero effect on existing deployments that never set `PRINTER_BACKEND`.

**Deployment scripts:**
- Hub (Linux cloud VM): `hub_setup.sh` (installs Python deps from `requirements-hub.txt`, creates `hub.env` from `hub.env.example`, initializes `hub.db`), `hub_run.sh`/`hub_stop.sh` (mirrors `run.sh`/`stop.sh`), `hub.service` (systemd unit — Linux equivalent of `install-autostart.sh`'s launchd plists, `RestartAlways`).
- Agent (per printer Mac): `agent_setup.sh` (mirrors `setup.sh`: checks Python version, creates `.venv`, installs `requirements-agent.txt`, detects the printer via `lpstat -p`, creates `agent.env` from `agent.env.example`), `agent_run.sh`/`agent_stop.sh`, and a launchd plist for `print_agent.py` added to `install-autostart.sh`'s existing pattern (alongside the existing print/monitor/gallery plists — `monitor.py` still runs locally too).

**New requirements files:**
- `requirements-hub.txt`: `aiohttp`, `aiosqlite`, `python-telegram-bot==21.6`, `python-dotenv` (no Pillow — hub never processes images).
- `requirements-agent.txt`: `pillow>=10.0`, `aiohttp`, `python-dotenv` (no python-telegram-bot — agent never talks to Telegram).

**New env example files:** `hub.env.example`, `agent.env.example` (existing `.env.example` for single-machine `bot.py` deployments stays untouched).

## What's explicitly out of scope for this pass (documented, not built)

- Live queue-depth-aware printer selection (using each agent's existing `PrinterBackend.queue()`, [printing/base.py:34](printing/base.py#L34)) — round robin by recency is the v1 balancing strategy.
- TLS in front of `hub/api.py` — **must be added before any real deployment** (nginx/Caddy + Let's Encrypt) since `PRINTER_API_KEY` travels as a bearer token, but is an infra step, not application code, and is called out here so it isn't forgotten.
- `HUB_DATA_DIR` retention/cleanup cron and hub-side `/stats` admin command — small additive follow-ups once the core pipeline is proven.
- Hub HA/replicas — out of scope at this scale; a nightly `sqlite3 hub.db .backup` cron is enough resilience.

## Verification (local, before any real cloud deployment)

1. Run `hub/main.py` locally against a scratch `hub.db` (schema auto-created).
2. Point a real test Telegram bot token at it — PTB polling works fine locally, no inbound networking needed.
3. `/admin <password>` → `/newevent`, `/newprinter` ×2, `/assign` both printers to the event.
4. Run two `print_agent.py` processes locally with `HUB_URL=http://127.0.0.1:8080` and `PRINTER_BACKEND=dummy` — no macOS/`lpr`/real printer needed.
5. From a real Telegram account: `/start <code>`, send a photo captioned `2`. Confirm `jobs` row goes `pending → claimed → printing → success`, the dummy backend logs the simulated print, guest receives preview + "Done!", and a gallery post appears if `GALLERY_BOT_TOKEN`/`GALLERY_CHANNEL_ID` are set.
6. Send a 3-photo album captioned `"1,2,3"` — confirm 3 job rows and round-robin spread across the two dummy printers.
7. Set `DUMMY_PRINTER_FAIL=1` on one agent — confirm the job is marked `failed`, error relayed to guest, gallery posting skipped (matches current success-only gallery posting, [bot.py:473](bot.py#L473)).
8. Concurrency check: burst several photos with both agents polling; `sqlite3 hub.db "select job_id,printer_id,status from jobs"` confirms no job is ever claimed by two printers.
9. Backward-compat check: run legacy `./run.sh` (`bot.py`+`monitor.py`+`gallery.py`) against a separate test token — confirms the `image_pipeline.py`/`caption_parsing.py`/`local_state.py` extraction didn't change single-machine behavior.
