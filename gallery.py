import os
import json
import logging
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

load_dotenv()

GALLERY_BOT_TOKEN = os.getenv("GALLERY_BOT_TOKEN", "")
MONITOR_PASSWORD = os.getenv("MONITOR_PASSWORD", "changeme")
GALLERY_LOG_FILE = os.getenv("GALLERY_LOG_FILE", "gallery_log.jsonl")
GALLERY_SESSIONS_FILE = ".gallery_sessions"
BATCH_SIZE = 20

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------

authenticated_sessions: set[int] = set()
prompted_sessions: set[int] = set()


def load_sessions() -> None:
    global authenticated_sessions
    if os.path.exists(GALLERY_SESSIONS_FILE):
        with open(GALLERY_SESSIONS_FILE) as f:
            authenticated_sessions = set(json.load(f))


def save_sessions() -> None:
    with open(GALLERY_SESSIONS_FILE, "w") as f:
        json.dump(list(authenticated_sessions), f)


def is_authenticated(chat_id: int) -> bool:
    return chat_id in authenticated_sessions


async def require_auth(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if not is_authenticated(update.effective_chat.id):
        await update.effective_message.reply_text(
            "This bot is password protected. Send the password to continue."
        )
        return False
    return True


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
# Log helpers
# ---------------------------------------------------------------------------

def read_gallery_entries() -> list[dict]:
    if not os.path.exists(GALLERY_LOG_FILE):
        return []
    entries = []
    with open(GALLERY_LOG_FILE) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return entries


def fmt_ts(ts_str: str) -> str:
    try:
        return datetime.fromisoformat(ts_str).astimezone().strftime("%-d %b %Y, %H:%M")
    except Exception:
        return ts_str


def parse_date(s: str):
    """Parse flexible date strings into a date object, or None."""
    s = s.strip()
    for fmt in ("%d%b%Y", "%d%b", "%d %b %Y", "%d %b"):
        try:
            d = datetime.strptime(s, fmt)
            if "%Y" not in fmt:
                d = d.replace(year=datetime.now().year)
            return d.date()
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------

pagination_state: dict[int, dict] = {}


def reset_pagination(chat_id: int, entries: list[dict]) -> None:
    pagination_state[chat_id] = {"entries": entries, "offset": 0}


async def send_next_batch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    state = pagination_state.get(chat_id)
    if not state or not state["entries"]:
        await update.effective_message.reply_text("No photos to show.")
        return

    entries = state["entries"]
    offset = state["offset"]
    batch = entries[offset : offset + BATCH_SIZE]

    for entry in batch:
        caption = f"{entry.get('user_name', '?')} • {fmt_ts(entry.get('timestamp', ''))}"
        try:
            await context.bot.send_photo(
                chat_id=chat_id,
                photo=entry["photo_file_id"],
                caption=caption,
            )
        except Exception as exc:
            logger.warning("Failed to resend photo: %s", exc)

    state["offset"] += len(batch)
    shown = state["offset"]
    total = len(entries)

    if shown < total:
        await update.effective_message.reply_text(
            f"[{shown} of {total} shown] — /more to continue"
        )
    else:
        pagination_state.pop(chat_id, None)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

async def cmd_latest(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_auth(update, context):
        return
    n = 10
    if context.args:
        try:
            n = min(int(context.args[0]), 20)
        except ValueError:
            pass

    entries = read_gallery_entries()
    if not entries:
        await update.effective_message.reply_text("No photos yet.")
        return

    # Last N, send oldest first within the batch
    batch = entries[-n:]
    if not batch:
        await update.effective_message.reply_text("No photos yet.")
        return

    reset_pagination(update.effective_chat.id, batch)
    await send_next_batch(update, context)


async def cmd_gallery(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_auth(update, context):
        return

    entries = read_gallery_entries()

    if not context.args:
        # List all dates with photo counts
        if not entries:
            await update.effective_message.reply_text("No photos yet.")
            return
        date_counts: dict[str, int] = defaultdict(int)
        for e in entries:
            day = e.get("timestamp", "")[:10]  # YYYY-MM-DD
            if day:
                date_counts[day] += 1
        lines = []
        for day in sorted(date_counts.keys(), reverse=True):
            try:
                label = datetime.strptime(day, "%Y-%m-%d").strftime("%-d %b %Y")
            except ValueError:
                label = day
            count = date_counts[day]
            lines.append(f"{label} — {count} {'photo' if count == 1 else 'photos'}")
        await update.effective_message.reply_text("\n".join(lines))
        return

    date_str = " ".join(context.args)
    target = parse_date(date_str)
    if target is None:
        await update.effective_message.reply_text(
            "Unrecognised date. Try: 25Apr, 25Apr2026, 25 Apr, 25 Apr 2026"
        )
        return

    target_prefix = target.isoformat()
    matched = [e for e in entries if e.get("timestamp", "").startswith(target_prefix)]
    label = target.strftime("%-d %b %Y")

    if not matched:
        await update.effective_message.reply_text(f"No photos on {label}.")
        return

    reset_pagination(update.effective_chat.id, matched)
    await send_next_batch(update, context)


async def cmd_photos(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_auth(update, context):
        return

    entries = read_gallery_entries()

    if not context.args:
        # Leaderboard
        if not entries:
            await update.effective_message.reply_text("No photos yet.")
            return
        user_counts: Counter = Counter()
        for e in entries:
            user_counts[e.get("user_name", "Unknown")] += 1
        lines = []
        for i, (name, count) in enumerate(user_counts.most_common(), start=1):
            lines.append(f"{i}. {name} — {count} {'photo' if count == 1 else 'photos'}")
        await update.effective_message.reply_text("\n".join(lines))
        return

    query = " ".join(context.args).lstrip("@").lower()
    matched = [
        e for e in entries
        if query == (e.get("user_name") or "").lower()
        or query == (e.get("username") or "").lstrip("@").lower()
    ]

    if not matched:
        await update.effective_message.reply_text(f"No photos found for {' '.join(context.args)}.")
        return

    reset_pagination(update.effective_chat.id, matched)
    await send_next_batch(update, context)


async def cmd_count(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_auth(update, context):
        return

    entries = read_gallery_entries()
    total = len(entries)

    if total == 0:
        await update.effective_message.reply_text("No photos in the gallery yet.")
        return

    now = datetime.now(timezone.utc)
    this_month = now.strftime("%Y-%m")
    last_month = (now.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")

    this_month_count = sum(1 for e in entries if e.get("timestamp", "").startswith(this_month))
    last_month_count = sum(1 for e in entries if e.get("timestamp", "").startswith(last_month))

    user_counts: Counter = Counter(e.get("user_name", "Unknown") for e in entries)
    top5 = user_counts.most_common(5)
    top5_lines = "\n".join(
        f"  {i}. {name} — {count}" for i, (name, count) in enumerate(top5, start=1)
    )

    # Plain text: Telegram rejects the whole message if a user_name contains
    # Markdown syntax (e.g. "Ben_10"), which silently swallowed the reply.
    msg = (
        f"📷 Gallery count\n"
        f"Total photos: {total}\n\n"
        f"This month: {this_month_count}\n"
        f"Last month: {last_month_count}\n\n"
        f"Top 5 photographers\n{top5_lines}"
    )
    await update.effective_message.reply_text(msg)


async def cmd_more(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_auth(update, context):
        return
    chat_id = update.effective_chat.id
    if chat_id not in pagination_state:
        await update.effective_message.reply_text("No active query. Run a command first.")
        return
    await send_next_batch(update, context)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_auth(update, context):
        return
    text = (
        "GALLERY BOT COMMANDS\n\n"
        "/latest [N] — last N photos (default 10, max 20)\n"
        "/gallery [date] — photos on a date, or list all dates\n"
        "/photos [name] — photos by a person, or full leaderboard\n"
        "/count — total photos, top 5 users, this vs last month\n"
        "/more — continue paginated results\n"
        "/logout — end your session\n"
        "/help — show this message\n\n"
        "Date formats: 25Apr, 25Apr2026, 25 Apr, 25 Apr 2026\n"
        "Name formats: Darren or @darren (case-insensitive)"
    )
    await update.effective_message.reply_text(text)


async def cmd_logout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if chat_id not in authenticated_sessions:
        await update.effective_message.reply_text("You are not logged in.")
        return
    authenticated_sessions.discard(chat_id)
    save_sessions()
    await update.effective_message.reply_text("Logged out.")


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

async def post_init(app: Application) -> None:
    load_sessions()
    logger.info("Gallery bot started. Authenticated sessions: %d", len(authenticated_sessions))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    if not GALLERY_BOT_TOKEN:
        raise SystemExit("Set GALLERY_BOT_TOKEN in .env before running.")

    app = ApplicationBuilder().token(GALLERY_BOT_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", cmd_help))
    app.add_handler(CommandHandler("latest", cmd_latest))
    app.add_handler(CommandHandler("gallery", cmd_gallery))
    app.add_handler(CommandHandler("photos", cmd_photos))
    app.add_handler(CommandHandler("count", cmd_count))
    app.add_handler(CommandHandler("more", cmd_more))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("logout", cmd_logout))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    app.run_polling()


if __name__ == "__main__":
    main()
