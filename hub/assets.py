"""Content-addressed store for watermark PNGs.

Files are named by the SHA-256 of their bytes, which gives three things for
free: uploading the same PNG twice costs nothing, a booth can tell whether it
already holds a file without downloading it, and the hash doubles as the config
version a heartbeat compares against.

The hub deliberately does not open these images. Validation is a magic-byte
check, not a decode, so the hub needs no Pillow — see tests/test_boundaries.py.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
MAX_ASSET_BYTES = 8 * 1024 * 1024


class AssetError(ValueError):
    """Upload rejected. The message is shown to the user in the dashboard."""


def validate_png(data: bytes) -> None:
    if not data:
        raise AssetError("That file is empty.")
    if len(data) > MAX_ASSET_BYTES:
        mb = len(data) / 1024 / 1024
        raise AssetError(
            f"That file is {mb:.1f}MB. Watermarks must be under "
            f"{MAX_ASSET_BYTES // 1024 // 1024}MB."
        )
    if not data.startswith(PNG_MAGIC):
        raise AssetError("Watermarks must be PNG files — transparency is needed "
                         "so the photo shows through.")


def store(assets_dir: str, data: bytes) -> str:
    """Write bytes under their hash. Returns the hash. Idempotent."""
    digest = hashlib.sha256(data).hexdigest()
    root = Path(assets_dir)
    root.mkdir(parents=True, exist_ok=True)
    target = root / f"{digest}.png"
    if not target.exists():
        # Write to a temp name then rename, so a reader can never observe a
        # half-written asset under its final hash.
        tmp = root / f".{digest}.partial"
        tmp.write_bytes(data)
        tmp.replace(target)
    return digest


def path_for(assets_dir: str, digest: str) -> Path | None:
    """Resolve a hash to a file, or None. Rejects anything that isn't a plain
    hex digest so a crafted hash cannot escape the assets directory."""
    if not digest or len(digest) != 64 or not all(c in "0123456789abcdef" for c in digest):
        return None
    target = Path(assets_dir) / f"{digest}.png"
    return target if target.exists() else None


def read(assets_dir: str, digest: str) -> bytes | None:
    target = path_for(assets_dir, digest)
    return target.read_bytes() if target else None
