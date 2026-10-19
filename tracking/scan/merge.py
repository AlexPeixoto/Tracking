"""Combining what the walk found: one item per series season, per game
(updates and DLC folded in), and per movie (duplicate copies folded in)."""

from __future__ import annotations

import logging

from .. import titles
from .classify import Hints
from .model import FoundItem
from .walk import walk

log = logging.getLogger(__name__)


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
