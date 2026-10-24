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
    if done:  # finished: off the board (it shows under "Done this year" from the Completed log)
        conn.executemany("UPDATE items SET plan_state=NULL, plan_position=NULL WHERE id=?",
                         [(i,) for i in item_ids])


def edit_item(conn: sqlite3.Connection, item: db.Item, *, title: str, category: str,
              platform: str | None, year: int | None, notes: str | None,
              store: str | None = None) -> bool:
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
    if store is not None:
        conn.execute("UPDATE items SET store=? WHERE id=?", (store.strip() or None, item.id))
    return (title or item.detected_title, category, platform, year) != (
        item.title, item.category, item.platform, item.year)


def use_match(conn: sqlite3.Connection, item: db.Item, title: str, year: int | None = None,
              steam_appid: int | None = None) -> None:
    """Find match: look the item up as ``title`` from now on (and as that Steam app),
    dropping what was fetched for the old name. Your notes / completion stay."""
    conn.execute(
        "UPDATE items SET user_title=?, year=COALESCE(?, year), steam_appid=COALESCE(?, steam_appid), "
        "enriched_at=NULL, poster=NULL, rating=NULL, rating_max=NULL, rating_source=NULL, "
        "summary=NULL, external_url=NULL, external_id=NULL, cover_pinned=0 WHERE id=?",
        (title if title != item.detected_title else None, year, steam_appid, item.id))
    conn.execute("DELETE FROM scores WHERE item_id=?", (item.id,))
    conn.execute("DELETE FROM episodes WHERE item_id=?", (item.id,))


def set_cover_url(conn: sqlite3.Connection, item_id: int, url: str) -> str:
    """Search cover…: download the cover you picked and keep it (refetch won't replace it).
    Returns the new file name."""
    from .enrich import _save_poster  # enrich imports the scanner: keep it lazy

    old = conn.execute("SELECT poster FROM items WHERE id=?", (item_id,)).fetchone()
    name = _save_poster(item_id, url=url)
    conn.execute("UPDATE items SET poster=?, cover_pinned=1 WHERE id=?", (name, item_id))
    if old and old[0] and old[0] != name:
        in_use = conn.execute("SELECT 1 FROM completions WHERE poster=?", (old[0],)).fetchone()
        if not in_use:
            (paths.posters_dir() / old[0]).unlink(missing_ok=True)
    return name


def move_items(conn: sqlite3.Connection, item_ids: list[int], category: str,
               platform: str | None = None) -> None:
    """Put items in another section (and platform, for games)."""
    platform = platform if categories.get(category).platforms else None
    conn.executemany("UPDATE items SET user_category=?, user_platform=? WHERE id=?",
                     [(category, platform, i) for i in item_ids])


def remove_items(conn: sqlite3.Connection, item_ids: list[int]) -> None:
    """Forget items (files on disk are never touched) and delete their covers."""
    _keep_completion_covers(conn, item_ids)
    _delete_covers(conn, "SELECT poster FROM items WHERE id=?", [(i,) for i in item_ids])
    conn.executemany("DELETE FROM items WHERE id=?", [(i,) for i in item_ids])


def rename_volume(conn: sqlite3.Connection, volume_uid: str, name: str | None) -> None:
    """Your own name for a drive / device, shown instead of its label ("" or None: the
    label again). Kept when the drive is scanned or added again."""
    conn.execute("UPDATE volumes SET name=? WHERE uid=?", ((name or "").strip() or None, volume_uid))


def remove_locations(conn: sqlite3.Connection, root_ids: list[int], keep_items: bool) -> None:
    """Stop scanning locations. Their items stay in the library (with completion
    and notes) unless ``keep_items`` is False."""
    if not keep_items:
        gone = [r["id"] for root in root_ids
                for r in conn.execute("SELECT id FROM items WHERE root_id=?", (root,))]
        _keep_completion_covers(conn, gone)
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


# Where something you add by hand is played: suggestions, any text works.
STORES = ["Xbox app / Game Pass", "Xbox console", "PlayStation console", "Nintendo console",
          "Epic Games", "EA app", "Ubisoft Connect", "Battle.net", "GOG Galaxy", "Amazon Games",
          "Itch.io", "Physical disc / cartridge", "Streaming", "Cloud gaming"]


def add_owned(conn: sqlite3.Connection, category: str, title: str, year: int | None = None,
              steam_appid: int | None = None, url: str | None = None, platform: str | None = None,
              store: str | None = None) -> int:
    """Add something you have that no scan will find (Game Pass, a console, a disc,
    a streaming service) straight to the library. Returns the item id."""
    title = title.strip()
    platform = platform if categories.get(category).platforms else None
    existing = conn.execute(
        "SELECT id FROM items WHERE source IN ('manual', ?) AND category=? AND title=? COLLATE NOCASE "
        "AND COALESCE(platform, '')=COALESCE(?, '')", (db.WANTED, category, title, platform)).fetchone()
    if existing:  # already on the list / in the library: it's yours now
        conn.execute("UPDATE items SET source='manual', store=COALESCE(?, store) WHERE id=?",
                     ((store or "").strip() or None, existing["id"]))
        return existing["id"]
    return conn.execute(
        "INSERT INTO items(volume_uid, rel_path, is_dir, category, platform, title, year, source, "
        "steam_appid, external_url, kind, store, added_at) "
        "VALUES(?, ?, 0, ?, ?, ?, ?, 'manual', ?, ?, ?, ?, datetime('now'))",
        (WANTED_VOLUME, f"{category}/{uuid.uuid4().hex}", category, platform, title, year,
         steam_appid, url, "series" if category == "tv" else None, (store or "").strip() or None),
    ).lastrowid


def acquire(conn: sqlite3.Connection, item_ids: list[int]) -> None:
    """"Got it": move items from the list into the library (no files yet).
    A later scan that finds the title on disk links it to the files."""
    conn.executemany(f"UPDATE items SET source='manual' WHERE id=? AND source='{db.WANTED}'",
                     [(i,) for i in item_ids])


def _keep_completion_covers(conn: sqlite3.Connection, item_ids: list[int]) -> None:
    """Completed entries show their library item's cover; before the item (and its cover
    file) goes, give them a copy of their own so past years keep their pictures."""
    import shutil

    from .completions import _cover_name

    posters = paths.posters_dir()
    for item_id in item_ids:
        row = conn.execute("SELECT poster FROM items WHERE id=?", (item_id,)).fetchone()
        src = posters / row["poster"] if row and row["poster"] else None
        if not src or not src.exists():
            continue
        for c in conn.execute("SELECT id FROM completions WHERE item_id=? AND poster IS NULL",
                              (item_id,)).fetchall():
            name = _cover_name(c["id"], src.suffix)
            shutil.copyfile(src, posters / name)
            conn.execute("UPDATE completions SET poster=? WHERE id=?", (name, c["id"]))


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
                    "Lesser-known movies need an OMDb or TMDB key, retro games an IGDB key.",
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


# --------------------------------------------------------------------------- planning

PLAN_NEXT, PLAN_PLAYING, PLAN_HOLD, PLAN_DROPPED = "next", "playing", "hold", "dropped"
PLAN_STATES = (PLAN_NEXT, PLAN_PLAYING, PLAN_HOLD, PLAN_DROPPED)


def set_plan(conn: sqlite3.Connection, item_ids: list[int], state: str | None,
             position: int | None = None) -> None:
    """Put items in a Planning column (None takes them off the board). Moving finished
    items back to a column un-completes them (their latest Completed entry goes)."""
    from datetime import datetime

    if state is not None and state not in PLAN_STATES:
        raise ValueError(state)
    now = datetime.now().isoformat(timespec="seconds")
    done = [r["id"] for r in conn.execute(
        f"SELECT id FROM items WHERE completed=1 AND id IN ({','.join('?' * len(item_ids))})", item_ids)]
    if done and state in (PLAN_NEXT, PLAN_PLAYING, PLAN_HOLD):
        set_completed(conn, done, False)
    if state == PLAN_NEXT:
        # New arrivals go to the end of the queue unless a spot was given.
        end = conn.execute("SELECT COALESCE(MAX(plan_position), -1) FROM items "
                           "WHERE plan_state=?", (PLAN_NEXT,)).fetchone()[0]
        for n, iid in enumerate(item_ids):
            conn.execute("UPDATE items SET plan_state=?, plan_position=?, plan_changed=? WHERE id=?",
                         (state, end + 1 + n, now, iid))
        if position is not None:
            queue = [r["id"] for r in conn.execute(
                "SELECT id FROM items WHERE plan_state=? AND id NOT IN "
                f"({','.join('?' * len(item_ids))}) ORDER BY plan_position, id",
                (PLAN_NEXT, *item_ids))]
            queue[position:position] = item_ids
            order_plan(conn, queue)
    else:
        conn.executemany("UPDATE items SET plan_state=?, plan_position=NULL, plan_changed=? WHERE id=?",
                         [(state, now, i) for i in item_ids])


def order_plan(conn: sqlite3.Connection, item_ids: list[int]) -> None:
    """Your order for "Up next"."""
    conn.executemany("UPDATE items SET plan_position=? WHERE id=?",
                     [(n, i) for n, i in enumerate(item_ids)])


def started(conn: sqlite3.Connection, item_id: int) -> None:
    """You played it (Play / an episode): it's "Playing" now, unless finished or dropped."""
    from datetime import datetime

    conn.execute("UPDATE items SET plan_state=?, plan_position=NULL, plan_changed=? WHERE id=? "
                 "AND completed=0 AND COALESCE(plan_state, '') NOT IN (?, ?)",
                 (PLAN_PLAYING, datetime.now().isoformat(timespec="seconds"), item_id,
                  PLAN_PLAYING, PLAN_DROPPED))


# --------------------------------------------------------------------------- what's left / what next

def in_library(item: db.Item) -> bool:
    """Yours: on disk or marked "got it" (not the wish list, not a Steam account game)."""
    return item.source not in (db.WANTED, db.STEAM)


def in_backlog(item: db.Item) -> bool:
    """Yours, not finished, not gone missing, not given up on."""
    return (in_library(item) and not item.completed and not item.missing
            and item.plan_state != PLAN_DROPPED)


def remaining_hours(item: db.Item) -> float:
    """Roughly how long is left: HLTB minus time played, episodes not played yet, runtime."""
    if item.category == "game":
        hltb = item.hltb_hours or 0
        return max(hltb - (item.playtime_min or 0) / 60, 0) if hltb else 0
    total = item.total_hours or 0
    if item.kind != "movie" and item.video_count and item.last_episode:
        left = max(item.video_count - item.last_episode, 0)
        return total * left / item.video_count
    return total


PICK_FROM = {"both": "Up next and Backlog", "next": "Up next", "backlog": "Backlog"}
PICK_TIME = {"any": ("Any length", None, None), "2": ("Under 2 h", 0, 2), "10": ("Under 10 h", 0, 10),
             "30": ("Under 30 h", 0, 30), "long": ("30 h or more", 30, None)}


def pick_candidates(items: list[db.Item], source: str = "both", sections: set[str] | None = None,
                    time: str = "any", online: set[str] | None = None) -> list[db.Item]:
    """What "What should I play?" chooses from. ``online``: volume ids of connected drives
    (None = don't care)."""
    _, low, high = PICK_TIME.get(time, PICK_TIME["any"])
    out = []
    for i in items:
        queued = i.plan_state == PLAN_NEXT
        if source == "next" and not queued:
            continue
        if source == "backlog" and not in_backlog(i):
            continue
        if source == "both" and not (queued or in_backlog(i)):
            continue
        if sections and i.category not in sections:
            continue
        hours = remaining_hours(i)
        if (low is not None or high is not None) and not hours:
            continue  # unknown length can't satisfy a length filter
        if (low is not None and hours < low) or (high is not None and hours >= high):
            continue
        if online is not None and in_library(i) and i.volume_uid not in online:
            continue
        out.append(i)
    return out


def pick(candidates: list[db.Item], favour_scores: bool = False, rng=None) -> db.Item | None:
    """One at random; well-scored ones and the front of Up next come up more often."""
    import random

    if not candidates:
        return None
    rng = rng or random.Random()

    def weight(i: db.Item) -> float:
        w = 1.0
        if favour_scores:
            w *= ((i.avg_score or 60) / 100) ** 3
        if i.plan_state == PLAN_NEXT and i.plan_position is not None:
            w *= 1 + 1 / (1 + i.plan_position)
        return w

    return rng.choices(candidates, weights=[weight(i) for i in candidates], k=1)[0]
