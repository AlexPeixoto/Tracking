"""Your library in other trackers' import formats.

  * MyAnimeList: the XML MAL's own export produces (and its importer, and AniList's, read).
    MAL needs its own id per anime; it comes from AniList (keyless), looked up by title.
  * Letterboxd: a diary CSV (movies you watched, with the dates from the Completed log)
    and a watchlist CSV (Up next / Want to acquire), with IMDb ids where known.
  * Everything: one CSV of the whole library with its status, for spreadsheets or any
    tool that imports CSV.

Status, everywhere: Completed > Dropped > On hold > Playing / Watching > planned.
"""

from __future__ import annotations

import csv
import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from xml.sax.saxutils import escape

from . import completions, db, library

log = logging.getLogger(__name__)

COMPLETED, WATCHING, ON_HOLD, DROPPED, PLANNED = (
    "Completed", "Watching", "On-Hold", "Dropped", "Plan to Watch")


def status(item: db.Item) -> str:
    """MAL's words for where you are with something (games read "Watching" as playing)."""
    if item.completed:
        return COMPLETED
    if item.plan_state == library.PLAN_DROPPED:
        return DROPPED
    if item.plan_state == library.PLAN_HOLD:
        return ON_HOLD
    if item.plan_state == library.PLAN_PLAYING or item.last_episode or item.last_launched:
        return WATCHING
    return PLANNED


@dataclass
class ExportResult:
    files: list[Path] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)   # anime MAL doesn't know

    def summary(self) -> str:
        text = ", ".join(f"{n} {what}" for what, n in self.counts.items())
        if self.skipped:
            text += (f". {len(self.skipped)} anime weren't found on MyAnimeList and were left out: "
                     + ", ".join(self.skipped[:8]) + ("…" if len(self.skipped) > 8 else ""))
        return text


def _finished_on(conn: sqlite3.Connection, item_id: int) -> str | None:
    r = conn.execute("SELECT MAX(completed_on) d FROM completions WHERE item_id=?", (item_id,)).fetchone()
    return r["d"] if r and r["d"] and len(r["d"]) == 10 else None


# --------------------------------------------------------------------------- MyAnimeList

def mal_xml(conn: sqlite3.Connection, items: list[db.Item],
            lookup: Callable[[db.Item], tuple[int, int | None] | None],
            progress: Callable[[str], None] = lambda s: None,
            cancelled: Callable[[], bool] = lambda: False) -> tuple[str, int, list[str]]:
    """(xml, anime written, titles left out). ``lookup(item)`` gives (MAL id, episodes)."""
    rows, skipped = [], []
    anime = [i for i in items if i.category == "anime"]
    for n, i in enumerate(anime, 1):
        if cancelled():
            break
        progress(f"MyAnimeList ids {n}/{len(anime)}: {i.title}")
        try:
            found = lookup(i)
        except Exception as e:  # one failed lookup shouldn't stop the export
            log.warning("MAL id for %r failed: %s", i.title, e)
            found = None
        if not found:
            skipped.append(i.title)
            continue
        mal, episodes = found
        st = status(i)
        watched = (episodes or i.video_count or 0) if st == COMPLETED else (i.last_episode or 0)
        finish = _finished_on(conn, i.id) if st == COMPLETED else None
        rows.append(
            "  <anime>\n"
            f"    <series_animedb_id>{mal}</series_animedb_id>\n"
            f"    <series_title><![CDATA[{i.title}]]></series_title>\n"
            f"    <series_episodes>{episodes or 0}</series_episodes>\n"
            f"    <my_watched_episodes>{watched}</my_watched_episodes>\n"
            "    <my_start_date>0000-00-00</my_start_date>\n"
            f"    <my_finish_date>{finish or '0000-00-00'}</my_finish_date>\n"
            "    <my_score>0</my_score>\n"
            f"    <my_status>{escape(st)}</my_status>\n"
            f"    <my_comments><![CDATA[{(i.notes or '').replace(']]>', ']] >')}]]></my_comments>\n"
            "    <update_on_import>1</update_on_import>\n"
            "  </anime>")
    xml = ('<?xml version="1.0" encoding="UTF-8" ?>\n<myanimelist>\n'
           "  <myinfo>\n    <user_export_type>1</user_export_type>\n  </myinfo>\n"
           + "\n".join(rows) + "\n</myanimelist>\n")
    return xml, len(rows), skipped


def anilist_lookup(item: db.Item) -> tuple[int, int | None] | None:
    from .providers import anilist

    return anilist.mal_id(item.title, item.year, item.kind)


# --------------------------------------------------------------------------- Letterboxd

LETTERBOXD = ["Title", "Year", "imdbID", "WatchedDate", "Rewatch"]


def letterboxd(conn: sqlite3.Connection, items: list[db.Item]) -> tuple[list[list], list[list]]:
    """(diary rows, watchlist rows). One diary row per time you finished a movie."""
    diary, watchlist = [], []
    for i in items:
        if i.category != "movie":
            continue
        imdb = i.external_id if (i.external_id or "").startswith("tt") else ""
        if i.completed or conn.execute("SELECT 1 FROM completions WHERE item_id=?", (i.id,)).fetchone():
            dates = [r["completed_on"] for r in conn.execute(
                "SELECT completed_on FROM completions WHERE item_id=? ORDER BY completed_on", (i.id,))]
            for n, d in enumerate(dates or [""]):
                diary.append([i.title, i.year or "", imdb, d if len(d) == 10 else "", "Yes" if n else ""])
        elif i.plan_state in (library.PLAN_NEXT, None) and i.plan_state != library.PLAN_DROPPED:
            watchlist.append([i.title, i.year or "", imdb])
    # Completed entries that aren't library items (imported year CSVs) are watched too.
    for c in completions.list_year(conn):
        if c.category == "movie" and c.item_id is None:
            diary.append([c.title, "", "", c.completed_on if len(c.completed_on) == 10 else "", ""])
    return diary, watchlist


# --------------------------------------------------------------------------- everything

ALL_COLUMNS = ["title", "section", "platform", "year", "status", "finished_on", "times_finished",
               "hours_played", "hltb_hours", "runtime_hours", "average_score", "imdb_id",
               "steam_appid", "notes", "path"]


def everything(conn: sqlite3.Connection, items: list[db.Item]) -> list[list]:
    rows = []
    for i in items:
        times = conn.execute("SELECT COUNT(*) FROM completions WHERE item_id=?", (i.id,)).fetchone()[0]
        rows.append([
            i.title, i.category, i.platform or "", i.year or "",
            "Want to acquire" if i.source == db.WANTED else status(i),
            _finished_on(conn, i.id) or "", times,
            round(i.playtime_min / 60, 1) if i.playtime_min else "",
            i.hltb_hours or "", round(i.duration_sec / 3600, 1) if i.duration_sec else "",
            round(i.avg_score / 10, 1) if i.avg_score is not None else "",
            i.external_id if (i.external_id or "").startswith("tt") else "",
            i.steam_appid or "", i.notes or "",
            "" if i.source in (db.WANTED, db.STEAM) else i.rel_path,
        ])
    return rows


# --------------------------------------------------------------------------- writing

KINDS = {"mal": "MyAnimeList (anime, .xml)", "letterboxd": "Letterboxd (movies: diary + watchlist, .csv)",
         "all": "Everything (one .csv for spreadsheets / other tools)"}


def _write_csv(path: Path, header: list[str], rows: list[list]) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def export(conn: sqlite3.Connection, folder: Path, kinds: set[str],
           lookup: Callable[[db.Item], tuple[int, int | None] | None] = anilist_lookup,
           progress: Callable[[str], None] = lambda s: None,
           cancelled: Callable[[], bool] = lambda: False) -> ExportResult:
    items = db.list_items(conn)
    res = ExportResult()
    if "all" in kinds:
        path = folder / "tracking-library.csv"
        rows = everything(conn, items)
        _write_csv(path, ALL_COLUMNS, rows)
        res.files.append(path)
        res.counts["items"] = len(rows)
    if "letterboxd" in kinds:
        diary, watchlist = letterboxd(conn, items)
        for name, header, rows in (("letterboxd-diary.csv", LETTERBOXD, diary),
                                   ("letterboxd-watchlist.csv", LETTERBOXD[:3], watchlist)):
            path = folder / name
            _write_csv(path, header, rows)
            res.files.append(path)
        res.counts["movies watched"] = len(diary)
        res.counts["on the watchlist"] = len(watchlist)
    if "mal" in kinds:
        xml, n, skipped = mal_xml(conn, items, lookup, progress, cancelled)
        path = folder / "myanimelist-anime.xml"
        path.write_text(xml, encoding="utf-8")
        res.files.append(path)
        res.counts["anime"] = n
        res.skipped = skipped
    return res
