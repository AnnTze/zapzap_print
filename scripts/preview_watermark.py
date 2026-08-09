"""Preview the print pipeline (EXIF fix -> fit-to-paper -> watermark) on a photo
without touching Telegram or the printer. Use this to tune WATERMARK_PATH /
WATERMARK_OPACITY / WATERMARK_SCALE in .env before burning paper and ribbon.

Run from the project root:

    python scripts/preview_watermark.py path/to/photo.jpg [output.jpg]

Reads the same .env config bot.py uses, so whatever WATERMARK_* values are set
there are exactly what gets previewed. Output defaults to preview_output.jpg.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image

from bot import fix_exif_rotation, fit_to_paper, apply_watermark, WATERMARK_PATH


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python scripts/preview_watermark.py path/to/photo.jpg [output.jpg]")
        return 1

    in_path = sys.argv[1]
    out_path = sys.argv[2] if len(sys.argv) > 2 else "preview_output.jpg"

    print(f"Watermark file in use: {WATERMARK_PATH}")

    img = Image.open(in_path)
    if img.mode != "RGB":
        img = img.convert("RGB")
    img = fix_exif_rotation(img)
    img = fit_to_paper(img)
    img = apply_watermark(img)
    img.save(out_path, "JPEG", quality=95, dpi=(300, 300))

    print(f"Canvas size: {img.size} ({'landscape' if img.width >= img.height else 'portrait'})")
    print(f"Preview saved to: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
