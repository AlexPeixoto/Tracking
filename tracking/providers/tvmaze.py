"""TV shows: TVmaze rating, poster and summary. Free API, no key (20 calls / 10 s)."""

from __future__ import annotations

import re

from .base import (
    Candidate,
    Episode,
    Metadata,
    Provider,
    Query,
    RateLimiter,
    ScoreEntry,
    best_match,
    cached_json,
    session,
    year_of,
)

SEARCH_URL = "https://api.tvmaze.com/search/shows"
EPISODES_URL = "https://api.tvmaze.com/shows/{id}/episodes"

_limit = RateLimiter(0.6)


def _shows(title: str) -> list[dict]:
    def fetch():
        _limit.wait()
        r = session().get(SEARCH_URL, params={"q": title}, timeout=20)
        r.raise_for_status()
        return r.json()

    return [r.get("show") or {} for r in cached_json("tvmaze", title.lower(), 14, fetch)]


def _search(title: str, year: int | None) -> Metadata | None:
    shows = _shows(title)
    best = best_match(title, year, shows, names=lambda s: [s.get("name")],
                      cand_year=lambda s: year_of(s.get("premiered")),
                      bonus=lambda s: 2 if s.get("type") == "Scripted" else 0)
    if not best:
        return None
    url = best.get("url")
    rating = (best.get("rating") or {}).get("average")
    image = best.get("image") or {}
    summary = re.sub(r"<[^>]+>", "", best.get("summary") or "").strip() or None
    return Metadata(
        title=best.get("name"),
        year=year_of(best.get("premiered")),
        external_id=str(best.get("id")),
        external_url=url,
        poster_url=image.get("original") or image.get("medium"),
        summary=summary,
        scores=[ScoreEntry("TVmaze", rating, 10, url)] if rating else [],
    )


def lookup(q: Query) -> Metadata | None:
    return _search(q.title, q.year)


def episodes(q: Query, show_id: str) -> list[Episode]:
    """Every episode of the item's season, with TVmaze's per-episode rating."""
    def fetch():
        _limit.wait()
        r = session().get(EPISODES_URL.format(id=show_id), timeout=20)
        r.raise_for_status()
        return r.json()

    season = q.season or 1
    out = []
    for e in cached_json("tvmaze", f"episodes-{show_id}", 7, fetch):
        if e.get("season") != season or e.get("number") is None:
            continue  # other seasons, and specials without a number
        out.append(Episode(season, e["number"], e.get("name"), e.get("airdate") or None,
                           e.get("runtime"), (e.get("rating") or {}).get("average"), 10, e.get("url")))
    return out


def search(q: Query) -> list[Candidate]:
    out = []
    for s in _shows(q.title):
        network = (s.get("network") or s.get("webChannel") or {}).get("name") or ""
        out.append(Candidate(s.get("name") or "?", year_of(s.get("premiered")),
                             " · ".join(x for x in ("TV series", network, s.get("status") or "") if x),
                             (s.get("image") or {}).get("medium"), s.get("url")))
    return out


PROVIDER = Provider("tvmaze", "TVmaze", frozenset({"tv"}), lookup, cover_rank=20, episodes=episodes,
                    search=search)
