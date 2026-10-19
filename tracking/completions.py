"""The "Completed" log: what you finished, and when.

One row per completion, so a game beaten in 2019 and again in 2024 is in both
years. Rows come from:

  * marking a library item completed (dated that day; unmarking removes it),
  * a year CSV ("2023.csv" with name, DD/MM/YYYY per line),
  * entries added or edited by hand.

Entries outside the library get their own cover, looked up by title like any
library item. The "Export year as image" grid lives in ui/year_image.py.
"""

from __future__ import annotations

import csv
import logging
import os
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from . import db, paths, providers, titles

log = logging.getLogger(__name__)


@dataclass
class Completion:
    id: int
    item_id: int | None
    title: str
    category: str
    platform: str | None
    completed_on: str            # 'YYYY-MM-DD' or 'YYYY'
    position: int | None
    poster: str | None           # file in the posters folder (own, or the library item's)
    notes: str | None

    @property
    def year(self) -> int:
        return int(self.completed_on[:4])

    @property
    def day(self) -> date | None:
        try:
            return date.fromisoformat(self.completed_on)
        except ValueError:
            return None

    def date_text(self) -> str:
        """DD/MM/YYYY, like the CSVs (or just the year when the day isn't known)."""
        d = self.day
        return d.strftime("%d/%m/%Y") if d else self.completed_on[:4]

    def poster_path(self) -> str | None:
        if not self.poster:
            return None
        p = paths.posters_dir() / self.poster
        return str(p) if p.exists() else None


_SELECT = """
SELECT c.id, c.item_id, c.title, c.category, c.platform, c.completed_on, c.position, c.notes,
       COALESCE(c.poster, i.poster) AS poster
FROM completions c LEFT JOIN items i ON i.id = c.item_id
"""
_ORDER = "ORDER BY c.position IS NULL, c.position, c.completed_on, c.id"


def _row(r: sqlite3.Row) -> Completion:
    return Completion(r["id"], r["item_id"], r["title"], r["category"], r["platform"],
                      r["completed_on"], r["position"], r["poster"], r["notes"])


def years(conn: sqlite3.Connection) -> dict[int, int]:
    """{year: entries}, newest year first."""
    rows = conn.execute("SELECT CAST(substr(completed_on, 1, 4) AS INTEGER) y, COUNT(*) n "
                        "FROM completions GROUP BY y ORDER BY y DESC")
    return {r["y"]: r["n"] for r in rows}


def list_year(conn: sqlite3.Connection, year: int | None = None) -> list[Completion]:
    """A year's entries in your order (by date unless you rearranged them); all when None."""
    if year is None:
        return [_row(r) for r in conn.execute(f"{_SELECT} ORDER BY c.completed_on DESC, c.id")]
    return [_row(r) for r in conn.execute(
        f"{_SELECT} WHERE substr(c.completed_on, 1, 4) = ? {_ORDER}", (f"{year:04d}",))]


def get(conn: sqlite3.Connection, cid: int) -> Completion | None:
    r = conn.execute(f"{_SELECT} WHERE c.id = ?", (cid,)).fetchone()
    return _row(r) if r else None


# --------------------------------------------------------------------------- dates

def today() -> str:
    return date.today().isoformat()


def parse_date(text: str, year_hint: int | None = None) -> str:
    """"31/12/2023" (the CSV format), "2023-12-31", "31-12-2023", "2023" -> stored form.
    An empty date takes ``year_hint`` (the year in the file name). ValueError otherwise."""
    t = (text or "").strip()
    if not t:
        if year_hint:
            return f"{year_hint:04d}"
        raise ValueError("no date")
    if re.fullmatch(r"\d{4}", t):
        return t
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%Y-%m-%d", "%Y/%m/%d", "%d/%m/%y"):
        try:
            return datetime.strptime(t, fmt).date().isoformat()
        except ValueError:
            continue
    raise ValueError(f"unknown date {t!r} (expected DD/MM/YYYY)")


# --------------------------------------------------------------------------- changes

def link_library(conn: sqlite3.Connection, title: str, category: str | None = None) -> int | None:
    """The library item with this title (same section when given), for its cover."""
    key = titles.normalize(titles.front_article(title))
    if not key:
        return None
    for i in db.list_items(conn):
        if i.source == db.WANTED or (category and i.category != category):
            continue
        if titles.normalize(titles.front_article(i.title)) == key:
            return i.id
    return None


def add(conn: sqlite3.Connection, title: str, completed_on: str, category: str = "game",
        platform: str | None = None, item_id: int | None = None, notes: str | None = None,
        link: bool = True) -> int:
    title = titles.front_article(title.strip())
    if item_id is None and link:
        item_id = link_library(conn, title, category)
    if item_id is not None and platform is None:
        r = conn.execute("SELECT COALESCE(user_platform, platform) p FROM items WHERE id=?",
                         (item_id,)).fetchone()
        platform = r["p"] if r else None
    return conn.execute(
        "INSERT INTO completions(item_id, title, category, platform, completed_on, notes) "
        "VALUES(?,?,?,?,?,?)", (item_id, title, category, platform, completed_on, notes)).lastrowid


def update(conn: sqlite3.Connection, cid: int, **fields) -> None:
    """Edit an entry: title, category, platform, completed_on, notes, item_id."""
    allowed = {"title", "category", "platform", "completed_on", "notes", "item_id"}
    fields = {k: v for k, v in fields.items() if k in allowed}
    if not fields:
        return
    old = get(conn, cid)
    if "completed_on" in fields and old and old.completed_on[:4] != fields["completed_on"][:4]:
        fields["position"] = None  # moved to another year: goes in by date there
    conn.execute(f"UPDATE completions SET {', '.join(f'{k}=?' for k in fields)} WHERE id=?",
                 [*fields.values(), cid])
    if old and old.item_id and "completed_on" in fields:
        _sync_item(conn, old.item_id)


def delete(conn: sqlite3.Connection, ids: list[int]) -> None:
    items = {r["item_id"] for r in conn.execute(
        f"SELECT item_id FROM completions WHERE id IN ({','.join('?' * len(ids))})", ids)
        if r["item_id"]}
    _delete_own_covers(conn, ids)
    conn.executemany("DELETE FROM completions WHERE id=?", [(i,) for i in ids])
    for item_id in items:
        _sync_item(conn, item_id)


def set_order(conn: sqlite3.Connection, ids: list[int]) -> None:
    """Your order for a year's entries (the image follows it)."""
    conn.executemany("UPDATE completions SET position=? WHERE id=?",
                     [(n, cid) for n, cid in enumerate(ids)])


def reset_order(conn: sqlite3.Connection, year: int) -> None:
    conn.execute("UPDATE completions SET position=NULL WHERE substr(completed_on, 1, 4)=?",
                 (f"{year:04d}",))


def _sync_item(conn: sqlite3.Connection, item_id: int) -> None:
    """A library item is completed while it has log entries; its date is the latest one."""
    last = conn.execute("SELECT MAX(completed_on) d FROM completions WHERE item_id=?",
                        (item_id,)).fetchone()["d"]
    if last:
        conn.execute("UPDATE items SET completed=1, completed_at=? WHERE id=?", (last, item_id))
    else:
        conn.execute("UPDATE items SET completed=0, completed_at=NULL WHERE id=?", (item_id,))


def on_items_completed(conn: sqlite3.Connection, item_ids: list[int], done: bool) -> None:
    """Marking items completed logs today; unmarking removes their latest entry."""
    for item_id in item_ids:
        item = db.get_item(conn, item_id)
        if not item or item.source == db.WANTED:
            continue
        if done:
            if not conn.execute("SELECT 1 FROM completions WHERE item_id=? AND completed_on=?",
                                (item_id, today())).fetchone():
                add(conn, item.title, today(), item.category, item.platform, item_id=item_id)
        else:
            row = conn.execute("SELECT id FROM completions WHERE item_id=? "
                               "ORDER BY completed_on DESC, id DESC LIMIT 1", (item_id,)).fetchone()
            if row:
                conn.execute("DELETE FROM completions WHERE id=?", (row["id"],))


def _delete_own_covers(conn: sqlite3.Connection, ids: list[int]) -> None:
    for r in conn.execute(f"SELECT poster FROM completions WHERE poster IS NOT NULL AND id IN "
                          f"({','.join('?' * len(ids))})", ids):
        try:
            (paths.posters_dir() / r["poster"]).unlink()
        except OSError:
            pass


# --------------------------------------------------------------------------- CSV

@dataclass
class ImportResult:
    added: int = 0
    linked: int = 0              # matched an item in your library (its cover is used)
    skipped: int = 0             # already in the log
    errors: list[str] = field(default_factory=list)
    years: set[int] = field(default_factory=set)
    new_ids: list[int] = field(default_factory=list)

    def summary(self) -> str:
        parts = [f"{self.added} added"]
        if self.linked:
            parts.append(f"{self.linked} matched your library")
        if self.skipped:
            parts.append(f"{self.skipped} already there")
        if self.errors:
            parts.append(f"{len(self.errors)} lines not understood")
        return ", ".join(parts)


def _year_from_name(path: str) -> int | None:
    m = re.search(r"(?<!\d)(19\d\d|20\d\d)(?!\d)", Path(path).stem)
    return int(m.group(1)) if m else None


def read_csv(path: str) -> list[list[str]]:
    with open(path, encoding="utf-8-sig", newline="") as f:
        text = f.read()
    try:
        dialect = csv.Sniffer().sniff(text[:2048], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    return [row for row in csv.reader(text.splitlines(), dialect) if any(c.strip() for c in row)]


def _looks_like_date(text: str) -> bool:
    try:
        parse_date(text)
        return bool(text.strip())
    except ValueError:
        return False


def import_csv(conn: sqlite3.Connection, path: str, category: str = "game",
               year: int | None = None) -> ImportResult:
    """Import "name, date" lines (DD/MM/YYYY). A header line is skipped; a missing date
    uses ``year`` or the year in the file name ("2023.csv"); an optional third column
    is the platform. Lines already in the log (same title and date) are skipped."""
    year = year or _year_from_name(path)
    res = ImportResult()
    for n, row in enumerate(read_csv(path), 1):
        row = [c.strip() for c in row]
        if n == 1 and row[0].lower() in ("name", "title", "game", "nome"):
            continue
        # The date is the first column that reads as one; anything before it is the name
        # ("Incredible Hulk, The,05/02/2023" in an unquoted CSV), after it the platform.
        at = next((i for i, c in enumerate(row[1:], 1) if _looks_like_date(c)), None)
        if at is None:
            name, when, extra = row[0], (row[1] if len(row) > 1 else ""), row[2:]
            if len(row) > 1 and when and not re.search(r"\d", when):  # "Hulk, The" without a date
                name, when = ", ".join(c for c in row if c), ""
                extra = []
        else:
            name, when, extra = ", ".join(c for c in row[:at] if c), row[at], row[at + 1:]
        row = [name, when, *extra]
        if not name:
            continue
        try:
            completed_on = parse_date(when, year)
        except ValueError as e:
            res.errors.append(f"line {n}: {name}: {e}")
            continue
        title = titles.front_article(name)
        dup = conn.execute("SELECT 1 FROM completions WHERE title=? COLLATE NOCASE AND completed_on=?",
                           (title, completed_on)).fetchone()
        if dup:
            res.skipped += 1
            continue
        platform = row[2].strip() or None if len(row) > 2 else None
        cid = add(conn, title, completed_on, category, platform)
        if get(conn, cid).item_id:
            res.linked += 1
        res.added += 1
        res.years.add(int(completed_on[:4]))
        res.new_ids.append(cid)
    return res


def export_csv(conn: sqlite3.Connection, year: int, path: str) -> int:
    """The year as "name,date" lines (DD/MM/YYYY), the same format import reads."""
    entries = list_year(conn, year)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["name", "date", "platform"])
        for c in entries:
            w.writerow([c.title, c.date_text(), c.platform or ""])
    return len(entries)


# --------------------------------------------------------------------------- covers

def needs_cover(conn: sqlite3.Connection) -> list[int]:
    return [r["id"] for r in conn.execute(
        "SELECT c.id FROM completions c LEFT JOIN items i ON i.id = c.item_id "
        "WHERE c.poster IS NULL AND (i.poster IS NULL OR c.item_id IS NULL)")]


def fetch_cover(conn: sqlite3.Connection, cid: int) -> bool:
    """Look the entry up by title (the same services as the library) and save a cover."""
    from .enrich import combine, search_titles  # enrich imports the scanner: keep it lazy

    c = get(conn, cid)
    if not c:
        return False
    settings = {r["key"]: r["value"] or "" for r in conn.execute("SELECT key, value FROM settings")}
    q = providers.Query(c.title, category=c.category, platform=c.platform, settings=settings)
    results = {}
    for p in providers.for_query(q):
        if not p.configured(q):
            continue
        try:
            for t in search_titles(_Named(c.title, c.category)):
                r = p.lookup(q.with_title(t))
                if r:
                    results[p.name] = r
                    break
        except Exception as e:  # one service down shouldn't stop the others
            log.debug("%s lookup for %r failed: %s", p.name, c.title, e)
    md = combine(c.category, results)
    if not md or not md.poster_url:
        log.info("no cover found for completed %r", c.title)
        return False
    from .providers.base import session

    resp = session().get(md.poster_url, timeout=30)
    resp.raise_for_status()
    ctype = resp.headers.get("Content-Type", "")
    name = f"done-{cid}{'.png' if 'png' in ctype else '.webp' if 'webp' in ctype else '.jpg'}"
    (paths.posters_dir() / name).write_bytes(resp.content)
    conn.execute("UPDATE completions SET poster=? WHERE id=?", (name, cid))
    return True


def set_cover_file(conn: sqlite3.Connection, cid: int, src: str) -> None:
    """Use an image of your own as the entry's cover."""
    import shutil

    ext = os.path.splitext(src)[1].lower() or ".jpg"
    name = f"done-{cid}{ext}"
    _delete_own_covers(conn, [cid])
    shutil.copyfile(src, paths.posters_dir() / name)
    conn.execute("UPDATE completions SET poster=? WHERE id=?", (name, cid))


@dataclass
class _Named:
    """search_titles() wants something with .title / .category."""
    title: str
    category: str
