import os
import re
import json
import time
import asyncio
import tempfile
import logging
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

from dotenv import load_dotenv
from PIL import Image, ExifTags
from telegram import Bot, Update
from telegram.ext import ApplicationBuilder, MessageHandler, CommandHandler, filters, ContextTypes

from printing import get_printer_backend

load_dotenv()

# --- Config ---
BOT_TOKEN = os.getenv("PRINT_BOT_TOKEN", "")
# Target printer. Set PRINTER_NAME in .env; falls back to the original
# hardcoded value so existing macOS deployments keep working unchanged.
PRINTER_NAME = os.getenv("PRINTER_NAME", "MITSUBISHI_CPD90D")
# Sized to the Mitsubishi CP-D90DW driver's actual ME_10x15 PageSize/ImageableArea
# (445x295pt per the PPD), not the nominal 15x10cm - ME_10x15 is a full-bleed
# template intentionally larger than the trimmed final size, so MEMarginCutOff
# has excess to physically cut off. Using the nominal 1772x1181 (15/10cm @300dpi)
# undersized the canvas by 82x48px, leaving a white border on Mac prints.
PAPER_W_PX = 1854   # landscape width at 300 DPI (445pt / 72 * 300, ME_10x15)
PAPER_H_PX = 1229   # landscape height at 300 DPI (295pt / 72 * 300, ME_10x15)
LOG_FILE = os.getenv("LOG_FILE", "print_log.jsonl")
GALLERY_BOT_TOKEN = os.getenv("GALLERY_BOT_TOKEN", "")
GALLERY_CHANNEL_ID = os.getenv("GALLERY_CHANNEL_ID", "")
GALLERY_LOG_FILE = os.getenv("GALLERY_LOG_FILE", "gallery_log.jsonl")
MAX_PRINTS_PER_MESSAGE = int(os.getenv("MAX_PRINTS_PER_MESSAGE", "5"))
MAX_COPIES = MAX_PRINTS_PER_MESSAGE
# Logo/image composited onto every printed photo (not applied to the gallery copy).
WATERMARK_PATH = os.getenv("WATERMARK_PATH", "watermark.png")
WATERMARK_OPACITY = float(os.getenv("WATERMARK_OPACITY", "0.35"))
WATERMARK_SCALE = float(os.getenv("WATERMARK_SCALE", "0.6"))  # fraction of shorter canvas side
WATERMARK_MARGIN = float(os.getenv("WATERMARK_MARGIN", "0.05"))  # edge gap, fraction of canvas width/height
# One of: bottom-right, bottom-left, top-right, top-left, center.
WATERMARK_POSITION = os.getenv("WATERMARK_POSITION", "bottom-right").strip().lower()
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


_watermark_img: Image.Image | None = None
_watermark_load_attempted = False


def get_watermark() -> Image.Image | None:
    """Load and cache the watermark image. Returns None (and logs once) if unavailable."""
    global _watermark_img, _watermark_load_attempted
    if _watermark_load_attempted:
        return _watermark_img
    _watermark_load_attempted = True
    if WATERMARK_PATH and os.path.exists(WATERMARK_PATH):
        try:
            _watermark_img = Image.open(WATERMARK_PATH).convert("RGBA")
        except Exception:
            logger.exception("Failed to load watermark image at %s", WATERMARK_PATH)
    else:
        logger.warning("WATERMARK_PATH %s not found; printing without watermark", WATERMARK_PATH)
    return _watermark_img


_WATERMARK_POSITIONS = {
    "bottom-right", "bottom-left", "top-right", "top-left", "center",
}


def _watermark_offset(position: str, canvas_w: int, canvas_h: int, new_w: int, new_h: int) -> tuple[int, int]:
    margin_x = int(canvas_w * WATERMARK_MARGIN)
    margin_y = int(canvas_h * WATERMARK_MARGIN)
    if position == "bottom-left":
        return margin_x, max(0, canvas_h - new_h - margin_y)
    if position == "top-right":
        return max(0, canvas_w - new_w - margin_x), margin_y
    if position == "top-left":
        return margin_x, margin_y
    if position == "center":
        return (canvas_w - new_w) // 2, (canvas_h - new_h) // 2
    # default: bottom-right
    return max(0, canvas_w - new_w - margin_x), max(0, canvas_h - new_h - margin_y)


def apply_watermark(img: Image.Image) -> Image.Image:
    """Composite the logo onto the print canvas at WATERMARK_POSITION."""
    watermark = get_watermark()
    if watermark is None:
        return img

    canvas_w, canvas_h = img.size
    wm_w, wm_h = watermark.size
    scale = (min(canvas_w, canvas_h) * WATERMARK_SCALE) / max(wm_w, wm_h)
    new_w, new_h = max(1, int(wm_w * scale)), max(1, int(wm_h * scale))
    wm = watermark.resize((new_w, new_h), Image.LANCZOS)

    if WATERMARK_OPACITY != 1.0:
        # >1.0 boosts alpha to push faint/anti-aliased edges (e.g. thin lines
        # softened by the resize above) back toward fully opaque/dark.
        alpha = wm.split()[3].point(lambda a: min(255, int(a * WATERMARK_OPACITY)))
        wm.putalpha(alpha)

    base = img.convert("RGBA")
    offset_x, offset_y = _watermark_offset(WATERMARK_POSITION, canvas_w, canvas_h, new_w, new_h)
    base.alpha_composite(wm, (offset_x, offset_y))
    return base.convert("RGB")


async def send_print_preview(message, img: Image.Image) -> None:
    """Reply with the final watermarked photo, exactly as it goes to the printer."""
    preview_buf = BytesIO()
    img.save(preview_buf, "JPEG", quality=90)
    preview_buf.seek(0)
    await message.reply_photo(preview_buf, caption="Here's your print!")


# Platform printer backend (CUPS on macOS, pywin32 on Windows), chosen at startup.
_printer_backend = get_printer_backend()


def send_to_printer(jpeg_path: str, copies: int) -> None:
    """Send an already-sized JPEG to the printer via the platform backend."""
    _printer_backend.print_image(jpeg_path, copies)


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


def _load_instructions() -> str:
    """Load the /start message from instructions.txt so it's editable without
    touching Python or MarkdownV2 escaping. Falls back to a plain notice if
    the file is missing so /start never crashes the bot."""
    path = Path(__file__).parent / "instructions.txt"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        logger.warning("instructions.txt not found; using fallback /start message")
        return "Send a photo to print it. (instructions.txt is missing)"
    return text.replace("{{MAX_PRINTS_PER_MESSAGE}}", str(MAX_PRINTS_PER_MESSAGE))


INSTRUCTIONS = _load_instructions()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(INSTRUCTIONS, parse_mode="Markdown")


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
        img = apply_watermark(img)
        await send_print_preview(message, img)

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

    except Exception as e:
        logger.exception("Print failed")
        await message.reply_text(f"Error: {e}")
        return

    # Gallery-channel repost is logging, not the print itself - a failure here
    # (e.g. a network timeout) must never overwrite the "success" already
    # logged above, or send the user a confusing error after "Done!".
    try:
        buf.seek(0)
        await post_to_gallery_channel(buf.read(), user, copies)
    except Exception:
        logger.exception("Gallery repost failed (print itself already succeeded)")
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
            img = apply_watermark(img)
            await send_print_preview(upd.effective_message, img)

            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
                tmp_path = tmp.name
                img.save(tmp_path, "JPEG", quality=95, dpi=(300, 300))

            try:
                send_to_printer(tmp_path, copies)
            finally:
                os.unlink(tmp_path)

            increment_supply_used(copies)
            append_print_log(user, photo.file_id, copies, "success", None)

        except Exception as e:
            logger.exception("Album photo %d/%d failed", i + 1, photo_count)
            all_success = False
            append_print_log(user, photo.file_id, copies, "failed", str(e))
            await updates[0].effective_message.reply_text(
                f"Photo {i + 1} of {photo_count} failed: {e}"
            )
            continue

        # Gallery-channel repost is logging, not the print itself - a failure
        # here (e.g. a network timeout) must never overwrite the "success"
        # already logged above, or mark an otherwise-successful photo failed.
        try:
            buf.seek(0)
            await post_to_gallery_channel(buf.read(), user, copies)
        except Exception:
            logger.exception(
                "Gallery repost failed for photo %d/%d (print itself already succeeded)",
                i + 1, photo_count,
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
            (filters.PHOTO | filters.Document.IMAGE) & ~filters.UpdateType.EDITED_MESSAGE,
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
