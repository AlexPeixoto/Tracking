"""Search cover…: game covers for any name, from every service that has them.

The keyless services the scan uses answer always (HowLongToBeat, the Steam store's portrait
library art for PC, libretro box art for retro platforms); IGDB joins when its Client ID and
Secret are set, with a cover per edition and port. Before, the window searched IGDB only, so
without a key it found nothing although the scan found covers fine.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor

from . import hltb, igdb, libretro, steam
from .base import Candidate, Query, similarity

# Steam: portrait art is checked per game (a HEAD request, cached), so only the first few.
STEAM_RESULTS = 8


def _igdb(title: str, platform: str | None, settings: Mapping[str, str]) -> list[Candidate]:
    if not igdb.configured(dict(settings)):
        return []
    return [Candidate(c.title, c.year, f"IGDB · {c.detail}" if c.detail else "IGDB", c.image_url, c.url)
            for c in igdb.search_covers(title, dict(settings))]


def _hltb(title: str, platform: str | None, settings: Mapping[str, str]) -> list[Candidate]:
    return [c for c in hltb.search(Query(title, category="game")) if c.image_url]


def _steam(title: str, platform: str | None, settings: Mapping[str, str]) -> list[Candidate]:
    if platform not in (None, "PC"):
        return []
    out = []
    for c in steam.search(Query(title, category="game"))[:STEAM_RESULTS]:
        art = steam.library_art(c.steam_appid) if c.steam_appid else None
        if art:
            out.append(Candidate(c.title, c.year, c.detail, art, c.url, c.steam_appid))
    return out


def _libretro(title: str, platform: str | None, settings: Mapping[str, str]) -> list[Candidate]:
    return libretro.search_covers(title, platform)


# In this order when titles are as close: IGDB's are the best scans, libretro's the right box.
SOURCES: list[tuple[str, Callable[[str, str | None, Mapping[str, str]], list[Candidate]]]] = [
    ("IGDB", _igdb), ("libretro", _libretro), ("HowLongToBeat", _hltb), ("Steam", _steam)]


def search(title: str, platform: str | None, settings: Mapping[str, str],
           limit: int = 60) -> tuple[str, list[Candidate]]:
    """(the services that answered, their covers with the closest names first).

    A service that fails is skipped; if every one fails, the first error is raised."""
    with ThreadPoolExecutor(len(SOURCES)) as pool:
        jobs = [(label, pool.submit(fn, title, platform, settings)) for label, fn in SOURCES]
    found: list[Candidate] = []
    labels: list[str] = []
    errors: list[Exception] = []
    for label, job in jobs:
        try:
            got = job.result()
        except Exception as e:
            errors.append(e)
            continue
        if got:
            labels.append(label)
            found += got
    if errors and not found:
        raise errors[0]
    best: list[Candidate] = []
    seen: set[str] = set()
    for c in sorted(found, key=lambda c: -similarity(title, c.title)):  # stable: SOURCES order on ties
        if c.image_url and c.image_url not in seen:
            seen.add(c.image_url)
            best.append(c)
    return " + ".join(labels), best[:limit]
