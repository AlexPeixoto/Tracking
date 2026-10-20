"""Anime: AniList score, cover and description. Free GraphQL API, no key."""

from __future__ import annotations

import re

from .base import (
    Candidate,
    Metadata,
    Provider,
    ProviderError,
    Query,
    RateLimiter,
    ScoreEntry,
    best_match,
    cached_json,
    media_format_bonus,
    session,
)

API_URL = "https://graphql.anilist.co"
QUERY = """
query ($search: String) {
  Page(perPage: 10) {
    media(search: $search, type: ANIME) {
      id siteUrl seasonYear format averageScore
      title { romaji english native }
      synonyms
      coverImage { extraLarge large }
      description(asHtml: false)
    }
  }
}
"""

_limit = RateLimiter(2.1)  # AniList allows ~30 requests/minute

def _media(title: str, fetch) -> list[dict]:
    data = cached_json("anilist", title.lower(), 14, fetch)
    return ((data.get("data") or {}).get("Page") or {}).get("media") or []


def _fetcher(title: str):
    def fetch():
        _limit.wait()
        r = session().post(API_URL, json={"query": QUERY, "variables": {"search": title}},
                           timeout=20)
        if r.status_code == 429:
            raise ProviderError("AniList rate limit hit, try again in a minute")
        r.raise_for_status()
        return r.json()
    return fetch


def _search(title: str, year: int | None, kind: str | None = None) -> Metadata | None:
    """``kind`` ("series" / "movie") steers the match towards TV or movie entries."""
    media = _media(title, _fetcher(title))
    best = best_match(
        title, year, media,
        names=lambda m: [*(m.get("title") or {}).values(), *(m.get("synonyms") or [])],
        cand_year=lambda m: m.get("seasonYear"),
        bonus=lambda m: media_format_bonus(m.get("format"), kind),
    )
    if not best:
        return None
    url = best.get("siteUrl")
    t = best.get("title") or {}
    cover = best.get("coverImage") or {}
    desc = re.sub(r"<[^>]+>", "", best.get("description") or "").strip() or None
    scores = [ScoreEntry("AniList", best["averageScore"], 100, url)] if best.get("averageScore") else []
    return Metadata(
        title=t.get("english") or t.get("romaji"),
        year=best.get("seasonYear"),
        external_url=url,
        poster_url=cover.get("extraLarge") or cover.get("large"),
        summary=desc,
        scores=scores,
    )


def lookup(q: Query) -> Metadata | None:
    return _search(q.title, q.year, q.kind)


def search(q: Query) -> list[Candidate]:
    out = []
    for m in _media(q.title, _fetcher(q.title)):
        t = m.get("title") or {}
        fmt = (m.get("format") or "").replace("_", " ").title()
        out.append(Candidate(t.get("english") or t.get("romaji") or "?", m.get("seasonYear"),
                             " · ".join(x for x in ("Anime", fmt) if x),
                             (m.get("coverImage") or {}).get("large"), m.get("siteUrl")))
    return out


PROVIDER = Provider("anilist", "AniList", frozenset({"anime"}), lookup, cover_rank=20, search=search)
