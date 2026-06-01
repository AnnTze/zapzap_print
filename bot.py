import os
import re
import json
import time
import asyncio
import subprocess
import tempfile
import logging
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

from dotenv import load_dotenv
from PIL import Image, ExifTags
from telegram import Bot, Update
from telegram.ext import ApplicationBuilder, MessageHandler, CommandHandler, filters, ContextTypes

load_dotenv()

# --- Config ---
BOT_TOKEN = os.getenv("PRINT_BOT_TOKEN", "")
PRINTER_NAME = "MITSUBISHI_CPD90D"
PAPER_W_PX = 1772   # landscape width at 300 DPI (15 cm / ME_10x15)
PAPER_H_PX = 1181   # landscape height at 300 DPI (10 cm / ME_10x15)
LOG_FILE = os.getenv("LOG_FILE", "print_log.jsonl")
GALLERY_BOT_TOKEN = os.getenv("GALLERY_BOT_TOKEN", "")
GALLERY_CHANNEL_ID = os.getenv("GALLERY_CHANNEL_ID", "")
GALLERY_LOG_FILE = os.getenv("GALLERY_LOG_FILE", "gallery_log.jsonl")
MAX_PRINTS_PER_MESSAGE = int(os.getenv("MAX_PRINTS_PER_MESSAGE", "5"))
MAX_COPIES = MAX_PRINTS_PER_MESSAGE
# --------------

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

album_buffer: dict[str, list] = {}
album_timers: dict[str, asyncio.TimerHandle] = {}


def parse_copy_list(caption: str | None, photo_count: int) -> list[int] | str:
    """Parse caption into a per-photo copy list. Returns list[int] or an error string."""
    if not caption or not caption.strip():
        return [1] * photo_count
    caption = caption.strip()
    if "," in caption:
        tokens = [t.strip() for t in caption.split(",")]
        if len(tokens) != photo_count:
            noun = "photo" if photo_count == 1 else "photos"
            return (
                f"You sent {photo_count} {noun} but {len(tokens)} copy "
                f"{'count' if len(tokens) == 1 else 'counts'} ({caption}). "
                f"Please resend with {photo_count} comma-separated values, "
                f"or one number to use the same count for all."
            )
        values = []
        for i, token in enumerate(tokens):
            m = re.fullmatch(r"[xX]?(\d+)[xX]?", token.strip())
            if not m:
                return (
                    f"Invalid copy count at position {i + 1}: '{token}'. "
                    f"Please use numbers only e.g. '3,5,1'."
                )
            values.append(max(1, min(int(m.group(1)), MAX_COPIES)))
        return values
    m = re.fullmatch(r"[xX]?(\d+)[xX]?", caption)
    if m:
        return [max(1, min(int(m.group(1)), MAX_COPIES))] * photo_count
    return [1] * photo_count


def parse_copies(caption: str | None) -> int:
    """Single-photo backward-compatible wrapper."""
    result = parse_copy_list(caption, 1)
    if isinstance(result, str):
        return 1
    return result[0]


def validate_print_limit(copy_list: list[int]) -> tuple[bool, int, str]:
    """
    Checks whether the total prints across all photos exceeds
    MAX_PRINTS_PER_MESSAGE.

    Returns:
      (ok: bool, total: int, error_msg: str)

    ok=True means the job is within limits.
    ok=False means it should be rejected with error_msg.
    """
    total = sum(copy_list)
    if total > MAX_PRINTS_PER_MESSAGE:
        if len(copy_list) == 1:
            error = (
                f"Too many copies requested ({total}). "
                f"Maximum is {MAX_PRINTS_PER_MESSAGE} prints per message.\n\n"
                f"Please resend with a caption of {MAX_PRINTS_PER_MESSAGE} or less."
            )
        else:
            breakdown = " + ".join(str(c) for c in copy_list)
            error = (
                f"Total prints too high ({breakdown} = {total} prints). "
                f"Maximum is {MAX_PRINTS_PER_MESSAGE} prints per message.\n\n"
                f"Please adjust your copy counts so they add up to "
                f"{MAX_PRINTS_PER_MESSAGE} or less."
            )
        return False, total, error
    return True, total, ""


def fix_exif_rotation(img: Image.Image) -> Image.Image:
    try:
        exif = img._getexif()
        if exif is None:
            return img
        orientation_key = next(
            (k for k, v in ExifTags.TAGS.items() if v == "Orientation"), None
        )
        if orientation_key is None:
            return img
        orientation = exif.get(orientation_key)
        rotations = {3: 180, 6: 270, 8: 90}
        if orientation in rotations:
            img = img.rotate(rotations[orientation], expand=True)
    except Exception:
        pass
    return img


def fit_to_paper(img: Image.Image) -> Image.Image:
    iw, ih = img.size
    # Choose landscape or portrait canvas based on image aspect ratio
    if iw >= ih:
        canvas_w, canvas_h = PAPER_W_PX, PAPER_H_PX
    else:
        canvas_w, canvas_h = PAPER_H_PX, PAPER_W_PX

    # Scale image to fill canvas (crop centre), no white borders
    scale = max(canvas_w / iw, canvas_h / ih)
    new_w = int(iw * scale)
    new_h = int(ih * scale)
    img = img.resize((new_w, new_h), Image.LANCZOS)

    offset_x = (new_w - canvas_w) // 2
    offset_y = (new_h - canvas_h) // 2
    return img.crop((offset_x, offset_y, offset_x + canvas_w, offset_y + canvas_h))


def send_to_printer(jpeg_path: str, copies: int) -> None:
    cmd = ["lpr", "-#", str(copies), "-o", "media=ME_10x15", "-o", "fit-to-page"]
    if PRINTER_NAME:
        cmd += ["-P", PRINTER_NAME]
    cmd.append(jpeg_path)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"lpr failed: {result.stderr.strip()}")


def write_log_entry(
    user_id: int,
    user_name: str,
    username: str | None,
    copies: int,
    status: str,
    error: str | None,
    photo_file_id: str,
) -> None:
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "user_id": user_id,
        "user_name": user_name,
        "username": username,
        "copies": copies,
        "status": status,
        "error": error,
        "photo_file_id": photo_file_id,
    }
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")


# --- Gallery helpers ---
_gallery_bot: Bot | None = None


async def _get_gallery_bot() -> Bot | None:
    global _gallery_bot
    if not GALLERY_BOT_TOKEN:
        return None
    if _gallery_bot is None:
        _gallery_bot = Bot(token=GALLERY_BOT_TOKEN)
        await _gallery_bot.initialize()
    return _gallery_bot


def write_gallery_log_entry(
    user_id: int,
    user_name: str,
    username: str | None,
    photo_file_id: str,
    copies: int,
) -> None:
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "user_id": user_id,
        "user_name": user_name,
        "username": username,
        "photo_file_id": photo_file_id,
        "copies": copies,
    }
    with open(GALLERY_LOG_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")


async def post_to_channel(file_bytes: bytes, user_name: str, copies: int) -> str | None:
    """Post photo to gallery channel silently. Returns the gallery bot's file_id or None."""
    if not GALLERY_BOT_TOKEN or not GALLERY_CHANNEL_ID:
        return None
    try:
        gbot = await _get_gallery_bot()
        if gbot is None:
            return None
        ts_str = datetime.now().strftime("%-d %b %Y, %H:%M")
        caption = f"📸 {user_name} • {ts_str}"
        if copies > 1:
            caption += f" • {copies} copies"
        msg = await gbot.send_photo(
            chat_id=int(GALLERY_CHANNEL_ID),
            photo=BytesIO(file_bytes),
            caption=caption,
        )
        return msg.photo[-1].file_id
    except Exception as exc:
        logger.warning("Gallery channel post failed: %s", exc)
        return None


def append_print_log(user, photo_file_id: str, copies: int, status: str, error: str | None) -> None:
    write_log_entry(
        user.id if user else 0,
        (user.first_name or "Unknown") if user else "Unknown",
        (user.username or None) if user else None,
        copies,
        status,
        error,
        photo_file_id,
    )


async def post_to_gallery_channel(file_bytes: bytes, user, copies: int) -> None:
    u_name = (user.first_name or "Unknown") if user else "Unknown"
    gfid = await post_to_channel(file_bytes, u_name, copies)
    if gfid:
        write_gallery_log_entry(
            user.id if user else 0,
            u_name,
            (user.username or None) if user else None,
            gfid,
            copies,
        )


# --- Pause + supply state (shared with monitor.py via flag/JSON files) ---

PAUSE_FILE = ".bot_paused"
SUPPLY_FILE = ".supply_state"
SUPPLY_LOCK = ".supply_lock"

DEFAULT_PAUSE_REASON = "The printer is currently offline."

DEFAULT_SUPPLY = {
    "ribbon": {"capacity": 700, "used": 0, "reset_at": None, "reset_by": None},
    "paper":  {"loaded": 50,    "used": 0, "reset_at": None, "reset_by": None},
    "alerts_sent": [],
}


def is_paused() -> tuple[bool, str]:
    """Returns (paused, reason). reason is empty when not paused."""
    pause_file = Path(PAUSE_FILE)
    if not pause_file.exists():
        return False, ""
    try:
        reason = pause_file.read_text().strip()
        return True, reason or DEFAULT_PAUSE_REASON
    except Exception:
        return True, DEFAULT_PAUSE_REASON


def load_supply() -> dict:
    """Read .supply_state or return defaults if missing/invalid."""
    try:
        return json.loads(Path(SUPPLY_FILE).read_text())
    except Exception:
        return json.loads(json.dumps(DEFAULT_SUPPLY))  # deep copy


def save_supply(state: dict) -> None:
    Path(SUPPLY_FILE).write_text(json.dumps(state, indent=2))


def increment_supply_used(copies: int) -> None:
    """Increment ribbon.used and paper.used by `copies` under a file lock.
    Retries up to 5 times if .supply_lock is held by monitor.py."""
    lock = Path(SUPPLY_LOCK)
    for _ in range(5):
        try:
            lock.touch(exist_ok=False)
            break
        except FileExistsError:
            time.sleep(0.1)
    try:
        state = load_supply()
        state["ribbon"]["used"] += copies
        state["paper"]["used"] += copies
        save_supply(state)
    finally:
        lock.unlink(missing_ok=True)


PAUSED_REPLY_TEMPLATE = (
    "The print bot is currently offline.\n\n"
    "Reason: {reason}\n\n"
    "Please try again later."
)


INSTRUCTIONS = f"""📸 *Photo Print Bot — How to Use*

Send a photo to print it on a 4x6 \(10x15 cm\) photo paper\.

*Single photo*
Just send a photo — prints 1 copy automatically\.
Add a caption to choose copies: `3` · `3x` · `x3`

*Multiple photos \(album\)*
Select several photos and send them together as an album\.
• No caption → 1 copy each
• `3` → 3 copies each
• `3,5,1` → photo 1 \= 3 copies, photo 2 \= 5, photo 3 \= 1
The number of values must match the number of photos\.

Maximum is {MAX_PRINTS_PER_MESSAGE} total prints per message\.

*Tips*
• Portrait and landscape photos are both supported\.
• You can send photos normally or as a file/document — both work\.
• The bot confirms your job then replies "Done\!" when finished\.
• If something goes wrong, the bot will reply with an error message\.

*What NOT to do*
• Do not send videos, stickers, or other file types\.
• Captions that are not a number default to 1 copy\."""


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(INSTRUCTIONS, parse_mode="MarkdownV2")


async def process_single_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle one photo or document image — same pipeline as before."""
    paused, reason = is_paused()
    if paused:
        await update.effective_message.reply_text(
            PAUSED_REPLY_TEMPLATE.format(reason=reason)
        )
        return

    message = update.effective_message
    if message.photo:
        file_obj = await message.photo[-1].get_file()
        photo_file_id = message.photo[-1].file_id
    elif message.document and message.document.mime_type and message.document.mime_type.startswith("image/"):
        file_obj = await message.document.get_file()
        photo_file_id = message.document.file_id
    else:
        return

    copies = parse_copies(message.caption)
    copy_list = [copies]
    ok, total, err = validate_print_limit(copy_list)
    if not ok:
        await message.reply_text(err)
        return

    user = message.from_user
    await message.reply_text(f"Printing {copies} {'copy' if copies == 1 else 'copies'}...")

    try:
        buf = BytesIO()
        await file_obj.download_to_memory(buf)
        buf.seek(0)

        img = Image.open(buf)
        if img.mode != "RGB":
            img = img.convert("RGB")
        img = fix_exif_rotation(img)
        img = fit_to_paper(img)

        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            tmp_path = tmp.name
            img.save(tmp_path, "JPEG", quality=95, dpi=(300, 300))

        try:
            send_to_printer(tmp_path, copies)
        finally:
            os.unlink(tmp_path)

        increment_supply_used(copies)
        await message.reply_text("Done!")
        append_print_log(user, photo_file_id, copies, "success", None)
        buf.seek(0)
        await post_to_gallery_channel(buf.read(), user, copies)

    except Exception as e:
        logger.exception("Print failed")
        await message.reply_text(f"Error: {e}")
        append_print_log(user, photo_file_id, copies, "failed", str(e))


async def process_album(media_group_id: str, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Called ~1.5 s after the last photo in an album arrives."""
    updates = album_buffer.pop(media_group_id, [])
    album_timers.pop(media_group_id, None)
    if not updates:
        return

    paused, reason = is_paused()
    if paused:
        await updates[0].effective_message.reply_text(
            PAUSED_REPLY_TEMPLATE.format(reason=reason)
        )
        return

    # Caption is only on the first message Telegram sends
    caption = None
    for u in updates:
        if u.effective_message.caption:
            caption = u.effective_message.caption
            break

    photo_count = len(updates)
    copy_list = parse_copy_list(caption, photo_count)

    if isinstance(copy_list, str):
        await updates[0].effective_message.reply_text(copy_list)
        return

    ok, total, err = validate_print_limit(copy_list)
    if not ok:
        await updates[0].effective_message.reply_text(err)
        return

    user = updates[0].effective_message.from_user
    total_copies = sum(copy_list)

    if photo_count == 1:
        c = copy_list[0]
        await updates[0].effective_message.reply_text(
            f"Printing {c} {'copy' if c == 1 else 'copies'}..."
        )
    else:
        summary = ", ".join(str(c) for c in copy_list)
        await updates[0].effective_message.reply_text(
            f"Got {photo_count} photos! "
            f"Printing [{summary}] copies ({total_copies} total)..."
        )

    all_success = True
    for i, (upd, copies) in enumerate(zip(updates, copy_list)):
        photo = upd.effective_message.photo[-1]
        try:
            tg_file = await context.bot.get_file(photo.file_id)
            buf = BytesIO()
            await tg_file.download_to_memory(buf)
            buf.seek(0)

            img = Image.open(buf)
            if img.mode != "RGB":
                img = img.convert("RGB")
            img = fix_exif_rotation(img)
            img = fit_to_paper(img)

            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
                tmp_path = tmp.name
                img.save(tmp_path, "JPEG", quality=95, dpi=(300, 300))

            try:
                send_to_printer(tmp_path, copies)
            finally:
                os.unlink(tmp_path)

            increment_supply_used(copies)
            append_print_log(user, photo.file_id, copies, "success", None)
            buf.seek(0)
            await post_to_gallery_channel(buf.read(), user, copies)

        except Exception as e:
            logger.exception("Album photo %d/%d failed", i + 1, photo_count)
            all_success = False
            append_print_log(user, photo.file_id, copies, "failed", str(e))
            await updates[0].effective_message.reply_text(
                f"Photo {i + 1} of {photo_count} failed: {e}"
            )

    if all_success:
        if photo_count == 1:
            await updates[0].effective_message.reply_text("Done!")
        else:
            await updates[0].effective_message.reply_text(
                f"Done! All {photo_count} photos sent to printer."
            )


async def handle_image(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message

    # Documents are never part of a Telegram album
    if message.document:
        await process_single_photo(update, context)
        return

    if not message.photo:
        return

    media_group_id = message.media_group_id
    if media_group_id:
        if media_group_id not in album_buffer:
            album_buffer[media_group_id] = []
        album_buffer[media_group_id].append(update)

        # Reset the timer each time a new photo in the group arrives
        if media_group_id in album_timers:
            album_timers[media_group_id].cancel()
        loop = asyncio.get_running_loop()
        album_timers[media_group_id] = loop.call_later(
            1.5,
            lambda mgid=media_group_id: asyncio.ensure_future(
                process_album(mgid, context)
            ),
        )
    else:
        await process_single_photo(update, context)


def main() -> None:
    if not BOT_TOKEN:
        raise SystemExit("Set PRINT_BOT_TOKEN in .env before running.")

    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(
        MessageHandler(
            (filters.PHOTO | filters.Document.IMAGE),
            handle_image,
        )
    )
    paused, reason = is_paused()
    if paused:
        logger.warning("Bot started in PAUSED state: %s", reason)
    else:
        logger.info("Bot started — accepting photos")
    app.run_polling()


if __name__ == "__main__":
    main()
