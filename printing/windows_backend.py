"""Windows printer backend (Mitsubishi CP-D90DW and similar).

Uses pywin32 (win32print / win32gui / win32ui) + PIL.ImageWin to render an
already-sized JPEG straight to the printer's GDI device context.

Only imported on Windows (see printing/__init__.py), so the `import win32*`
lines never run on macOS.

Install deps with:  pip install -r requirements-windows.txt
"""

from __future__ import annotations

import logging

try:
    import win32con
    import win32gui
    import win32print
    import win32ui
except ImportError as exc:  # pragma: no cover - Windows only
    raise ImportError(
        "pywin32 is required for Windows printing. "
        "Install it with:  pip install -r requirements-windows.txt"
    ) from exc

from PIL import Image, ImageWin

from .base import PrinterBackend, QueueJob

logger = logging.getLogger(__name__)

# Printer status bits (defined locally so we don't depend on their presence in a
# particular pywin32 build's constants).
PRINTER_STATUS_ERROR = 0x00000002
PRINTER_STATUS_OFFLINE = 0x00000080
PRINTER_STATUS_PAPER_OUT = 0x00000010
PRINTER_STATUS_NOT_AVAILABLE = 0x00001000
_NOT_READY_MASK = (
    PRINTER_STATUS_ERROR
    | PRINTER_STATUS_OFFLINE
    | PRINTER_STATUS_PAPER_OUT
    | PRINTER_STATUS_NOT_AVAILABLE
)

# DeviceCapabilities indices (win32con exposes these, but pin them for safety).
DC_PAPERS = 2
DC_PAPERNAMES = 16


def _human_size(n: int | None) -> str:
    if not n:
        return ""
    for unit in ("B", "K", "M", "G"):
        if n < 1024:
            return f"{n}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}T"


class WindowsPrinterBackend(PrinterBackend):
    def __init__(self, printer_name: str, paper_form: str = "") -> None:
        # On Windows an empty name is ambiguous, so fall back to the OS default.
        self.printer_name = printer_name or win32print.GetDefaultPrinter()
        # WINDOWS_PAPER_FORM_NAME: preferably a numeric DEVMODE.PaperSize id
        # (from scripts/find_paper_form.py); a form name also works but is
        # resolved live against the driver.
        self.paper_form = (paper_form or "").strip()

    # ------------------------------------------------------------------ paper
    def _resolve_paper_id(self, port: str) -> int | None:
        """Return the DEVMODE.PaperSize id for the configured form, or None."""
        if not self.paper_form:
            return None
        if self.paper_form.isdigit():
            return int(self.paper_form)
        # Name -> id lookup via the driver's paper list.
        try:
            names = win32print.DeviceCapabilities(
                self.printer_name, port, DC_PAPERNAMES
            )
            ids = win32print.DeviceCapabilities(self.printer_name, port, DC_PAPERS)
        except Exception:
            return None
        for name, pid in zip(names or [], ids or []):
            if name.strip().lower() == self.paper_form.lower():
                return int(pid)
        return None

    # ----------------------------------------------------------------- print
    def print_image(self, image_path: str, copies: int) -> None:
        img = Image.open(image_path)
        if img.mode != "RGB":
            img = img.convert("RGB")
        img_is_landscape = img.size[0] >= img.size[1]

        hprinter = win32print.OpenPrinter(self.printer_name)
        try:
            info = win32print.GetPrinter(hprinter, 2)
            devmode = info["pDevMode"]
            port = info.get("pPortName", "")
            paper_id = self._resolve_paper_id(port)
            if devmode is not None:
                if paper_id is not None:
                    devmode.PaperSize = paper_id
                    devmode.Fields |= win32con.DM_PAPERSIZE
                # The driver's stored default orientation (usually Portrait)
                # otherwise wins regardless of the photo's own shape, which
                # squeezes/crops landscape photos into a portrait frame.
                devmode.Orientation = (
                    win32con.DMORIENT_LANDSCAPE
                    if img_is_landscape
                    else win32con.DMORIENT_PORTRAIT
                )
                devmode.Fields |= win32con.DM_ORIENTATION
            # Create a device context bound to *this* devmode (per-job, without
            # mutating the printer's global defaults via SetPrinter).
            hdc = win32gui.CreateDC("WINSPOOL", self.printer_name, devmode)
        finally:
            win32print.ClosePrinter(hprinter)

        dc = win32ui.CreateDCFromHandle(hdc)
        try:
            # Fill the full printable area (borderless). bot.fit_to_paper has
            # already cropped the image to the 10x15 aspect ratio, but the
            # driver's printable rect isn't guaranteed to match that ratio
            # exactly (margins, wrong WINDOWS_PAPER_FORM_NAME, etc). Stretching
            # straight to (printable_w, printable_h) in that case would distort
            # the image non-uniformly - most visible on the watermark logo.
            # Center-crop to the printable rect's own aspect ratio first so the
            # final draw only ever scales uniformly.
            printable_w = dc.GetDeviceCaps(win32con.HORZRES)
            printable_h = dc.GetDeviceCaps(win32con.VERTRES)
            img_w, img_h = img.size
            target_ratio = printable_w / printable_h
            src_ratio = img_w / img_h
            logger.info(
                "print geometry: printable=%dx%d (ratio=%.4f) image=%dx%d (ratio=%.4f)",
                printable_w, printable_h, target_ratio, img_w, img_h, src_ratio,
            )
            if abs(src_ratio - target_ratio) > 1e-3:
                if src_ratio > target_ratio:
                    new_w = max(1, round(img_h * target_ratio))
                    x0 = (img_w - new_w) // 2
                    img = img.crop((x0, 0, x0 + new_w, img_h))
                else:
                    new_h = max(1, round(img_w / target_ratio))
                    y0 = (img_h - new_h) // 2
                    img = img.crop((0, y0, img_w, y0 + new_h))
            dib = ImageWin.Dib(img)

            dc.StartDoc("ZapZap Photo")
            try:
                # One StartPage/EndPage per copy => N physical prints, regardless
                # of whether the driver honours DEVMODE.Copies for GDI jobs
                # (unreliable on dye-sub photo drivers).
                for _ in range(max(1, copies)):
                    dc.StartPage()
                    dib.draw(dc.GetHandleOutput(), (0, 0, printable_w, printable_h))
                    dc.EndPage()
                dc.EndDoc()
            except Exception:
                dc.AbortDoc()
                raise
        except Exception as exc:
            raise RuntimeError(f"Windows print failed: {exc}") from exc
        finally:
            dc.DeleteDC()

    # ----------------------------------------------------------------- queue
    def queue(self) -> list[QueueJob]:
        try:
            hprinter = win32print.OpenPrinter(self.printer_name)
        except Exception:
            return []
        try:
            raw = win32print.EnumJobs(hprinter, 0, 999, 1)
        except Exception:
            return []
        finally:
            win32print.ClosePrinter(hprinter)

        jobs: list[QueueJob] = []
        for j in raw or []:
            jobs.append(
                {
                    "id": str(j.get("JobId", "")),
                    "user": j.get("pUserName", "") or "",
                    "title": j.get("pDocument", "") or "",
                    "size": _human_size(j.get("Size")),
                    "status": j.get("pStatus", "") or "",
                }
            )
        return jobs

    # ----------------------------------------------------------------- ready
    def is_ready(self) -> bool:
        try:
            hprinter = win32print.OpenPrinter(self.printer_name)
        except Exception:
            return False
        try:
            status = win32print.GetPrinter(hprinter, 2)["Status"]
        except Exception:
            return False
        finally:
            win32print.ClosePrinter(hprinter)
        return not (status & _NOT_READY_MASK)
