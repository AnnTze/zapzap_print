# Booth Setup Checklist

Follow this **at the machines**, in order. For the reasoning behind any step,
see [HUB_SETUP.md](HUB_SETUP.md) — this file is just the doing.

> **If anything here fails, printing still works.** The hub is never in the
> print path. A booth with no `HUB_URL` behaves exactly as it does today, so
> you can stop at any point and the booth is still event-ready.

---

## Before you start

- [ ] Hub Mac (yours) is on and awake
- [ ] Both booth computers are on, with their printers connected
- [ ] You know which login you'll use for Tailscale — **the same account on all
      three machines**. Google or Microsoft sign-in is easiest. Nothing works if
      the booths join a different tailnet from the hub.
- [ ] All three machines can reach the internet
- [ ] You can install software on each machine (admin rights / the Mac's password)

**None of the three machines has Tailscale yet**, so each one needs an install
and a browser sign-in. Budget ~10 minutes per machine for that part alone.

**Write these down as you go:**

| | Value |
|---|---|
| Hub Tailscale address | `100.___.___.___` |
| Booth A (Mac) `PRINTER_API_KEY` | |
| Booth B (Windows) `PRINTER_API_KEY` | |
| Event id (optional) | |

---

## Part 1 — Hub (your Mac). Do this first.

You need the API keys before you can set up either booth.

- [ ] **Install Tailscale** — in Terminal.app (it needs your password):
      ```bash
      brew install --cask tailscale
      ```
      *(Or download the installer from <https://tailscale.com/download/mac>.)*
      Then open Tailscale from Applications → **Log in**. This first sign-in
      creates your tailnet; the booths will join it.

- [ ] **Note the address.** Click the Tailscale menu-bar icon; the top line shows
      `100.x.y.z`. Write it in the table above.

- [ ] **Configure the hub:**
      ```bash
      cd ~/Desktop/Everything/Code/zapzap_print
      git fetch origin && git checkout hub-dashboard && git pull
      cp hub.env.example hub.env
      ```
      Edit `hub.env` and set `HUB_BIND=100.x.y.z` (your address).

- [ ] **Install hub dependencies:**
      ```bash
      pip install -r requirements-hub.txt
      ```

- [ ] **Register both booths** — each prints a key. Copy them into the table.
      ```bash
      python -m hub.main add-printer "Booth A"
      python -m hub.main add-printer "Booth B"
      ```

- [ ] **Create an event** (needed later for watermarks):
      ```bash
      python -m hub.main add-event "Test Event"
      python -m hub.main assign <booth-a-printer-id> <event-id>
      python -m hub.main assign <booth-b-printer-id> <event-id>
      ```

- [ ] **Start the hub:**
      ```bash
      python -m hub.main serve
      ```
      Leave this running. Open `http://100.x.y.z:8080` on the Mac — you should
      see both booths listed as **offline**. That's correct: they haven't
      reported yet.

---

## Part 2 — Booth A (Mac)

### 2a. Tailscale

- [ ] **Download the installer** — <https://tailscale.com/download/mac> → the
      standard macOS download. Open the `.pkg` and follow it through.

      *(If this Mac has Homebrew you can instead run
      `brew install --cask tailscale` in Terminal.app — it will ask for the
      Mac's password. The downloaded installer works either way and needs no
      Homebrew.)*

- [ ] **Open Tailscale** from Applications → **Log in** → sign in with the
      **same account you used on the hub**. A browser window opens; approve it.

- [ ] **Check the menu bar** — the Tailscale icon should be solid, and clicking
      it lists all machines on the tailnet, including your hub Mac.

- [ ] **Confirm it can see the hub** — in Terminal:
      ```bash
      ping -c 3 100.x.y.z
      ```
      Replies mean the tunnel is up.

- [ ] **Open the dashboard** in this Mac's browser: `http://100.x.y.z:8080`

      **Do not continue until this loads.** If it doesn't, it's a Tailscale
      problem, and no amount of `.env` editing will fix it. Check both machines
      are signed into the same account.

### 2b. The bots

- [ ] **Update the code:**
      ```bash
      cd <repo folder>
      ./stop.sh
      git fetch origin && git checkout hub-dashboard && git pull
      ```

- [ ] **Add two lines to `.env`** (keep everything already in it):
      ```ini
      HUB_URL=http://100.x.y.z:8080
      PRINTER_API_KEY=<Booth A key from the table>
      ```

- [ ] **Start the bots:**
      ```bash
      ./run.sh
      ./status.sh
      ```

- [ ] **Confirm on the dashboard** — Booth A turns **online** within 10 seconds,
      showing ribbon, paper and queue.

- [ ] **Send a test photo** to the print bot. Confirm it prints, and that
      *Prints today* increments on the dashboard.

---

## Part 3 — Booth B (Windows)

Same sequence, PowerShell commands. Run PowerShell **as your normal user** in
the repo folder (not "as Administrator" — the bots run as you).

### 3a. Tailscale

- [ ] **Download the installer** — <https://tailscale.com/download/windows> →
      run the `.exe`. It will ask for admin permission once.

- [ ] **Sign in** — Tailscale opens a browser window. Use the **same account you
      used on the hub**.

- [ ] **Check the system tray** (bottom-right, possibly under the `^` arrow) —
      the Tailscale icon should show *Connected*, and its menu lists the other
      machines.

- [ ] **Confirm it can see the hub** — in PowerShell:
      ```powershell
      ping 100.x.y.z
      ```
      Replies mean the tunnel is up.

      *(`tailscale ip -4` also works if the installer added it to PATH. If
      PowerShell says it isn't recognised, ignore it and use the tray icon.)*

- [ ] **Open the dashboard** in this PC's browser: `http://100.x.y.z:8080`

      Stop here if it doesn't load — that's Tailscale, not the bots.

### 3b. The bots

- [ ] **Update the code:**
      ```powershell
      cd <repo folder>
      .\stop.ps1
      git fetch origin; git checkout hub-dashboard; git pull
      ```

- [ ] **Add two lines to `.env`:**
      ```ini
      HUB_URL=http://100.x.y.z:8080
      PRINTER_API_KEY=<Booth B key from the table>
      ```

- [ ] **Start the bots:**
      ```powershell
      .\run.ps1
      .\status.ps1
      ```

- [ ] **Confirm on the dashboard** — Booth B turns **online**.

---

## Part 4 — Windows first-run proof

These three checks confirm fixes that have **never run on real Windows
hardware**. Do them before trusting the machine at an event.

- [ ] **`/newribbon 700`** in the monitor bot → replies normally.
      *(This used to crash outright — `%-d` date formatting.)*

- [ ] **Print a photo, then `/ink`** → the counter has moved.
      *(Proves the `msvcrt` file-locking path in `supply_lock.py`.)*

- [ ] **That same photo appears in the gallery channel.**
      *(This was silently failing on Windows — every print was missing from the
      gallery.)*

If any of these fail, note the exact error from `logs\monitor.log` or
`logs\bot.log` — that's the useful bit.

---

## Part 5 — Watermark from the hub

- [ ] On the dashboard, scroll to **Events & watermarks**
- [ ] **Drag a PNG** onto your test event
- [ ] Adjust **size / opacity / margin / position** — the preview shows where it
      lands on the paper
- [ ] Press **Apply to booths**
- [ ] **Within 10 seconds**, print a photo from either booth — the new mark
      should be on it, with no restart of anything

- [ ] Press **Clear** and print again — the booth returns to whatever
      `WATERMARK_PATH` in its own `.env` points at

Size is a fraction of the **shorter** side of the paper, so `0.6` is large.
`0.3–0.4` is usually what you want.

---

## Part 6 — Leave it running (optional, for a real event)

- [ ] Booths: `./install-autostart.sh` (Mac) or `.\install-autostart.ps1`
      (Windows) so the bots survive a reboot

      ⚠️ **Don't run both `./run.sh` and autostart** — two copies of the same
      bot cause Telegram "Conflict: terminated by other getUpdates request".
      `stop.sh` first.

- [ ] Hub Mac: prevent sleep, or the dashboard dies mid-event
      ```bash
      caffeinate -dims
      ```
      (Printing is unaffected either way.)

---

## If something's wrong

| Symptom | Cause | Fix |
|---|---|---|
| Booth shows **offline**, but prints fine | `monitor.py` not running, or wrong `HUB_URL`/key | `./status.sh`, then check `logs/monitor.log` |
| Dashboard won't load on a booth | Tailscale not connected on that machine | Check both are in the same tailnet |
| `Can't assign requested address` on hub start | Hub started before Tailscale came up | Wait, start it again |
| Booth online but no watermark change | Booth not assigned to that event | `python -m hub.main printers` |
| Telegram "Conflict: terminated by other getUpdates" | Two copies of a bot running | `./stop.sh`, then start one way only |

`logs/monitor.log` says `Hub connected` on success and `Hub unreachable` on
failure — that one line answers most questions.
