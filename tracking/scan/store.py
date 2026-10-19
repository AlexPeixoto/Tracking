"""Saving scan results: locations, items, their files, and what went missing."""

from __future__ import annotations

import logging
import os
import sqlite3
from datetime import datetime, timezone
from typing import Callable

from .. import db, volumes
from .merge import collect
from .model import FoundItem, ScanResult, VolumeOffline, fs_path
from .walk import root_hints

log = logging.getLogger(__name__)


def add_root(conn: sqlite3.Connection, path: str, category: str = "auto",
             platform: str | None = None) -> int:
    loc = volumes.identify(path)
    v = loc.volume
    conn.execute(
        "INSERT INTO volumes(uid, serial, label, last_mount) VALUES(?,?,?,?) "
        "ON CONFLICT(uid) DO UPDATE SET serial=excluded.serial, label=excluded.label, "
        "last_mount=excluded.last_mount",
        (v.uid, v.serial, v.label, v.mount),
    )
    conn.execute(
        "INSERT INTO roots(volume_uid, rel_path, category, platform) VALUES(?,?,?,?) "
        "ON CONFLICT(volume_uid, rel_path) DO UPDATE SET category=excluded.category, "
        "platform=excluded.platform",
        (v.uid, loc.rel_path, category, platform),
    )
    return conn.execute(
        "SELECT id FROM roots WHERE volume_uid=? AND rel_path=?", (v.uid, loc.rel_path)
    ).fetchone()["id"]


def mount_for(conn: sqlite3.Connection, volume_uid: str,
              known: list[volumes.Volume] | None = None) -> str | None:
    row = conn.execute("SELECT serial FROM volumes WHERE uid=?", (volume_uid,)).fetchone()
    vol = volumes.resolve(volume_uid, row["serial"] if row else None, known)
    if vol:
        conn.execute("UPDATE volumes SET last_mount=? WHERE uid=?", (vol.mount, volume_uid))
        return vol.mount
    return None


def scan_root(conn: sqlite3.Connection, root_id: int,
              progress: Callable[[str], None] | None = None,
              cancelled: Callable[[], bool] = lambda: False,
              missing_completed: bool = False) -> ScanResult:
    """Scan one location. Items no longer on disk are flagged ``missing`` (never
    deleted); with ``missing_completed`` they are also marked as completed, on the
    idea that you delete things once you've watched / finished them."""
    root = conn.execute("SELECT * FROM roots WHERE id=?", (root_id,)).fetchone()
    mount = mount_for(conn, root["volume_uid"])
    if not mount:
        raise VolumeOffline(root["volume_uid"])
    root_abs = volumes.join(mount, root["rel_path"])
    hints = root_hints(root["category"], root["platform"], root["rel_path"])
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    log.info("scanning %s (category=%s, hints=%s)", root_abs, root["category"], hints)
    result = ScanResult()
    seen: list[str] = []
    found_items = collect(root_abs, root["rel_path"], hints, progress=progress, cancelled=cancelled)
    found_paths = {f.rel_path for f in found_items}
    for found in found_items:
        result.found += 1
        seen.append(found.rel_path)
        result.new += _upsert(conn, root_id, root["volume_uid"], found, now, found_paths)
        conn.commit()

    if not cancelled():
        prev = {r["rel_path"]: r["id"] for r in conn.execute(
            "SELECT id, rel_path FROM items WHERE root_id=?", (root_id,))}
        gone = [prev[g] for g in set(prev) - set(seen)]
        result.missing = len(gone)
        conn.executemany("UPDATE items SET missing=1 WHERE id=?", [(i,) for i in gone])
        if missing_completed:
            db.set_completed(conn, gone, True)
        conn.commit()
    log.info("scan of %s done: %d items, %d new, %d missing%s", root_abs, result.found,
             result.new, result.missing, " (marked completed)" if missing_completed and gone else "")
    return result


def _upsert(conn: sqlite3.Connection, root_id: int, volume_uid: str, f: FoundItem, now: str,
            found_paths: set[str] = frozenset()) -> int:
    row = conn.execute(
        "SELECT id FROM items WHERE volume_uid=? AND rel_path=?", (volume_uid, f.rel_path)
    ).fetchone()
    if not row:
        # Moved to another drive / folder? Re-link the old entry so its completed
        # state, notes and edits follow it instead of creating a duplicate.
        # Only entries we know are gone: flagged missing, or on a connected drive
        # where the old path no longer exists (an unplugged drive may still have it).
        row = None
        for cand in conn.execute(
            "SELECT id, volume_uid, rel_path, missing FROM items WHERE category=? AND title=? "
            "AND COALESCE(platform, '')=COALESCE(?, '') AND user_category IS NULL",
            (f.category, f.title, f.platform),
        ).fetchall():
            mount = None if cand["missing"] else mount_for(conn, cand["volume_uid"])
            gone = mount and not os.path.exists(volumes.join(mount, fs_path(cand["rel_path"])))
            # A season split out of a folder keeps its entry when the layout changes.
            regrouped = (cand["volume_uid"] == volume_uid and cand["rel_path"] not in found_paths
                         and fs_path(cand["rel_path"]) == fs_path(f.rel_path))
            if cand["missing"] or gone or regrouped:
                row = cand
                break
        if row:
            log.info("re-linked %r: %s -> %s", f.title, row["rel_path"], f.rel_path)
            conn.execute("UPDATE items SET volume_uid=?, rel_path=? WHERE id=?",
                         (volume_uid, f.rel_path, row["id"]))
    if row:
        item_id = row["id"]
        conn.execute(
            "UPDATE items SET root_id=?, is_dir=?, category=?, platform=?, title=?, year=?, "
            "file_count=?, size_bytes=?, kind=?, season=?, detected_how=?, missing=0, scanned_at=? "
            "WHERE id=?",
            (root_id, f.is_dir, f.category, f.platform, f.title, f.year,
             f.file_count, f.size_bytes, f.kind, f.season, f.reason, now, item_id),
        )
        is_new = 0
    else:
        item_id = conn.execute(
            "INSERT INTO items(root_id, volume_uid, rel_path, is_dir, category, platform, title, "
            "year, file_count, size_bytes, kind, season, detected_how, scanned_at, added_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (root_id, volume_uid, f.rel_path, f.is_dir, f.category, f.platform, f.title,
             f.year, f.file_count, f.size_bytes, f.kind, f.season, f.reason, now, now),
        ).lastrowid
        is_new = 1

    # Keep measured durations for files that haven't changed.
    old = {r["rel_path"]: r for r in conn.execute(
        "SELECT rel_path, size_bytes, mtime, duration_sec FROM media_files WHERE item_id=?",
        (item_id,))}
    conn.execute("DELETE FROM media_files WHERE item_id=?", (item_id,))
    for v in f.videos:
        prev = old.get(v.rel_path)
        dur = prev["duration_sec"] if prev and prev["size_bytes"] == v.size and prev["mtime"] == v.mtime else None
        conn.execute(
            "INSERT INTO media_files(item_id, rel_path, size_bytes, mtime, duration_sec, season, "
            "episode) VALUES(?,?,?,?,?,?,?)",
            (item_id, v.rel_path, v.size, v.mtime, dur, v.season, v.episode),
        )
    return is_new
