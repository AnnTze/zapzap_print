"""One-off Windows setup helper: list a printer's paper forms and their DEVMODE
PaperSize ids, so you can pick the right value for WINDOWS_PAPER_FORM_NAME in .env.

Run this ONCE while setting up a new Windows laptop, after the printer driver is
installed:

    python scripts/find_paper_form.py                # default / PRINTER_NAME printer
    python scripts/find_paper_form.py "CP-D90DW"     # a specific printer name

Then copy the numeric id of the borderless 4x6 / 10x15 form into .env:

    WINDOWS_PAPER_FORM_NAME=<id>

Using the numeric id (rather than the name) means the bot never has to enumerate
the driver live at an event.
"""

import sys

try:
    import win32print
except ImportError:
    sys.exit(
        "pywin32 is not installed. Run:  pip install -r requirements-windows.txt\n"
        "(This script only runs on Windows.)"
    )

DC_PAPERS = 2
DC_PAPERNAMES = 16


def main() -> int:
    if len(sys.argv) > 1:
        printer = sys.argv[1]
    else:
        import os

        printer = os.getenv("PRINTER_NAME") or win32print.GetDefaultPrinter()

    print(f"Printer: {printer}\n")

    try:
        hprinter = win32print.OpenPrinter(printer)
        try:
            port = win32print.GetPrinter(hprinter, 2).get("pPortName", "")
        finally:
            win32print.ClosePrinter(hprinter)
    except Exception as exc:
        return _fail(f"Could not open printer '{printer}': {exc}")

    try:
        names = win32print.DeviceCapabilities(printer, port, DC_PAPERNAMES)
        ids = win32print.DeviceCapabilities(printer, port, DC_PAPERS)
    except Exception as exc:
        return _fail(f"Could not read paper list: {exc}")

    if not names:
        return _fail("Driver returned no paper forms.")

    print(f"{'ID':>6}  Form name")
    print(f"{'--':>6}  ---------")
    for name, pid in zip(names, ids):
        print(f"{pid:>6}  {name}")

    print(
        "\nPick the borderless 4x6 / 10x15 / postcard form and put its ID in .env:\n"
        "    WINDOWS_PAPER_FORM_NAME=<id>"
    )
    return 0


def _fail(msg: str) -> int:
    print(f"ERROR: {msg}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
