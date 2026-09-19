"""Verify the built SDK wheel matches the current ``src/`` tree.

CI guard against wheel/source drift: any ``agent_eval/*.py`` inside the wheel
that differs from the corresponding file under ``sdk/python/src/agent_eval``
causes a non-zero exit. This ensures the committed wheel never silently falls
behind the source of truth.

Usage:
    python scripts/check_wheel_consistency.py
"""

from __future__ import annotations

import os
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SDK = os.path.join(ROOT, "sdk", "python")
WHEEL = os.path.join(SDK, "dist", "agent_eval_sdk-0.1.0-py3-none-any.whl")
SRC = os.path.join(SDK, "src", "agent_eval")


def main() -> int:
    if not os.path.exists(WHEEL):
        print(f"ERROR: wheel not found at {WHEEL}", file=sys.stderr)
        return 1

    with zipfile.ZipFile(WHEEL) as z:
        wheel_files = {i.filename for i in z.infolist()}
        wheel_data = {i.filename: z.read(i.filename) for i in z.infolist()}

    src_files = sorted(f for f in os.listdir(SRC) if f.endswith(".py"))

    mismatches: list[str] = []
    for name in src_files:
        arc = f"agent_eval/{name}"
        if arc not in wheel_files:
            mismatches.append(f"{arc}: missing from wheel")
            continue
        src_bytes = open(os.path.join(SRC, name), "rb").read()
        if wheel_data[arc] != src_bytes:
            mismatches.append(f"{arc}: content differs from src/")

    if mismatches:
        print("ERROR: SDK wheel is out of sync with src/. Refresh it with "
              "`python scripts/rebuild_wheel.py`.", file=sys.stderr)
        for line in mismatches:
            print("  -", line, file=sys.stderr)
        return 1

    print(f"OK: {len(src_files)} source files match the wheel.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
