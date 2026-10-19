"""Walking a location and turning folders / files into items.

  * Folders named after a category ("Anime", "Movies", "TV Shows", "Games") or a
    platform ("PS2", "Sony - PlayStation 2", "PC Games") are containers: we walk
    into them and remember the hint. So is any folder holding 3+ releases.
  * Any other folder inside a container is an item (a series, a movie, a game).
  * Loose files inside a container are grouped by title, so
    "Game (Disc 1).cue" + "Game (Disc 2).cue" or "[Grp] Show - 01..12.mkv"
    each become a single item.
"""

from __future__ import annotations

import logging
import os
from typing import Callable, Iterator

from .. import titles
from . import classify, discid
from .classify import ARCHIVE_EXT, DISC_EXT, ROM_EXT, VIDEO_EXT, Hints
from .model import GROUP_SEP, FileEntry, FoundItem, vol_rel
from .videos import (
    EXTRA_DIRS, display_title, group_videos, is_extra_dir, is_extra_file, is_extra_path, season_tag,
)

log = logging.getLogger(__name__)

MAX_DEPTH = 8
_RELEVANT_LOOSE = VIDEO_EXT | DISC_EXT | ARCHIVE_EXT | set(ROM_EXT)


def _is_sample(name: str) -> bool:
    low = name.lower()
    return "sample" in low or "trailer" in low


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
                    and not is_extra_path(rel_inner)):
                videos.append((FileEntry(vol_rel(dir_rel, rel_inner), st.st_size, st.st_mtime),
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
        name = titles.strip_order_prefix(name)
        title = titles.addon_base_title(name) if addon else titles.clean_game_title(name)
        platform, title, reason = _ask_disc(d.platform, title, d.reason,
                                            [p for _, p in pairs], addon)
        item = FoundItem(dir_rel, True, "game", platform, title, None,
                         [], count, total, kind="addon" if addon else None, reason=reason)
        log.debug("game %r [%s] <- %s (%s)", item.title, d.platform or "?", dir_rel, d.reason)
        return [item] + extra

    groups = group_videos(videos)
    folder = titles.parse_video(titles.strip_order_prefix(name))
    if len(groups) <= 1:
        g = groups[0] if groups else None
        kind = g.kind if g else folder.kind
        season = g.season if g else folder.season
        if folder.season and kind == "series":
            season = folder.season
        # A folder called "Season 2" says nothing about the show's name.
        # A folder called "Season 2" (or one that still parses to just a number)
        # says nothing about the show's name: use what the video files say.
        weak = titles.season_from_dir(name) is not None or titles.is_weak_title(folder.title)
        # "1a. MSG 0079" in a collection pack: abbreviated; its files say "Mobile Suit GUNDAM 0079".
        in_pack = titles.strip_order_prefix(name) != name
        use_files = g and not titles.is_weak_title(g.title) and (
            weak or (in_pack and len(g.title) > len(folder.title)))
        base = g.title if use_files else folder.title
        if g and titles.normalize(g.title) and titles.normalize(g.title) == titles.normalize(folder.title):
            base = folder.title  # "86 (Eighty Six)/[Judas] 86 - S01E01.mkv": "86" really is the name
        elif titles.is_weak_title(base):  # "4b. OVA (2005)" holding "01.mkv": the collection's name
            base = _folder_title(base, dir_rel)
        item = FoundItem(dir_rel, True, d.category, None, display_title(base, kind, season),
                         folder.year or (g.year if g else None),
                         [e for e, _ in g.files] if g else [], count, total,
                         kind=kind, season=season, reason=d.reason,
                         series_key=g.key if g and kind == "series" else None)
        log.debug("%s %s %r%s, %d videos <- %s (%s)", d.category, kind, item.title, season_tag(season),
                  len(item.videos), dir_rel, d.reason)
        return [item]

    # Several seasons / movies in one folder: one item each.
    items = []
    for g in groups:
        size = sum(e.size for e, _ in g.files)
        base = g.title
        if titles.is_weak_title(base):
            base = folder.title if not titles.is_weak_title(folder.title) else _folder_title(base, dir_rel)
        item = FoundItem(f"{dir_rel}{GROUP_SEP}{g.slug}", True, d.category, None,
                         display_title(base, g.kind, g.season), g.year or folder.year,
                         [e for e, _ in g.files], len(g.files), size,
                         kind=g.kind, season=g.season, reason=d.reason + "; split by season/movie",
                         series_key=g.key if g.kind == "series" else None)
        log.debug("%s %s %r%s, %d videos <- %s (split)", d.category, g.kind, item.title,
                  season_tag(g.season), len(item.videos), item.rel_path)
        items.append(item)
    return items


_NAMELESS = {"copy", "newfolder", "game", "games", "disc", "disc1", "iso", "rom", "backup", "image"}


def _ask_disc(platform: str | None, title: str, reason: str, paths: list[str],
              addon: bool) -> tuple[str | None, str, str]:
    """When the platform is unknown or the name says nothing ("copy"), read the disc
    itself (SYSTEM.CNF / PARAM.SFO / Nintendo header) like an emulator would."""
    weak = titles.is_weak_title(title) or titles.normalize(title) in _NAMELESS
    if addon or (platform and not weak):
        return platform, title, reason
    images = [p for p in paths if p.lower().endswith(discid.IMAGE_EXT + (".zip",))]
    for path in sorted(images, key=lambda p: -_size(p))[:2]:
        info = discid.identify(path)
        if not info:
            continue
        if weak and info.title:
            title = titles.clean_game_title(info.title)
        platform = platform or info.platform
        return platform, title, f"{reason}; {info.describe()}"
    return platform, title, reason


def _size(path: str) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def _folder_title(fallback: str, dir_rel: str) -> str:
    """The nearest folder above ``dir_rel`` with a real name ("1b" / "OVA" don't count)."""
    for part in reversed(dir_rel.split("/")[:-1]):
        t = titles.parse_video(titles.strip_order_prefix(part)).title
        if not titles.is_weak_title(t):
            return t
    return fallback


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
            log.debug("skip %s: scrambled name / tool / firmware", vol_rel(dir_rel, e.name))
            continue
        try:
            stats[e.name] = e.stat()
        except OSError:
            continue
        if ext in VIDEO_EXT:
            if is_extra_file(e.name):
                continue
            st = stats[e.name]
            vids.append((FileEntry(vol_rel(dir_rel, e.name), st.st_size, st.st_mtime), e.name))
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
        # "01.mkv" files carry no title: the folder they sit in names the show.
        base = g.title
        if titles.is_weak_title(base) and dir_rel:
            base = _folder_title(base, vol_rel(dir_rel, "x"))
        item = FoundItem(vol_rel(dir_rel, names[0]), False, d.category, None,
                         display_title(base, g.kind, g.season), g.year,
                         [e for e, _ in g.files], len(g.files), sum(e.size for e, _ in g.files),
                         kind=g.kind, season=g.season, reason=d.reason + "; loose files",
                         series_key=g.key if g.kind == "series" else None)
        log.debug("%s %s %r%s, %d loose videos in %s", d.category, g.kind, item.title,
                  season_tag(g.season), len(names), dir_rel or "/")
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
                            _path_platform(vol_rel(dir_rel, first.name)), name_is_game=is_game)
        if not d:
            log.debug("skip %s: archive/disc without platform hints", vol_rel(dir_rel, first.name))
            continue
        stem = stems[first.name]
        title = titles.addon_base_title(stem) if only_addons else titles.clean_game_title(stem)
        platform, reason = d.platform, d.reason
        if d.category == "game":
            platform, title, reason = _ask_disc(platform, title, reason,
                                                [m.path for m in members], only_addons)
        log.debug("%s%s %r [%s] <- %s (%s)", d.category, " add-on for" if only_addons else "", title,
                  platform or "?", vol_rel(dir_rel, first.name), reason)
        yield FoundItem(vol_rel(dir_rel, first.name), False, d.category, platform, title, None,
                        [], len(members), sum(stats[m.name].st_size for m in members),
                        kind="addon" if only_addons else None, reason=reason)


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
        if hints.category != "game" and is_extra_dir(e.name):
            log.debug("skip %s: extras", vol_rel(dir_rel, e.name))
            continue
        rel = vol_rel(dir_rel, e.name)
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


def root_hints(category: str, platform: str | None, rel_path: str) -> Hints:
    hints = Hints(category=None if category == "auto" else category, platform=platform)
    for part in [p for p in rel_path.split("/") if p]:
        hints, _ = hints.child(part)
    if category != "auto":
        hints = Hints(category=category, platform=platform or hints.platform)
    return hints
