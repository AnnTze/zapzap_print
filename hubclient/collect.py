"""Gather one snapshot of this booth's state.

Deliberately takes its inputs as arguments rather than importing monitor.py:
keeps the client testable on its own and keeps the import graph one-directional
(monitor -> hubclient, never back).

Everything here is snapshot telemetry, not an event stream. That matters: a
missed heartbeat costs nothing, because the next one carries the current truth.
It is what lets v1 skip a spool file without losing the dashboard's accuracy.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


def _read_supply(supply_file: str) -> dict:
    try:
        return json.loads(Path(supply_file).read_text())
    except Exception:
        return {}


def _prints_today(log_file: str) -> int:
    """Successful copies printed today, counted from the local print log."""
    today = datetime.now(timezone.utc).date().isoformat()
    total = 0
    try:
        with open(log_file) as f:
            for line in f:
                line = line.strip()
                if not line or today not in line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("timestamp", "").startswith(today) and entry.get("status") == "success":
                    total += entry.get("copies", 0)
    except FileNotFoundError:
        return 0
    except Exception:
        logger.exception("Could not count today's prints")
    return total


def collect(
    backend,
    *,
    log_file: str,
    supply_file: str,
    pause_file: str,
    auto_pause_file: str,
    printer_name: str = "",
) -> dict:
    """Build the heartbeat payload.

    `backend` is a printing.PrinterBackend. Both queue() and is_ready() shell
    out, so callers must run this off the event loop (asyncio.to_thread).
    """
    paused = Path(pause_file).exists()
    reason = ""
    if paused:
        try:
            reason = Path(pause_file).read_text().strip()
        except Exception:
            reason = ""

    try:
        queue = backend.queue()
    except Exception:
        logger.exception("Reading the print queue failed")
        queue = []

    try:
        ready = backend.is_ready()
    except Exception:
        logger.exception("Reading printer readiness failed")
        ready = None

    supply = _read_supply(supply_file)
    ribbon = supply.get("ribbon", {})
    paper = supply.get("paper", {})

    def remaining(block: dict, total_key: str) -> int | None:
        if not block:
            return None
        return max(0, block.get(total_key, 0) - block.get("used", 0))

    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "printer_name": printer_name,
        "paused": paused,
        "pause_reason": reason,
        "pause_auto": Path(auto_pause_file).exists(),
        "printer_ready": ready,
        "queue_depth": len(queue),
        "queue": queue[:10],
        "ribbon_remaining": remaining(ribbon, "capacity"),
        "ribbon_capacity": ribbon.get("capacity"),
        "paper_remaining": remaining(paper, "loaded"),
        "paper_loaded": paper.get("loaded"),
        "prints_today": _prints_today(log_file),
    }
