"""SQLite storage for the hub.

Plain stdlib sqlite3 rather than aiosqlite: every query here is a handful of
rows against a local file, and the callers wrap access in asyncio.to_thread, so
an async driver would buy nothing but a dependency.

Watermarks resolve per event, not per printer: every booth assigned to an event
uses that event's mark. A NULL watermark means the hub has no opinion and the
booth falls back to its own .env — it never means "print without a watermark".
"""

from __future__ import annotations

import json
import secrets
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
PRAGMA legacy_alter_table=OFF;

CREATE TABLE IF NOT EXISTS events (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    watermark_asset TEXT REFERENCES assets(sha256),
    wm_opacity      REAL,
    wm_scale        REAL,
    wm_margin       REAL,
    wm_position     TEXT,
    created_at      TEXT NOT NULL
);

-- Content-addressed watermark files. Storing by hash means re-assigning a PNG
-- a booth already holds transfers nothing.
CREATE TABLE IF NOT EXISTS assets (
    sha256      TEXT PRIMARY KEY,
    filename    TEXT NOT NULL,
    size_bytes  INTEGER NOT NULL,
    uploaded_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS printers (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    api_key         TEXT NOT NULL UNIQUE,
    event_id        TEXT REFERENCES events(id),
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS printer_status (
    printer_id  TEXT PRIMARY KEY REFERENCES printers(id),
    last_seen   TEXT NOT NULL,
    payload     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_printers_event ON printers(event_id);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    # WAL so the dashboard can read while a heartbeat writes.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path: str) -> None:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with connect(db_path) as conn:
        conn.executescript(SCHEMA)


# --- printers ---------------------------------------------------------------

def create_printer(db_path: str, name: str) -> tuple[str, str]:
    """Register a printer. Returns (printer_id, api_key)."""
    printer_id = uuid.uuid4().hex[:12]
    api_key = secrets.token_urlsafe(32)
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO printers (id, name, api_key, created_at) VALUES (?, ?, ?, ?)",
            (printer_id, name, api_key, now_iso()),
        )
    return printer_id, api_key


def printer_for_key(db_path: str, api_key: str) -> sqlite3.Row | None:
    with connect(db_path) as conn:
        return conn.execute(
            "SELECT * FROM printers WHERE api_key = ?", (api_key,)
        ).fetchone()


def list_printers(db_path: str) -> list[dict]:
    """Every printer with its latest status payload merged in."""
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT p.id, p.name, p.event_id,
                   e.name AS event_name, e.watermark_asset,
                   a.filename AS watermark_filename,
                   s.last_seen, s.payload
            FROM printers p
            LEFT JOIN events e ON e.id = p.event_id
            LEFT JOIN assets a ON a.sha256 = e.watermark_asset
            LEFT JOIN printer_status s ON s.printer_id = p.id
            ORDER BY p.name
            """
        ).fetchall()

    out = []
    for r in rows:
        entry = {
            "id": r["id"],
            "name": r["name"],
            "event_id": r["event_id"],
            "event_name": r["event_name"],
            "watermark_filename": r["watermark_filename"],
            "last_seen": r["last_seen"],
            "status": {},
        }
        if r["payload"]:
            try:
                entry["status"] = json.loads(r["payload"])
            except json.JSONDecodeError:
                pass
        out.append(entry)
    return out


def record_heartbeat(db_path: str, printer_id: str, payload: dict) -> None:
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO printer_status (printer_id, last_seen, payload)
            VALUES (?, ?, ?)
            ON CONFLICT(printer_id) DO UPDATE SET
                last_seen = excluded.last_seen,
                payload   = excluded.payload
            """,
            (printer_id, now_iso(), json.dumps(payload)),
        )


# --- assets ------------------------------------------------------------------

def record_asset(db_path: str, sha256: str, filename: str, size_bytes: int) -> None:
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO assets (sha256, filename, size_bytes, uploaded_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(sha256) DO UPDATE SET filename = excluded.filename
            """,
            (sha256, filename, size_bytes, now_iso()),
        )


def list_assets(db_path: str) -> list[dict]:
    with connect(db_path) as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM assets ORDER BY uploaded_at DESC"
        ).fetchall()]


def set_event_watermark(db_path: str, event_id: str, sha256: str | None,
                        settings: dict | None = None) -> bool:
    """Assign a watermark to an event. sha256=None clears it, which returns the
    event's booths to whatever their own .env specifies."""
    settings = settings or {}
    with connect(db_path) as conn:
        cur = conn.execute(
            """
            UPDATE events SET watermark_asset = ?, wm_opacity = ?, wm_scale = ?,
                              wm_margin = ?, wm_position = ?
            WHERE id = ?
            """,
            (sha256, settings.get("opacity"), settings.get("scale"),
             settings.get("margin"), settings.get("position"), event_id),
        )
        return cur.rowcount > 0


def watermark_for_printer(db_path: str, printer_id: str) -> dict | None:
    """Resolve the watermark a booth should be using.

    One mark per event, no per-printer override. None means the hub has no
    opinion and the booth keeps using its own .env — it does not mean 'print
    without a watermark'.
    """
    with connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT e.watermark_asset, e.wm_opacity, e.wm_scale, e.wm_margin,
                   e.wm_position, a.filename
            FROM printers p
            JOIN events e ON e.id = p.event_id
            LEFT JOIN assets a ON a.sha256 = e.watermark_asset
            WHERE p.id = ?
            """,
            (printer_id,),
        ).fetchone()
    if row is None or not row["watermark_asset"]:
        return None
    return {
        "sha256": row["watermark_asset"],
        "filename": row["filename"],
        "opacity": row["wm_opacity"],
        "scale": row["wm_scale"],
        "margin": row["wm_margin"],
        "position": row["wm_position"],
    }


# --- events ------------------------------------------------------------------

def create_event(db_path: str, name: str) -> str:
    event_id = uuid.uuid4().hex[:12]
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO events (id, name, created_at) VALUES (?, ?, ?)",
            (event_id, name, now_iso()),
        )
    return event_id


def list_events(db_path: str) -> list[dict]:
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT e.*, a.filename AS watermark_filename,
                   (SELECT COUNT(*) FROM printers p WHERE p.event_id = e.id) AS printer_count
            FROM events e
            LEFT JOIN assets a ON a.sha256 = e.watermark_asset
            ORDER BY e.created_at DESC
            """
        ).fetchall()
        return [dict(r) for r in rows]


def assign_printer_to_event(db_path: str, printer_id: str, event_id: str | None) -> bool:
    with connect(db_path) as conn:
        cur = conn.execute(
            "UPDATE printers SET event_id = ? WHERE id = ?", (event_id, printer_id)
        )
        return cur.rowcount > 0
