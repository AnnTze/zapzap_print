"""macOS / CUPS printer backend.

Refactor of the printing logic that previously lived inline in bot.py
(`lpr`) and monitor.py (`lpstat -o`).
"""

from __future__ import annotations

import subprocess

from .base import PrinterBackend, QueueJob


class CupsPrinterBackend(PrinterBackend):
    def __init__(self, printer_name: str | None, media: str = "ME_10x15") -> None:
        # printer_name None/"" means "use the system default printer" (lpr with no -P)
        self.printer_name = printer_name or None
        self.media = media

    def print_image(self, image_path: str, copies: int) -> None:
        # print-scaling=fill crops to the driver's real printable-area aspect
        # ratio and fills it edge-to-edge, mirroring what windows_backend.py
        # does manually via GetDeviceCaps. The old fit-to-page instead
        # letterboxes (scales the whole image down, no cropping), which left
        # a different effective margin than Windows whenever the printable
        # rect wasn't exactly 3:2 - most visible on the watermark's size.
        cmd = [
            "lpr", "-#", str(copies),
            "-o", f"media={self.media}",
            "-o", "print-scaling=fill",
        ]
        if self.printer_name:
            cmd += ["-P", self.printer_name]
        cmd.append(image_path)
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"lpr failed: {result.stderr.strip()}")

    def queue(self) -> list[QueueJob]:
        try:
            result = subprocess.run(
                ["lpstat", "-o"], capture_output=True, text=True, timeout=5
            )
        except Exception:
            return []
        jobs: list[QueueJob] = []
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            # lpstat -o format: "<job-id> <user> <size> <date...>"
            parts = line.split(None, 3)
            job: QueueJob = {"id": parts[0] if parts else ""}
            if len(parts) >= 2:
                job["user"] = parts[1]
            if len(parts) >= 3:
                job["size"] = parts[2]
            jobs.append(job)
        return jobs

    def is_ready(self) -> bool:
        cmd = ["lpstat", "-p"]
        if self.printer_name:
            cmd.append(self.printer_name)
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        except Exception:
            return False
        out = result.stdout.lower()
        if not out.strip():
            return False
        # "disabled" printers are not ready; "enabled"/"idle"/"printing" are.
        if "disabled" in out:
            return False
        return "enabled" in out or "idle" in out or "printing" in out
