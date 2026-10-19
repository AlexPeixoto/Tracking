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
    anidb, anilist, hltb, igdb, kitsu, mal, omdb, opencritic, rawg, steam, tmdb, trakt, tvmaze,
)
from .base import Episode, Metadata, Provider, ProviderError, Query, ScoreEntry, Setting

REGISTRY: list[Provider] = [m.PROVIDER for m in (
    anidb, omdb, rawg, tmdb, igdb, anilist, tvmaze, hltb, mal, kitsu, opencritic, trakt, steam,
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


def credential_keys() -> set[str]:
    """Every setting a service declares (keys, secrets, client ids): left out of
    exports unless asked for, kept on import."""
    return {s.key for p in REGISTRY for s in p.settings}


__all__ = ["Episode", "Metadata", "Provider", "ProviderError", "Query", "REGISTRY", "ScoreEntry", "Setting",
           "credential_keys", "settings_for",
           "for_category", "for_query"]
