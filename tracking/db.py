"""SQLite storage.

Every path stored here is *relative to a volume*, and volumes are identified by
a stable id (see :mod:`tracking.volumes`), never by drive letter. That way a
drive that comes back as ``F:`` instead of ``E:`` keeps all of its items.

Each thread should open its own connection with :func:`connect`.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from . import categories, paths
from . import scores as score_math

log = logging.getLogger(__name__)

CATEGORIES = categories.KEYS
UNKNOWN_PLATFORM = "Unknown"

SCHEMA = """
CREATE TABLE IF NOT EXISTS volumes (
    uid        TEXT PRIMARY KEY,   -- e.g. 'guid:{...}', 'serial:1A2B-3C4D', 'unc:\\\\nas\\media'
    serial     TEXT,               -- filesystem serial, used to recognise the drive on another PC
    label      TEXT,
    last_mount TEXT                -- last place we saw it mounted (display only)
);

CREATE TABLE IF NOT EXISTS roots (
    id         INTEGER PRIMARY KEY,
    volume_uid TEXT NOT NULL REFERENCES volumes(uid) ON DELETE CASCADE,
    rel_path   TEXT NOT NULL,      -- '' means the whole drive
    category   TEXT NOT NULL DEFAULT 'auto',   -- auto | anime | movie | game
    platform   TEXT,               -- optional forced platform for game roots
    UNIQUE (volume_uid, rel_path)
);

CREATE TABLE IF NOT EXISTS items (
    id             INTEGER PRIMARY KEY,
    -- Items outlive their location: removing a root or unplugging a drive keeps
    -- the entry (and its completed state / notes); volume_uid + rel_path is
    -- just where it was last seen.
    root_id        INTEGER REFERENCES roots(id) ON DELETE SET NULL,
    volume_uid     TEXT NOT NULL,
    rel_path       TEXT NOT NULL,
    is_dir         INTEGER NOT NULL,
    category       TEXT NOT NULL,
    platform       TEXT,
    title          TEXT NOT NULL,
    year           INTEGER,
    user_category  TEXT,           -- manual overrides win over detection
    user_platform  TEXT,
    user_title     TEXT,
    kind           TEXT,           -- 'series' | 'movie' for video items
    season         INTEGER,
    detected_how   TEXT,           -- why the scanner chose this category / platform
    -- Where the item comes from: 'disk' (scanned files), 'steam' (a Steam account),
    -- 'wish' (on the "Want to acquire" list), 'manual' (marked as acquired by hand).
    source         TEXT NOT NULL DEFAULT 'disk',
    steam_appid    INTEGER,        -- known for Steam games: exact store / artwork lookups
    playtime_min   REAL,           -- Steam: minutes played
    last_played    TEXT,
    file_count     INTEGER NOT NULL DEFAULT 0,
    size_bytes     INTEGER NOT NULL DEFAULT 0,
    duration_sec   REAL,           -- anime / movie: sum of all video files
    hltb_hours     REAL,           -- game: HowLongToBeat main story
    hltb_extra     REAL,           -- main + extras
    hltb_complete  REAL,           -- completionist
    rating         REAL,
    rating_max     REAL,
    rating_source  TEXT,           -- 'IMDb' | 'AniDB' | 'Metacritic'
    external_id    TEXT,
    external_url   TEXT,
    poster         TEXT,           -- file name inside the posters dir
    summary        TEXT,
    missing        INTEGER NOT NULL DEFAULT 0,  -- not found on the last scan of its location
    completed      INTEGER NOT NULL DEFAULT 0,  -- watched / beaten
    completed_at   TEXT,
    notes          TEXT,
    added_at       TEXT,
    scanned_at     TEXT,
    enriched_at    TEXT,
    UNIQUE (volume_uid, rel_path)
);

CREATE TABLE IF NOT EXISTS media_files (
    item_id      INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    rel_path     TEXT NOT NULL,
    size_bytes   INTEGER NOT NULL,
    mtime        REAL NOT NULL,
    duration_sec REAL,
    season       INTEGER,          -- parsed from the file name (episodic items)
    episode      INTEGER,
    PRIMARY KEY (item_id, rel_path)
);

-- Episode list of a series season, with per-episode scores, from one service.
CREATE TABLE IF NOT EXISTS episodes (
    item_id     INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    season      INTEGER NOT NULL,
    number      INTEGER NOT NULL,
    title       TEXT,
    aired       TEXT,
    runtime_min REAL,
    score       REAL,
    score_max   REAL,
    source      TEXT NOT NULL,      -- 'TVmaze', 'MAL', 'AniDB', 'TMDB'
    url         TEXT,
    note        TEXT,               -- 'filler', 'recap', ...
    PRIMARY KEY (item_id, season, number)
);

-- Every score we know for an item (IMDb, Rotten Tomatoes, Metacritic, AniDB, MAL...).
CREATE TABLE IF NOT EXISTS scores (
    item_id   INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    source    TEXT NOT NULL,
    value     REAL NOT NULL,
    max_value REAL NOT NULL,
    url       TEXT,
    PRIMARY KEY (item_id, source)
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- The "Completed" log: one row per time you finished something. Marking an item
-- completed adds one (dated that day); year CSVs and manual entries add rows that
-- need not be in the library at all (item_id NULL, own cover).
CREATE TABLE IF NOT EXISTS completions (
    id           INTEGER PRIMARY KEY,
    item_id      INTEGER REFERENCES items(id) ON DELETE SET NULL,
    title        TEXT NOT NULL,
    category     TEXT NOT NULL DEFAULT 'game',
    platform     TEXT,
    completed_on TEXT NOT NULL,          -- 'YYYY-MM-DD', or just 'YYYY' when the day is unknown
    position     INTEGER,                -- your order within the year (image order); NULL = by date
    poster       TEXT,                   -- own cover file (entries not in the library)
    notes        TEXT,
    added_at     TEXT NOT NULL DEFAULT (datetime('now'))
);
-- What a location looked like at its last real scan, so it can be re-processed without
-- the drive (scan/fs.py): one row per folder, entries as JSON [[name, is_dir, size, mtime]].
CREATE TABLE IF NOT EXISTS snapshots (
    root_id  INTEGER PRIMARY KEY REFERENCES roots(id) ON DELETE CASCADE,
    taken_at TEXT NOT NULL,
    files    INTEGER NOT NULL,
    probes   TEXT                  -- disc header results: {rel path: {"sniff": .., "disc": ..}}
);
CREATE TABLE IF NOT EXISTS snapshot_dirs (
    root_id INTEGER NOT NULL REFERENCES roots(id) ON DELETE CASCADE,
    rel     TEXT NOT NULL,
    entries TEXT NOT NULL,
    PRIMARY KEY (root_id, rel)
) WITHOUT ROWID;

-- What happened to an item's files: copied / moved between drives (transfer.py).
CREATE TABLE IF NOT EXISTS item_history (
    item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    at      TEXT NOT NULL,
    action  TEXT NOT NULL,
    detail  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS completions_year ON completions(substr(completed_on, 1, 4));
"""


# Columns added after the first release: (table, column, definition).
MIGRATIONS = [
    ("items", "kind", "TEXT"),
    ("items", "season", "INTEGER"),
    ("items", "detected_how", "TEXT"),
    ("media_files", "season", "INTEGER"),
    ("media_files", "episode", "INTEGER"),
    ("items", "source", "TEXT NOT NULL DEFAULT 'disk'"),
    ("items", "steam_appid", "INTEGER"),
    ("items", "playtime_min", "REAL"),
    ("items", "last_played", "TEXT"),
    ("items", "install_state", "TEXT"),     # games: installed / installer / image
    ("items", "launch_path", "TEXT"),       # volume-relative file Play starts (detected)
    ("items", "user_launch", "TEXT"),       # your choice instead (path; absolute if elsewhere)
    ("items", "launch_args", "TEXT"),       # extra command-line arguments
    ("items", "last_launched", "TEXT"),     # last time Play started it
    ("items", "launch_count", "INTEGER NOT NULL DEFAULT 0"),
    ("items", "last_episode", "INTEGER"),   # series: last episode played from here
    ("items", "last_episode_season", "INTEGER"),
    ("items", "last_watched", "TEXT"),      # when it was played
    ("items", "playtime_local_min", "REAL"),  # time in games started from Tracking
    ("completions", "hours", "REAL"),       # play time when you finished it (Steam / timed)
    ("items", "copy_of", "INTEGER"),        # a copy made with Copy to… (of this item)
    ("items", "plan_state", "TEXT"),        # Planning board: next / playing / hold / dropped
    ("items", "plan_position", "INTEGER"),  # order within "Up next"
    ("items", "plan_changed", "TEXT"),      # when it last moved on the board
    ("items", "cover_pinned", "INTEGER NOT NULL DEFAULT 0"),  # a cover you picked: refetch keeps it
]


def _migrate(conn: sqlite3.Connection) -> None:
    for table, column, ddl in MIGRATIONS:
        cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        if column not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
    conn.commit()


def _seed_completions(conn: sqlite3.Connection) -> None:
    """Once per library: items already marked completed start the Completed log."""
    if conn.execute("SELECT 1 FROM settings WHERE key='completions_seeded'").fetchone():
        return
    try:
        conn.execute(
            "INSERT INTO completions(item_id, title, category, platform, completed_on) "
            "SELECT id, COALESCE(user_title, title), COALESCE(user_category, category), "
            "COALESCE(user_platform, platform), substr(COALESCE(completed_at, datetime('now')), 1, 10) "
            "FROM items WHERE completed=1 AND source != 'wish'")
    except sqlite3.OperationalError as e:  # a pre-1.0 items table without those columns
        log.debug("no completions to seed: %s", e)
        return
    conn.execute("INSERT INTO settings(key, value) VALUES('completions_seeded', '1')")
    conn.commit()


_ready: set[str] = set()       # database files whose schema is set up in this process
_ready_lock = threading.Lock()


def connect(db_file: Path | None = None) -> sqlite3.Connection:
    """A new connection (one per thread). Schema creation and migrations run on
    the first connection to each file only, so parallel workers don't all try
    to write the schema at once."""
    path = Path(db_file or paths.db_path())
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    key = str(path.resolve())
    with _ready_lock:
        if key not in _ready or not path.exists():
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA)
            _migrate(conn)
            _seed_completions(conn)
            _ready.add(key)
    return conn


@contextmanager
def session(db_file: Path | None = None) -> Iterator[sqlite3.Connection]:
    conn = connect(db_file)
    try:
        with conn:
            yield conn
    finally:
        conn.close()


# --- settings -----------------------------------------------------------------

def get_setting(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row and row["value"] is not None else default


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO settings(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


# --- items --------------------------------------------------------------------

@dataclass
class Item:
    id: int
    volume_uid: str
    rel_path: str
    is_dir: bool
    category: str
    platform: str | None
    title: str
    year: int | None
    file_count: int
    size_bytes: int
    duration_sec: float | None
    hltb_hours: float | None
    hltb_extra: float | None
    hltb_complete: float | None
    completed: bool
    completed_at: str | None
    notes: str | None
    kind: str | None
    season: int | None
    detected_how: str | None
    source: str
    steam_appid: int | None
    playtime_min: float | None
    last_played: str | None
    rating: float | None
    rating_max: float | None
    rating_source: str | None
    external_url: str | None
    poster: str | None
    summary: str | None
    missing: bool
    detected_title: str
    video_count: int
    scores: list[Score] = field(default_factory=list)  # every site's score, best-known first
    install_state: str | None = None
    launch_path: str | None = None       # what Play starts: your choice, else the detected one
    launch_args: str | None = None
    last_launched: str | None = None
    launch_count: int = 0
    last_episode: int | None = None      # series: last episode played from here
    last_episode_season: int | None = None
    last_watched: str | None = None
    plan_state: str | None = None        # Planning board column (None = not on the board)
    external_id: str | None = None       # the matched service's id (IMDb tt…, TVmaze, Steam…)
    plan_position: int | None = None
    root_id: int | None = None           # the location it was found in (Locations filter)

    @property
    def avg_score(self) -> float | None:
        """Average of all scores on a 0-100 scale."""
        return score_math.average(self.scores)

    @property
    def best_score(self) -> float | None:
        return score_math.best(self.scores)

    @property
    def total_hours(self) -> float | None:
        if self.category == "game":
            return self.hltb_hours
        return self.duration_sec / 3600 if self.duration_sec else None


# Effective values: a manual override always wins over what the scanner guessed.
ITEM_SELECT = """
SELECT id, volume_uid, rel_path, is_dir,
       COALESCE(user_category, category) AS category,
       COALESCE(user_platform, platform) AS platform,
       COALESCE(user_title, title)       AS title,
       title AS detected_title,
       year, file_count, size_bytes, duration_sec, hltb_hours, hltb_extra, hltb_complete,
       completed, completed_at, notes, kind, season, detected_how,
       source, steam_appid,
       -- Steam's play time plus sessions of games started from Tracking
       CASE WHEN playtime_min IS NULL AND playtime_local_min IS NULL THEN NULL
            ELSE COALESCE(playtime_min, 0) + COALESCE(playtime_local_min, 0) END AS playtime_min,
       last_played,
       install_state, COALESCE(user_launch, launch_path) AS launch_path, launch_args,
       last_launched, launch_count, last_episode, last_episode_season, last_watched,
       plan_state, plan_position, root_id,
       rating, rating_max, rating_source, external_id, external_url, poster, summary, missing,
       (SELECT COUNT(*) FROM media_files m WHERE m.item_id = items.id) AS video_count
FROM items
"""


def row_to_item(row: sqlite3.Row) -> Item:
    return Item(
        id=row["id"],
        volume_uid=row["volume_uid"],
        rel_path=row["rel_path"],
        is_dir=bool(row["is_dir"]),
        category=row["category"],
        platform=row["platform"],
        title=row["title"],
        year=row["year"],
        file_count=row["file_count"],
        size_bytes=row["size_bytes"],
        duration_sec=row["duration_sec"],
        hltb_hours=row["hltb_hours"],
        hltb_extra=row["hltb_extra"],
        hltb_complete=row["hltb_complete"],
        completed=bool(row["completed"]),
        completed_at=row["completed_at"],
        notes=row["notes"],
        kind=row["kind"],
        season=row["season"],
        detected_how=row["detected_how"],
        source=row["source"],
        steam_appid=row["steam_appid"],
        playtime_min=row["playtime_min"],
        last_played=row["last_played"],
        install_state=row["install_state"],
        launch_path=row["launch_path"],
        launch_args=row["launch_args"],
        last_launched=row["last_launched"],
        launch_count=row["launch_count"] or 0,
        last_episode=row["last_episode"],
        external_id=row["external_id"],
        last_episode_season=row["last_episode_season"],
        last_watched=row["last_watched"],
        plan_state=row["plan_state"],
        plan_position=row["plan_position"],
        root_id=row["root_id"],
        rating=row["rating"],
        rating_max=row["rating_max"],
        rating_source=row["rating_source"],
        external_url=row["external_url"],
        poster=row["poster"],
        summary=row["summary"],
        missing=bool(row["missing"]),
        detected_title=row["detected_title"],
        video_count=row["video_count"],
    )


def all_settings(conn: sqlite3.Connection) -> dict[str, str]:
    """Every setting (keys, emulators, filters...) as {key: value}, "" for unset."""
    return {r["key"]: r["value"] or "" for r in conn.execute("SELECT key, value FROM settings")}


def list_items(conn: sqlite3.Connection, category: str | None = None,
               platform: str | None = None) -> list[Item]:
    sql = f"SELECT * FROM ({ITEM_SELECT}) WHERE 1=1"
    args: list = []
    if category:
        sql += " AND category=?"
        args.append(category)
    if platform == UNKNOWN_PLATFORM:
        sql += " AND platform IS NULL"
    elif platform:
        sql += " AND platform=?"
        args.append(platform)
    sql += " ORDER BY title COLLATE NOCASE"
    items = [row_to_item(r) for r in conn.execute(sql, args)]
    _attach_scores(conn, items)
    return items


def get_item(conn: sqlite3.Connection, item_id: int) -> Item | None:
    row = conn.execute(f"{ITEM_SELECT} WHERE id=?", (item_id,)).fetchone()
    if not row:
        return None
    item = row_to_item(row)
    _attach_scores(conn, [item])
    return item


def _attach_scores(conn: sqlite3.Connection, items: list[Item]) -> None:
    if not items:
        return
    by_item: dict[int, list[Score]] = defaultdict(list)
    if len(items) == 1:
        rows = conn.execute("SELECT * FROM scores WHERE item_id=?", (items[0].id,))
    else:
        rows = conn.execute("SELECT * FROM scores")  # one query for the whole grid
    for r in rows:
        by_item[r["item_id"]].append(Score(r["source"], r["value"], r["max_value"], r["url"]))
    for it in items:
        it.scores = score_math.ordered(by_item.get(it.id, []), it.category)


@dataclass
class Score:
    source: str
    value: float
    max_value: float
    url: str | None


def get_scores(conn: sqlite3.Connection, item_id: int) -> list[Score]:
    item = get_item(conn, item_id)
    return item.scores if item else []


@dataclass
class EpisodeRow:
    season: int
    number: int
    title: str | None
    aired: str | None
    runtime_min: float | None
    score: float | None
    score_max: float | None
    source: str
    url: str | None
    note: str | None
    on_disk: bool


def get_episodes(conn: sqlite3.Connection, item_id: int) -> list[EpisodeRow]:
    """The item's episode list, marking the ones whose file is in the library."""
    have = {(r["season"] or 1, r["episode"]) for r in conn.execute(
        "SELECT season, episode FROM media_files WHERE item_id=? AND episode IS NOT NULL", (item_id,))}
    return [EpisodeRow(r["season"], r["number"], r["title"], r["aired"], r["runtime_min"],
                       r["score"], r["score_max"], r["source"], r["url"], r["note"],
                       (r["season"], r["number"]) in have)
            for r in conn.execute("SELECT * FROM episodes WHERE item_id=? ORDER BY season, number",
                                  (item_id,))]


def set_completed(conn: sqlite3.Connection, item_ids: list[int], done: bool) -> None:
    conn.executemany(
        "UPDATE items SET completed=?, completed_at=CASE WHEN ? THEN "
        "COALESCE(completed_at, datetime('now')) END WHERE id=?",
        [(int(done), int(done), i) for i in item_ids],
    )


WANTED = "wish"  # items.source of "Want to acquire" entries
STEAM = "steam"  # items.source of games from a Steam account: their own section
OWN_SECTION = (WANTED, STEAM)  # sources shown in their own sidebar section, not the library


def in_library(source: str) -> bool:
    """Counted in the library sections (Anime, Games...), not a section of its own."""
    return source not in OWN_SECTION


def category_counts(conn: sqlite3.Connection, wanted: bool = False) -> dict[str, int]:
    """Items per section: the library (default) or the "Want to acquire" list."""
    if wanted:
        where, args = "source = ?", (WANTED,)
    else:
        where, args = "source NOT IN (?, ?)", OWN_SECTION
    rows = conn.execute(
        f"SELECT category, COUNT(*) n FROM ({ITEM_SELECT}) WHERE {where} GROUP BY category", args)
    return {r["category"]: r["n"] for r in rows}


def source_count(conn: sqlite3.Connection, source: str) -> int:
    return conn.execute("SELECT COUNT(*) n FROM items WHERE source=?", (source,)).fetchone()["n"]


def platform_counts(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute(
        f"SELECT COALESCE(platform, '{UNKNOWN_PLATFORM}') p,COUNT(*) n FROM ({ITEM_SELECT}) "
        "WHERE category='game' AND source NOT IN (?, ?) GROUP BY p ORDER BY p", OWN_SECTION
    )
    return {r["p"]: r["n"] for r in rows}
