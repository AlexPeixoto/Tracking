"""Changes the user makes to the library (as opposed to what scans and lookups
write). The UI calls these instead of writing SQL itself, so they're testable
without Qt and every rule lives in one place."""

from __future__ import annotations

import sqlite3

from . import categories, db, paths

set_completed = db.set_completed


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


def _delete_covers(conn: sqlite3.Connection, query: str, params: list[tuple]) -> None:
    posters = paths.posters_dir()
    for p in params:
        for r in conn.execute(query, p):
            if r["poster"]:
                (posters / r["poster"]).unlink(missing_ok=True)
