"""Cross-platform advisory file lock, used to guard .supply_state.

bot.py and monitor.py both read-modify-write the supply counters, so the update
has to be exclusive. The lock is taken with the OS's own locking primitive
(fcntl on macOS/Linux, msvcrt on Windows) rather than by creating a lock *file*
and checking whether it exists: the kernel drops an OS lock when the holding
process dies, so a bot that gets SIGKILLed (or is stopped by stop.sh escalating
to -9) mid-update can never leave a lock behind that wedges every later print.
"""

from __future__ import annotations

import os
from contextlib import contextmanager

if os.name == "nt":  # Windows
    import msvcrt

    def _acquire(fh) -> None:
        fh.seek(0)
        # Locks one byte at offset 0. LK_LOCK retries for ~10s, then raises
        # OSError — which propagates as a failed print rather than a silently
        # corrupted counter.
        msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)

    def _release(fh) -> None:
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)

else:  # macOS / Linux
    import fcntl

    def _acquire(fh) -> None:
        fcntl.flock(fh, fcntl.LOCK_EX)

    def _release(fh) -> None:
        fcntl.flock(fh, fcntl.LOCK_UN)


@contextmanager
def locked(path: str):
    """Hold an exclusive lock on `path` for the duration of the block.

    The lock file's contents are irrelevant — only the lock matters — but it
    must be a real file, and on Windows it must be non-empty for msvcrt to have
    a byte to lock.
    """
    # "a+" creates the file if absent without truncating a file another process
    # may already hold a lock on.
    with open(path, "a+") as fh:
        if os.name == "nt" and fh.tell() == 0:
            fh.write("lock")
            fh.flush()
        _acquire(fh)
        try:
            yield
        finally:
            try:
                _release(fh)
            except OSError:
                pass  # closing the handle drops the lock anyway
