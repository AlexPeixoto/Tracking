"""Fill in the things the scanner can't know: durations, ratings, posters, HLTB time."""

from __future__ import annotations

import logging
import os
import shutil
import sqlite3
from datetime import datetime, timezone
from typing import Callable

from . import db, duration, paths, scanner, volumes
from .providers import Metadata, ProviderError
from .providers import anidb, anilist, games, igdb, kitsu, omdb, opencritic, steam, tmdb, trakt
from .providers.base import search_variants, session

log = logging.getLogger(__name__)

LOCAL_ART = ("cover", "folder", "poster", "boxart", "front")
ART_EXT = (".jpg", ".jpeg", ".png", ".webp")


def item_abs_path(mount: str, item: db.Item) -> str:
    return volumes.join(mount, scanner.fs_path(item.rel_path))


# --------------------------------------------------------------------------- durations

def measure_durations(conn: sqlite3.Connection, item_id: int, mount: str,
                      cancelled: Callable[[], bool] = lambda: False) -> float | None:
    pending = conn.execute(
        "SELECT rel_path FROM media_files WHERE item_id=? AND duration_sec IS NULL", (item_id,)
    ).fetchall()
    for r in pending:
        if cancelled():
            return None
        secs = duration.probe(volumes.join(mount, r["rel_path"]))
        conn.execute(
            "UPDATE media_files SET duration_sec=? WHERE item_id=? AND rel_path=?",
            (secs if secs is not None else 0.0, item_id, r["rel_path"]),
        )
    total = conn.execute(
        "SELECT SUM(duration_sec) s FROM media_files WHERE item_id=?", (item_id,)
    ).fetchone()["s"]
    conn.execute("UPDATE items SET duration_sec=? WHERE id=?", (total or None, item_id))
    conn.commit()
    return total


# --------------------------------------------------------------------------- artwork

def _local_art(path: str) -> str | None:
    if not os.path.isdir(path):
        return None
    try:
        names = os.listdir(path)
    except OSError:
        return None
    lower = {n.lower(): n for n in names}
    for stem in LOCAL_ART:
        for ext in ART_EXT:
            if stem + ext in lower:
                return os.path.join(path, lower[stem + ext])
    return None


def _save_poster(item_id: int, src_path: str | None = None, url: str | None = None) -> str | None:
    if src_path:
        ext = os.path.splitext(src_path)[1].lower()
        name = f"{item_id}{ext}"
        shutil.copyfile(src_path, paths.posters_dir() / name)
        return name
    if url:
        r = session().get(url, timeout=30)
        r.raise_for_status()
        ctype = r.headers.get("Content-Type", "")
        ext = ".png" if "png" in ctype else ".webp" if "webp" in ctype else ".jpg"
        name = f"{item_id}{ext}"
        (paths.posters_dir() / name).write_bytes(r.content)
        return name
    return None


# --------------------------------------------------------------------------- metadata

# Which score becomes the headline rating on the card, best first.
RATING_PREF = {
    "anime": ["AniDB", "MAL", "AniList", "Kitsu", "Trakt"],
    "movie": ["IMDb", "TMDB", "Trakt", "Metacritic", "Rotten Tomatoes"],
    "game": ["Metacritic", "OpenCritic", "IGDB critics", "Steam", "IGDB users", "RAWG users",
             "HLTB users"],
}
# Services that do nothing without a setting (OMDb falls back to IMDb search, so it isn't here).
NEEDS_KEY = {
    "rawg": "rawg_key", "igdb": "igdb_client_id", "opencritic": "opencritic_key",
    "tmdb": "tmdb_key", "trakt": "trakt_client_id", "anidb": "anidb_client",
}
# Whose cover art to use, best first (others only fill in when these have none).
POSTER_PREF = {
    "anime": ["anidb", "anilist", "mal", "kitsu"],
    "movie": ["tmdb", "omdb"],
    "game": ["igdb", "hltb", "rawg", "steam"],
}


def _sources(item: db.Item, s: Callable[[str], str],
             t: str) -> list[tuple[str, Callable[[], Metadata | None]]]:
    """(name, lookup) pairs for the item's category, searching for title ``t``.
    Order = priority for title, summary and links; services without a configured
    key return None."""
    y = item.year
    if item.category == "movie":
        return [
            ("omdb", lambda: omdb.lookup(t, y, s("omdb_key"))),
            ("tmdb", lambda: tmdb.lookup(t, y, s("tmdb_key"))),
            ("trakt", lambda: trakt.lookup(t, y, s("trakt_client_id"), "movie")),
        ]
    if item.category == "anime":
        return [
            ("anidb", lambda: anidb.lookup_anidb(t, s("anidb_client"), s("anidb_clientver"))),
            ("anilist", lambda: anilist.lookup(t, y, item.kind)),
            ("mal", lambda: anidb.lookup_mal(t, y, item.kind)),
            ("kitsu", lambda: kitsu.lookup(t, y, item.kind)),
            ("trakt", lambda: trakt.lookup(t, y, s("trakt_client_id"),
                                           "movie" if item.kind == "movie" else "show")),
        ]
    if item.category == "game":
        out = [
            ("rawg", lambda: games.lookup_rawg(t, item.platform, s("rawg_key"))),
            ("igdb", lambda: igdb.lookup(t, item.platform, s("igdb_client_id"), s("igdb_client_secret"))),
            ("hltb", lambda: games.lookup_hltb(t, item.platform)),
            ("opencritic", lambda: opencritic.lookup(t, s("opencritic_key"))),
        ]
        if item.platform in (None, "PC"):
            out.append(("steam", lambda: steam.lookup(t)))
        return out
    return []


def combine(category: str, results: dict[str, Metadata]) -> Metadata | None:
    """Merge per-service results (dict order = priority) into one record."""
    if not results:
        return None
    md = Metadata()
    seen: set[str] = set()
    for r in results.values():
        for field in ("title", "year", "summary", "external_url", "external_id",
                      "hltb_hours", "hltb_extra", "hltb_complete"):
            if getattr(md, field) is None and getattr(r, field) is not None:
                setattr(md, field, getattr(r, field))
        for sc in r.scores:
            if sc.source not in seen:  # e.g. Metacritic from both RAWG and Steam
                seen.add(sc.source)
                md.scores.append(sc)
    order = POSTER_PREF.get(category, []) + list(results)
    md.poster_url = next((results[k].poster_url for k in order
                          if k in results and results[k].poster_url), None)
    by_source = {sc.source: sc for sc in md.scores}
    head = next((by_source[n] for n in RATING_PREF.get(category, []) if n in by_source),
                md.scores[0] if md.scores else None)
    if head:
        md.rating, md.rating_max, md.rating_source = head.value, head.max_value, head.source
    return md


def lookup(conn: sqlite3.Connection, item: db.Item) -> tuple[Metadata | None, list[str]]:
    warnings: list[str] = []
    s = lambda k: db.get_setting(conn, k).strip()  # noqa: E731
    results: dict[str, Metadata] = {}
    variants = search_variants(item.title)
    for name, _ in _sources(item, s, item.title):
        if name in NEEDS_KEY and not s(NEEDS_KEY[name]):
            log.debug("%s: skipped (no key in Settings)", name)
            continue
        try:
            r = None
            for t in variants:  # retry "Show 1st GIG" as "Show"
                r = dict(_sources(item, s, t))[name]()
                if r:
                    break
        except ProviderError as e:
            log.warning("%s: %s", name, e)
            warnings.append(str(e))
            continue
        except Exception as e:  # one service being down shouldn't block the others
            log.warning("%s lookup for %r failed: %s", name, item.title, e)
            warnings.append(f"{name} lookup for {item.title}: {e}")
            continue
        if r:
            log.debug("%s: %r -> %r (%s) %s", name, item.title, r.title, r.year,
                      ", ".join(f"{sc.source} {sc.value:g}" for sc in r.scores) or "no score")
            results[name] = r
        else:
            log.debug("%s: no match for %r", name, item.title)
    md = combine(item.category, results)
    log.info("metadata for %r: %s", item.title,
             f"{md.rating_source} {md.rating:g}" if md and md.rating is not None else "no rating")
    return md, warnings


def enrich_item(conn: sqlite3.Connection, item_id: int, mount: str | None,
                force: bool = False) -> list[str]:
    """Fetch metadata + artwork for one item. Returns warnings to show the user."""
    item = db.get_item(conn, item_id)
    if not item:
        return []
    md, warnings = lookup(conn, item)

    poster = None if force else item.poster
    if mount and not poster:
        art = _local_art(item_abs_path(mount, item))
        if art:
            poster = _save_poster(item.id, src_path=art)
    if md and md.poster_url and not poster:
        try:
            poster = _save_poster(item.id, url=md.poster_url)
        except Exception as e:
            warnings.append(f"Poster for {item.title}: {e}")

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if md:
        conn.execute(
            "UPDATE items SET rating=?, rating_max=?, rating_source=?, external_id=?, "
            "external_url=?, summary=?, hltb_hours=COALESCE(?, hltb_hours), "
            "hltb_extra=COALESCE(?, hltb_extra), hltb_complete=COALESCE(?, hltb_complete), "
            "year=COALESCE(year, ?), poster=?, enriched_at=? WHERE id=?",
            (md.rating, md.rating_max, md.rating_source, md.external_id, md.external_url,
             md.summary, md.hltb_hours, md.hltb_extra, md.hltb_complete, md.year, poster, now,
             item.id),
        )
        conn.execute("DELETE FROM scores WHERE item_id=?", (item.id,))
        conn.executemany(
            "INSERT OR REPLACE INTO scores(item_id, source, value, max_value, url) VALUES(?,?,?,?,?)",
            [(item.id, sc.source, sc.value, sc.max_value, sc.url) for sc in md.scores],
        )
    else:
        # Nothing found: mark as done unless it failed, so a later run retries errors.
        conn.execute("UPDATE items SET poster=?, enriched_at=? WHERE id=?",
                     (poster, None if warnings else now, item.id))
    conn.commit()
    return warnings
