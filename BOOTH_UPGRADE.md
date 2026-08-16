# Upgrading a Booth In Place

Same Mac, same printer, same `.env` — just moving the code to the current
branch (`hub-dashboard`) and connecting it to the hub.

**Nothing is migrated.** Your ribbon counts, print history, gallery log,
sessions and `.env` are all gitignored, so `git checkout` never touches them.
They stay exactly as they are.

Rough time: 10 minutes, most of it verification.

---

## Step 1 — Look before you leap

```bash
cd <repo folder>
git branch --show-current      # write this down — it's your rollback
git status --short             # should be empty
```

- [ ] **Note the current branch.** If anything goes wrong, `git checkout <that
      branch>` puts you straight back.

- [ ] **If `git status` is not empty**, someone edited tracked files on this
      machine. Don't blow them away — run `git stash` to set them aside, or
      show me the output and we'll work out what they are.

- [ ] **Note your supply counts** — send `/ink` to the monitor bot and write
      down the ribbon and paper numbers. You'll confirm they survived at the end.

---

## Step 2 — Stop the bots

- [ ] **Find out how they're running:**
      ```bash
      ./status.sh
      launchctl list | grep zapzap
      ```

- [ ] **If started with `./run.sh`:**
      ```bash
      ./stop.sh
      ```

- [ ] **If `launchctl` printed anything**, they auto-start and `./stop.sh` is
      not enough — launchd restarts them within seconds:
      ```bash
      ./uninstall-autostart.sh
      ```

- [ ] **Confirm they're really stopped:**
      ```bash
      ps aux | grep -E "[b]ot\.py|[m]onitor\.py|[g]allery\.py"
      ```
      No output means clear. If anything lingers:
      ```bash
      pkill -f bot.py; pkill -f monitor.py; pkill -f gallery.py
      ```

---

## Step 3 — Update the code

```bash
git fetch origin
git checkout hub-dashboard
git pull
```

- [ ] No new packages are needed — the booth's dependencies are unchanged.
      (`hubclient` uses `httpx`, which python-telegram-bot already installs.)

---

## Step 4 — Check the watermark before you print anything

**This is the step people get surprised by.**

`bot.py` defaults `WATERMARK_PATH` to `watermark.png`, and that file *is* in the
repo. So if your `.env` has no `WATERMARK_*` lines, **every print will now carry
the watermark** at 60% of the paper's shorter side — large, and very visible.

- [ ] **Check what your `.env` says:**
      ```bash
      grep WATERMARK .env
      ```

- [ ] **If nothing is printed above**, decide now:

      **Want watermarks?** Add settings and tune them:
      ```ini
      WATERMARK_PATH=watermark.png
      WATERMARK_SCALE=0.35
      WATERMARK_OPACITY=0.35
      WATERMARK_POSITION=bottom-right
      ```

      **Don't want them?** Turn it off explicitly with an empty value:
      ```ini
      WATERMARK_PATH=
      ```

This only applies if you're coming from `main`. If this booth was already on
`windows-cross-platform-printing`, it has been watermarking all along and
nothing changes.

---

## Step 5 — Connect it to the hub

- [ ] **Install Tailscale** if this machine doesn't have it —
      <https://tailscale.com/download/mac> — and sign in with the **same
      account** as the hub.

- [ ] **Confirm it can reach the hub:** open `http://100.x.y.z:8080` in this
      Mac's browser. If the page doesn't load, that's Tailscale, and editing
      `.env` will not fix it.

- [ ] **Add two lines to `.env`** (leave everything else alone):
      ```ini
      HUB_URL=http://100.x.y.z:8080
      PRINTER_API_KEY=<this booth's key from the hub>
      ```

Skipping this step is fine — the booth just runs standalone, exactly as before.

---

## Step 6 — Rotate the exposed token (recommended)

The print bot's token was committed to the public repo in the first commit and
is still readable in git history.

- [ ] @BotFather → `/mybots` → the print bot → **API Token** → **Revoke current
      token**
- [ ] Put the new value in `.env` as **both** `BOT_TOKEN` and `PRINT_BOT_TOKEN`

Do this while the bots are stopped and you only have to restart once.

---

## Step 7 — Start and verify

```bash
./run.sh
./status.sh
```

- [ ] All three bots show **RUNNING**
- [ ] **`/ink`** → ribbon and paper match what you wrote down in Step 1
- [ ] **`/stats`** → your history is intact
- [ ] **Send a test photo** → it prints, and looks how you expect (watermark or
      not, per Step 4)
- [ ] The photo lands in the **gallery channel**
- [ ] The booth shows **online** on the hub dashboard within 10 seconds
- [ ] `logs/bot.log` has no `Conflict: terminated by other getUpdates`

- [ ] **Re-enable autostart** if you uninstalled it in Step 2:
      ```bash
      ./install-autostart.sh
      ```
      Never combine this with `./run.sh` — pick one, or the two copies fight
      over Telegram.

---

## What you gain

| | |
|---|---|
| Supply auto-pause | The bot stops itself when ribbon or paper hits zero, instead of feeding an empty printer |
| `/newribbon`, `/newpaper` | Resume the booth automatically once you reload |
| Hub dashboard | Status, queue, ribbon and paper visible from your phone |
| Watermarks from the hub | Assign a PNG to an event; booths apply it without a restart |
| Six bug fixes | EXIF rotation, supply-counter races, log rotation data loss, Markdown crashes on names like `Ben_10`, album limits, silent alert failures |

---

## Rolling back

```bash
./stop.sh
git checkout <the branch from Step 1>
./run.sh
```

Your data is untouched by any of this — the counters, logs and `.env` are the
same files throughout. If you rotated the token in Step 6, the old branch works
fine with the new token; nothing else needs changing.
