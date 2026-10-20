"""Copy / move an item to another folder or drive (e.g. from an external disk to the
internal SSD before playing), keeping the library in step.

  * move: the files go, the item follows (completion, notes, launch settings, history)
  * copy: a second item for the new copy, linked to the first (copy_of), so Library
    health doesn't flag the pair as duplicates

Files are verified (sizes) before a move deletes anything, and every transfer is noted in
the item's history.
"""

from __future__ import annotations

import logging
import os
import shutil
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from . import db, paths, volumes
from .scan.model import GROUP_SEP, fs_path

log = logging.getLogger(__name__)
CHUNK = 8 * 1024 * 1024


class TransferError(Exception):
    """Why a copy / move can't happen (shown to the user)."""


@dataclass
class Plan:
    item_id: int
    title: str
    sources: list[tuple[str, str]]   # (absolute source, path relative to the item's base)
    src_base: str                    # the item's folder, or the folder of a loose file
    dest: str                        # where the item ends up (folder or file path)
    size: int
    move: bool
    is_dir: bool = True

    @property
    def files_to(self) -> str:
        """The folder the files are written into (the new item folder, or a loose file's folder)."""
        return self.dest if self.is_dir else os.path.dirname(self.dest)


def plan(conn: sqlite3.Connection, item: db.Item, mount: str | None, dest_dir: str,
         move: bool) -> Plan:
    if item.source not in ("disk",) or item.volume_uid.startswith("steam:"):
        raise TransferError(f"{item.title} has no files to copy.")
    if GROUP_SEP in item.rel_path:
        raise TransferError(f"{item.title} is one part of a bigger folder "
                            f"({fs_path(item.rel_path).rsplit('/', 1)[-1]}). Move that folder instead.")
    if not mount:
        raise TransferError(f"{item.title} is on a drive that isn't connected.")
    src = volumes.join(mount, item.rel_path)
    if not os.path.exists(src):
        raise TransferError(f"{src} isn't there anymore. Rescan first.")
    # Never a whole drive or a whole location: a move deletes what it copied.
    is_root = conn.execute("SELECT 1 FROM roots WHERE volume_uid=? AND rel_path=?",
                           (item.volume_uid, item.rel_path)).fetchone()
    if not item.rel_path.strip("/") or is_root:
        raise TransferError(f"{src} is a whole drive or location, not one item.")
    # Other items inside this folder (e.g. stray ROMs found in a game folder) would lose
    # their files without their entries knowing.
    inside = conn.execute(
        "SELECT COUNT(*) FROM items WHERE volume_uid=? AND id != ? AND substr(rel_path, 1, ?) = ?",
        (item.volume_uid, item.id, len(item.rel_path) + 1, item.rel_path + "/")).fetchone()[0]
    if item.is_dir and inside:
        raise TransferError(f"{item.title}'s folder also holds {inside} other item(s); move those "
                            "first, or move the folder outside Tracking and rescan.")
    a, b = os.path.normcase(os.path.abspath(dest_dir)), os.path.normcase(os.path.abspath(src))
    if os.path.commonpath([a, b]) == b:
        raise TransferError("The destination is inside the item itself.")
    if item.is_dir:
        sources = []
        for cur, _dirs, files in os.walk(src):
            for f in files:
                p = os.path.join(cur, f)
                sources.append((p, os.path.relpath(p, src)))
        base, dest = src, os.path.join(dest_dir, os.path.basename(src))
    else:
        rels = [r["rel_path"] for r in conn.execute(
            "SELECT rel_path FROM media_files WHERE item_id=?", (item.id,))] or [item.rel_path]
        if item.file_count > len(rels):
            raise TransferError(f"{item.title} is several loose files; put them in a folder first.")
        base = os.path.dirname(src)
        sources = [(volumes.join(mount, r), os.path.basename(r)) for r in rels]
        dest = os.path.join(dest_dir, os.path.basename(src))
    if os.path.exists(dest):
        raise TransferError(f"{dest} already exists.")
    size = sum(os.path.getsize(s) for s, _ in sources)
    free = shutil.disk_usage(dest_dir).free
    if size > free:
        raise TransferError(f"Not enough space: {size / 1e9:.1f} GB needed, {free / 1e9:.1f} GB free.")
    return Plan(item.id, item.title, sources, base, dest, size, move, item.is_dir)


def copy_files(p: Plan, progress: Callable[[str], None] = lambda s: None,
               cancelled: Callable[[], bool] = lambda: False) -> None:
    """Copy everything to the destination, verifying sizes; on failure / cancel the
    partial copy is removed and the originals are untouched."""
    done = 0
    made: list[str] = []
    try:
        for src, rel in p.sources:
            out = os.path.join(p.files_to, rel)
            os.makedirs(os.path.dirname(out), exist_ok=True)
            made.append(out)
            with open(src, "rb") as fi, open(out, "wb") as fo:
                while chunk := fi.read(CHUNK):
                    if cancelled():
                        raise TransferError("Cancelled.")
                    fo.write(chunk)
                    done += len(chunk)
                    progress(f"{'Moving' if p.move else 'Copying'} {p.title}: "
                             f"{100 * done // max(p.size, 1)}% of {p.size / 1e9:.1f} GB")
            shutil.copystat(src, out)
            if os.path.getsize(out) != os.path.getsize(src):
                raise TransferError(f"{out} came out the wrong size; nothing was deleted.")
    except BaseException:
        for f in made:
            try:
                os.remove(f)
            except OSError:
                pass
        if p.is_dir and os.path.isdir(p.dest):
            shutil.rmtree(p.dest, ignore_errors=True)
        raise


def remove_sources(p: Plan) -> None:
    """After a verified move: delete the originals (the whole folder for folder items)."""
    if p.is_dir:
        shutil.rmtree(p.src_base)
    else:
        for src, _ in p.sources:
            os.remove(src)


def apply(conn: sqlite3.Connection, p: Plan) -> int:
    """Point the library at the new place. Returns the id of the item now there."""
    loc = volumes.identify(p.dest)
    v = loc.volume
    conn.execute("INSERT INTO volumes(uid, serial, label, last_mount) VALUES(?,?,?,?) "
                 "ON CONFLICT(uid) DO UPDATE SET last_mount=excluded.last_mount",
                 (v.uid, v.serial, v.label, v.mount))
    old = conn.execute("SELECT * FROM items WHERE id=?", (p.item_id,)).fetchone()
    old_rel, new_rel = old["rel_path"], loc.rel_path
    old_where = describe(conn, old["volume_uid"], old_rel)
    new_where = describe(conn, v.uid, new_rel)
    # The innermost location the new place is in (exact prefix test: no LIKE wildcards).
    root = conn.execute(
        "SELECT id FROM roots WHERE volume_uid=? AND (rel_path='' OR ?=rel_path OR "
        "substr(?, 1, length(rel_path) + 1) = rel_path || '/') ORDER BY length(rel_path) DESC",
        (v.uid, new_rel, new_rel)).fetchone()

    def moved(path: str | None) -> str | None:
        if path and not os.path.isabs(path) and (path == old_rel or path.startswith(old_rel + "/")):
            return new_rel + path[len(old_rel):]
        if path and not os.path.isabs(path) and not old["is_dir"]:  # loose: same folder
            old_dir = old_rel.rsplit("/", 1)[0] if "/" in old_rel else ""
            new_dir = new_rel.rsplit("/", 1)[0] if "/" in new_rel else ""
            if path.startswith(old_dir):
                return (new_dir + path[len(old_dir):]).lstrip("/")
        return path

    if p.move:
        target = p.item_id
        conn.execute("UPDATE items SET volume_uid=?, rel_path=?, root_id=?, missing=0, launch_path=?, "
                     "user_launch=? WHERE id=?",
                     (v.uid, new_rel, root["id"] if root else None, moved(old["launch_path"]),
                      moved(old["user_launch"]), target))
        for m in conn.execute("SELECT rel_path FROM media_files WHERE item_id=?", (target,)).fetchall():
            conn.execute("UPDATE media_files SET rel_path=? WHERE item_id=? AND rel_path=?",
                         (moved(m["rel_path"]), target, m["rel_path"]))
        note(conn, target, "moved", f"Moved from {old_where} to {new_where}")
    else:
        skip = ("id", "volume_uid", "rel_path", "root_id", "poster", "copy_of", "launch_path",
                "user_launch")
        cols = [c for c in old.keys() if c not in skip]  # noqa: SIM118 - a Row iterates values
        target = conn.execute(
            f"INSERT INTO items(volume_uid, rel_path, root_id, copy_of, launch_path, user_launch, "
            f"{', '.join(cols)}) VALUES(?,?,?,?,?,?,{', '.join('?' * len(cols))})",
            [v.uid, new_rel, root["id"] if root else None, old["copy_of"] or p.item_id,
             moved(old["launch_path"]), moved(old["user_launch"]), *[old[c] for c in cols]]).lastrowid
        for s in conn.execute("SELECT * FROM scores WHERE item_id=?", (p.item_id,)).fetchall():
            conn.execute("INSERT INTO scores(item_id, source, value, max_value, url) VALUES(?,?,?,?,?)",
                         (target, s["source"], s["value"], s["max_value"], s["url"]))
        for m in conn.execute("SELECT * FROM media_files WHERE item_id=?", (p.item_id,)).fetchall():
            conn.execute("INSERT INTO media_files(item_id, rel_path, size_bytes, mtime, duration_sec, "
                         "season, episode) VALUES(?,?,?,?,?,?,?)",
                         (target, moved(m["rel_path"]), m["size_bytes"], m["mtime"], m["duration_sec"],
                          m["season"], m["episode"]))
        if old["poster"]:
            src = paths.posters_dir() / old["poster"]
            if src.exists():
                name = f"{target}{src.suffix}"
                shutil.copyfile(src, paths.posters_dir() / name)
                conn.execute("UPDATE items SET poster=? WHERE id=?", (name, target))
        note(conn, target, "copied", f"Copied from {old_where} to {new_where}")
        note(conn, p.item_id, "copied", f"Copied to {new_where}")
    log.info("%s %r: %s -> %s", "moved" if p.move else "copied", p.title, old_where, new_where)
    return target


def describe(conn: sqlite3.Connection, volume_uid: str, rel: str) -> str:
    r = conn.execute("SELECT label, last_mount FROM volumes WHERE uid=?", (volume_uid,)).fetchone()
    where = (r["label"] or r["last_mount"] or "drive") if r else "drive"
    return f"{where}:/{fs_path(rel)}"


def note(conn: sqlite3.Connection, item_id: int, action: str, detail: str) -> None:
    conn.execute("INSERT INTO item_history(item_id, at, action, detail) VALUES(?,?,?,?)",
                 (item_id, datetime.now().isoformat(timespec="seconds"), action, detail))


def history(conn: sqlite3.Connection, item_id: int) -> list[sqlite3.Row]:
    return conn.execute("SELECT at, action, detail FROM item_history WHERE item_id=? "
                        "ORDER BY at DESC, rowid DESC",
                        (item_id,)).fetchall()
