"""Anime: Kitsu community rating, cover, synopsis and episode list. Free JSON:API, no key.

Kitsu's episode list has titles, air dates and lengths but no per-episode
scores, so it's used after AniDB and MyAnimeList (which have them).
"""

from __future__ import annotations

from .base import (
    Episode,
    Metadata,
    Provider,
    Query,
    RateLimiter,
    ScoreEntry,
    best_match,
    cached_json,
    media_format_bonus,
    session,
    year_of,
)

API_URL = "https://kitsu.app/api/edge/anime"
EPISODES_URL = "https://kitsu.app/api/edge/anime/{id}/episodes"
HEADERS = {"Accept": "application/vnd.api+json"}
PAGE = 20  # Kitsu's maximum page size

_limit = RateLimiter(0.5)


def _get(url: str, params: dict) -> dict:
    _limit.wait()
    r = session().get(url, params=params, headers=HEADERS, timeout=20)
    r.raise_for_status()
    return r.json()


def _search(title: str, year: int | None, kind: str | None = None) -> Metadata | None:
    data = cached_json("kitsu", title.lower(), 14,
                       lambda: _get(API_URL, {"filter[text]": title, "page[limit]": 10}))
    best = best_match(
        title, year, data.get("data") or [],
        names=lambda a: [a["attributes"].get("canonicalTitle"),
                         *(a["attributes"].get("titles") or {}).values(),
                         *(a["attributes"].get("abbreviatedTitles") or [])],
        cand_year=lambda a: year_of(a["attributes"].get("startDate")),
        bonus=lambda a: media_format_bonus(a["attributes"].get("subtype"), kind),
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
        external_id=str(best.get("id")),
        external_url=url,
        poster_url=poster.get("large") or poster.get("original"),
        summary=attr.get("synopsis") or None,
        scores=[ScoreEntry("Kitsu", float(rating), 100, url)] if rating else [],
    )


def lookup(q: Query) -> Metadata | None:
    return _search(q.title, q.year, q.kind)


def episodes(q: Query, kitsu_id: str) -> list[Episode]:
    """Titles, air dates and lengths (Kitsu has no per-episode scores)."""
    season, out = q.season or 1, []
    for page in range(50):  # 1000 episodes is plenty
        data = cached_json("kitsu", f"episodes-{kitsu_id}-{page}", 7, lambda page=page: _get(
            EPISODES_URL.format(id=kitsu_id),
            {"page[limit]": PAGE, "page[offset]": page * PAGE, "sort": "number"}))
        rows = data.get("data") or []
        for e in rows:
            a = e.get("attributes") or {}
            if a.get("number") is None:
                continue
            title = a.get("canonicalTitle") or (a.get("titles") or {}).get("en_us")
            out.append(Episode(season, int(a["number"]), title, a.get("airdate"), a.get("length"),
                               None, 10, f"https://kitsu.app/anime/{kitsu_id}/episodes/{a['number']}"))
        if len(rows) < PAGE:
            break
    return out


PROVIDER = Provider("kitsu", "Kitsu", frozenset({"anime"}), lookup, cover_rank=40, episodes=episodes)
