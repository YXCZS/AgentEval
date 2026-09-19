"""Verify every structural migration upgrades and downgrades cleanly.

Data migrations that intentionally refuse to downgrade (they require restoring a
backup instead of reversing a lossy data rewrite) are excluded. The round-trip
starts at the first structural migration after those data migrations
(``b9d0c02d2e51``) and drives an empty SQLite database through
``upgrade head -> downgrade floor -> upgrade head``. Any structural migration
whose downgrade fails breaks the round-trip and fails the check.

Usage:
    python scripts/check_migrations_roundtrip.py [--floor REVISION]
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]

# The last data migration; its downgrade is intentionally NotImplementedError
# ("restore a backup instead"). Everything after it is structural and must
# round-trip.
DEFAULT_FLOOR = "b9d0c02d2e51"


def sqlite_url(path: Path) -> str:
    return f"sqlite:///{path.resolve().as_posix()}"


def run_alembic(database_path: Path, command: str, revision: str) -> None:
    environment = os.environ.copy()
    environment["DATABASE_URL"] = sqlite_url(database_path)
    subprocess.run(
        [sys.executable, "-m", "alembic", command, revision],
        cwd=REPOSITORY_ROOT,
        env=environment,
        check=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--floor", default=DEFAULT_FLOOR)
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="agent-eval-roundtrip-") as tmp:
        database = Path(tmp) / "roundtrip.sqlite3"

        run_alembic(database, "upgrade", "head")
        run_alembic(database, "downgrade", args.floor)
        run_alembic(database, "upgrade", "head")

    print(f"Migration round-trip passed: head -> {args.floor} -> head with no errors.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

