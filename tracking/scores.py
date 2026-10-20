"""Comparing scores from different sites: order, short names, and a common scale.

Every score is put on 0-100 (7.9/10 -> 79, 4.3/5 -> 86, 87% -> 87) so averages
and "highest" mean the same thing across IMDb, Metacritic, MAL, Steam, ...
Anything with ``source``, ``value`` and ``max_value`` attributes works here.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol


class ScoreLike(Protocol):
    source: str
    value: float
    max_value: float


# Most trusted / best-known first, per category. The first one present is the
# item's headline rating; the card lists them in this order.
RATING_PREF = {
    "anime": ["AniDB", "MAL", "AniList", "Kitsu", "Trakt"],
    "movie": ["IMDb", "TMDB", "Trakt", "Metacritic", "Rotten Tomatoes"],
    "tv": ["IMDb", "TMDB", "TVmaze", "Trakt"],
    "game": ["Metacritic", "OpenCritic", "IGDB critics", "Steam", "IGDB users", "RAWG users",
             "HLTB users"],
}

# Short labels for the score chips on cards.
SHORT = {
    "Metacritic": "MC", "Rotten Tomatoes": "RT", "OpenCritic": "OC", "OpenCritic % rec.": "OC%",
    "IGDB critics": "IGDB", "IGDB users": "IGDB·u", "RAWG users": "RAWG", "HLTB users": "HLTB",
    "AniList": "AL",
}


def pct(s: ScoreLike) -> float:
    """The score on a 0-100 scale."""
    return 100.0 * s.value / (s.max_value or 100)


def fmt(s: ScoreLike) -> str:
    """How a score reads on its own site: 7.9 (of 10), 74 (of 100), 4.3 (of 5)."""
    if s.max_value == 100:
        return f"{s.value:.0f}"
    return f"{s.value:.1f}"


def ordered(scores: Iterable[ScoreLike], category: str) -> list[ScoreLike]:
    pref = RATING_PREF.get(category, [])
    return sorted(scores, key=lambda s: (pref.index(s.source) if s.source in pref else len(pref),
                                         s.source))


def average(scores: Iterable[ScoreLike]) -> float | None:
    vals = [pct(s) for s in scores]
    return sum(vals) / len(vals) if vals else None


def best(scores: Iterable[ScoreLike]) -> float | None:
    vals = [pct(s) for s in scores]
    return max(vals) if vals else None
