"""Replay: what would the scanner make of your library *now*? A safety net for rule changes.

Every location's saved snapshot (scan/fs.py) is re-processed with the current rules,
read-only (the library isn't touched), and each item it would produce is written as one
line: location, category, platform, kind, season, files, title, path. Save that once as a
baseline, change a rule, run it again: the diff is exactly what the change does to your
real library - nothing else should move.

    python -m tracking.scan.replay --save     # record the baseline
    python -m tracking.scan.replay            # compare with it (exit code 1 if different)

The baseline lives in the data folder (``replay-baseline.tsv``), never in the repository:
it lists your own files.
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from collections.abc import Iterator
from pathlib import Path

from .. import paths
from . import fs
from .merge import collect
from .store import ACCOUNT_PREFIX, root_hints, snapshot_disk

BASELINE = "replay-baseline.tsv"


def _readonly(db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def lines(conn: sqlite3.Connection) -> Iterator[str]:
    """One tab-separated line per item the current rules find in the saved snapshots."""
    roots = conn.execute(
        "SELECT r.*, COALESCE(v.name, v.label) AS label FROM roots r "
        "JOIN volumes v ON v.uid=r.volume_uid ORDER BY r.id").fetchall()
    for root in roots:
        if root["volume_uid"].startswith(ACCOUNT_PREFIX):
            continue  # an online account: nothing on disk to replay
        disk = snapshot_disk(conn, root)
        if disk is None:
            continue
        where = f"{root['label'] or '(no label)'}/{root['rel_path']}"
        with fs.using(disk):
            found = collect(disk.root, root["rel_path"],
                            root_hints(root["category"], root["platform"], root["rel_path"]))
        for f in sorted(found, key=lambda f: f.rel_path):
            yield "\t".join(str(x) for x in (where, f.category, f.platform or "", f.kind or "",
                                             f.season or "", len(f.videos) or f.file_count,
                                             f.title, f.rel_path))


def diff(old: list[str], new: list[str]) -> tuple[list[str], list[str]]:
    """(lines only in the baseline, lines only in the new replay)."""
    a, b = set(old), set(new)
    return sorted(a - b), sorted(b - a)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m tracking.scan.replay", description=__doc__.split("\n")[0])
    ap.add_argument("--save", action="store_true", help="record the result as the new baseline")
    ap.add_argument("--db", type=Path, default=None, help="library file (default: your library)")
    ap.add_argument("--baseline", type=Path, default=None,
                    help=f"baseline file (default: data folder/{BASELINE})")
    args = ap.parse_args(argv)
    logging.disable(logging.WARNING)

    db = args.db or paths.db_path()
    if not db.exists():
        print(f"No library at {db}: nothing to replay.")
        return 0
    base = args.baseline or db.parent / BASELINE
    with _readonly(db) as conn:
        now = list(lines(conn))
    if args.save or not base.exists():
        base.write_text("\n".join(now) + "\n", encoding="utf-8")
        print(f"Baseline saved: {len(now)} items -> {base}")
        return 0
    old = base.read_text(encoding="utf-8").splitlines()
    gone, added = diff(old, now)
    for line in gone:
        print("-", line)
    for line in added:
        print("+", line)
    print(f"{len(now)} items; {len(gone)} lines gone, {len(added)} new "
          + ("(same as the baseline)" if not (gone or added) else f"(baseline: {base})"))
    return 1 if gone or added else 0


if __name__ == "__main__":
    sys.exit(main())
