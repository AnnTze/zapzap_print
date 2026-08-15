"""The hub/booth split, enforced rather than documented.

The hub must never import Pillow or the printing backends: that is what keeps
it a small pure-Python service you can move to any Linux box in an afternoon.
The booth client must never import telegram: monitor.py talks to Telegram, the
hub client does not.

Run with:  python tests/test_boundaries.py
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

CASES = [
    ("hub", ["hub.db", "hub.api", "hub.main"], ["PIL", "printing"]),
    ("hubclient", ["hubclient", "hubclient.client", "hubclient.collect"], ["telegram", "PIL"]),
]

PROBE = """
import sys
for mod in {imports!r}:
    __import__(mod)
leaked = [m for m in {forbidden!r} if m in sys.modules or any(
    k == m or k.startswith(m + ".") for k in sys.modules)]
print(",".join(leaked))
"""


def main() -> int:
    failures = 0
    for name, imports, forbidden in CASES:
        proc = subprocess.run(
            [sys.executable, "-c", PROBE.format(imports=imports, forbidden=forbidden)],
            capture_output=True, text=True, cwd=ROOT,
        )
        if proc.returncode != 0:
            print(f"FAIL  {name}: could not import\n{proc.stderr.strip()}")
            failures += 1
            continue
        leaked = [m for m in proc.stdout.strip().split(",") if m]
        if leaked:
            print(f"FAIL  {name}/ imported forbidden module(s): {', '.join(leaked)}")
            failures += 1
        else:
            print(f"ok    {name}/ imports none of: {', '.join(forbidden)}")

    print("\nPASS" if not failures else f"\n{failures} boundary violation(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
