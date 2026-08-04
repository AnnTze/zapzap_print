# Windows Setup Guide — ZapZap Photobooth (second instance)

This sets up a **second, fully independent photobooth instance** on a Windows
laptop with its own printer. It has **its own Telegram bots, its own gallery
channel, and its own logs** — it shares nothing with the Mac instance.

The same `bot.py` / `monitor.py` / `gallery.py` run on both platforms. Only the
printing layer (`printing/windows_backend.py`) and the `.ps1` lifecycle scripts
differ from macOS.

This guide assumes a **brand-new Windows laptop with nothing installed**.

---

## 0. What you'll end up installing

| Thing | Why |
|---|---|
| Python 3.12 | Runs the bots (**not 3.14** — breaks `python-telegram-bot` 21.6) |
| The project files | This repo |
| CP-D90DW driver | So Windows can print to the Mitsubishi |
| pywin32 (auto, via setup) | Lets Python drive the Windows printer |
| NSSM | Runs the 3 bots as auto-start services (production only) |
| 3 new Telegram bots + 1 private channel | This instance's own bots |

---

## 1. Install Python 3.12

1. Download **Python 3.12.x** (Windows installer, 64-bit) from
   <https://www.python.org/downloads/windows/>.
   > Do **not** install 3.14 — the bots crash on it.
2. Run the installer. On the first screen, **tick "Add python.exe to PATH"**,
   then click **Install Now**.
3. Verify — open **PowerShell** and run:
   ```powershell
   py -3.12 --version
   ```
   You should see `Python 3.12.x`.

---

## 2. Get the project onto the laptop

Either copy the project folder over (USB / cloud), **or** install Git from
<https://git-scm.com/download/win> and clone it:

```powershell
git clone <your-repo-url> zapzap
cd zapzap
```

For the rest of this guide, run all commands **from inside the project folder**.

### Allow the scripts to run (one-time)

Windows blocks unsigned scripts by default. Allow local scripts for your user:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

(Or prefix each script with `powershell -ExecutionPolicy Bypass -File .\setup.ps1`.)

---

## 3. Install the printer + driver

1. Download and install the **Mitsubishi CP-D90DW Windows driver** from
   Mitsubishi's site.
2. Plug the printer in via USB and let Windows finish installing it.
3. Confirm Windows sees it:
   ```powershell
   Get-Printer | Select-Object Name
   ```
   Note the **exact** printer name — you'll put it in `.env` as `PRINTER_NAME`.
4. **Set the paper size in the driver** to the borderless 4×6 / 10×15 form:
   **Settings → Bluetooth & devices → Printers & scanners →** the CP-D90DW **→
   Printing preferences →** set paper size to the borderless **4×6 (10×15)** form
   and set it as the default. This makes prints come out correctly sized even
   before you set `WINDOWS_PAPER_FORM_NAME`.

---

## 4. Run setup

```powershell
.\setup.ps1
```

This finds Python 3.12, creates `.venv\`, installs `requirements-windows.txt`
(which includes **pywin32**), checks for the printer, creates `.env` from
`.env.example`, and syntax-checks the bots.

---

## 5. Find the paper form id

With the driver installed, list the printer's paper forms and their ids:

```powershell
.venv\Scripts\python.exe scripts\find_paper_form.py
```

Find the borderless **4×6 / 10×15 / postcard** row and note its **ID** (a
number). You'll put it in `.env` as `WINDOWS_PAPER_FORM_NAME`.

> Using the numeric id means the bot never has to ask the driver for the paper
> list while it's running at an event.

---

## 6. Create this instance's Telegram bots + channel

This instance must **not** reuse the Mac instance's bots.

1. In Telegram, message **@BotFather** → `/newbot` **three times**. Save the
   three tokens:
   - `PRINT_BOT_TOKEN` — the public photo bot
   - `MONITOR_BOT_TOKEN` — the admin/stats bot
   - `GALLERY_BOT_TOKEN` — the gallery search bot (also posts to the channel)
2. Create a **new private channel** (e.g. "Photobooth Gallery — Laptop 2").
   → Manage Channel → Administrators → add the **gallery bot** → grant **Post
   Messages**.
3. Get the channel id: post a message in the channel, forward it to
   **@userinfobot** — it replies with a negative number like `-1001234567890`.
   That's your `GALLERY_CHANNEL_ID`.

---

## 7. Fill in `.env`

Open `.env` in Notepad and set:

```ini
PRINT_BOT_TOKEN=...            # from @BotFather (this laptop's print bot)
MONITOR_BOT_TOKEN=...          # from @BotFather
GALLERY_BOT_TOKEN=...          # from @BotFather
GALLERY_CHANNEL_ID=-100...     # from @userinfobot (this laptop's channel)
MONITOR_PASSWORD=choose-a-password

PRINTER_NAME=...               # exact name from Get-Printer (step 3)
WINDOWS_PAPER_FORM_NAME=...    # the numeric id from find_paper_form.py (step 5)
```

Leave `CUPS_MEDIA` alone (it's only used on macOS).

---

## 8. Prevent the laptop from sleeping

If Windows sleeps, the bots stop receiving photos. For an always-on photobooth:

- **Settings → System → Power → Screen and sleep →** set **"When plugged in, put
  my device to sleep after"** to **Never**.
- Keep it plugged into mains power during events.

---

## 9. Test manually

```powershell
.\run.ps1
.\status.ps1
```

`status.ps1` should show all three bots **RUNNING** and the printer **ONLINE**.
Send a photo to the print bot from Telegram and confirm it prints at the correct
size. Stop with:

```powershell
.\stop.ps1
```

---

## 10. Production auto-start (services)

For an unattended photobooth, run the bots as Windows services that start at
boot and restart on crash. This uses **NSSM**.

1. Install NSSM (easiest with [Chocolatey](https://chocolatey.org/install)):
   ```powershell
   choco install nssm
   ```
   Or download `nssm.exe` from <https://nssm.cc/> and put it on your PATH.
2. Open an **Administrator** PowerShell in the project folder and run:
   ```powershell
   .\install-autostart.ps1
   ```
3. Verify:
   ```powershell
   Get-Service ZapZap*
   .\status.ps1
   ```

To go back to manual running:
```powershell
.\uninstall-autostart.ps1
```

> Do **not** run `.\run.ps1` while the services are installed — two copies of
> the same bot cause Telegram's *"Conflict: terminated by other getUpdates
> request"* error. Pick one method.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `running scripts is disabled on this system` | Run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` (step 2). |
| `No compatible Python found` | Install Python **3.12** and tick "Add to PATH". Not 3.14. |
| Prints are the wrong size / have borders | Set the driver default to borderless 4×6 (step 3) **and** set `WINDOWS_PAPER_FORM_NAME` to the id from `find_paper_form.py`. |
| `pywin32 is required` error | `.venv\Scripts\python.exe -m pip install -r requirements-windows.txt` |
| Printer shows NOT READY in `status.ps1` | Check it's powered on, online, and has paper/ribbon; confirm `PRINTER_NAME` matches `Get-Printer` exactly. |
| `NSSM not found on PATH` | `choco install nssm`, or add `nssm.exe` to PATH. |
| Services won't install | Use an **Administrator** PowerShell. |
| `Conflict: terminated by other getUpdates request` | The same bot is running twice — don't mix `run.ps1` and the services; also make sure these tokens aren't reused by the Mac instance. |

---

## Notes for maintainers

- The printer abstraction lives in `printing/`. `get_printer_backend()` picks
  `WindowsPrinterBackend` on Windows and `CupsPrinterBackend` on macOS — the bots
  themselves are unchanged across platforms.
- `WindowsPrinterBackend.print_image` renders the (already 10×15-cropped) JPEG to
  the full printable area and issues one page per copy, which is more reliable on
  dye-sub drivers than relying on `DEVMODE.Copies`.
- Ribbon/paper tracking (`/ink`, `/newribbon`, `/newpaper`) is manual count-based
  (`.supply_state`) and already cross-platform — it does **not** read the printer.
