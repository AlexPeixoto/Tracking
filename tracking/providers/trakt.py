"""Movies and anime: Trakt community rating. Needs a free app's Client ID
(trakt.tv/oauth/applications/new; any redirect URI, e.g. urn:ietf:wg:oauth:2.0:oob)."""

from __future__ import annotations

from .base import (
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
)

API_URL = "https://api.trakt.tv"

_limit = RateLimiter(0.3)


def _search(title: str, year: int | None, client_id: str, kind: str = "movie") -> Metadata | None:
    """``kind`` is 'movie' or 'show' (anime series)."""
    if not client_id:
        return None

    def fetch():
        _limit.wait()
        r = session().get(f"{API_URL}/search/{kind}", timeout=20,
                          params={"query": title, "extended": "full", "limit": 10},
                          headers={"trakt-api-version": "2", "trakt-api-key": client_id,
                                   "Content-Type": "application/json"})
        if r.status_code in (401, 403):
            raise ProviderError("Trakt rejected the Client ID")
        r.raise_for_status()
        return r.json()

    results = [r.get(kind) or {} for r in cached_json("trakt", f"{kind}-{title.lower()}", 30, fetch)]
    best = best_match(title, year, results, names=lambda m: [m.get("title")],
                      cand_year=lambda m: m.get("year"),
                      bonus=lambda m: min(3.0, (m.get("votes") or 0) / 1000))
    if not best:
        return None
    slug = (best.get("ids") or {}).get("slug")
    url = f"https://trakt.tv/{'movies' if kind == 'movie' else 'shows'}/{slug}" if slug else None
    scores = []
    if best.get("rating") and (best.get("votes") or 0) >= 10:
        scores.append(ScoreEntry("Trakt", round(best["rating"], 1), 10, url))
    return Metadata(title=best.get("title"), year=best.get("year"), external_url=url,
                    summary=best.get("overview") or None, scores=scores)


def lookup(q: Query) -> Metadata | None:
    kind = "movie" if q.category == "movie" or q.kind == "movie" else "show"
    return _search(q.title, q.year, q.setting("trakt_client_id"), kind)


PROVIDER = Provider(
    "trakt", "Trakt", frozenset({"movie", "tv", "anime"}), lookup,
    settings=(Setting("trakt_client_id", "Trakt Client ID",
                      "Trakt score. Free app: trakt.tv/oauth/applications/new, redirect URI "
                      "urn:ietf:wg:oauth:2.0:oob."),),
    required=("trakt_client_id",), cover_rank=60,
)
