"""Anime: MyAnimeList score, cover and synopsis through Jikan (no key)."""

from __future__ import annotations

import time

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
)

API_URL = "https://api.jikan.moe/v4/anime"
EPISODES_URL = "https://api.jikan.moe/v4/anime/{id}/episodes"

_limit = RateLimiter(1.0)  # Jikan allows 3 requests/second, 60/minute


def lookup(q: Query) -> Metadata | None:
    def fetch():
        _limit.wait()
        params = {"q": q.title, "limit": 10, "sfw": "false"}
        r = session().get(API_URL, params=params, timeout=20)
        if r.status_code == 429:
            time.sleep(2)
            r = session().get(API_URL, params=params, timeout=20)
        r.raise_for_status()
        return r.json()

    data = cached_json("jikan", q.title.lower(), 14, fetch)
    best = best_match(
        q.title, q.year, data.get("data") or [],
        names=lambda a: [a.get("title"), a.get("title_english"),
                         *(t.get("title") for t in a.get("titles") or [])],
        cand_year=lambda a: a.get("year"),
        bonus=lambda a: media_format_bonus(a.get("type"), q.kind),
    )
    if not best:
        return None
    img = (best.get("images") or {}).get("jpg") or {}
    url = best.get("url")
    return Metadata(
        title=best.get("title_english") or best.get("title"),
        year=best.get("year"),
        external_id=str(best.get("mal_id")),
        external_url=url,
        poster_url=img.get("large_image_url") or img.get("image_url"),
        summary=best.get("synopsis"),
        scores=[ScoreEntry("MAL", best["score"], 10, url)] if best.get("score") else [],
    )


def episodes(q: Query, mal_id: str) -> list[Episode]:
    """MAL's per-episode user score (a 1-5 poll), titles and filler / recap flags."""
    out: list[Episode] = []
    season = q.season or 1
    for page in range(1, 21):  # 100 episodes a page; long runners stop at 2000
        def fetch(page=page):
            _limit.wait()
            r = session().get(EPISODES_URL.format(id=mal_id), params={"page": page}, timeout=20)
            r.raise_for_status()
            return r.json()

        data = cached_json("jikan", f"episodes-{mal_id}-{page}", 7, fetch)
        for e in data.get("data") or []:
            note = "filler" if e.get("filler") else "recap" if e.get("recap") else None
            out.append(Episode(season, e["mal_id"], e.get("title"), (e.get("aired") or "")[:10] or None,
                               None, e.get("score"), 5, e.get("url"), note))
        if not (data.get("pagination") or {}).get("has_next_page"):
            break
    return out


PROVIDER = Provider("mal", "MyAnimeList (Jikan)", frozenset({"anime"}), lookup, cover_rank=30,
                    episodes=episodes)
