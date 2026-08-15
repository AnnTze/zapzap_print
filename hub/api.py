"""HTTP surface: telemetry ingest, dashboard JSON, and the dashboard page.

Nothing here imports PIL or printing/ — the hub never touches an image or a
printer, which is what keeps it portable to a cheap VM later.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from aiohttp import web

from . import assets, db

logger = logging.getLogger(__name__)

# A booth heartbeats every 10s; three missed beats is a generous offline call
# that survives one slow poll without flapping.
OFFLINE_AFTER_SECONDS = 35

MAX_BODY_BYTES = 64 * 1024

# Mirrors bot.py's _WATERMARK_POSITIONS. Kept as a literal rather than imported
# so the hub never reaches into booth code.
WATERMARK_POSITIONS = {"bottom-right", "bottom-left", "top-right", "top-left", "center"}


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

    wm = await asyncio.to_thread(db.watermark_for_printer, db_path, printer["id"])
    return web.json_response({
        "printer_id": printer["id"],
        "printer_name": printer["name"],
        # The hash IS the version: change the file or a setting and it moves.
        "config_version": _config_version(wm),
        "watermark": _watermark_response(wm),
    })


def _config_version(wm: dict | None) -> str:
    """A stable fingerprint of the resolved config, for cheap change detection."""
    if not wm:
        return "none"
    parts = [wm["sha256"]] + [
        "" if wm.get(k) is None else str(wm[k])
        for k in ("opacity", "scale", "margin", "position")
    ]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


def _watermark_response(wm: dict | None) -> dict | None:
    """None means the hub has no opinion; the booth keeps its own .env."""
    if not wm:
        return None
    return {
        "sha256": wm["sha256"],
        "filename": wm["filename"],
        "url": f"/assets/{wm['sha256']}",
        "opacity": wm.get("opacity"),
        "scale": wm.get("scale"),
        "margin": wm.get("margin"),
        "position": wm.get("position"),
    }


async def get_asset(request: web.Request) -> web.Response:
    """Serve a watermark PNG by hash. Reachable without a bearer token: the hub
    is already behind Tailscale and the dashboard needs to render previews."""
    digest = request.match_info["digest"]
    data = await asyncio.to_thread(assets.read, request.app["assets_dir"], digest)
    if data is None:
        return web.json_response({"error": "no such asset"}, status=404)
    return web.Response(
        body=data,
        content_type="image/png",
        # Immutable: the hash changes whenever the bytes do.
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


async def upload_asset(request: web.Request) -> web.Response:
    """Drag-and-drop target. Accepts one PNG as multipart form data."""
    reader = await request.multipart()
    field = await reader.next()
    while field is not None and field.name != "file":
        field = await reader.next()
    if field is None:
        return web.json_response({"error": "no file in upload"}, status=400)

    # read_chunk rather than iter_chunked: BodyPartReader has no iter_chunked.
    data = bytearray()
    while True:
        chunk = await field.read_chunk(64 * 1024)
        if not chunk:
            break
        data.extend(chunk)
        if len(data) > assets.MAX_ASSET_BYTES:
            return web.json_response(
                {"error": f"File is larger than "
                          f"{assets.MAX_ASSET_BYTES // 1024 // 1024}MB."}, status=413)
    data = bytes(data)

    try:
        assets.validate_png(data)
    except assets.AssetError as exc:
        return web.json_response({"error": str(exc)}, status=400)

    digest = await asyncio.to_thread(assets.store, request.app["assets_dir"], data)
    filename = field.filename or "watermark.png"
    await asyncio.to_thread(db.record_asset, request.app["db_path"], digest,
                            filename, len(data))
    return web.json_response({"sha256": digest, "filename": filename,
                              "size_bytes": len(data), "url": f"/assets/{digest}"})


async def set_watermark(request: web.Request) -> web.Response:
    """Assign a watermark (and optional settings) to an event."""
    event_id = request.match_info["event_id"]
    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        return web.json_response({"error": "body must be JSON"}, status=400)

    sha256 = body.get("sha256") or None
    if sha256 and assets.path_for(request.app["assets_dir"], sha256) is None:
        return web.json_response({"error": "unknown asset"}, status=400)

    settings = {}
    for key, lo, hi in (("opacity", 0.0, 3.0), ("scale", 0.01, 1.0), ("margin", 0.0, 0.5)):
        val = body.get(key)
        if val is not None:
            try:
                val = float(val)
            except (TypeError, ValueError):
                return web.json_response({"error": f"{key} must be a number"}, status=400)
            if not lo <= val <= hi:
                return web.json_response(
                    {"error": f"{key} must be between {lo} and {hi}"}, status=400)
        settings[key] = val

    position = body.get("position")
    if position is not None and position not in WATERMARK_POSITIONS:
        return web.json_response(
            {"error": f"position must be one of: {', '.join(sorted(WATERMARK_POSITIONS))}"},
            status=400)
    settings["position"] = position

    ok = await asyncio.to_thread(db.set_event_watermark, request.app["db_path"],
                                 event_id, sha256, settings)
    if not ok:
        return web.json_response({"error": "no such event"}, status=404)
    return web.json_response({"ok": True})


async def api_assets(request: web.Request) -> web.Response:
    rows = await asyncio.to_thread(db.list_assets, request.app["db_path"])
    for r in rows:
        r["url"] = f"/assets/{r['sha256']}"
    return web.json_response({"assets": rows})


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


def build_app(db_path: str, assets_dir: str = "hub_assets") -> web.Application:
    app = web.Application(client_max_size=assets.MAX_ASSET_BYTES + 1024 * 1024)
    app["db_path"] = db_path
    app["assets_dir"] = assets_dir
    app.add_routes([
        web.get("/", dashboard),
        web.get("/healthz", healthz),
        web.get("/api/printers", api_printers),
        web.get("/api/events", api_events),
        web.get("/api/assets", api_assets),
        web.post("/api/assets", upload_asset),
        web.post("/api/events/{event_id}/watermark", set_watermark),
        web.get("/assets/{digest}", get_asset),
        web.post("/ingest/heartbeat", ingest_heartbeat),
    ])
    return app
