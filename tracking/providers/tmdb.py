"""Movies: TMDB user score, poster and overview. Free key: themoviedb.org/settings/api.

Accepts either the short v3 "API key" or the long v4 "read access token".
"""

from __future__ import annotations

from .base import (
    Episode,
    Metadata,
    Provider,
    ProviderError,
    Query,
    RateLimiter,
    ScoreEntry,
    Setting,
    best_match,
    cached_json,
    session,
    year_of,
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


def _search(title: str, year: int | None, key: str, kind: str = "movie") -> Metadata | None:
    """``kind`` is "movie" or "series" (TV shows use TMDB's /tv endpoints)."""
    if not key:
        return None
    tv = kind == "series"
    path = "tv" if tv else "movie"
    params = {"query": title, "include_adult": "false"}
    data = cached_json("tmdb", f"{path}-{title.lower()}", 30,
                       lambda: _get(f"/search/{path}", key, params))
    date_field = "first_air_date" if tv else "release_date"
    best = best_match(title, year, data.get("results") or [],
                      names=lambda m: [m.get("name" if tv else "title"),
                                       m.get("original_name" if tv else "original_title")],
                      cand_year=lambda m: year_of(m.get(date_field)),
                      bonus=lambda m: min(3.0, (m.get("vote_count") or 0) / 500))
    if not best:
        return None
    url = f"https://www.themoviedb.org/{path}/{best['id']}"
    scores = []
    if best.get("vote_count", 0) >= 10 and best.get("vote_average"):
        scores.append(ScoreEntry("TMDB", round(best["vote_average"], 1), 10, url))
    return Metadata(
        title=best.get("name" if tv else "title"),
        year=year_of(best.get(date_field)),
        external_id=str(best["id"]),
        external_url=url,
        poster_url=IMAGE_URL.format(best["poster_path"]) if best.get("poster_path") else None,
        summary=best.get("overview") or None,
        scores=scores,
    )


def lookup(q: Query) -> Metadata | None:
    return _search(q.title, q.year, q.setting("tmdb_key"), "series" if q.category == "tv" else "movie")


def episodes(q: Query, tv_id: str) -> list[Episode]:
    """TV only: the season's episodes with TMDB's vote average."""
    if q.category != "tv":
        return []
    key, season = q.setting("tmdb_key"), q.season or 1
    data = cached_json("tmdb", f"season-{tv_id}-{season}", 7,
                       lambda: _get(f"/tv/{tv_id}/season/{season}", key, {}))
    return [
        Episode(season, e["episode_number"], e.get("name"), e.get("air_date") or None,
                e.get("runtime"),
                round(e["vote_average"], 1) if (e.get("vote_count") or 0) >= 3 else None, 10,
                f"https://www.themoviedb.org/tv/{tv_id}/season/{season}/episode/{e['episode_number']}")
        for e in data.get("episodes") or []
    ]


PROVIDER = Provider(
    "tmdb", "TMDB", frozenset({"movie", "tv"}), lookup, episodes=episodes,
    settings=(Setting("tmdb_key", "TMDB API key / token",
                      "TMDB score, sharper posters, plot. Free: themoviedb.org/settings/api "
                      "(either the API key or the read access token)."),),
    required=("tmdb_key",), cover_rank=10,
)
