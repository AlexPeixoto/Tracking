"""Walk a library root and turn folders / files into catalogue items.

Layout rules
  * Folders named after a category ("Anime", "Movies", "Games") or a platform
    ("PS2", "Sony - PlayStation 2", "PC Games") are containers: we walk into them
    and remember the hint.
  * Any other folder inside a container is an item (a series, a movie, a game).
  * Loose files inside a container are grouped by title, so
    "Game (Disc 1).cue" + "Game (Disc 2).cue" or "[Grp] Show - 01..12.mkv"
    each become a single item.

Video grouping (anime / series)
  * Every video file is parsed for title, season and episode. Files without
    episode numbers are movies and are never merged with a series.
  * One item per (series, season): a folder holding "Season 1" and "Season 2"
    becomes two items, and so does a folder of S01Exx + S02Exx files.
  * Episodes of the same series + season found in different places of one
    location (a folder plus a few loose files) are merged into one item, so the
    episode count and total runtime cover all of them.
  * Extras (NCOP / NCED / menus / trailers...) are not counted as episodes.

Rel paths with "::" mark one group inside a folder ("Show::s2"); ':' can't
appear in real file names on Windows, so these never clash with real paths.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Iterator

from rapidfuzz import fuzz

from . import classify, db, titles, volumes
from .classify import ARCHIVE_EXT, DISC_EXT, ROM_EXT, VIDEO_EXT, Hints

log = logging.getLogger(__name__)

MAX_DEPTH = 8
GROUP_SEP = "::"
_RELEVANT_LOOSE = VIDEO_EXT | DISC_EXT | ARCHIVE_EXT | set(ROM_EXT)
EXTRA_DIRS = {
    "extras", "extra", "bonus", "bonuses", "nc", "ncop", "nced", "ncop & nced", "creditless",
    "menu", "menus", "trailers", "trailer", "pv", "cm", "cms", "previews", "sample", "samples",
    "featurettes", "behind the scenes", "deleted scenes", "interviews", "scans", "fonts",
    "subs", "subtitles", "ost", "soundtrack", "other", "others",
}


class VolumeOffline(Exception):
    pass


@dataclass
class FileEntry:
    rel_path: str  # relative to the volume
    size: int
    mtime: float


@dataclass
class FoundItem:
    rel_path: str
    is_dir: bool
    category: str
    platform: str | None
    title: str
    year: int | None
    videos: list[FileEntry] = field(default_factory=list)
    file_count: int = 0
    size_bytes: int = 0
    kind: str | None = None        # 'series' | 'movie' for video items
    season: int | None = None
    reason: str = ""
    series_key: str | None = None  # merge key for episodes found in several places


@dataclass
class ScanResult:
    found: int = 0
    new: int = 0
    missing: int = 0


def fs_path(rel_path: str) -> str:
    """The on-disk part of an item path (drops the "::group" suffix)."""
    return rel_path.split(GROUP_SEP, 1)[0]


def _vol_rel(base_rel: str, name: str) -> str:
    return f"{base_rel}/{name}" if base_rel else name


def _is_sample(name: str) -> bool:
    low = name.lower()
    return "sample" in low or "trailer" in low


def _is_extra_path(inner_rel: str) -> bool:
    parts = inner_rel.lower().split("/")[:-1]
    return any(p in EXTRA_DIRS or p.startswith(("nc ", "extras", "bonus")) for p in parts)


# --------------------------------------------------------------------------- video groups

@dataclass
class _Group:
    kind: str
    title: str
    season: int | None
    year: int | None
    files: list[tuple[FileEntry, str]] = field(default_factory=list)  # (entry, inner rel)

    @property
    def key(self) -> str:
        if self.kind == "series":
            return f"{titles.normalize(self.title)}|s{self.season or 1}"
        return f"{titles.normalize(self.title)}|m{self.year or ''}"

    @property
    def slug(self) -> str:
        if self.kind == "series":
            return f"s{self.season or 1}"
        return f"movie-{titles.normalize(self.title)[:40]}"


def _video_info(inner_rel: str) -> titles.VideoInfo:
    name = inner_rel.rsplit("/", 1)[-1]
    info = titles.parse_video(name)
    if info.season is None:
        parent = inner_rel.rsplit("/", 2)[-2] if "/" in inner_rel else ""
        season = titles.season_from_dir(parent) if parent else None
        if season is not None:
            info = titles.VideoInfo(info.title, info.year, season, info.episode, "series")
    return info


def group_videos(videos: list[tuple[FileEntry, str]]) -> list[_Group]:
    """Split video files into series seasons and movies."""
    groups: list[_Group] = []
    for entry, inner in videos:
        info = _video_info(inner)
        season = (info.season or 1) if info.kind == "series" else None
        target = None
        for g in groups:
            if g.kind != info.kind or (info.kind == "series" and g.season != season):
                continue
            same = titles.normalize(g.title) == titles.normalize(info.title)
            if same or fuzz.ratio(titles.normalize(g.title), titles.normalize(info.title)) >= 90:
                target = g
                break
        if target is None:
            target = _Group(info.kind, info.title, season, info.year)
            groups.append(target)
        target.files.append((entry, inner))
    # Season 0 / specials ride along with the first season of the same show.
    for g in [g for g in groups if g.kind == "series" and g.season == 0]:
        host = next((h for h in groups if h is not g and h.kind == "series" and h.season
                     and titles.normalize(h.title) == titles.normalize(g.title)), None)
        if host:
            host.files += g.files
            groups.remove(g)
    groups.sort(key=lambda g: (g.kind != "series", g.season or 0, g.title))
    return groups


def _season_tag(season: int | None) -> str:
    return f" S{season}" if season else ""


def _display_title(base: str, kind: str | None, season: int | None) -> str:
    if kind == "series" and season and season > 1 and titles.season_from_dir(base) is None:
        return f"{base} Season {season}"
    return base


# --------------------------------------------------------------------------- items

def _gather(dir_abs: str, dir_rel: str):
    """All files under an item folder: (inner rel, abs) pairs, (video, inner) pairs, count, bytes."""
    pairs, videos, count, total = [], [], 0, 0
    for cur, dirs, files in os.walk(dir_abs, onerror=lambda e: None):
        dirs[:] = [d for d in dirs if not classify.is_skipped(d)]
        inner = os.path.relpath(cur, dir_abs).replace("\\", "/")
        inner = "" if inner == "." else inner
        for f in files:
            if classify.is_junk_file(f):
                continue
            p = os.path.join(cur, f)
            try:
                st = os.stat(p)
            except OSError:
                continue
            count += 1
            total += st.st_size
            rel_inner = f"{inner}/{f}" if inner else f
            pairs.append((rel_inner, p))
            if (os.path.splitext(f)[1].lower() in VIDEO_EXT and not _is_sample(f)
                    and not _is_extra_path(rel_inner)):
                videos.append((FileEntry(_vol_rel(dir_rel, rel_inner), st.st_size, st.st_mtime),
                               rel_inner))
    return pairs, videos, count, total


def _path_platform(rel: str) -> str | None:
    return classify.platform_from_tokens(*rel.split("/"))


def _stray_roms(pairs: list[tuple[str, str]], folder_name: str) -> set[str]:
    """Top-level ROM files that belong to a *different* game than the folder,
    e.g. Zelda .nsp files dropped into a Fatal Frame PC repack folder."""
    folder_key = titles.normalize(titles.clean_game_title(folder_name))
    has_other_game_files = any(
        os.path.splitext(r)[1].lower() in (classify.PC_EXT | DISC_EXT) for r, _ in pairs)
    if not has_other_game_files or len(folder_key) < 3:
        return set()
    strays = set()
    for rel, _ in pairs:
        stem, ext = os.path.splitext(rel)
        if "/" in rel or ext.lower() not in ROM_EXT or classify.is_junk_file(rel):
            continue
        key = titles.normalize(titles.clean_game_title(stem))
        if key and not (key.startswith(folder_key) or folder_key.startswith(key)):
            strays.add(rel)
    return strays


def _dir_items(dir_abs: str, dir_rel: str, name: str, hints: Hints) -> list[FoundItem]:
    pairs, videos, count, total = _gather(dir_abs, dir_rel)
    strays = _stray_roms(pairs, name)
    extra: list[FoundItem] = []
    if strays:
        log.debug("%s: %d files belong to other games: %s", dir_rel, len(strays), sorted(strays))
        with os.scandir(dir_abs) as it:
            extra = list(_loose_items([e for e in it if e.name in strays], dir_rel, hints))
        pairs = [p for p in pairs if p[0] not in strays]
        count -= len(strays)
        total -= sum(i.size_bytes for i in extra)
    sig = classify.inspect_files(pairs)
    is_game, _ = classify.game_name_evidence(name)
    d = classify.decide(hints, sig, classify.platform_from_tags(name), _path_platform(dir_rel),
                        name_is_game=is_game)
    if not d:
        log.debug("skip %s: no media found", dir_rel)
        return []
    if d.category == "game":
        addon = _is_addon(name)
        title = titles.addon_base_title(name) if addon else titles.clean_game_title(name)
        item = FoundItem(dir_rel, True, "game", d.platform, title, None,
                         [], count, total, kind="addon" if addon else None, reason=d.reason)
        log.debug("game %r [%s] <- %s (%s)", item.title, d.platform or "?", dir_rel, d.reason)
        return [item] + extra

    groups = group_videos(videos)
    folder = titles.parse_video(name)
    if len(groups) <= 1:
        g = groups[0] if groups else None
        kind = g.kind if g else folder.kind
        season = g.season if g else folder.season
        if folder.season and kind == "series":
            season = folder.season
        # A folder called "Season 2" says nothing about the show's name.
        base = g.title if (g and titles.season_from_dir(name) is not None) else folder.title
        item = FoundItem(dir_rel, True, d.category, None, _display_title(base, kind, season),
                         folder.year or (g.year if g else None),
                         [e for e, _ in g.files] if g else [], count, total,
                         kind=kind, season=season, reason=d.reason,
                         series_key=g.key if g and kind == "series" else None)
        log.debug("%s %s %r%s, %d videos <- %s (%s)", d.category, kind, item.title, _season_tag(season),
                  len(item.videos), dir_rel, d.reason)
        return [item]

    # Several seasons / movies in one folder: one item each.
    items = []
    for g in groups:
        size = sum(e.size for e, _ in g.files)
        item = FoundItem(f"{dir_rel}{GROUP_SEP}{g.slug}", True, d.category, None,
                         _display_title(g.title, g.kind, g.season), g.year,
                         [e for e, _ in g.files], len(g.files), size,
                         kind=g.kind, season=g.season, reason=d.reason + "; split by season/movie",
                         series_key=g.key if g.kind == "series" else None)
        log.debug("%s %s %r%s, %d videos <- %s (split)", d.category, g.kind, item.title,
                  _season_tag(g.season), len(item.videos), item.rel_path)
        items.append(item)
    return items


def _loose_items(entries: list[os.DirEntry], dir_rel: str, hints: Hints) -> Iterator[FoundItem]:
    vids: list[tuple[FileEntry, str]] = []
    game_groups: dict[str, list[os.DirEntry]] = {}
    stats: dict[str, os.stat_result] = {}
    for e in entries:
        ext = os.path.splitext(e.name)[1].lower()
        stem = os.path.splitext(e.name)[0]
        if ext not in _RELEVANT_LOOSE or _is_sample(e.name) or classify.is_junk_file(e.name):
            continue
        if classify.looks_random(stem) or classify.is_tool_name(stem):
            log.debug("skip %s: scrambled name / tool / firmware", _vol_rel(dir_rel, e.name))
            continue
        try:
            stats[e.name] = e.stat()
        except OSError:
            continue
        if ext in VIDEO_EXT:
            st = stats[e.name]
            vids.append((FileEntry(_vol_rel(dir_rel, e.name), st.st_size, st.st_mtime), e.name))
        else:
            title = titles.addon_base_title(stem) if _is_addon(stem) else titles.clean_game_title(stem)
            game_groups.setdefault(titles.normalize(title) or e.name, []).append(e)

    by_name = {e.name: e for e in entries}
    for g in group_videos(vids):
        names = sorted(inner for _, inner in g.files)
        sig = classify.inspect_files([(n, by_name[n].path) for n in names])
        d = classify.decide(hints, sig)
        if not d:
            continue
        item = FoundItem(_vol_rel(dir_rel, names[0]), False, d.category, None,
                         _display_title(g.title, g.kind, g.season), g.year,
                         [e for e, _ in g.files], len(g.files), sum(e.size for e, _ in g.files),
                         kind=g.kind, season=g.season, reason=d.reason + "; loose files",
                         series_key=g.key if g.kind == "series" else None)
        log.debug("%s %s %r%s, %d loose videos in %s", d.category, g.kind, item.title,
                  _season_tag(g.season), len(names), dir_rel or "/")
        yield item

    for members in game_groups.values():
        stems = {m.name: os.path.splitext(m.name)[0] for m in members}
        # The game itself first; updates / DLC after it.
        members.sort(key=lambda m: (_is_addon(stems[m.name]), m.name))
        first = members[0]
        only_addons = _is_addon(stems[first.name])
        is_game = any(classify.game_name_evidence(m.name)[0] for m in members)
        tag_platform = next((p for p in (classify.platform_from_tags(stems[m.name]) for m in members) if p), None)
        sig = classify.inspect_files([(m.name, m.path) for m in members])
        d = classify.decide(hints, sig, tag_platform,
                            _path_platform(_vol_rel(dir_rel, first.name)), name_is_game=is_game)
        if not d:
            log.debug("skip %s: archive/disc without platform hints", _vol_rel(dir_rel, first.name))
            continue
        stem = stems[first.name]
        title = titles.addon_base_title(stem) if only_addons else titles.clean_game_title(stem)
        log.debug("%s%s %r [%s] <- %s (%s)", d.category, " add-on for" if only_addons else "", title,
                  d.platform or "?", _vol_rel(dir_rel, first.name), d.reason)
        yield FoundItem(_vol_rel(dir_rel, first.name), False, d.category, d.platform, title, None,
                        [], len(members), sum(stats[m.name].st_size for m in members),
                        kind="addon" if only_addons else None, reason=d.reason)


def _has_keyword_child(dir_abs: str) -> bool:
    try:
        with os.scandir(dir_abs) as it:
            for e in it:
                if e.is_dir(follow_symlinks=False) and (
                    classify.platform_from_name(e.name) or classify.category_from_name(e.name)
                ):
                    return True
    except OSError:
        pass
    return False


def _looks_like_container(dir_abs: str) -> bool:
    """A folder with an arbitrary name ("tpex", "stuff") that holds several releases.

    It's a container when it has 3+ sub-folders that aren't part of one release
    (not MD5 / Data / Subs / Season N / extras...), and no .exe of its own
    unless the sub-folders clearly outnumber that (5+).
    """
    subdirs, has_exe = 0, False
    try:
        with os.scandir(dir_abs) as it:
            for e in it:
                if e.is_dir(follow_symlinks=False):
                    n = e.name
                    if not (classify.is_skipped(n) or classify.is_junk_subdir(n)
                            or titles.season_from_dir(n) is not None or n.lower() in EXTRA_DIRS):
                        subdirs += 1
                elif e.name.lower().endswith(".exe"):
                    has_exe = True
    except OSError:
        return False
    return subdirs >= 5 or (subdirs >= 3 and not has_exe)


def _is_addon(stem: str) -> bool:
    return titles.is_addon_name(stem) or classify.switch_addon(stem)


def walk(dir_abs: str, dir_rel: str, hints: Hints, depth: int = 0,
         progress: Callable[[str], None] | None = None,
         cancelled: Callable[[], bool] = lambda: False) -> Iterator[FoundItem]:
    try:
        with os.scandir(dir_abs) as it:
            entries = sorted(it, key=lambda e: e.name.lower())
    except OSError as e:
        log.warning("can't read %s: %s", dir_abs, e)
        return

    files = [e for e in entries if e.is_file(follow_symlinks=False)]
    yield from _loose_items(files, dir_rel, hints)

    for e in entries:
        if cancelled():
            return
        if not e.is_dir(follow_symlinks=False) or classify.is_skipped(e.name):
            continue
        rel = _vol_rel(dir_rel, e.name)
        child_hints, is_keyword = hints.child(e.name)
        container = (
            is_keyword
            or classify.is_generic_container(e.name)
            or (not hints.category and _has_keyword_child(e.path))
            or _looks_like_container(e.path)
        )
        if container and depth < MAX_DEPTH:
            log.debug("container %s -> %s", rel, child_hints)
            yield from walk(e.path, rel, child_hints, depth + 1, progress, cancelled)
            continue
        if progress:
            progress(rel)
        yield from _dir_items(e.path, rel, e.name, child_hints)


def merge_series(items: list[FoundItem]) -> list[FoundItem]:
    """Fold episodes of the same series + season found in several places into one item.

    The folder item wins as the home (largest one if several); loose groups join it.
    """
    out: list[FoundItem] = []
    homes: dict[tuple[str, str], FoundItem] = {}
    ordered = sorted(items, key=lambda i: (not i.is_dir, -len(i.videos)))
    for it in ordered:
        if it.kind != "series" or not it.series_key:
            out.append(it)
            continue
        k = (it.category, it.series_key)
        home = homes.get(k)
        if home is None:
            homes[k] = it
            out.append(it)
            continue
        log.debug("merge %s into %s (%s)", it.rel_path, home.rel_path, it.series_key)
        home.videos += it.videos
        home.file_count += it.file_count
        home.size_bytes += it.size_bytes
    return out


def merge_addons(items: list[FoundItem]) -> list[FoundItem]:
    """Updates / patches / DLC join the game they belong to (counted in its files
    and size). Add-ons for games that aren't here are dropped (logged)."""
    games = [i for i in items if i.category == "game" and i.kind != "addon"]
    keys = [(titles.normalize(g.title), g) for g in games]
    out = [i for i in items if i.kind != "addon"]
    for a in (i for i in items if i.kind == "addon"):
        ak = titles.normalize(a.title)
        home = next((g for k, g in keys if k == ak), None) or next(
            (g for k, g in keys if len(k) >= 4 and len(ak) >= 4 and (ak.startswith(k) or k.startswith(ak))),
            None)
        if home:
            log.debug("add-on %s -> %r", a.rel_path, home.title)
            home.file_count += a.file_count
            home.size_bytes += a.size_bytes
        else:
            log.debug("skip add-on %s: no %r game in this location", a.rel_path, a.title)
    return out


def merge_copies(items: list[FoundItem]) -> list[FoundItem]:
    """Two copies of the same game (a repack folder and a .rar, an .nsp and an .xci)
    or the same movie (1080p and 2160p) become one item. For movies only the
    biggest copy's video counts towards the runtime."""
    out: list[FoundItem] = []
    homes: dict[tuple, FoundItem] = {}
    for it in sorted(items, key=lambda i: (not i.is_dir, -i.size_bytes)):
        if it.category == "game" and it.kind != "addon":
            key = ("game", titles.normalize(it.title), it.platform)
        elif it.kind == "movie" and it.category in ("movie", "anime"):
            key = ("movie", it.category, titles.normalize(it.title), it.year)
        else:
            out.append(it)
            continue
        home = homes.get(key)
        if home is None:
            homes[key] = it
            out.append(it)
            continue
        log.debug("same %s as %s: %s", it.category, home.rel_path, it.rel_path)
        home.file_count += it.file_count
        home.size_bytes += it.size_bytes
    return out


def collect(dir_abs: str, dir_rel: str, hints: Hints, **kw) -> list[FoundItem]:
    """Walk + merge: everything one location contributes to the library."""
    return merge_copies(merge_addons(merge_series(list(walk(dir_abs, dir_rel, hints, **kw)))))


def root_hints(category: str, platform: str | None, rel_path: str) -> Hints:
    hints = Hints(category=None if category == "auto" else category, platform=platform)
    for part in [p for p in rel_path.split("/") if p]:
        hints, _ = hints.child(part)
    if category != "auto":
        hints = Hints(category=category, platform=platform or hints.platform)
    return hints


# --------------------------------------------------------------------------- persistence

def add_root(conn: sqlite3.Connection, path: str, category: str = "auto",
             platform: str | None = None) -> int:
    loc = volumes.identify(path)
    v = loc.volume
    conn.execute(
        "INSERT INTO volumes(uid, serial, label, last_mount) VALUES(?,?,?,?) "
        "ON CONFLICT(uid) DO UPDATE SET serial=excluded.serial, label=excluded.label, "
        "last_mount=excluded.last_mount",
        (v.uid, v.serial, v.label, v.mount),
    )
    conn.execute(
        "INSERT INTO roots(volume_uid, rel_path, category, platform) VALUES(?,?,?,?) "
        "ON CONFLICT(volume_uid, rel_path) DO UPDATE SET category=excluded.category, "
        "platform=excluded.platform",
        (v.uid, loc.rel_path, category, platform),
    )
    return conn.execute(
        "SELECT id FROM roots WHERE volume_uid=? AND rel_path=?", (v.uid, loc.rel_path)
    ).fetchone()["id"]


def mount_for(conn: sqlite3.Connection, volume_uid: str,
              known: list[volumes.Volume] | None = None) -> str | None:
    row = conn.execute("SELECT serial FROM volumes WHERE uid=?", (volume_uid,)).fetchone()
    vol = volumes.resolve(volume_uid, row["serial"] if row else None, known)
    if vol:
        conn.execute("UPDATE volumes SET last_mount=? WHERE uid=?", (vol.mount, volume_uid))
        return vol.mount
    return None


def scan_root(conn: sqlite3.Connection, root_id: int,
              progress: Callable[[str], None] | None = None,
              cancelled: Callable[[], bool] = lambda: False,
              missing_completed: bool = False) -> ScanResult:
    """Scan one location. Items no longer on disk are flagged ``missing`` (never
    deleted); with ``missing_completed`` they are also marked as completed, on the
    idea that you delete things once you've watched / finished them."""
    root = conn.execute("SELECT * FROM roots WHERE id=?", (root_id,)).fetchone()
    mount = mount_for(conn, root["volume_uid"])
    if not mount:
        raise VolumeOffline(root["volume_uid"])
    root_abs = volumes.join(mount, root["rel_path"])
    hints = root_hints(root["category"], root["platform"], root["rel_path"])
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    log.info("scanning %s (category=%s, hints=%s)", root_abs, root["category"], hints)
    result = ScanResult()
    seen: list[str] = []
    found_items = collect(root_abs, root["rel_path"], hints, progress=progress, cancelled=cancelled)
    found_paths = {f.rel_path for f in found_items}
    for found in found_items:
        result.found += 1
        seen.append(found.rel_path)
        result.new += _upsert(conn, root_id, root["volume_uid"], found, now, found_paths)
        conn.commit()

    if not cancelled():
        prev = {r["rel_path"]: r["id"] for r in conn.execute(
            "SELECT id, rel_path FROM items WHERE root_id=?", (root_id,))}
        gone = [prev[g] for g in set(prev) - set(seen)]
        result.missing = len(gone)
        conn.executemany("UPDATE items SET missing=1 WHERE id=?", [(i,) for i in gone])
        if missing_completed:
            db.set_completed(conn, gone, True)
        conn.commit()
    log.info("scan of %s done: %d items, %d new, %d missing%s", root_abs, result.found,
             result.new, result.missing, " (marked completed)" if missing_completed and gone else "")
    return result


def _upsert(conn: sqlite3.Connection, root_id: int, volume_uid: str, f: FoundItem, now: str,
            found_paths: set[str] = frozenset()) -> int:
    row = conn.execute(
        "SELECT id FROM items WHERE volume_uid=? AND rel_path=?", (volume_uid, f.rel_path)
    ).fetchone()
    if not row:
        # Moved to another drive / folder? Re-link the old entry so its completed
        # state, notes and edits follow it instead of creating a duplicate.
        # Only entries we know are gone: flagged missing, or on a connected drive
        # where the old path no longer exists (an unplugged drive may still have it).
        row = None
        for cand in conn.execute(
            "SELECT id, volume_uid, rel_path, missing FROM items WHERE category=? AND title=? "
            "AND COALESCE(platform, '')=COALESCE(?, '') AND user_category IS NULL",
            (f.category, f.title, f.platform),
        ).fetchall():
            mount = None if cand["missing"] else mount_for(conn, cand["volume_uid"])
            gone = mount and not os.path.exists(volumes.join(mount, fs_path(cand["rel_path"])))
            # A season split out of a folder keeps its entry when the layout changes.
            regrouped = (cand["volume_uid"] == volume_uid and cand["rel_path"] not in found_paths
                         and fs_path(cand["rel_path"]) == fs_path(f.rel_path))
            if cand["missing"] or gone or regrouped:
                row = cand
                break
        if row:
            log.info("re-linked %r: %s -> %s", f.title, row["rel_path"], f.rel_path)
            conn.execute("UPDATE items SET volume_uid=?, rel_path=? WHERE id=?",
                         (volume_uid, f.rel_path, row["id"]))
    if row:
        item_id = row["id"]
        conn.execute(
            "UPDATE items SET root_id=?, is_dir=?, category=?, platform=?, title=?, year=?, "
            "file_count=?, size_bytes=?, kind=?, season=?, detected_how=?, missing=0, scanned_at=? "
            "WHERE id=?",
            (root_id, f.is_dir, f.category, f.platform, f.title, f.year,
             f.file_count, f.size_bytes, f.kind, f.season, f.reason, now, item_id),
        )
        is_new = 0
    else:
        item_id = conn.execute(
            "INSERT INTO items(root_id, volume_uid, rel_path, is_dir, category, platform, title, "
            "year, file_count, size_bytes, kind, season, detected_how, scanned_at, added_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (root_id, volume_uid, f.rel_path, f.is_dir, f.category, f.platform, f.title,
             f.year, f.file_count, f.size_bytes, f.kind, f.season, f.reason, now, now),
        ).lastrowid
        is_new = 1

    # Keep measured durations for files that haven't changed.
    old = {r["rel_path"]: r for r in conn.execute(
        "SELECT rel_path, size_bytes, mtime, duration_sec FROM media_files WHERE item_id=?",
        (item_id,))}
    conn.execute("DELETE FROM media_files WHERE item_id=?", (item_id,))
    for v in f.videos:
        prev = old.get(v.rel_path)
        dur = prev["duration_sec"] if prev and prev["size_bytes"] == v.size and prev["mtime"] == v.mtime else None
        conn.execute(
            "INSERT INTO media_files(item_id, rel_path, size_bytes, mtime, duration_sec) VALUES(?,?,?,?,?)",
            (item_id, v.rel_path, v.size, v.mtime, dur),
        )
    return is_new
