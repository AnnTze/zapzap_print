"""Date formatting that works on macOS and Windows alike.

The bots were written with strftime("%-d %b %Y"). The %-d ("day, no leading
zero") flag is a glibc/BSD extension: on Windows the C runtime rejects it and
strftime raises ValueError. Windows' own spelling is %#d, so there is no single
format string that works on both.

Building the day number in Python sidesteps the whole problem — %b and %Y are
standard C89 and behave the same everywhere.
"""

from __future__ import annotations

from datetime import date, datetime


def fmt_date(d: date | datetime) -> str:
    """5 Jun 2026"""
    return f"{d.day} {d:%b %Y}"


def fmt_datetime(dt: datetime, comma: bool = False) -> str:
    """5 Jun 2026 14:03 — or with comma=True, 5 Jun 2026, 14:03"""
    sep = ", " if comma else " "
    return f"{dt.day} {dt:%b %Y}{sep}{dt:%H:%M}"
