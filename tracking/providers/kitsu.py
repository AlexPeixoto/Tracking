"""Anime: Kitsu community rating. Free JSON:API, no key."""

from __future__ import annotations

from .anilist import format_bonus
from .base import Metadata, RateLimiter, ScoreEntry, best_match, cached_json, session, year_of

API_URL = "https://kitsu.app/api/edge/anime"

_limit = RateLimiter(0.5)


def lookup(title: str, year: int | None, kind: str | None = None) -> Metadata | None:
    def fetch():
        _limit.wait()
        r = session().get(API_URL, params={"filter[text]": title, "page[limit]": 10},
                          headers={"Accept": "application/vnd.api+json"}, timeout=20)
        r.raise_for_status()
        return r.json()

    data = cached_json("kitsu", title.lower(), 14, fetch)
    best = best_match(
        title, year, data.get("data") or [],
        names=lambda a: [a["attributes"].get("canonicalTitle"),
                         *(a["attributes"].get("titles") or {}).values(),
                         *(a["attributes"].get("abbreviatedTitles") or [])],
        cand_year=lambda a: year_of(a["attributes"].get("startDate")),
        bonus=lambda a: format_bonus(a["attributes"].get("subtype"), kind),
    )
    if not best:
        return None
    attr = best["attributes"]
    url = f"https://kitsu.app/anime/{attr.get('slug') or best.get('id')}"
    rating = attr.get("averageRating")  # "82.15", out of 100
    poster = attr.get("posterImage") or {}
    return Metadata(
        title=attr.get("canonicalTitle"),
        year=year_of(attr.get("startDate")),
        external_url=url,
        poster_url=poster.get("large") or poster.get("original"),
        summary=attr.get("synopsis") or None,
        scores=[ScoreEntry("Kitsu", float(rating), 100, url)] if rating else [],
    )
