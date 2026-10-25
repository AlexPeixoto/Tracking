"""Fill in the things the scanner can't know: durations, ratings, posters, HLTB time."""

from __future__ import annotations

import logging
import os
import re
import shutil
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime

from . import categories, db, duration, paths, providers, scan, titles, volumes
from .providers import Metadata, ProviderError
from .providers.base import search_variants, session
from .scan.videos import is_short_extra
from .scores import RATING_PREF

log = logging.getLogger(__name__)

LOCAL_ART = ("cover", "folder", "poster", "boxart", "front")
ART_EXT = (".jpg", ".jpeg", ".png", ".webp")


def item_abs_path(mount: str, item: db.Item) -> str:
    return volumes.join(mount, scan.fs_path(item.rel_path))


# --------------------------------------------------------------------------- durations

def drop_short_extras(conn: sqlite3.Connection, item_id: int) -> int:
    """Forget openings / endings / promos counted as episodes (short, and named so)."""
    rows = conn.execute("SELECT rel_path, duration_sec FROM media_files WHERE item_id=?",
                        (item_id,)).fetchall()
    gone = [r["rel_path"] for r in rows if is_short_extra(r["rel_path"], r["duration_sec"])]
    conn.executemany("DELETE FROM media_files WHERE item_id=? AND rel_path=?",
                     [(item_id, p) for p in gone])
    return len(gone)


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
    drop_short_extras(conn, item_id)  # a 1-minute "Opening 01" isn't an episode
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


_SEASON_SUFFIX = re.compile(r"(?i)\s+(?:season|s)\s*\d+$")


def borrow_cover(conn: sqlite3.Connection, item_id: int) -> bool:
    """Nothing found a cover: use the first season's, or the same title's from elsewhere
    in your library (the Steam copy of a Switch game). Blank only when neither exists."""
    return borrow_season_cover(conn, item_id) or borrow_title_cover(conn, item_id)


def borrow_title_cover(conn: sqlite3.Connection, item_id: int) -> bool:
    """ "GalGun 2" on Switch, with no cover found anywhere, shows the cover of the
    "Gal*Gun 2" you own on Steam: same name once spacing and symbols are ignored."""
    row = conn.execute("SELECT COALESCE(user_title, title) AS title, COALESCE(user_category, "
                       "category) AS category, poster FROM items WHERE id=?", (item_id,)).fetchone()
    if not row or row["poster"]:
        return False
    key = re.sub(r"[^a-z0-9]", "", titles.normalize(row["title"]))
    if len(key) < 4:
        return False
    for cand in conn.execute(
            "SELECT COALESCE(user_title, title) AS title, poster FROM items WHERE id!=? "
            "AND poster IS NOT NULL AND COALESCE(user_category, category)=?", (item_id, row["category"])):
        if re.sub(r"[^a-z0-9]", "", titles.normalize(cand["title"])) != key:
            continue
        src = paths.posters_dir() / cand["poster"]
        if src.exists():
            name = _save_poster(item_id, src_path=str(src))
            conn.execute("UPDATE items SET poster=? WHERE id=?", (name, item_id))
            log.info("no cover for %r: using %r's", row["title"], cand["title"])
            return True
    return False


def borrow_season_cover(conn: sqlite3.Connection, item_id: int) -> bool:
    """A later season nothing has a cover for ("Agent AIKa Season 2": no service knows that
    name) shows the first season's cover instead of a blank card. A copy, so it survives
    the first season being removed; a real cover found later replaces it on refetch."""
    row = conn.execute(
        "SELECT id, volume_uid, rel_path, COALESCE(user_title, title) AS title, "
        "COALESCE(user_category, category) AS category, season, poster FROM items WHERE id=?",
        (item_id,)).fetchone()
    if not row or row["poster"] or not categories.get(row["category"]).episodic:
        return False
    base = _SEASON_SUFFIX.sub("", row["title"])
    if base == row["title"] and not (row["season"] or 1) > 1:
        return False
    folder = row["rel_path"].split(scan.GROUP_SEP)[0]
    show = titles.normalize(base)
    def same_folder(c) -> bool:
        return c["volume_uid"] == row["volume_uid"] and c["rel_path"].split(scan.GROUP_SEP)[0] == folder

    # Same show name, or a first season in the same release folder ("Blood Blockade
    # Battlefront" next to "Blood Blockade Season 2"). Same folder first.
    cands = [c for c in conn.execute(
        "SELECT rel_path, volume_uid, COALESCE(user_title, title) AS title, kind, poster FROM items "
        "WHERE id!=? AND poster IS NOT NULL AND COALESCE(user_category, category)=? "
        "AND COALESCE(season, 1) <= 1", (item_id, row["category"]))
        if titles.normalize(c["title"]) == show or (same_folder(c) and c["kind"] == "series")]
    cands.sort(key=lambda c: (not same_folder(c), titles.normalize(c["title"]) != show))
    for cand in cands:
        src = paths.posters_dir() / cand["poster"]
        if not src.exists():
            continue
        name = _save_poster(item_id, src_path=str(src))
        conn.execute("UPDATE items SET poster=? WHERE id=?", (name, item_id))
        log.info("no cover for %r: using %r's", row["title"], cand["title"])
        return True
    return False


# --------------------------------------------------------------------------- metadata

def combine(category: str, results: dict[str, Metadata],
            cover_rank: dict[str, int] | None = None) -> Metadata | None:
    """Merge per-service results (dict order = priority) into one record.

    Scores are pooled (first one of each name wins, e.g. Metacritic via RAWG and
    Steam), the cover comes from the service with the best ``cover_rank``, and
    the headline rating is the first score in scores.RATING_PREF order.
    """
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
            if sc.source not in seen:
                seen.add(sc.source)
                md.scores.append(sc)
    ranks = cover_rank or {p.name: p.cover_rank for p in providers.REGISTRY}
    with_cover = [name for name, r in results.items() if r.poster_url]
    if with_cover:
        # A service sure of its art (Steam's library image for a known app) goes first.
        md.poster_url = results[min(with_cover, key=lambda n: (not results[n].cover_first,
                                                               ranks.get(n, 50)))].poster_url
    by_source = {sc.source: sc for sc in md.scores}
    head = next((by_source[n] for n in RATING_PREF.get(category, []) if n in by_source),
                md.scores[0] if md.scores else None)
    if head:
        md.rating, md.rating_max, md.rating_source = head.value, head.max_value, head.source
    return md


def search_titles(item: db.Item) -> list[str]:
    """Titles to try, best first. TV ratings are per show, so "Bad Sisters
    Season 2" is looked up as "Bad Sisters" first."""
    variants = search_variants(item.title)
    if item.category == "tv":
        show = re.sub(r"(?i)\s+season\s+\d+$", "", item.title)
        variants = search_variants(show) + [v for v in variants if v not in search_variants(show)]
    return variants


def make_query(conn: sqlite3.Connection, item: db.Item) -> providers.Query:
    settings = db.all_settings(conn)
    return providers.Query(item.title, year=item.year, category=item.category, kind=item.kind,
                           platform=item.platform, season=item.season,
                           steam_appid=item.steam_appid, settings=settings)


def lookup(conn: sqlite3.Connection, item: db.Item
           ) -> tuple[Metadata | None, list[str], dict[str, Metadata]]:
    """Ask every service registered for the item's category. Returns the merged
    result, warnings for the user, and each service's own result."""
    warnings: list[str] = []
    query = make_query(conn, item)
    results: dict[str, Metadata] = {}
    for p in providers.for_query(query):
        if not p.configured(query):
            log.debug("%s: skipped (no key in Settings)", p.name)
            continue
        try:
            r = None
            for title in search_titles(item):  # retry "Show 1st GIG" as "Show", etc.
                r = p.lookup(query.with_title(title))
                if r:
                    break
        except ProviderError as e:
            log.warning("%s: %s", p.name, e)
            warnings.append(str(e))
            continue
        except Exception as e:  # one service being down shouldn't block the others
            log.warning("%s lookup for %r failed: %s", p.name, item.title, e)
            warnings.append(f"{p.label} lookup for {item.title}: {e}")
            continue
        if r:
            log.debug("%s: %r -> %r (%s) %s", p.name, item.title, r.title, r.year,
                      ", ".join(f"{sc.source} {sc.value:g}" for sc in r.scores) or "no score")
            results[p.name] = r
        else:
            log.debug("%s: no match for %r", p.name, item.title)
    md = combine(item.category, results)
    log.info("metadata for %r: %s", item.title,
             f"{md.rating_source} {md.rating:g}" if md and md.rating is not None else "no rating")
    return md, warnings, results


def fetch_episodes(query: providers.Query, results: dict[str, Metadata]
                   ) -> tuple[str, list[providers.Episode]] | None:
    """Episode list from the first service (registry order) that found the show
    and can list its episodes: AniDB, TMDB, TVmaze, MAL. None if nobody can."""
    for p in providers.for_query(query):
        found = results.get(p.name)
        if not (p.episodes and found and found.external_id):
            continue
        try:
            eps = p.episodes(query, found.external_id)
        except Exception as e:
            log.warning("%s episodes for %r failed: %s", p.name, query.title, e)
            continue
        if eps:
            return p.label.split(" (")[0], eps
    return None


FILES_SOURCE = "your files"


def episodes_from_files(conn: sqlite3.Connection, item: db.Item
                        ) -> tuple[str, list[providers.Episode]] | None:
    """The episode numbers found in the item's files, as a bare episode list."""
    rows = conn.execute(
        "SELECT DISTINCT COALESCE(season, ?) s, episode FROM media_files "
        "WHERE item_id=? AND episode IS NOT NULL ORDER BY s, episode", (item.season or 1, item.id))
    eps = [providers.Episode(r["s"], r["episode"]) for r in rows]
    return (FILES_SOURCE, eps) if eps else None


def save_episodes(conn: sqlite3.Connection, item_id: int, source: str,
                  episodes: list[providers.Episode]) -> None:
    conn.execute("DELETE FROM episodes WHERE item_id=?", (item_id,))
    conn.executemany(
        "INSERT OR REPLACE INTO episodes(item_id, season, number, title, aired, runtime_min, score, "
        "score_max, source, url, note) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        [(item_id, e.season, e.number, e.title, e.aired, e.runtime_min, e.score, e.score_max,
          source, e.url, e.note) for e in episodes])


def enrich_item(conn: sqlite3.Connection, item_id: int, mount: str | None,
                force: bool = False) -> list[str]:
    """Fetch metadata + artwork for one item. Returns warnings to show the user."""
    item = db.get_item(conn, item_id)
    if not item:
        return []
    md, warnings, results = lookup(conn, item)
    if categories.get(item.category).episodic and item.kind != "movie":
        found = fetch_episodes(make_query(conn, item), results) if results else None
        # No service could list them (offline, unknown show): show what's on disk at least.
        found = found or episodes_from_files(conn, item)
        if found:
            save_episodes(conn, item.id, *found)
            # Commit now: the cover download below can take a while, and an open write
            # would block the other metadata workers ("database is locked").
            conn.commit()
            log.info("episodes for %r: %d from %s", item.title, len(found[1]), found[0])

    # Refetch looks for a new cover, but keeps the old one if none turns up (a service
    # being down shouldn't leave the item blank).
    old_poster = item.poster
    pinned = conn.execute("SELECT cover_pinned FROM items WHERE id=?", (item.id,)).fetchone()[0]
    poster = None if force and not pinned else old_poster
    if mount and not poster:
        art = _local_art(item_abs_path(mount, item))
        if art:
            poster = _save_poster(item.id, src_path=art)
    if md and md.poster_url and not poster:
        try:
            poster = _save_poster(item.id, url=md.poster_url)
        except Exception as e:
            warnings.append(f"Poster for {item.title}: {e}")
    poster = poster or old_poster

    now = datetime.now(UTC).isoformat(timespec="seconds")
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
        if warnings:
            # A service failed this time: replace only the scores we got, keep its old one.
            conn.executemany("DELETE FROM scores WHERE item_id=? AND source=?",
                             [(item.id, sc.source) for sc in md.scores])
        else:
            conn.execute("DELETE FROM scores WHERE item_id=?", (item.id,))
        conn.executemany(
            "INSERT OR REPLACE INTO scores(item_id, source, value, max_value, url) VALUES(?,?,?,?,?)",
            [(item.id, sc.source, sc.value, sc.max_value, sc.url) for sc in md.scores],
        )
    else:
        # Nothing found: mark as done unless it failed, so a later run retries errors.
        conn.execute("UPDATE items SET poster=?, enriched_at=? WHERE id=?",
                     (poster, None if warnings else now, item.id))
    if not poster:
        borrow_cover(conn, item.id)
    conn.commit()
    return warnings
