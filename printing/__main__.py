"""Tiny CLI so the shell / PowerShell lifecycle scripts can query the printer
through the same abstraction the bots use, instead of calling lpstat directly.

    python -m printing status   # prints ONLINE/OFFLINE, exit 0 if ready else 1
    python -m printing queue    # prints the current queue (or "Queue empty")
"""

from __future__ import annotations

import os
import sys

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:
    pass

from . import format_queue_text, get_printer_backend


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else "status"
    backend = get_printer_backend()
    name = os.getenv("PRINTER_NAME", "MITSUBISHI_CPD90D") or "(system default)"

    if cmd == "status":
        try:
            ready = backend.is_ready()
        except Exception as exc:  # never let a driver hiccup crash the script
            print(f"Printer status check failed: {exc}")
            return 1
        if ready:
            print(f"Printer ONLINE: {name}")
            return 0
        print(f"Printer NOT READY: {name}")
        return 1

    if cmd == "queue":
        try:
            print(format_queue_text(backend.queue()))
        except Exception as exc:
            print(f"Queue check failed: {exc}")
            return 1
        return 0

    print(f"Unknown command: {cmd}. Use 'status' or 'queue'.")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
