# Handoff — read this first

State of the project as of **16 August 2026**. Written so a fresh Claude Code
session (or a future you) can pick up without re-deriving anything.

Everything below is on branch **`hub-dashboard`**, pushed to
`github.com/AnnTze/zapzap_print`. It has **not** been merged to `main`, and
`main` is months behind.

---

## What runs where, right now

| Machine | Role | Runs | Lifecycle |
|---|---|---|---|
| Darren's MacBook Pro | **Hub only** — no bots, no printer | `python3 -m hub.main serve` | `nohup`, **dies on reboot** |
| `photog-mb3` (Mac) | Booth A | `bot.py`, `monitor.py` (+ `gallery.py`, disabled) | launchd, autostarts |
| Windows PC | Booth B | `bot.py`, `monitor.py`, `gallery.py` | NSSM services |

- Hub dashboard: `http://100.119.92.54:8080` (the MacBook's Tailscale address).
- All machines plus an iPhone are on one tailnet, account `darrenwongjunhui@`.
- `HUB_BIND` is the Tailscale address, so the dashboard is **not** exposed on
  venue wifi. It has no login — tailnet membership is the authentication.
- Both booths report every 10s and were verified live. Booth A has ribbon
  capacity 400; Booth B is 700 ribbon / 200 paper.

**Credentials are not in this repo.** `PRINTER_API_KEY` values live in
`hub.db` on the hub and in each booth's `.env`. Both are gitignored.

---

## What's built and working

- **Hub + telemetry.** Booths post a status snapshot every 10s; the dashboard
  shows online/offline, prints today, queue, ribbon and paper. Snapshot-based,
  so a missed beat self-heals and no spool file is needed.
- **Watermark push.** Drop a PNG on an event in the dashboard, adjust
  size/opacity/margin/position, Apply — booths fetch it within 10s and use it
  on the next print with no restart. Verified end to end.
- **Supply auto-pause.** `bot.py` pauses itself when ribbon or paper hits zero
  and `/newribbon` or `/newpaper` resumes it automatically. This fired for real
  during setup.
- **Cross-platform fixes.** `strftime("%-d")` crashed `/newribbon`,
  `/newpaper` and `/gallery` on Windows and silently dropped every Windows
  print from the gallery channel; `supply_lock.py` now uses `fcntl`/`msvcrt`.
- **Lifecycle.** `run.sh`/`stop.sh`/`status.sh`/`install-autostart.sh` start the
  hub too, gated on `hub.env` existing; bot agents gated on `.env` existing.

Guides: `HUB_SETUP.md` (reference), `BOOTH_SETUP_CHECKLIST.md` (field
checklist), `BOOTH_UPGRADE.md` (in-place upgrade), `WINDOWS_SETUP.md`,
`DEPLOY.md`.

---

## Next steps, ranked

### 1. Give the hub a launchd agent — it currently dies on reboot
Running under `nohup` on the MacBook. Needs a plist plus `caffeinate -dims`,
or the dashboard goes dark mid-event. `install-autostart.sh` already installs a
hub agent when `hub.env` exists, but it has never been run on the hub machine.

### 2. Four LumaBooth checks — ~10 minutes on the Windows booth
These gate every LumaBooth integration decision (findings below):
1. **Port and password** — Settings → General → API on the booth.
2. **Does the API bind LAN or localhost?** From the Mac:
   `curl "http://<booth-tailscale-ip>:<port>/api/lockscreen/hide?password=<pw>"`.
   JSON back = remote control is possible. Connection refused = it isn't.
3. **Does a hung trigger URL stall a session?** Point the trigger at
   `http://192.0.2.1:9/` (hangs rather than refuses) and run a full session.
   If it stalls, the hub must respond before touching SQLite. Potential blocker.
4. **What is already in the trigger URL field?** There appears to be only one,
   so adding ours may displace something in use.

### 3. Time-to-empty on the dashboard
"Paper: ~35 min" instead of "254/400". Rated highly by the design research —
the difference between a state and a decision. Needs a print-rate calculation
in the hub, not a CSS change.

### 4. Silence `httpx` request logging
`httpx` logs full URLs at INFO, so every bot token is written into
`logs/bot.log` on every poll. Means log files can't be shared with anyone.
One line per bot.

---

## Open decisions

- **Gallery bot is deliberately disabled** on Booth A (token commented out in
  `.env`). Consequence: **photos are not reaching the gallery channel** from
  that booth, because `bot.py` uses the same token to post. `gallery.py` also
  crash-loops under launchd. Decide whether to re-enable or remove the agent.
- **Three bot tokens were pasted into a chat** during setup and should be
  rotated via @BotFather when convenient. Separately, a token committed in the
  initial commit is still readable in git history — that one appears to have
  already been rotated (the live token differs).
- **`MONITOR_PASSWORD` on the Windows booth was `changeme`** at last check.

---

## Research findings worth keeping

Two agents researched these; the full reports exist only in the original
conversation, so the conclusions are captured here.

### Apple HIG (already implemented, 16 Aug)

- **"Clarity, Deference, Depth" is retired** — that HIG page 404s. Don't cite it.
- Apple's **default system colours are fills, not text**: systemGreen as text is
  2.22:1 against a 4.5:1 minimum. Even the increased-contrast variants only just
  clear 4.5:1 on pure white, and **any tint breaks them** — measured. The pills
  therefore use text darker than Apple's AX variants; all six light/dark states
  now measure 4.86–6.21:1.
- **Ribbon/paper are capacity gauges, not progress indicators** (progress
  indicators are transient by definition). Fill changes at thresholds.
- **Never colour alone** — green/orange/red is the worst triple for the
  commonest colour blindness, and the HIG names it. Status now carries glyph +
  word + colour.
- **SF Symbols are licensed for Apple-platform software only** — not usable on
  a web page, even as traced paths. Glyphs in `dashboard.html` are hand-drawn.
- **Safari cannot report Reduce Transparency**, so the header's base colour must
  carry text on its own; blur is garnish.
- Not done: time-to-empty (see above).

### LumaBooth (nothing implemented — user asked to review first)

The API surface was retrieved verbatim from the vendor's Postman collection.

- **Six routes only**, all GET, auth via `?password=` in the URL:
  `/api/start?mode=print|gif|boomerang|video`, `/api/print?count=N` (reprints
  the *last* picture only), `/api/share/email`, `/api/share/sms`,
  `/api/lockscreen/show`, `/api/lockscreen/hide`.
- **Always returns HTTP 200** — errors are in an `IsSuccessful` field in the
  body. Branch on that, not the status code.
- **Triggers are positional**: `?event_type=printing&param1=<file>&param2=<copies>&param3=<printer>`,
  not named. Indices may shift between versions; parse defensively.
- **Put the auth token in the URL path, not the query string** — LumaBooth
  appends `?event_type=…` and may not detect an existing query string.
- **Recommended approach: a Windows spooler sidecar, not triggers.**
  `printing/windows_backend.py` already has `queue()` and `is_ready()`, and
  `hubclient` already speaks the heartbeat protocol. Reading the spooler is
  snapshot-based and self-healing, catches prints LumaBooth never reports, and
  gives printer error state that LumaBooth does not expose at all. Trigger
  counting fails in both directions with no reconciliation: `printing` fires on
  submission not completion, and there is no `print_complete`/`print_failed`.
- **Watermarks can never reach a LumaBooth booth** — no template or overlay
  control exists. Such a booth is a telemetry source and remote-control target
  only. The dashboard must show this distinction or an operator will assume a
  watermark was pushed when it wasn't.
- **Hazard: if a LumaBooth booth and a Telegram booth share one physical
  printer, both counting schemes break silently** and the ribbon runs out with
  both counters reading healthy. Supply would have to be tracked per printer at
  the spooler, not per booth.
- `file_upload` yields a **public** `fotoshare.co` URL, usable for a gallery
  view, but only when fotoShare is enabled and the booth is online.

---

## Gotchas that have already bitten

- **Never mix `./run.sh` with autostart.** Two copies of a bot cause
  `Conflict: terminated by other getUpdates request`. This happened twice.
  With launchd in charge, restart via
  `launchctl kickstart -k gui/$(id -u)/com.local.zapzap.<name>`; with NSSM, use
  `nssm restart <service>`.
- **Each booth needs its own three Telegram bots.** Telegram allows one poller
  per bot; reusing tokens across machines causes the same Conflict.
- `.env` holds `HUB_URL` and `PRINTER_API_KEY` (client side). `hub.env` holds
  `HUB_BIND`/`HUB_PORT`/`HUB_DB` (server side). Putting `HUB_URL` in `hub.env`
  fails silently — nothing reads it there.
- The hub is **never** in the print path. Restarting it, editing the dashboard,
  or losing the MacBook entirely cannot stop a booth printing.
- `tests/test_boundaries.py` enforces that `hub/` never imports PIL or
  `printing/`, and `hubclient/` never imports telegram. Run it after touching
  either package.
