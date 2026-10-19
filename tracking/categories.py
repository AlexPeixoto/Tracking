"""The library's sections, in one place.

To add a section, add a row here; the sidebar, item page, location dialog,
colours and wording all read from this table. Then teach scan/classify.py (decide) when
to pick it and register metadata providers for it (see providers/__init__.py).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Category:
    key: str          # stored in the database
    label: str        # sidebar / headings ("TV Shows")
    singular: str     # item page ("TV show")
    done: str         # what completing it means ("Watched", "Beaten")
    colors: tuple[str, str]  # placeholder-card gradient
    episodic: bool = False   # counts episodes, groups seasons
    platforms: bool = False  # has a platform (games)


CATEGORIES: list[Category] = [
    Category("anime", "Anime", "Anime", "Watched", ("#7b3fe4", "#2b1659"), episodic=True),
    Category("movie", "Movies", "Movie", "Watched", ("#d9822b", "#4a2408")),
    Category("tv", "TV Shows", "TV show", "Watched", ("#d63a6a", "#4a0f22"), episodic=True),
    Category("game", "Games", "Game", "Beaten", ("#1f9d74", "#0b3a2c"), platforms=True),
    Category("unknown", "Unknown", "Unknown", "Completed", ("#6b7280", "#262a33")),
]

BY_KEY: dict[str, Category] = {c.key: c for c in CATEGORIES}
KEYS: tuple[str, ...] = tuple(BY_KEY)


def get(key: str | None) -> Category:
    return BY_KEY.get(key or "", BY_KEY["unknown"])
