"""Rebuild the SDK wheel in-place by refreshing stale source files.

The old wheel under sdk/python/dist ships outdated models.py (still carries
deprecated `agent_connection_id` / `endpoint_config` fields) and runner.py
(missing `_finalize` + `_refresh_finalized_items`). This script refreshes every
`agent_eval/*.py` entry from the current `src/` tree and rewrites the wheel
atomically, preserving the dist-info metadata.
"""
from __future__ import annotations

import hashlib
import io
import os
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SDK = os.path.join(ROOT, "sdk", "python")
WHEEL = os.path.join(SDK, "dist", "agent_eval_sdk-0.1.0-py3-none-any.whl")
SRC = os.path.join(SDK, "src", "agent_eval")


def main() -> None:
    with zipfile.ZipFile(WHEEL) as zin:
        entries = {i.filename: zin.read(i.filename) for i in zin.infolist()}

    refreshed: list[str] = []
    for name in sorted(os.listdir(SRC)):
        if not name.endswith(".py"):
            continue
        arc = f"agent_eval/{name}"
        new = open(os.path.join(SRC, name), "rb").read()
        if arc in entries and entries[arc] != new:
            entries[arc] = new
            refreshed.append(arc)

    if not refreshed:
        print("wheel already up to date; nothing refreshed")
        return

    # Rewrite atomically: build to a temp buffer, then write to a temp file and
    # replace via os.replace (avoids leaving a half-written wheel).
    tmp = WHEEL + ".tmp"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
        for name, data in entries.items():
            zout.writestr(name, data)

    os.replace(tmp, WHEEL)
    print("refreshed entries:")
    for arc in refreshed:
        print("  -", arc)

    # Verify the result parses and matches src.
    with zipfile.ZipFile(WHEEL) as z:
        for arc in refreshed:
            assert z.read(arc) == open(os.path.join(SRC, arc.split("/", 1)[1]), "rb").read()
    print("verify OK:", os.path.getsize(WHEEL), "bytes")


if __name__ == "__main__":
    main()
