"""Hub: telemetry sink, dashboard, and (later) watermark distribution.

Never imports PIL or printing/ — the hub does not process images or drive
printers, which keeps it portable to any cheap Linux box.
"""
