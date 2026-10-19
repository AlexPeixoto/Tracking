"""Export the library to a .zip and import one back.

Export
    library.sqlite3 (a consistent snapshot, safe while the app runs), optionally
    the posters folder, and manifest.json describing what's inside. API keys and
    other service credentials are left out unless asked for, so an export is
    safe to share.

Import, two modes
    replace  the zip's library becomes yours (current items, settings and covers go).
    merge    the zip's items are added to yours. An item that exists in both
             (same drive + path) keeps your edits and notes; it counts as
             completed if either copy is; scores / covers / summary are taken
             from the zip only where yours has none. Settings (API keys) are
             added only when you don't have that setting yet.

Either way, credentials you already have are kept when the export has none.

Covers are named after item ids, which differ between libraries, so they are
renamed on merge. A database-only zip has no covers: imported items are marked
for "Update metadata" so their covers (and scores) are fetched again.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import __version__, db, paths, providers

FORMAT = 1
DB_NAME = "library.sqlite3"
MANIFEST = "manifest.json"


class ArchiveError(Exception):
    """Not a Tracking export, or it can't be read."""


@dataclass
class ImportResult:
    mode: str
    added: int = 0
    updated: int = 0
    covers: int = 0
    need_metadata: int = 0  # items marked for "Update metadata"

    def __str__(self) -> str:
        parts = [f"{self.added} items added"]
        if self.mode == "merge":
            parts.append(f"{self.updated} existing items updated")
        parts.append(f"{self.covers} covers")
        if self.need_metadata:
            parts.append(f"{self.need_metadata} items need “Update metadata” for covers")
        return ", ".join(parts)


# --------------------------------------------------------------------------- export

def _snapshot(dest: Path) -> None:
    """Copy the live database with SQLite's backup API (consistent even mid-write)."""
    src = sqlite3.connect(paths.db_path())
    dst = sqlite3.connect(dest)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def export_zip(zip_path: Path, images: bool = True, credentials: bool = False) -> int:
    """Write the export; returns the number of files in it (manifest not counted)."""
    with tempfile.TemporaryDirectory() as tmp:
        snapshot = Path(tmp) / DB_NAME
        _snapshot(snapshot)
        if not credentials:
            snap = sqlite3.connect(snapshot)
            keys = sorted(providers.credential_keys())
            snap.execute(f"DELETE FROM settings WHERE key IN ({','.join('?' * len(keys))})", keys)
            snap.commit()
            snap.execute("VACUUM")  # really drop the deleted values from the file
            snap.close()
        covers = sorted(f for f in paths.posters_dir().iterdir() if f.is_file()) if images else []
        manifest = {
            "format": FORMAT, "app_version": __version__, "images": images,
            "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "covers": len(covers), "credentials": credentials,
        }
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr(MANIFEST, json.dumps(manifest, indent=2))
            z.write(snapshot, DB_NAME)
            for f in covers:
                # Images are already compressed; storing them is faster.
                z.write(f, f"posters/{f.name}", compress_type=zipfile.ZIP_STORED)
    return 1 + len(covers)


# --------------------------------------------------------------------------- import

def read_manifest(zip_path: Path) -> dict:
    try:
        with zipfile.ZipFile(zip_path) as z:
            names = set(z.namelist())
            if DB_NAME not in names:
                raise ArchiveError("This zip has no library.sqlite3; it isn't a Tracking export.")
            if MANIFEST in names:
                return json.loads(z.read(MANIFEST))
            # Backups made before manifests existed always included covers.
            return {"format": FORMAT, "images": any(n.startswith("posters/") for n in names)}
    except zipfile.BadZipFile as e:
        raise ArchiveError(f"Not a valid zip file: {e}") from e


def import_zip(zip_path: Path, mode: str) -> ImportResult:
    """``mode`` is "replace" or "merge". Don't run while a scan / fetch is running."""
    if mode not in ("replace", "merge"):
        raise ValueError(mode)
    manifest = read_manifest(zip_path)
    if manifest.get("format", FORMAT) > FORMAT:
        raise ArchiveError("This export comes from a newer version of Tracking.")
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        with zipfile.ZipFile(zip_path) as z:
            z.extract(DB_NAME, tmp)
            covers_dir = tmp / "posters"
            for n in z.namelist():
                if n.startswith("posters/") and not n.endswith("/") and "/" not in n[8:]:
                    z.extract(n, tmp)
        # Bring an older export's schema up to date before reading it.
        src = db.connect(tmp / DB_NAME)
        try:
            if mode == "replace":
                return _replace(src, covers_dir)
            return _merge(src, covers_dir)
        finally:
            src.close()


def _replace(src: sqlite3.Connection, covers_dir: Path) -> ImportResult:
    with db.session() as conn:
        keys = providers.credential_keys()
        mine = [(r["key"], r["value"]) for r in conn.execute("SELECT key, value FROM settings")
                if r["key"] in keys and r["value"]]
    live = sqlite3.connect(paths.db_path(), timeout=30)
    try:
        src.backup(live)
    finally:
        live.close()
    with db.session() as conn:
        # An export without credentials shouldn't wipe yours.
        conn.executemany("INSERT INTO settings(key, value) VALUES(?, ?) "
                         "ON CONFLICT(key) DO UPDATE SET value=excluded.value "
                         "WHERE settings.value IS NULL OR settings.value = ''", mine)
    posters = paths.posters_dir()
    for f in posters.iterdir():
        if f.is_file():
            f.unlink()
    result = ImportResult("replace")
    if covers_dir.is_dir():
        for f in covers_dir.iterdir():
            shutil.copy2(f, posters / f.name)
            result.covers += 1
    with db.session() as conn:
        result.added = conn.execute("SELECT COUNT(*) n FROM items").fetchone()["n"]
        # Items whose cover file didn't come along: fetch it again.
        for r in conn.execute("SELECT id, poster FROM items WHERE poster IS NOT NULL").fetchall():
            if not (posters / r["poster"]).exists():
                conn.execute("UPDATE items SET poster=NULL, enriched_at=NULL WHERE id=?", (r["id"],))
                result.need_metadata += 1
    return result


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [r["name"] for r in conn.execute(f"PRAGMA table_info({table})")]


# Your own data on an item: never overwritten by a merge when you've set it.
_USER_FIELDS = ("user_title", "user_category", "user_platform", "notes")
# Looked-up data: taken from the import only when yours is empty.
_METADATA_FIELDS = ("rating", "rating_max", "rating_source", "external_id", "external_url",
                    "summary", "hltb_hours", "hltb_extra", "hltb_complete", "duration_sec",
                    "enriched_at")


def _merge(src: sqlite3.Connection, covers_dir: Path) -> ImportResult:
    result = ImportResult("merge")
    posters = paths.posters_dir()
    with db.session() as dst:
        for r in src.execute("SELECT * FROM volumes"):
            dst.execute("INSERT OR IGNORE INTO volumes(uid, serial, label, last_mount) VALUES(?,?,?,?)",
                        (r["uid"], r["serial"], r["label"], r["last_mount"]))
        root_map: dict[int, int] = {}
        for r in src.execute("SELECT * FROM roots"):
            dst.execute("INSERT OR IGNORE INTO roots(volume_uid, rel_path, category, platform) "
                        "VALUES(?,?,?,?)", (r["volume_uid"], r["rel_path"], r["category"], r["platform"]))
            root_map[r["id"]] = dst.execute(
                "SELECT id FROM roots WHERE volume_uid=? AND rel_path=?",
                (r["volume_uid"], r["rel_path"])).fetchone()["id"]

        cols = [c for c in _columns(src, "items") if c != "id" and c in _columns(dst, "items")]
        for r in src.execute("SELECT * FROM items").fetchall():
            mine = dst.execute("SELECT * FROM items WHERE volume_uid=? AND rel_path=?",
                               (r["volume_uid"], r["rel_path"])).fetchone()
            if mine:
                _merge_item(dst, src, mine, r, covers_dir, posters, result)
                continue
            values = {c: r[c] for c in cols}
            values["root_id"] = root_map.get(r["root_id"])
            values["poster"] = None
            new_id = dst.execute(
                f"INSERT INTO items({', '.join(values)}) VALUES({', '.join('?' * len(values))})",
                list(values.values())).lastrowid
            _copy_children(dst, src, r["id"], new_id)
            if not _copy_cover(dst, r, new_id, covers_dir, posters, result):
                dst.execute("UPDATE items SET enriched_at=NULL WHERE id=?", (new_id,))
                result.need_metadata += 1
            result.added += 1

        for r in src.execute("SELECT * FROM settings"):
            dst.execute("INSERT OR IGNORE INTO settings(key, value) VALUES(?,?)", (r["key"], r["value"]))
    return result


def _merge_item(dst, src, mine, theirs, covers_dir, posters, result) -> None:
    updates: dict = {}
    for f in _USER_FIELDS:
        if mine[f] is None and theirs[f] is not None:
            updates[f] = theirs[f]
    if theirs["completed"] and not mine["completed"]:
        updates["completed"] = 1
        updates["completed_at"] = theirs["completed_at"]
    if mine["enriched_at"] is None and theirs["enriched_at"] is not None:
        for f in _METADATA_FIELDS:
            updates[f] = theirs[f]
        dst.execute("DELETE FROM scores WHERE item_id=?", (mine["id"],))
        for s in src.execute("SELECT * FROM scores WHERE item_id=?", (theirs["id"],)):
            dst.execute("INSERT INTO scores(item_id, source, value, max_value, url) VALUES(?,?,?,?,?)",
                        (mine["id"], s["source"], s["value"], s["max_value"], s["url"]))
    if not mine["poster"]:
        _copy_cover(dst, theirs, mine["id"], covers_dir, posters, result)
    if updates:
        dst.execute(f"UPDATE items SET {', '.join(f'{k}=?' for k in updates)} WHERE id=?",
                    [*updates.values(), mine["id"]])
        result.updated += 1


def _copy_children(dst, src, old_id: int, new_id: int) -> None:
    for s in src.execute("SELECT * FROM scores WHERE item_id=?", (old_id,)):
        dst.execute("INSERT OR REPLACE INTO scores(item_id, source, value, max_value, url) "
                    "VALUES(?,?,?,?,?)", (new_id, s["source"], s["value"], s["max_value"], s["url"]))
    for m in src.execute("SELECT * FROM media_files WHERE item_id=?", (old_id,)):
        dst.execute("INSERT OR REPLACE INTO media_files(item_id, rel_path, size_bytes, mtime, "
                    "duration_sec) VALUES(?,?,?,?,?)",
                    (new_id, m["rel_path"], m["size_bytes"], m["mtime"], m["duration_sec"]))


def _copy_cover(dst, item_row, new_id: int, covers_dir: Path, posters: Path, result) -> bool:
    """Copy the imported item's cover under its new id. False if the zip has none."""
    name = item_row["poster"]
    if not name or not (covers_dir / name).is_file():
        return False
    new_name = f"{new_id}{Path(name).suffix}"
    shutil.copy2(covers_dir / name, posters / new_name)
    dst.execute("UPDATE items SET poster=? WHERE id=?", (new_name, new_id))
    result.covers += 1
    return True
