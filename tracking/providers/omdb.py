"""Movies and TV shows: IMDb rating + poster through OMDb (free key: https://www.omdbapi.com/apikey.aspx).

Without a key we fall back to IMDb's public search suggestions, which give a
poster and IMDb link but no rating.
"""

from __future__ import annotations

import re

from .base import (
    Candidate, Metadata, Provider, ProviderError, Query, ScoreEntry, Setting, cached_json, session,
    similarity,
)

OMDB_URL = "https://www.omdbapi.com/"
SUGGEST_URL = "https://v3.sg.media-imdb.com/suggestion/x/{q}.json"


def _omdb(params: dict) -> dict:
    key = "&".join(f"{k}={v}" for k, v in sorted(params.items()) if k != "apikey")

    def fetch():
        r = session().get(OMDB_URL, params=params, timeout=15)
        if r.status_code == 401:
            raise ProviderError("OMDb rejected the API key")
        r.raise_for_status()
        return r.json()

    data = cached_json("omdb", key, 30, fetch)
    if data.get("Error", "").lower().startswith("invalid api key"):
        raise ProviderError("OMDb rejected the API key")
    return data


def _from_omdb(d: dict) -> Metadata:
    year = re.match(r"\d{4}", d.get("Year") or "")
    poster = d.get("Poster")
    url = f"https://www.imdb.com/title/{d['imdbID']}/" if d.get("imdbID") else None
    scores = []
    # OMDb's "Ratings": IMDb "7.9/10", Rotten Tomatoes "87%", Metacritic "74/100"
    for r in d.get("Ratings") or []:
        src, val = r.get("Source", ""), r.get("Value", "")
        m = re.match(r"([\d.]+)\s*(?:/\s*([\d.]+)|%)", val)
        if not m:
            continue
        name = "IMDb" if src == "Internet Movie Database" else src
        scores.append(ScoreEntry(name, float(m.group(1)), float(m.group(2) or 100),
                                 url if name == "IMDb" else None))
    return Metadata(
        scores=scores,
        title=d.get("Title"),
        year=int(year.group()) if year else None,
        external_id=d.get("imdbID"),
        external_url=url,
        poster_url=poster if poster and poster != "N/A" else None,
        summary=d.get("Plot") if d.get("Plot") not in (None, "N/A") else None,
    )


_SUGGEST_KINDS = {"movie": ("movie", "tvMovie", "video"), "series": ("tvSeries", "tvMiniSeries")}


def _search(title: str, year: int | None, api_key: str, kind: str = "movie") -> Metadata | None:
    """``kind`` is "movie" or "series" (TV shows)."""
    if api_key:
        return _lookup_omdb(title, year, api_key, kind)
    return _lookup_suggest(title, year, kind)


def _lookup_omdb(title: str, year: int | None, api_key: str, kind: str = "movie") -> Metadata | None:
    base = {"apikey": api_key, "type": kind, "plot": "short"}
    if year:
        d = _omdb({**base, "t": title, "y": year})
        if d.get("Response") == "True":
            return _from_omdb(d)
    d = _omdb({**base, "t": title})
    if d.get("Response") == "True" and similarity(title, d.get("Title", "")) >= 80:
        return _from_omdb(d)
    # Fuzzy: search and take the best title / year match.
    s = _omdb({"apikey": api_key, "s": title, "type": kind})
    best, best_score = None, 0.0
    for r in s.get("Search") or []:
        score = similarity(title, r.get("Title", ""))
        if year and str(year) in (r.get("Year") or ""):
            score += 10
        if score > best_score:
            best, best_score = r, score
    if best and best_score >= 75:
        d = _omdb({**base, "i": best["imdbID"]})
        if d.get("Response") == "True":
            return _from_omdb(d)
    return None


def _lookup_suggest(title: str, year: int | None, kind: str = "movie") -> Metadata | None:
    q = re.sub(r"[^\w ]+", "", title.lower()).strip().replace(" ", "_")
    if not q:
        return None

    def fetch():
        r = session().get(SUGGEST_URL.format(q=q), timeout=15)
        r.raise_for_status()
        return r.json()

    data = cached_json("imdb-suggest", q, 30, fetch)
    best, best_score = None, 0.0
    for r in data.get("d") or []:
        if r.get("qid") not in _SUGGEST_KINDS.get(kind, _SUGGEST_KINDS["movie"]):
            continue
        score = similarity(title, r.get("l", ""))
        if year and r.get("y") == year:
            score += 10
        if score > best_score:
            best, best_score = r, score
    if not best or best_score < 75:
        return None
    return Metadata(
        title=best.get("l"),
        year=best.get("y"),
        external_id=best.get("id"),
        external_url=f"https://www.imdb.com/title/{best['id']}/",
        poster_url=(best.get("i") or {}).get("imageUrl"),
    )


def lookup(q: Query) -> Metadata | None:
    return _search(q.title, q.year, q.setting("omdb_key"), "series" if q.category == "tv" else "movie")


def _suggest(title: str) -> list[dict]:
    q = re.sub(r"[^\w ]+", "", title.lower()).strip().replace(" ", "_")
    if not q:
        return []

    def fetch():
        r = session().get(SUGGEST_URL.format(q=q), timeout=15)
        r.raise_for_status()
        return r.json()

    return cached_json("imdb-suggest", q, 30, fetch).get("d") or []


def _thumb(url: str | None) -> str | None:
    """IMDb image URLs take a size suffix: ask for a small one for search results."""
    return url.replace("._V1_.", "._V1_UX140_.") if url and "._V1_." in url else url


def search(q: Query) -> list[Candidate]:
    kind = "series" if q.category == "tv" else "movie"
    kinds = _SUGGEST_KINDS[kind]
    labels = {"movie": "Movie", "tvMovie": "TV movie", "video": "Video",
              "tvSeries": "TV series", "tvMiniSeries": "Mini-series"}
    return [Candidate(r.get("l") or "?", r.get("y"),
                      " · ".join(x for x in (labels.get(r.get("qid"), ""), r.get("s") or "") if x),
                      _thumb((r.get("i") or {}).get("imageUrl")),
                      f"https://www.imdb.com/title/{r['id']}/")
            for r in _suggest(q.title) if r.get("qid") in kinds and r.get("id")]


# Not "required": without a key it still finds posters and IMDb links.
PROVIDER = Provider(
    "omdb", "IMDb / OMDb", frozenset({"movie", "tv"}), lookup, search=search,
    settings=(Setting("omdb_key", "OMDb API key",
                      "IMDb rating, plus Rotten Tomatoes and Metacritic for movies. Free: "
                      "omdbapi.com/apikey.aspx. Without it you still get IMDb posters and links."),),
    cover_rank=40,
)
