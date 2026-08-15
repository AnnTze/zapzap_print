# Hub & Dashboard Setup

How to get the monitoring dashboard running and point booths at it.

**The hub is never in the print path.** A booth with no `HUB_URL` set behaves
exactly as it always has, and a booth whose hub is asleep or unreachable keeps
printing. If any step here fails, printing is unaffected.

Machines involved:

| Role | Machine | Runs |
|---|---|---|
| Hub | Darren's MacBook Pro (home) | `python -m hub.main serve` |
| Booth A | Mac + CP-D90DW | `bot.py`, `monitor.py`, `gallery.py` |
| Booth B | Windows + CP-D90DW | same three, via the `.ps1` scripts |
| Viewer | Your phone | browser only |

All of them need Tailscale, because the booths travel to venues and are not on
the hub's network.

---

## 1. Tailscale

Booths sit behind whatever NAT the venue has, and the hub sits at home. Nothing
needs an open port: every connection is outbound, and Tailscale gives the hub a
stable address that follows it between networks.

### On the hub Mac

```bash
brew install --cask tailscale
```

Run this in **Terminal.app** — it needs a sudo password and cannot be run
through an automated shell. Or download the installer from
<https://tailscale.com/download/mac> and double-click it.

Then open Tailscale from Applications and click **Log in**. Use an account you
can sign into on every device (Google is usually easiest). This first sign-in
creates your tailnet. The free plan allows 100 devices.

### Find the hub's address

Click the Tailscale menu-bar icon. The top entry reads:

```
This device: darrens-macbook-pro   100.x.y.z
```

That `100.x.y.z` is stable and follows the Mac between networks. From a
terminal:

```bash
/Applications/Tailscale.app/Contents/MacOS/Tailscale ip -4
```

The Homebrew cask installs the GUI app; the CLI lives inside the bundle, so an
alias is handy:

```bash
alias tailscale="/Applications/Tailscale.app/Contents/MacOS/Tailscale"
```

### On every other device

- **Windows booth** — <https://tailscale.com/download/windows>, install, sign in
  with the **same account**.
- **Mac booth** — same as the hub Mac above.
- **Your phone** — Tailscale from the App Store / Play Store, same account.
  This is how you view the dashboard during an event.

Each device appears in the menu-bar device list once signed in.

---

## 2. The hub

On the hub Mac, from the repo:

```bash
cp hub.env.example hub.env
pip install -r requirements-hub.txt
```

Edit `hub.env`:

```ini
HUB_DB=hub.db
HUB_BIND=100.x.y.z      # the Tailscale address from step 1
HUB_PORT=8080
```

**Bind the Tailscale address, not `0.0.0.0`.** The dashboard has no login, so on
`0.0.0.0` anyone on the venue's wifi could open it. Tailscale traffic is
WireGuard-encrypted, which is why no TLS certificate is needed.

Register the booths — each command prints an API key you'll need in step 3:

```bash
python -m hub.main add-printer "Booth A"
python -m hub.main add-printer "Booth B"
python -m hub.main add-event "Sarah & Tom"      # optional
python -m hub.main assign <printer_id> <event_id>
```

Start it:

```bash
python -m hub.main serve
```

Other useful commands:

```bash
python -m hub.main printers    # status from the terminal, no browser needed
```

---

## 3. Test the network before involving the bots

With the hub running, open `http://100.x.y.z:8080` **in a browser on the
Windows booth**. You should see the dashboard saying "No booths registered yet"
or listing your printers as offline.

Do not skip this. It separates "Tailscale isn't routing" from "the bot isn't
reporting", and those two failures look identical from the dashboard.

---

## 4. The booths

On each booth Mac/PC, pull the branch and add two lines to `.env`:

```ini
HUB_URL=http://100.x.y.z:8080
PRINTER_API_KEY=<the key printed by add-printer for THIS booth>
```

Restart `monitor.py` (`./stop.sh && ./run.sh` on Mac, the `.ps1` equivalents on
Windows). Within 10 seconds the booth appears on the dashboard.

No new packages are needed — `hubclient` uses `httpx`, which python-telegram-bot
already installs.

Telemetry rides inside `monitor.py`. If `monitor.py` isn't running, that booth
shows offline on the dashboard while continuing to print perfectly.

---

## 5. First-run checks on the Windows booth

Two code paths have never executed on real Windows hardware. Confirm them:

| Check | Proves |
|---|---|
| `/newribbon 700` replies instead of throwing | the `datefmt` fix — this used to be a hard crash |
| Print a photo, then `/ink` shows the counter moved | the `msvcrt` file-locking branch in `supply_lock.py` |
| That same photo appears in the gallery channel | a silent failure that dropped every Windows print from the gallery |

---

## Troubleshooting

**Booth shows offline but is printing fine.** `monitor.py` isn't running, or
`HUB_URL` / `PRINTER_API_KEY` is wrong. Check `logs/monitor.log` — the client
logs "Hub connected" on success and "Hub unreachable" on failure, then goes
quiet rather than warning every 10 seconds.

**`Can't assign requested address` when the hub starts at boot.** The hub
started before Tailscale brought its interface up. `KeepAlive=true` in a launchd
plist retries until it succeeds. Expected on reboot, not a real failure.

**Dashboard unreachable from the phone.** Check the phone is signed into the
same tailnet, and that the hub Mac is awake. A closed lid stops the dashboard —
printing continues.

**Hub Mac sleeping.** For an event, the hub needs `caffeinate -dims` and a
launchd plist with `KeepAlive`, following the pattern in `install-autostart.sh`.
Not needed for testing.

---

## What isn't built yet

- Watermark assignment from the dashboard (drag-and-drop upload, one mark per
  event). The config channel exists in the heartbeat response but carries
  nothing yet.
- Per-print history and failure lists. `prints_today` comes from the snapshot;
  there is no stored per-print record on the hub.
- Photos are **not** stored on the hub by design — the dashboard will link to
  the existing Telegram gallery channel.
- launchd plist for the hub itself.
