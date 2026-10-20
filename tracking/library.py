"""Changes the user makes to the library (as opposed to what scans and lookups
write). The UI calls these instead of writing SQL itself, so they're testable
without Qt and every rule lives in one place."""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass

from . import categories, db, paths



def set_completed(conn: sqlite3.Connection, item_ids: list[int], done: bool) -> None:
    """You marked items (not) completed: also logged in the Completed section, dated today.
    (Scans marking missing items completed use db.set_completed: no date to log.)"""
    from . import completions

    db.set_completed(conn, item_ids, done)
    completions.on_items_completed(conn, item_ids, done)


def edit_item(conn: sqlite3.Connection, item: db.Item, *, title: str, category: str,
              platform: str | None, year: int | None, notes: str | None) -> bool:
    """Save edits from the item page. Returns True when what we search for changed
    (title, category, platform or year), meaning metadata should be fetched again.

    The title is stored as an override only when it differs from what the scanner
    detected, so a later rename on disk still shows through for unedited items.
    """
    title = title.strip()
    platform = platform if categories.get(category).platforms else None
    conn.execute(
        "UPDATE items SET user_title=?, user_category=?, user_platform=?, year=?, notes=? WHERE id=?",
        (title if title and title != item.detected_title else None, category, platform, year,
         (notes or "").strip() or None, item.id),
    )
    return (title or item.detected_title, category, platform, year) != (
        item.title, item.category, item.platform, item.year)


def use_match(conn: sqlite3.Connection, item: db.Item, title: str, year: int | None = None,
              steam_appid: int | None = None) -> None:
    """Find match: look the item up as ``title`` from now on (and as that Steam app),
    dropping what was fetched for the old name. Your notes / completion stay."""
    conn.execute(
        "UPDATE items SET user_title=?, year=COALESCE(?, year), steam_appid=COALESCE(?, steam_appid), "
        "enriched_at=NULL, poster=NULL, rating=NULL, rating_max=NULL, rating_source=NULL, "
        "summary=NULL, external_url=NULL, external_id=NULL WHERE id=?",
        (title if title != item.detected_title else None, year, steam_appid, item.id))
    conn.execute("DELETE FROM scores WHERE item_id=?", (item.id,))
    conn.execute("DELETE FROM episodes WHERE item_id=?", (item.id,))


def move_items(conn: sqlite3.Connection, item_ids: list[int], category: str,
               platform: str | None = None) -> None:
    """Put items in another section (and platform, for games)."""
    platform = platform if categories.get(category).platforms else None
    conn.executemany("UPDATE items SET user_category=?, user_platform=? WHERE id=?",
                     [(category, platform, i) for i in item_ids])


def remove_items(conn: sqlite3.Connection, item_ids: list[int]) -> None:
    """Forget items (files on disk are never touched) and delete their covers."""
    _delete_covers(conn, "SELECT poster FROM items WHERE id=?", [(i,) for i in item_ids])
    conn.executemany("DELETE FROM items WHERE id=?", [(i,) for i in item_ids])


def remove_locations(conn: sqlite3.Connection, root_ids: list[int], keep_items: bool) -> None:
    """Stop scanning locations. Their items stay in the library (with completion
    and notes) unless ``keep_items`` is False."""
    if not keep_items:
        _delete_covers(conn, "SELECT poster FROM items WHERE root_id=?", [(r,) for r in root_ids])
        conn.executemany("DELETE FROM items WHERE root_id=?", [(r,) for r in root_ids])
    conn.executemany("DELETE FROM roots WHERE id=?", [(r,) for r in root_ids])


# --------------------------------------------------------------------------- "Want to acquire"

WANTED_VOLUME = "wish"  # wanted items aren't on any drive


def add_wanted(conn: sqlite3.Connection, category: str, title: str, year: int | None = None,
               steam_appid: int | None = None, url: str | None = None) -> int:
    """Put something on the "Want to acquire" list. Returns the new item id.
    A Steam result is a PC game; for other games the platform is left to you."""
    title = title.strip()
    existing = conn.execute(
        "SELECT id FROM items WHERE source=? AND category=? AND title=? COLLATE NOCASE",
        (db.WANTED, category, title)).fetchone()
    if existing:
        return existing["id"]
    platform = "PC" if steam_appid and categories.get(category).platforms else None
    return conn.execute(
        "INSERT INTO items(volume_uid, rel_path, is_dir, category, platform, title, year, source, "
        "steam_appid, external_url, kind, added_at) "
        "VALUES(?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))",
        (WANTED_VOLUME, f"{category}/{uuid.uuid4().hex}", category, platform, title, year,
         db.WANTED, steam_appid, url, "series" if category == "tv" else None),
    ).lastrowid


def acquire(conn: sqlite3.Connection, item_ids: list[int]) -> None:
    """"Got it": move items from the list into the library (no files yet).
    A later scan that finds the title on disk links it to the files."""
    conn.executemany(f"UPDATE items SET source='manual' WHERE id=? AND source='{db.WANTED}'",
                     [(i,) for i in item_ids])


def _delete_covers(conn: sqlite3.Connection, query: str, params: list[tuple]) -> None:
    posters = paths.posters_dir()
    for p in params:
        for r in conn.execute(query, p):
            if r["poster"]:
                (posters / r["poster"]).unlink(missing_ok=True)


# --------------------------------------------------------------------------- list export

EXPORT_COLUMNS = ("id", "section", "platform", "title", "year", "season", "cover", "scores",
                  "hours", "detected_how", "path", "sample_files")


def missing_data(item: db.Item) -> bool:
    """No cover, or no score from any site: the items worth a closer look."""
    return not item.poster or not item.scores


def export_list(conn: sqlite3.Connection, items: list[db.Item], path: str) -> int:
    """Write ``items`` as a CSV (opens in Excel; easy to share when reporting
    titles that were misread or not found). Returns the number of rows."""
    import csv

    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(EXPORT_COLUMNS)
        for i in items:
            files = [r["rel_path"].rsplit("/", 1)[-1] for r in conn.execute(
                "SELECT rel_path FROM media_files WHERE item_id=? ORDER BY rel_path LIMIT 3", (i.id,))]
            hours = i.hltb_hours or (round(i.duration_sec / 3600, 1) if i.duration_sec else None)
            w.writerow([
                i.id, i.category, i.platform or "", i.title, i.year or "", i.season or "",
                "yes" if i.poster else "no",
                "; ".join(f"{s.source} {s.value:g}" for s in i.scores),
                hours or "", i.detected_how or "", i.rel_path, " | ".join(files),
            ])
    return len(items)


# --------------------------------------------------------------------------- duplicates

def find_duplicates(items: list[db.Item], copies: dict[int, int] | None = None) -> list[list[db.Item]]:
    """Library items that look like the same thing: same section and title (articles
    and punctuation ignored), e.g. a PS2 and a PC copy, or one movie on two drives.
    Biggest groups first; within a group, by platform and path."""
    from . import titles

    copies = copies or {}  # item id -> the item it's a copy of
    groups: dict[tuple, list[db.Item]] = {}
    for i in items:
        if i.source in (db.WANTED, db.STEAM):
            continue
        key = (i.category, titles.normalize(titles.front_article(i.title)), i.season)
        if key[1]:
            groups.setdefault(key, []).append(i)
    # Copies made with "Copy to…" are on purpose: only distinct originals count.
    found = [sorted(g, key=lambda i: (i.platform or "", i.rel_path)) for g in groups.values()
             if len({copies.get(i.id) or i.id for i in g}) > 1]
    return sorted(found, key=lambda g: (-len(g), g[0].title.lower()))


# --------------------------------------------------------------------------- health

@dataclass
class HealthCheck:
    key: str
    label: str
    hint: str
    ids: list[int]
    fetch: bool = False          # "Fetch again" makes sense for these


def health(items: list[db.Item], copies: dict[int, int] | None = None) -> list[HealthCheck]:
    """What in the library could use a look, for Library health."""
    lib = [i for i in items if i.source not in (db.WANTED, db.STEAM)]
    present = [i for i in lib if not i.missing]
    dupes = [i.id for g in find_duplicates(lib, copies) for i in g]
    return [
        HealthCheck("no_cover", "No cover", "Nothing found yet. Fetch again retries them all; for one\n"
                    "that stays empty, use Find match… on its page.",
                    [i.id for i in present if not i.poster], fetch=True),
        HealthCheck("no_score", "No score from any site",
                    "Movies need an OMDb or TMDB key, retro games an IGDB key.",
                    [i.id for i in present if not i.scores], fetch=True),
        HealthCheck("no_platform", "Games without a platform",
                    "Right-click › Move to › Games › a platform.",
                    [i.id for i in lib if i.category == "game" and not i.platform]),
        HealthCheck("unknown", "In “Unknown”", "Move them to the right section.",
                    [i.id for i in lib if i.category == "unknown"]),
        HealthCheck("duplicates", "Possible duplicates",
                    "Same title twice: another platform, or a copy on another drive.", dupes),
        HealthCheck("missing", "Not found on the last scan",
                    "Files moved or deleted. A rescan re-links moved ones.",
                    [i.id for i in lib if i.missing]),
    ]
