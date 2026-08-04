"""Cross-platform printing package.

`get_printer_backend()` returns the right backend for the current OS, chosen by
`platform.system()`:

    Darwin  -> CupsPrinterBackend   (lpr / lpstat)
    Windows -> WindowsPrinterBackend (pywin32)

Callers (bot.py, monitor.py) import from here and never touch platform-specific
printing code directly.
"""

from __future__ import annotations

import os
import platform

from .base import PrinterBackend, QueueJob, format_queue_text

__all__ = ["get_printer_backend", "PrinterBackend", "QueueJob", "format_queue_text"]


def get_printer_backend() -> PrinterBackend:
    system = platform.system()
    # PRINTER_NAME falls back to the original hardcoded CP-D90DW name so existing
    # macOS deployments keep working even without the var set in .env.
    printer_name = os.getenv("PRINTER_NAME", "MITSUBISHI_CPD90D")

    if system == "Windows":
        from .windows_backend import WindowsPrinterBackend

        return WindowsPrinterBackend(
            printer_name=printer_name,
            paper_form=os.getenv("WINDOWS_PAPER_FORM_NAME", ""),
        )

    # Darwin (and any other Unix with CUPS) -> CUPS backend
    from .cups_backend import CupsPrinterBackend

    return CupsPrinterBackend(
        printer_name=printer_name,
        media=os.getenv("CUPS_MEDIA", "ME_10x15"),
    )
