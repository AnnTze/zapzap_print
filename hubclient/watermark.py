"""Apply the watermark config the hub hands down in each heartbeat response.

The booth stays authoritative over its own disk: this writes the PNG that
bot.py already reads, rather than reaching into bot.py's state. bot.py notices
the file changed by its mtime and reloads on the next print.

A null watermark in the response means the hub has no opinion — the booth keeps
whatever its .env specifies. It never means "remove the watermark", so a hub
outage or an unassigned event can't silently strip branding off prints.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# Records which config the local watermark file came from, so a booth restart
# doesn't re-download a file it already has.
STATE_FILE = ".watermark_state"


def _load_state(state_path: str) -> dict:
    try:
        return json.loads(Path(state_path).read_text())
    except Exception:
        return {}


def _save_state(state_path: str, state: dict) -> None:
    try:
        Path(state_path).write_text(json.dumps(state, indent=2))
    except Exception:
        logger.exception("Could not record watermark state")


def current_version(state_path: str = STATE_FILE) -> str | None:
    return _load_state(state_path).get("config_version")


def write_watermark(target_path: str, data: bytes) -> None:
    """Replace the watermark PNG atomically.

    bot.py may open this file at any moment. Writing to a temp name and renaming
    means it can never read a half-written image. On Windows the rename can fail
    if bot.py holds the file open at that instant, so the caller retries.
    """
    target = Path(target_path)
    if target.parent and not target.parent.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.partial")
    tmp.write_bytes(data)
    os.replace(tmp, target)


async def apply_config(client, config: dict, *, watermark_path: str,
                       state_path: str = STATE_FILE) -> bool:
    """Bring the local watermark in line with the hub's config.

    Returns True if the file on disk changed. Never raises: a failure here must
    not affect printing.
    """
    try:
        version = config.get("config_version")
        wm = config.get("watermark")
        state = _load_state(state_path)

        if not wm:
            # Hub has no opinion. Leave the booth's own file alone.
            return False

        if state.get("config_version") == version and Path(watermark_path).exists():
            return False

        digest = wm.get("sha256")
        data = await client.fetch_asset(digest)
        if data is None:
            logger.warning("Could not fetch watermark %s from hub", digest)
            return False

        for attempt in range(3):
            try:
                write_watermark(watermark_path, data)
                break
            except OSError:
                if attempt == 2:
                    raise
                logger.warning("Watermark file busy, retrying (%d)", attempt + 1)
        else:
            return False

        _save_state(state_path, {
            "config_version": version,
            "sha256": digest,
            "filename": wm.get("filename"),
            "opacity": wm.get("opacity"),
            "scale": wm.get("scale"),
            "margin": wm.get("margin"),
            "position": wm.get("position"),
        })
        logger.info("Watermark updated from hub: %s (%s)", wm.get("filename"), digest[:12])
        return True

    except Exception:
        logger.exception("Applying watermark config failed; keeping the current one")
        return False
