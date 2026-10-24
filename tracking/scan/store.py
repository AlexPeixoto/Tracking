"""Saving scan results: locations, items, their files, and what went missing."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime

from .. import db, paths, titles, volumes
from . import classify, fs
from .merge import collect
from .model import FoundItem, ScanResult, VolumeOffline, fs_path
from .videos import is_short_extra
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


ACCOUNT_PREFIX = "steam:"  # volume uid of an online account (sources/steam.py)


def mount_for(conn: sqlite3.Connection, volume_uid: str,
              known: list[volumes.Volume] | None = None) -> str | None:
    """Where the volume is mounted now, or None if it isn't connected. An online
    account is always reachable; its "mount" is its uid."""
    if volume_uid.startswith(ACCOUNT_PREFIX):
        return volume_uid
    row = conn.execute("SELECT serial FROM volumes WHERE uid=?", (volume_uid,)).fetchone()
    vol = volumes.resolve(volume_uid, row["serial"] if row else None, known)
    if vol:
        conn.execute("UPDATE volumes SET last_mount=? WHERE uid=?", (vol.mount, volume_uid))
        return vol.mount
    return None


class NoSnapshot(Exception):
    """The location was never scanned with this version, so there's nothing to replay."""


def scan_root(conn: sqlite3.Connection, root_id: int,
              progress: Callable[[str], None] | None = None,
              cancelled: Callable[[], bool] = lambda: False,
              missing_completed: bool = False, from_snapshot: bool = False) -> ScanResult:
    """Scan one location. Items no longer on disk are flagged ``missing`` (never
    deleted); with ``missing_completed`` they are also marked as completed, on the
    idea that you delete things once you've watched / finished them.

    A real scan records the location's snapshot; ``from_snapshot`` re-processes that
    record instead of the drive (new detection rules, drive not needed)."""
    root = conn.execute("SELECT * FROM roots WHERE id=?", (root_id,)).fetchone()
    if root["volume_uid"].startswith(ACCOUNT_PREFIX):
        from ..sources import steam
        return steam.sync(conn, root_id, progress=progress, cancelled=cancelled)
    if from_snapshot:
        # Where the drive was last seen (only used to name paths; nothing is read there).
        r = conn.execute("SELECT last_mount FROM volumes WHERE uid=?", (root["volume_uid"],)).fetchone()
        mount = (r and r["last_mount"]) or f"\\\\snapshot\\{root['volume_uid'][:8]}\\"
        root_abs = volumes.join(mount, root["rel_path"])
        disk = fs.load(conn, root_id, root_abs)
        if disk is None:
            raise NoSnapshot(root_id)
        disk.volume_uid, disk.mount = root["volume_uid"], mount
    else:
        mount = mount_for(conn, root["volume_uid"])
        if not mount:
            raise VolumeOffline(root["volume_uid"])
        root_abs = volumes.join(mount, root["rel_path"])
        disk = fs.RecordingFS(root_abs)
    hints = root_hints(root["category"], root["platform"], root["rel_path"])
    now = datetime.now(UTC).isoformat(timespec="seconds")

    log.info("%s %s (category=%s, hints=%s)", "re-processing snapshot of" if from_snapshot
             else "scanning", root_abs, root["category"], hints)
    result = ScanResult()
    seen: list[str] = []
    with fs.using(disk):
        found_items = collect(root_abs, root["rel_path"], hints, progress=progress, cancelled=cancelled)
        found_paths = {f.rel_path for f in found_items}
        for found in found_items:
            result.found += 1
            seen.append(found.rel_path)
            result.new += _upsert(conn, root_id, root["volume_uid"], found, now, found_paths)
            conn.commit()

    if not cancelled() and not from_snapshot:
        if progress:
            progress("Saving the folder tree")
        extra = disk.complete(classify.is_skipped, cancelled)
        log.debug("snapshot of %s: %d folders the scan skipped added", root_abs, extra)
        files = fs.save(conn, root_id, disk, now)
        log.info("snapshot of %s: %d files", root_abs, files)
        conn.commit()

    if not cancelled():
        prev = {r["rel_path"]: r["id"] for r in conn.execute(
            "SELECT id, rel_path FROM items WHERE root_id=?", (root_id,))}
        gone = [prev[g] for g in set(prev) - set(seen)]
        with fs.using(disk):
            gone = _drop_regrouped(conn, gone, seen)
        result.missing = len(gone)
        conn.executemany("UPDATE items SET missing=1 WHERE id=?", [(i,) for i in gone])
        if missing_completed:
            db.set_completed(conn, gone, True)
        conn.commit()
    log.info("scan of %s done: %d items, %d new, %d missing%s", root_abs, result.found,
             result.new, result.missing, " (marked completed)" if missing_completed and gone else "")
    return result


def _drop_regrouped(conn: sqlite3.Connection, gone: list[int], seen: list[str]) -> list[int]:
    """Items that vanished only because the same files are now grouped differently
    (an older scan split "Show/01.mkv, 02.mkv" into items "01" and "02"; now they're
    one "Show") are deleted instead of lingering as "missing" - unless they carry
    something of the user's (completed, notes, edits). Returns the truly gone ids."""
    seen_dirs = {fs_path(p) for p in seen}
    seen_dirs |= {d.rsplit("/", 1)[0] for d in seen_dirs if "/" in d}
    keep = []
    for item_id in gone:
        r = conn.execute("SELECT rel_path, poster, completed, notes, user_title, user_category, "
                         "user_platform FROM items WHERE id=?", (item_id,)).fetchone()
        folder = fs_path(r["rel_path"])
        regrouped = folder in seen_dirs or folder.rsplit("/", 1)[0] in seen_dirs
        mine = r["completed"] or r["notes"] or r["user_title"] or r["user_category"] or r["user_platform"]
        if regrouped and not mine and _files_exist(conn, item_id):
            log.info("dropping %s: its files now belong to another item", r["rel_path"])
            if r["poster"]:
                (paths.posters_dir() / r["poster"]).unlink(missing_ok=True)
            conn.execute("DELETE FROM items WHERE id=?", (item_id,))
        else:
            keep.append(item_id)
    return keep


def _files_exist(conn: sqlite3.Connection, item_id: int) -> bool:
    """Its files are still on disk (so it was regrouped, not deleted)."""
    r = conn.execute("SELECT i.volume_uid, i.rel_path FROM items i WHERE i.id=?", (item_id,)).fetchone()
    mount = _mount(conn, r["volume_uid"])
    return bool(mount) and fs.current().exists(volumes.join(mount, fs_path(r["rel_path"])))


def _mount(conn: sqlite3.Connection, volume_uid: str) -> str | None:
    """Where a drive is: the snapshot's own drive counts as there while it's replayed."""
    disk = fs.current()
    if isinstance(disk, fs.SnapshotFS) and getattr(disk, "volume_uid", None) == volume_uid:
        return disk.mount
    return mount_for(conn, volume_uid)


def _claim_wanted(conn: sqlite3.Connection, volume_uid: str, f: FoundItem):
    """A newly found item that's on the "Want to acquire" list (or was marked "Got it")
    takes over that entry - notes, edits and fetched metadata included - instead
    of appearing twice. Matched on section + title; the platform isn't required to
    match, since a wish-list entry usually has none."""
    key = titles.normalize(f.title)
    for cand in conn.execute(
            "SELECT id, COALESCE(user_title, title) t FROM items WHERE source IN (?, 'manual') "
            "AND COALESCE(user_category, category)=?", (db.WANTED, f.category)).fetchall():
        if titles.normalize(cand["t"]) == key:
            log.info("found %r on disk: moved from the Want to acquire list", f.title)
            conn.execute("UPDATE items SET volume_uid=?, rel_path=?, source='disk' WHERE id=?",
                         (volume_uid, f.rel_path, cand["id"]))
            return cand
    return None


def _upsert(conn: sqlite3.Connection, root_id: int, volume_uid: str, f: FoundItem, now: str,
            found_paths: set[str] = frozenset()) -> int:
    row = conn.execute(
        "SELECT id FROM items WHERE volume_uid=? AND rel_path=?", (volume_uid, f.rel_path)
    ).fetchone()
    if not row:
        row = _claim_wanted(conn, volume_uid, f)
    if not row:
        # Moved to another drive / folder? Re-link the old entry so its completed
        # state, notes and edits follow it instead of creating a duplicate.
        # Only entries we know are gone: flagged missing, or on a connected drive
        # where the old path no longer exists (an unplugged drive may still have it).
        row = None
        for cand in conn.execute(
            "SELECT id, volume_uid, rel_path, missing FROM items WHERE category=? AND title=? "
            "AND COALESCE(platform, '')=COALESCE(?, '') AND user_category IS NULL "
            "AND volume_uid NOT LIKE 'steam:%'",
            (f.category, f.title, f.platform),
        ).fetchall():
            mount = None if cand["missing"] else _mount(conn, cand["volume_uid"])
            gone = mount and not fs.current().exists(volumes.join(mount, fs_path(cand["rel_path"])))
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
        old = conn.execute("SELECT title, category, platform, season, kind, user_title, user_category "
                           "FROM items WHERE id=?", (item_id,)).fetchone()
        # The scanner now reads it differently (a parsing fix, a renamed folder): what was
        # looked up was for the old name, so fetch again - unless the user's own title /
        # category override decides what we search for anyway.
        redetected = (old["title"], old["category"], old["platform"], old["season"], old["kind"]) != (
            f.title, f.category, f.platform, f.season, f.kind)
        if redetected and not (old["user_title"] or old["user_category"]):
            log.info("re-detected %r as %r (%s): metadata will be fetched again",
                     old["title"], f.title, f.category)
            conn.execute("UPDATE items SET enriched_at=NULL, poster=NULL, rating=NULL, "
                         "rating_max=NULL, rating_source=NULL, summary=NULL, external_url=NULL "
                         "WHERE id=?", (item_id,))
            conn.execute("DELETE FROM scores WHERE item_id=?", (item_id,))
            conn.execute("DELETE FROM episodes WHERE item_id=?", (item_id,))
        conn.execute(
            # year: keep the one a lookup found when the name has none (games rarely do)
            "UPDATE items SET root_id=?, is_dir=?, category=?, platform=?, title=?, "
            "year=COALESCE(?, year), "
            "file_count=?, size_bytes=?, kind=?, season=?, detected_how=?, missing=0, scanned_at=?, "
            "install_state=?, launch_path=? WHERE id=?",
            (root_id, f.is_dir, f.category, f.platform, f.title, f.year,
             f.file_count, f.size_bytes, f.kind, f.season, f.reason, now,
             f.install_state, f.launch_path, item_id),
        )
        is_new = 0
    else:
        item_id = conn.execute(
            "INSERT INTO items(root_id, volume_uid, rel_path, is_dir, category, platform, title, "
            "year, file_count, size_bytes, kind, season, detected_how, scanned_at, added_at, "
            "install_state, launch_path) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (root_id, volume_uid, f.rel_path, f.is_dir, f.category, f.platform, f.title,
             f.year, f.file_count, f.size_bytes, f.kind, f.season, f.reason, now, now,
             f.install_state, f.launch_path),
        ).lastrowid
        is_new = 1

    # Keep measured durations for files that haven't changed.
    old = {r["rel_path"]: r for r in conn.execute(
        "SELECT rel_path, size_bytes, mtime, duration_sec FROM media_files WHERE item_id=?",
        (item_id,))}
    conn.execute("DELETE FROM media_files WHERE item_id=?", (item_id,))
    for v in f.videos:
        prev = old.get(v.rel_path)
        unchanged = prev and prev["size_bytes"] == v.size and prev["mtime"] == v.mtime
        dur = prev["duration_sec"] if unchanged else None
        if is_short_extra(v.rel_path, dur):
            continue  # measured before: an opening / ending, not an episode
        conn.execute(
            "INSERT INTO media_files(item_id, rel_path, size_bytes, mtime, duration_sec, season, "
            "episode) VALUES(?,?,?,?,?,?,?)",
            (item_id, v.rel_path, v.size, v.mtime, dur, v.season, v.episode),
        )
    return is_new
