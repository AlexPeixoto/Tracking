"""Movies: TMDB user score, poster and overview. Free key: themoviedb.org/settings/api.

Accepts either the short v3 "API key" or the long v4 "read access token".
"""

from __future__ import annotations

from .base import (
    Metadata, ProviderError, RateLimiter, ScoreEntry, best_match, cached_json, session, year_of,
)

API_URL = "https://api.themoviedb.org/3"
IMAGE_URL = "https://image.tmdb.org/t/p/w780{}"

_limit = RateLimiter(0.1)


def _get(path: str, key: str, params: dict) -> dict:
    _limit.wait()
    headers = {}
    if len(key) > 40:  # v4 read access token (a JWT)
        headers["Authorization"] = f"Bearer {key}"
    else:
        params = {**params, "api_key": key}
    r = session().get(API_URL + path, params=params, headers=headers, timeout=20)
    if r.status_code == 401:
        raise ProviderError("TMDB rejected the API key")
    r.raise_for_status()
    return r.json()


def lookup(title: str, year: int | None, key: str) -> Metadata | None:
    if not key:
        return None
    params = {"query": title, "include_adult": "false"}
    data = cached_json("tmdb", f"movie-{title.lower()}", 30,
                       lambda: _get("/search/movie", key, params))
    best = best_match(title, year, data.get("results") or [],
                      names=lambda m: [m.get("title"), m.get("original_title")],
                      cand_year=lambda m: year_of(m.get("release_date")),
                      bonus=lambda m: min(3.0, (m.get("vote_count") or 0) / 500))
    if not best:
        return None
    url = f"https://www.themoviedb.org/movie/{best['id']}"
    scores = []
    if best.get("vote_count", 0) >= 10 and best.get("vote_average"):
        scores.append(ScoreEntry("TMDB", round(best["vote_average"], 1), 10, url))
    return Metadata(
        title=best.get("title"),
        year=year_of(best.get("release_date")),
        external_url=url,
        poster_url=IMAGE_URL.format(best["poster_path"]) if best.get("poster_path") else None,
        summary=best.get("overview") or None,
        scores=scores,
    )
