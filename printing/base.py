"""Printer backend interface shared by the CUPS (macOS) and Windows backends.

The rest of the codebase talks to printing only through this interface, so the
same bot.py / monitor.py run unchanged on macOS and Windows — only the concrete
backend differs.
"""

from __future__ import annotations

from typing import TypedDict


class QueueJob(TypedDict, total=False):
    """One print job, normalised to the same shape on every platform so callers
    (e.g. monitor.py's /queue) never need per-OS formatting."""

    id: str        # job id as a string
    user: str      # submitting user, or "" if unknown
    title: str     # document / file name, or "" if unknown
    size: str      # human-ish size string (e.g. "1.2M" or "1024 bytes"), or ""
    status: str    # backend status text, or "" if unknown


class PrinterBackend:
    """Abstract printer backend. Concrete subclasses live in cups_backend.py
    (macOS) and windows_backend.py (Windows)."""

    def print_image(self, image_path: str, copies: int) -> None:
        """Send an already-sized JPEG at `image_path` to the printer `copies`
        times. Must raise RuntimeError (or a subclass) on failure so bot.py can
        report the error to the user and log a failed attempt."""
        raise NotImplementedError

    def queue(self) -> list[QueueJob]:
        """Return the current print queue as a list of QueueJob dicts. Returns
        an empty list when the queue is empty or cannot be read."""
        raise NotImplementedError

    def is_ready(self) -> bool:
        """Return True if the printer exists and is not offline / errored."""
        raise NotImplementedError


def format_queue_text(jobs: list[QueueJob]) -> str:
    """Render a queue (list of QueueJob) into a compact monospace-friendly block
    for Telegram. Shared by both backends so /queue looks identical everywhere."""
    if not jobs:
        return "Queue empty"
    lines = []
    for j in jobs:
        parts = [j.get("id", "").strip()]
        who = j.get("user", "").strip()
        if who:
            parts.append(who)
        title = j.get("title", "").strip()
        if title:
            parts.append(title)
        size = j.get("size", "").strip()
        if size:
            parts.append(size)
        lines.append("  ".join(p for p in parts if p))
    return "\n".join(lines)
