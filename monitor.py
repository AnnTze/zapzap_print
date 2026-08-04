import os
import json
import time
import asyncio
import shutil
import logging
from datetime import datetime, timezone, timedelta
from io import BytesIO
from pathlib import Path

from dotenv import load_dotenv
from telegram import Update, Bot
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

from printing import get_printer_backend, format_queue_text

load_dotenv()

MONITOR_BOT_TOKEN = os.getenv("MONITOR_BOT_TOKEN", "")
PRINT_BOT_TOKEN = os.getenv("PRINT_BOT_TOKEN", "")
MONITOR_PASSWORD = os.getenv("MONITOR_PASSWORD", "changeme")
RIBBON_CAPACITY = int(os.getenv("RIBBON_CAPACITY", "700"))
DEFAULT_PAPER_LOAD = 50
LOG_FILE = os.getenv("LOG_FILE", "print_log.jsonl")
LOG_ARCHIVE_DIR = os.getenv("LOG_ARCHIVE_DIR", "logs")
SESSIONS_FILE = ".sessions"
INK_ALERT_FLAG = ".ink_alerted"   # legacy, only referenced by rotate_log_if_needed
PAUSE_FILE = ".bot_paused"
SUPPLY_FILE = ".supply_state"
SUPPLY_LOCK = ".supply_lock"

DEFAULT_PAUSE_REASON = "The printer is currently offline."

RIBBON_THRESHOLDS = [200, 150, 100, 50, 10]
PAPER_THRESHOLDS = [50, 30, 20, 10, 5]

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Platform printer backend (CUPS on macOS, pywin32 on Windows), chosen at startup.
_printer_backend = get_printer_backend()

# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------

authenticated_sessions: set[int] = set()
prompted_sessions: set[int] = set()


def load_sessions() -> None:
    global authenticated_sessions
    if os.path.exists(SESSIONS_FILE):
        with open(SESSIONS_FILE) as f:
            authenticated_sessions = set(json.load(f))


def save_sessions() -> None:
    with open(SESSIONS_FILE, "w") as f:
        json.dump(list(authenticated_sessions), f)


def is_authenticated(chat_id: int) -> bool:
    return chat_id in authenticated_sessions


# ---------------------------------------------------------------------------
# Log helpers
# ---------------------------------------------------------------------------

def read_entries(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    entries = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return entries


def read_all_entries() -> list[dict]:
    """Current log + all archived logs, sorted by timestamp."""
    entries = []
    archive_dir = Path(LOG_ARCHIVE_DIR)
    if archive_dir.exists():
        for archive_file in sorted(archive_dir.glob("print_log_*.jsonl")):
            entries.extend(read_entries(str(archive_file)))
    entries.extend(read_entries(LOG_FILE))
    return entries


# ---------------------------------------------------------------------------
# Supply state (shared with bot.py via .supply_state JSON)
# ---------------------------------------------------------------------------

def _default_supply() -> dict:
    return {
        "ribbon": {"capacity": RIBBON_CAPACITY, "used": 0, "reset_at": None, "reset_by": None},
        "paper":  {"loaded": DEFAULT_PAPER_LOAD, "used": 0, "reset_at": None, "reset_by": None},
        "alerts_sent": [],
    }


def load_supply() -> dict:
    """Read .supply_state or return defaults if missing/invalid."""
    try:
        return json.loads(Path(SUPPLY_FILE).read_text())
    except Exception:
        return _default_supply()


def save_supply(state: dict) -> None:
    Path(SUPPLY_FILE).write_text(json.dumps(state, indent=2))


def ribbon_remaining(state: dict) -> int:
    return max(0, state["ribbon"]["capacity"] - state["ribbon"]["used"])


def paper_remaining(state: dict) -> int:
    return max(0, state["paper"]["loaded"] - state["paper"]["used"])


def supply_bar(remaining: int, total: int, width: int = 10) -> str:
    """Returns a text progress bar e.g. [████████░░] 560/700"""
    filled = round((remaining / total) * width) if total > 0 else 0
    bar = "█" * filled + "░" * (width - filled)
    return f"[{bar}] {remaining}/{total}"


def _with_supply_lock(modifier) -> dict:
    """Atomically read-modify-write .supply_state under .supply_lock."""
    lock = Path(SUPPLY_LOCK)
    for _ in range(5):
        try:
            lock.touch(exist_ok=False)
            break
        except FileExistsError:
            time.sleep(0.1)
    try:
        state = load_supply()
        modifier(state)
        save_supply(state)
        return state
    finally:
        lock.unlink(missing_ok=True)


def _fmt_reset(block: dict) -> str:
    ts = block.get("reset_at")
    by = block.get("reset_by") or "?"
    if not ts:
        return "never"
    try:
        ts_str = datetime.fromisoformat(ts).astimezone().strftime("%-d %b %Y %H:%M")
    except Exception:
        ts_str = ts
    return f"{ts_str} by {by}"


# ---------------------------------------------------------------------------
# Monthly log rotation
# ---------------------------------------------------------------------------

def rotate_log_if_needed() -> None:
    entries = read_entries(LOG_FILE)
    if not entries:
        return
    try:
        first_ts = datetime.fromisoformat(entries[0]["timestamp"])
    except (KeyError, ValueError):
        return
    first_month = first_ts.strftime("%Y_%m")
    current_month = datetime.now(timezone.utc).strftime("%Y_%m")
    if first_month == current_month:
        return
    archive_dir = Path(LOG_ARCHIVE_DIR)
    archive_dir.mkdir(exist_ok=True)
    archive_path = archive_dir / f"print_log_{first_month}.jsonl"
    shutil.copy(LOG_FILE, archive_path)
    open(LOG_FILE, "w").close()
    if os.path.exists(INK_ALERT_FLAG):
        os.remove(INK_ALERT_FLAG)
    print(f"Rotated log for {first_month.replace('_', '-')}")


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

async def require_auth(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    chat_id = update.effective_chat.id
    if not is_authenticated(chat_id):
        await update.effective_message.reply_text(
            "This bot is password protected. Send the password to continue."
        )
        return False
    return True


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_auth(update, context):
        return
    entries = read_all_entries()
    total_jobs = len(entries)
    total_copies = sum(e.get("copies", 0) for e in entries)
    success = sum(1 for e in entries if e.get("status") == "success")
    rate = (success / total_jobs * 100) if total_jobs else 0
    await update.effective_message.reply_text(
        f"📊 *Stats (all time)*\n"
        f"Total print jobs: {total_jobs}\n"
        f"Total copies: {total_copies}\n"
        f"Success rate: {rate:.1f}%",
        parse_mode="Markdown",
    )


async def cmd_today(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_auth(update, context):
        return
    today = datetime.now(timezone.utc).date().isoformat()
    entries = [e for e in read_entries(LOG_FILE) if e.get("timestamp", "").startswith(today)]
    total_copies = sum(e.get("copies", 0) for e in entries)
    user_copies: dict[str, int] = {}
    for e in entries:
        name = e.get("user_name", "Unknown")
        user_copies[name] = user_copies.get(name, 0) + e.get("copies", 0)
    lines = [f"📅 *Today ({today})*", f"Jobs: {len(entries)}, Copies: {total_copies}"]
    if user_copies:
        lines.append("")
        for name, copies in sorted(user_copies.items(), key=lambda x: -x[1]):
            lines.append(f"• {name}: {copies} {'copy' if copies == 1 else 'copies'}")
    await update.effective_message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_users(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_auth(update, context):
        return
    entries = read_all_entries()
    user_copies: dict[str, int] = {}
    for e in entries:
        name = e.get("user_name", "Unknown")
        user_copies[name] = user_copies.get(name, 0) + e.get("copies", 0)
    if not user_copies:
        await update.effective_message.reply_text("No print history yet.")
        return
    lines = ["🏆 *All-time leaderboard*"]
    for i, (name, copies) in enumerate(
        sorted(user_copies.items(), key=lambda x: -x[1]), start=1
    ):
        lines.append(f"{i}. {name}: {copies} copies")
    await update.effective_message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_auth(update, context):
        return
    n = 10
    if context.args:
        try:
            n = int(context.args[0])
        except ValueError:
            pass
    entries = read_entries(LOG_FILE)
    recent = list(reversed(entries[-n:]))
    if not recent:
        await update.effective_message.reply_text("No print history yet.")
        return
    lines = [f"📋 *Last {len(recent)} jobs*"]
    for e in recent:
        try:
            ts = datetime.fromisoformat(e["timestamp"]).astimezone()
            ts_str = ts.strftime("%-d %b %Y %H:%M")
        except Exception:
            ts_str = e.get("timestamp", "?")
        icon = "✅" if e.get("status") == "success" else "❌"
        c = e.get("copies", 1)
        lines.append(
            f"{icon} {e.get('user_name', '?')} — {c} {'copy' if c == 1 else 'copies'} — {ts_str}"
        )
    await update.effective_message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_lastphoto(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_auth(update, context):
        return
    all_entries = read_all_entries()
    for entry in reversed(all_entries):
        if entry.get("status") == "success" and entry.get("photo_file_id"):
            file_id = entry["photo_file_id"]
            try:
                print_bot = Bot(token=PRINT_BOT_TOKEN)
                tg_file = await print_bot.get_file(file_id)
                buf = BytesIO()
                await tg_file.download_to_memory(buf)
                buf.seek(0)
                try:
                    ts = datetime.fromisoformat(entry["timestamp"]).astimezone()
                    ts_str = ts.strftime("%-d %b %Y %H:%M")
                except Exception:
                    ts_str = entry.get("timestamp", "?")
                await context.bot.send_photo(
                    chat_id=update.effective_chat.id,
                    photo=buf,
                    caption=f"Last print by {entry.get('user_name', '?')} at {ts_str}",
                )
            except Exception as exc:
                await update.effective_message.reply_text(f"Could not fetch photo: {exc}")
            return
    await update.effective_message.reply_text("No successful prints found.")


async def cmd_ink(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_auth(update, context):
        return
    if not Path(SUPPLY_FILE).exists():
        await update.effective_message.reply_text(
            "No supply data yet. Use /newribbon and /newpaper to initialise."
        )
        return
    state = load_supply()
    r_left = ribbon_remaining(state)
    p_left = paper_remaining(state)
    r_cap = state["ribbon"]["capacity"]
    p_load = state["paper"]["loaded"]

    msg = (
        "Supply status\n\n"
        "Ribbon\n"
        f"{supply_bar(r_left, r_cap)}\n"
        f"{r_left} prints remaining of {r_cap}\n"
        f"Last replaced: {_fmt_reset(state['ribbon'])}\n\n"
        "Paper\n"
        f"{supply_bar(p_left, p_load)}\n"
        f"{p_left} sheets remaining of {p_load}\n"
        f"Last loaded: {_fmt_reset(state['paper'])}"
    )
    await update.effective_message.reply_text(msg)


async def cmd_pause(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_auth(update, context):
        return
    if Path(PAUSE_FILE).exists():
        await update.effective_message.reply_text(
            "Bot is already paused. Use /resume to bring it back."
        )
        return
    reason = " ".join(context.args).strip() if context.args else DEFAULT_PAUSE_REASON
    Path(PAUSE_FILE).write_text(reason)
    user = update.effective_message.from_user
    name = (user.first_name or "Unknown") if user else "Unknown"
    await update.effective_message.reply_text(
        f"Print bot paused.\n\nReason: {reason}\n\nUse /resume to bring it back online."
    )
    await notify_others(
        context.application,
        f"{name} paused the print bot. Reason: {reason}",
        update.effective_chat.id,
    )


async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_auth(update, context):
        return
    if not Path(PAUSE_FILE).exists():
        await update.effective_message.reply_text("Bot is already running.")
        return
    Path(PAUSE_FILE).unlink(missing_ok=True)
    user = update.effective_message.from_user
    name = (user.first_name or "Unknown") if user else "Unknown"
    await update.effective_message.reply_text(
        "Print bot resumed. Photos will now be accepted."
    )
    await notify_others(
        context.application,
        f"{name} resumed the print bot.",
        update.effective_chat.id,
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_auth(update, context):
        return

    if Path(PAUSE_FILE).exists():
        try:
            reason = Path(PAUSE_FILE).read_text().strip() or DEFAULT_PAUSE_REASON
        except Exception:
            reason = DEFAULT_PAUSE_REASON
        status_line = f"Status: PAUSED\nReason: {reason}"
    else:
        status_line = "Status: ONLINE"

    today = datetime.now(timezone.utc).date().isoformat()
    today_entries = [
        e for e in read_entries(LOG_FILE)
        if e.get("timestamp", "").startswith(today) and e.get("status") == "success"
    ]
    today_count = sum(e.get("copies", 0) for e in today_entries)

    try:
        queue_text = format_queue_text(_printer_backend.queue()) or "Queue empty"
    except Exception:
        queue_text = "Queue empty"

    if Path(SUPPLY_FILE).exists():
        state = load_supply()
        r_left = ribbon_remaining(state)
        p_left = paper_remaining(state)
        r_cap = state["ribbon"]["capacity"]
        p_load = state["paper"]["loaded"]
        supply_lines = (
            f"Ribbon: {supply_bar(r_left, r_cap)}\n"
            f"Paper:  {supply_bar(p_left, p_load)}"
        )
    else:
        supply_lines = "Supply: not initialised (use /newribbon and /newpaper)"

    await update.effective_message.reply_text(
        f"{status_line}\n\n"
        f"Prints today: {today_count}\n\n"
        f"Queue:\n{queue_text}\n\n"
        f"{supply_lines}"
    )


async def cmd_newribbon(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_auth(update, context):
        return
    capacity = RIBBON_CAPACITY
    if context.args:
        try:
            capacity = max(1, int(context.args[0]))
        except ValueError:
            await update.effective_message.reply_text(
                f"Usage: /newribbon [capacity] e.g. /newribbon {RIBBON_CAPACITY}"
            )
            return
    user = update.effective_message.from_user
    name = (user.first_name or "Unknown") if user else "Unknown"
    now_iso = datetime.now(timezone.utc).isoformat()

    def modifier(state: dict) -> None:
        state["ribbon"] = {
            "capacity": capacity,
            "used": 0,
            "reset_at": now_iso,
            "reset_by": name,
        }
        state["alerts_sent"] = [
            a for a in state.get("alerts_sent", []) if not a.startswith("ribbon_")
        ]

    _with_supply_lock(modifier)
    now_local = datetime.fromisoformat(now_iso).astimezone().strftime("%-d %b %Y %H:%M")
    await update.effective_message.reply_text(
        "New ribbon loaded!\n"
        f"Capacity: {capacity} prints\n"
        f"Ribbon: {supply_bar(capacity, capacity)}\n"
        f"Loaded by: {name} at {now_local}"
    )


async def cmd_newpaper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_auth(update, context):
        return
    if not context.args:
        await update.effective_message.reply_text(
            "Usage: /newpaper <count> e.g. /newpaper 50"
        )
        return
    try:
        count = max(1, int(context.args[0]))
    except ValueError:
        await update.effective_message.reply_text(
            "Usage: /newpaper <count> e.g. /newpaper 50"
        )
        return
    user = update.effective_message.from_user
    name = (user.first_name or "Unknown") if user else "Unknown"
    now_iso = datetime.now(timezone.utc).isoformat()

    def modifier(state: dict) -> None:
        state["paper"] = {
            "loaded": count,
            "used": 0,
            "reset_at": now_iso,
            "reset_by": name,
        }
        state["alerts_sent"] = [
            a for a in state.get("alerts_sent", []) if not a.startswith("paper_")
        ]

    _with_supply_lock(modifier)
    now_local = datetime.fromisoformat(now_iso).astimezone().strftime("%-d %b %Y %H:%M")
    await update.effective_message.reply_text(
        "Paper reloaded!\n"
        f"Sheets loaded: {count}\n"
        f"Paper: {supply_bar(count, count)}\n"
        f"Loaded by: {name} at {now_local}"
    )


async def cmd_queue(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_auth(update, context):
        return
    jobs = _printer_backend.queue()
    if jobs:
        output = format_queue_text(jobs)
        await update.effective_message.reply_text(
            f"🖨️ *Print queue:*\n```\n{output}\n```", parse_mode="Markdown"
        )
    else:
        await update.effective_message.reply_text("Queue is empty.")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_auth(update, context):
        return
    text = (
        "*Monitor Bot Commands*\n"
        "/status — bot status, queue, and supply levels\n"
        "/pause \\[reason\\] — pause the print bot\n"
        "/resume — resume the print bot\n"
        "/ink — detailed ribbon and paper levels\n"
        "/newribbon \\[cap\\] — log a new ribbon cassette \\(resets counter\\)\n"
        "/newpaper <count> — log a paper reload \\(resets counter\\)\n"
        "/stats — total prints, copies, success rate (all time)\n"
        "/today — today's jobs and copies, per user\n"
        "/users — all-time leaderboard by copies\n"
        "/history \\[N\\] — last N jobs (default 10)\n"
        "/lastphoto — resend the last successfully printed photo\n"
        "/queue — current print queue\n"
        "/logout — end your session\n"
        "/help — show this message"
    )
    await update.effective_message.reply_text(text, parse_mode="Markdown")


async def cmd_logout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if chat_id not in authenticated_sessions:
        await update.effective_message.reply_text("You are not logged in.")
        return
    authenticated_sessions.discard(chat_id)
    save_sessions()
    await update.effective_message.reply_text("Logged out.")


# ---------------------------------------------------------------------------
# Auth message handler
# ---------------------------------------------------------------------------

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    text = (update.effective_message.text or "").strip()

    if is_authenticated(chat_id):
        return

    if text == MONITOR_PASSWORD:
        authenticated_sessions.add(chat_id)
        prompted_sessions.discard(chat_id)
        save_sessions()
        await update.effective_message.reply_text("Access granted.")
    elif chat_id not in prompted_sessions:
        prompted_sessions.add(chat_id)
        await update.effective_message.reply_text(
            "This bot is password protected. Send the password to continue."
        )
    else:
        await update.effective_message.reply_text("Incorrect password.")


# ---------------------------------------------------------------------------
# Alert helpers
# ---------------------------------------------------------------------------

async def send_alert(app: Application, text: str) -> None:
    for chat_id in list(authenticated_sessions):
        try:
            await app.bot.send_message(chat_id=chat_id, text=text)
        except Exception as exc:
            logger.warning("Alert to %s failed: %s", chat_id, exc)


async def notify_others(app: Application, text: str, exclude_chat_id: int) -> None:
    """Send a message to all authenticated sessions except the originator."""
    for chat_id in list(authenticated_sessions):
        if chat_id == exclude_chat_id:
            continue
        try:
            await app.bot.send_message(chat_id=chat_id, text=text)
        except Exception as exc:
            logger.warning("Notification to %s failed: %s", chat_id, exc)


# ---------------------------------------------------------------------------
# Background tasks
# ---------------------------------------------------------------------------

last_line_count: int = 0


async def poll_log(app: Application) -> None:
    global last_line_count
    while True:
        await asyncio.sleep(10)
        if not os.path.exists(LOG_FILE):
            continue
        lines = []
        with open(LOG_FILE) as f:
            lines = [line.strip() for line in f if line.strip()]
        new_lines = lines[last_line_count:]
        last_line_count = len(lines)
        for line in new_lines:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("status") == "failed":
                await send_alert(
                    app,
                    f"⚠️ Print failed for {entry.get('user_name', '?')}: "
                    f"{entry.get('error', 'unknown error')}",
                )
            elif entry.get("status") == "success":
                if not Path(SUPPLY_FILE).exists():
                    continue  # supply tracking not initialised yet
                state = load_supply()
                r_left = ribbon_remaining(state)
                p_left = paper_remaining(state)
                new_alerts: list[tuple[str, str]] = []  # (key, message)

                for threshold in RIBBON_THRESHOLDS:
                    key = f"ribbon_{threshold}"
                    if r_left <= threshold and key not in state["alerts_sent"]:
                        new_alerts.append((
                            key,
                            f"Ribbon alert: {r_left} prints remaining!\n"
                            f"{supply_bar(r_left, state['ribbon']['capacity'])}\n"
                            f"Use /newribbon when you change the cassette.",
                        ))

                for threshold in PAPER_THRESHOLDS:
                    key = f"paper_{threshold}"
                    if p_left <= threshold and key not in state["alerts_sent"]:
                        new_alerts.append((
                            key,
                            f"Paper alert: {p_left} sheets remaining!\n"
                            f"{supply_bar(p_left, state['paper']['loaded'])}\n"
                            f"Use /newpaper <count> when you reload.",
                        ))

                for key, msg in new_alerts:
                    await send_alert(app, msg)

                if new_alerts:
                    def add_alerts(s: dict) -> None:
                        existing = set(s.get("alerts_sent", []))
                        for k, _ in new_alerts:
                            if k not in existing:
                                s["alerts_sent"].append(k)
                    _with_supply_lock(add_alerts)


async def daily_rotation_task() -> None:
    while True:
        now = datetime.now()
        tomorrow = (now + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        await asyncio.sleep((tomorrow - now).total_seconds())
        rotate_log_if_needed()


# ---------------------------------------------------------------------------
# Startup hook
# ---------------------------------------------------------------------------

async def post_init(app: Application) -> None:
    global last_line_count
    load_sessions()
    rotate_log_if_needed()
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE) as f:
            last_line_count = sum(1 for line in f if line.strip())
    asyncio.create_task(poll_log(app))
    asyncio.create_task(daily_rotation_task())
    if Path(PAUSE_FILE).exists():
        try:
            reason = Path(PAUSE_FILE).read_text().strip() or DEFAULT_PAUSE_REASON
        except Exception:
            reason = DEFAULT_PAUSE_REASON
        logger.warning("Monitor started — print bot is PAUSED: %s", reason)
    logger.info("Monitor bot started. Authenticated sessions: %d", len(authenticated_sessions))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    if not MONITOR_BOT_TOKEN:
        raise SystemExit("Set MONITOR_BOT_TOKEN in .env before running.")

    app = ApplicationBuilder().token(MONITOR_BOT_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", cmd_help))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("today", cmd_today))
    app.add_handler(CommandHandler("users", cmd_users))
    app.add_handler(CommandHandler("history", cmd_history))
    app.add_handler(CommandHandler("lastphoto", cmd_lastphoto))
    app.add_handler(CommandHandler("ink", cmd_ink))
    app.add_handler(CommandHandler("queue", cmd_queue))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("pause", cmd_pause))
    app.add_handler(CommandHandler("resume", cmd_resume))
    app.add_handler(CommandHandler("newribbon", cmd_newribbon))
    app.add_handler(CommandHandler("newpaper", cmd_newpaper))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("logout", cmd_logout))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    app.run_polling()


if __name__ == "__main__":
    main()
