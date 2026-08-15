"""Hub entry point and admin CLI.

    python -m hub.main serve
    python -m hub.main add-printer "Booth A"
    python -m hub.main add-event "Sarah & Tom"
    python -m hub.main assign <printer_id> <event_id>
    python -m hub.main printers

Binding defaults to 127.0.0.1. Set HUB_BIND=100.x.x.x (your Tailscale address)
to reach the dashboard from a phone without exposing it on venue wifi.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

from aiohttp import web
from dotenv import load_dotenv

from . import db
from .api import build_app

load_dotenv("hub.env")

DB_PATH = os.getenv("HUB_DB", "hub.db")
ASSETS_DIR = os.getenv("HUB_ASSETS", "hub_assets")
BIND = os.getenv("HUB_BIND", "127.0.0.1")
PORT = int(os.getenv("HUB_PORT", "8080"))

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("hub")


def cmd_serve(args: argparse.Namespace) -> None:
    db.init_db(DB_PATH)
    app = build_app(DB_PATH, ASSETS_DIR)
    logger.info("Hub listening on http://%s:%s  (db: %s)", BIND, PORT, DB_PATH)
    if BIND == "127.0.0.1":
        logger.info("Bound to localhost only. Set HUB_BIND to your Tailscale "
                    "address to reach the dashboard from another device.")
    web.run_app(app, host=BIND, port=PORT, print=None)


def cmd_add_printer(args: argparse.Namespace) -> None:
    db.init_db(DB_PATH)
    printer_id, api_key = db.create_printer(DB_PATH, args.name)
    print(f"Registered '{args.name}'\n")
    print(f"  PRINTER_ID      {printer_id}")
    print(f"  PRINTER_API_KEY {api_key}\n")
    print("Put these in that booth Mac's .env, along with:")
    print(f"  HUB_URL=http://<hub-tailscale-address>:{PORT}")


def cmd_add_event(args: argparse.Namespace) -> None:
    db.init_db(DB_PATH)
    event_id = db.create_event(DB_PATH, args.name)
    print(f"Created event '{args.name}' -> {event_id}")


def cmd_assign(args: argparse.Namespace) -> None:
    db.init_db(DB_PATH)
    ok = db.assign_printer_to_event(DB_PATH, args.printer_id, args.event_id)
    print("Assigned." if ok else f"No printer with id {args.printer_id}")


def cmd_printers(args: argparse.Namespace) -> None:
    db.init_db(DB_PATH)
    printers = db.list_printers(DB_PATH)
    if not printers:
        print("No printers registered. Use: python -m hub.main add-printer \"Booth A\"")
        return
    for p in printers:
        status = p.get("status") or {}
        seen = p["last_seen"] or "never"
        event = p["event_name"] or "-"
        print(f"{p['id']}  {p['name']:<16} event={event:<16} last_seen={seen}")
        if status:
            print(f"    queue={status.get('queue_depth')} "
                  f"ribbon={status.get('ribbon_remaining')} "
                  f"paper={status.get('paper_remaining')} "
                  f"paused={status.get('paused')}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="hub", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("serve", help="run the hub and dashboard").set_defaults(func=cmd_serve)

    p = sub.add_parser("add-printer", help="register a booth, printing its API key")
    p.add_argument("name")
    p.set_defaults(func=cmd_add_printer)

    p = sub.add_parser("add-event", help="create an event")
    p.add_argument("name")
    p.set_defaults(func=cmd_add_event)

    p = sub.add_parser("assign", help="assign a printer to an event")
    p.add_argument("printer_id")
    p.add_argument("event_id")
    p.set_defaults(func=cmd_assign)

    sub.add_parser("printers", help="list printers and last status").set_defaults(func=cmd_printers)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    sys.exit(main())
