"""Metadata services, one module each, registered here.

Adding a service:
  1. Create providers/<name>.py with ``lookup(q: Query) -> Metadata | None``
     (return None for "no match"; raise ProviderError for things the user should
     see, like a rejected key) and ``PROVIDER = Provider(...)`` declaring its
     categories, Settings fields, required keys and cover priority.
  2. Add the module to REGISTRY below. Its position is its priority when
     several services know a title, summary or link.
  3. If it brings a new score name, add it to scores.RATING_PREF / SHORT.
Settings, enrichment and logging pick it up from there.
"""

from __future__ import annotations

from . import (
    anidb, anilist, hltb, igdb, kitsu, libretro, mal, omdb, opencritic, rawg, steam, tmdb, trakt,
    tvmaze, wikidata,
)
from .base import Candidate, Episode, Metadata, Provider, ProviderError, Query, ScoreEntry, Setting

REGISTRY: list[Provider] = [m.PROVIDER for m in (
    anidb, omdb, rawg, tmdb, igdb, anilist, tvmaze, hltb, libretro, mal, kitsu, opencritic, trakt,
    steam, wikidata,
)]


def for_query(q: Query) -> list[Provider]:
    """Services that handle the query's category (and platform), in priority order."""
    return [p for p in REGISTRY if q.category in p.categories and p.applies(q)]


def for_category(category: str) -> list[Provider]:
    return [p for p in REGISTRY if category in p.categories]


def settings_for(category: str) -> list[Setting]:
    """Settings fields for a category's services, each key once, in registry order."""
    seen, out = set(), []
    for p in for_category(category):
        for s in p.settings:
            if s.key not in seen:
                seen.add(s.key)
                out.append(s)
    return out


def search(q: Query, limit: int = 20) -> tuple[str, list[Candidate]]:
    """Free-text search for the "Want to acquire" box: the first configured service
    (registry order) for the category that can search. Returns (service, results)."""
    # HowLongToBeat last: Steam's results carry store ids (artwork, play time) it lacks.
    for p in sorted(for_query(q), key=lambda p: p.name == "hltb"):
        if p.search and p.configured(q):
            return p.label, p.search(q)[:limit]
    return "", []


def search_all(q: Query, limit: int = 12) -> list[Candidate]:
    """Find match: every configured service for the item that can search, merged
    (each result's detail names its service). A failing service is skipped."""
    out: list[Candidate] = []
    for p in for_query(q):
        if not (p.search and p.configured(q)):
            continue
        try:
            out += p.search(q)[:limit]
        except Exception:  # noqa: BLE001 - one service down shouldn't hide the others
            continue
    return out


def credential_keys() -> set[str]:
    """Every setting a service declares (keys, secrets, client ids): left out of
    exports unless asked for, kept on import."""
    return {s.key for p in REGISTRY for s in p.settings}


__all__ = ["Candidate", "Episode", "Metadata", "Provider", "ProviderError", "Query", "REGISTRY", "ScoreEntry", "Setting",
           "credential_keys", "search", "search_all", "settings_for",
           "for_category", "for_query"]
