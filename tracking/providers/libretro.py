"""Console box art without a key: the libretro thumbnail collection (what RetroArch shows).

Each system has a public folder of Redump / No-Intro named images ("Ookami (USA).png").
Its file list is fetched once per system (cached 30 days) and matched by title, preferring
USA, then World / Europe, then Japan releases.
"""

from __future__ import annotations

import html
import re
from urllib.parse import quote, unquote

from .base import Metadata, Provider, Query, RateLimiter, best_match, cached_json, session

BASE = "https://thumbnails.libretro.com"
SYSTEMS = {
    "PS1": "Sony - PlayStation", "PS2": "Sony - PlayStation 2", "PS3": "Sony - PlayStation 3",
    "PS4": "Sony - PlayStation 4", "PSP": "Sony - PlayStation Portable",
    "PS Vita": "Sony - PlayStation Vita",
    "Saturn": "Sega - Saturn", "Dreamcast": "Sega - Dreamcast", "Mega Drive": "Sega - Mega Drive - Genesis",
    "Sega CD": "Sega - Mega-CD - Sega CD", "Master System": "Sega - Master System - Mark III",
    "Game Gear": "Sega - Game Gear", "32X": "Sega - 32X", "SG-1000": "Sega - SG-1000",
    "NES": "Nintendo - Nintendo Entertainment System",
    "SNES": "Nintendo - Super Nintendo Entertainment System", "N64": "Nintendo - Nintendo 64",
    "GameCube": "Nintendo - GameCube", "Wii": "Nintendo - Wii", "Wii U": "Nintendo - Wii U",
    "Game Boy": "Nintendo - Game Boy", "Game Boy Color": "Nintendo - Game Boy Color",
    "GBA": "Nintendo - Game Boy Advance", "DS": "Nintendo - Nintendo DS", "3DS": "Nintendo - Nintendo 3DS",
    "Virtual Boy": "Nintendo - Virtual Boy", "Pokemon Mini": "Nintendo - Pokemon Mini",
    "Xbox": "Microsoft - Xbox", "Xbox 360": "Microsoft - Xbox 360",
    "PC Engine": "NEC - PC Engine - TurboGrafx 16", "PC-FX": "NEC - PC-FX", "Neo Geo": "SNK - Neo Geo",
    "Neo Geo Pocket": "SNK - Neo Geo Pocket Color", "WonderSwan": "Bandai - WonderSwan Color",
    "Atari 2600": "Atari - 2600", "Atari 5200": "Atari - 5200", "Atari 7800": "Atari - 7800",
    "Atari Lynx": "Atari - Lynx", "Atari Jaguar": "Atari - Jaguar", "ColecoVision": "Coleco - ColecoVision",
    "Intellivision": "Mattel - Intellivision", "Vectrex": "GCE - Vectrex",
    "Commodore 64": "Commodore - 64", "Amiga": "Commodore - Amiga", "MSX": "Microsoft - MSX",
}
# Collections mix these up (a "GB" folder full of Game Boy Color games): look in the
# sibling's art too when the system's own has nothing.
_SIBLINGS = {"Game Boy": "Game Boy Color", "Game Boy Color": "Game Boy",
             "Master System": "Game Gear", "Game Gear": "Master System"}
_REGION_PREF = ("USA", "World", "Europe", "Japan")
_TAGS = re.compile(r"\s*\([^)]*\)|\s*\[[^\]]*\]")
_limit = RateLimiter(1.0)


def names(system: str) -> list[str]:
    """Every box art name for a system ("Ookami (USA)"), from its folder listing."""
    def fetch():
        _limit.wait()
        r = session().get(f"{BASE}/{quote(system)}/Named_Boxarts/", timeout=60)
        r.raise_for_status()
        return [html.unescape(unquote(n))[:-4] for n in re.findall(r'href="([^"?/]+\.png)"', r.text)]
    return cached_json("libretro", system, 30, fetch)


def base_title(name: str) -> str:
    """"Ookami (USA) (En,Fr)" -> "Ookami"; "Legend of Zelda, The - X (USA)" -> "The Legend of Zelda - X"."""
    from .. import titles

    return titles.front_article(_TAGS.sub("", name).strip())


def _region_rank(name: str) -> int:
    tags = " ".join(re.findall(r"\(([^)]*)\)", name))
    for n, region in enumerate(_REGION_PREF):
        if region in tags:
            return n
    return len(_REGION_PREF)


def lookup(q: Query) -> Metadata | None:
    found = _lookup(q.title, SYSTEMS.get(q.platform or ""))
    sibling = _SIBLINGS.get(q.platform or "")
    return found or (_lookup(q.title, SYSTEMS[sibling]) if sibling else None)


def _lookup(title: str, system: str | None) -> Metadata | None:
    if not system:
        return None
    candidates = [n for n in names(system) if "(Beta" not in n and "(Proto" not in n and "(Demo" not in n]
    # Best region first, so equal title scores pick the USA release.
    candidates.sort(key=_region_rank)
    best = best_match(title, None, candidates, names=lambda n: [base_title(n)],
                      bonus=lambda n: 2 - _region_rank(n) * 0.5)
    if not best:
        return None
    url = f"{BASE}/{quote(system)}/Named_Boxarts/{quote(best + '.png')}"
    return Metadata(title=base_title(best), poster_url=url)


PROVIDER = Provider(
    "libretro", "libretro thumbnails", frozenset({"game"}), lookup, cover_rank=15,
    applies=lambda q: q.platform in SYSTEMS,
)
