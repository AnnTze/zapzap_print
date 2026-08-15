"""HTTP surface: telemetry ingest, dashboard JSON, and the dashboard page.

Nothing here imports PIL or printing/ — the hub never touches an image or a
printer, which is what keeps it portable to a cheap VM later.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from aiohttp import web

from . import db

logger = logging.getLogger(__name__)

# A booth heartbeats every 10s; three missed beats is a generous offline call
# that survives one slow poll without flapping.
OFFLINE_AFTER_SECONDS = 35

MAX_BODY_BYTES = 64 * 1024


def _bearer(request: web.Request) -> str | None:
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        return header[7:].strip()
    return None


async def ingest_heartbeat(request: web.Request) -> web.Response:
    """A booth reporting in. The response carries config back down."""
    api_key = _bearer(request)
    if not api_key:
        return web.json_response({"error": "missing bearer token"}, status=401)

    db_path = request.app["db_path"]
    printer = await asyncio.to_thread(db.printer_for_key, db_path, api_key)
    if printer is None:
        return web.json_response({"error": "unknown api key"}, status=403)

    if request.content_length and request.content_length > MAX_BODY_BYTES:
        return web.json_response({"error": "payload too large"}, status=413)
    try:
        payload = await request.json()
    except (json.JSONDecodeError, ValueError):
        return web.json_response({"error": "body must be JSON"}, status=400)
    if not isinstance(payload, dict):
        return web.json_response({"error": "body must be a JSON object"}, status=400)

    await asyncio.to_thread(db.record_heartbeat, db_path, printer["id"], payload)

    # Config channel. v1 has nothing to hand down yet, but the shape is fixed
    # now so adding watermarks later isn't a wire change.
    return web.json_response({
        "printer_id": printer["id"],
        "printer_name": printer["name"],
        "config_version": 0,
        "watermark": None,
    })


async def api_printers(request: web.Request) -> web.Response:
    printers = await asyncio.to_thread(db.list_printers, request.app["db_path"])
    now = datetime.now(timezone.utc)

    for p in printers:
        age = None
        if p["last_seen"]:
            try:
                age = (now - datetime.fromisoformat(p["last_seen"])).total_seconds()
            except ValueError:
                age = None
        p["seconds_since_seen"] = age
        p["online"] = age is not None and age <= OFFLINE_AFTER_SECONDS

    return web.json_response({
        "printers": printers,
        "generated_at": now.isoformat(),
        "offline_after_seconds": OFFLINE_AFTER_SECONDS,
    })


async def api_events(request: web.Request) -> web.Response:
    events = await asyncio.to_thread(db.list_events, request.app["db_path"])
    return web.json_response({"events": events})


async def dashboard(request: web.Request) -> web.Response:
    html = Path(__file__).parent / "dashboard.html"
    return web.Response(text=html.read_text(encoding="utf-8"), content_type="text/html")


async def healthz(request: web.Request) -> web.Response:
    return web.json_response({"ok": True})


def build_app(db_path: str) -> web.Application:
    app = web.Application()
    app["db_path"] = db_path
    app.add_routes([
        web.get("/", dashboard),
        web.get("/healthz", healthz),
        web.get("/api/printers", api_printers),
        web.get("/api/events", api_events),
        web.post("/ingest/heartbeat", ingest_heartbeat),
    ])
    return app
