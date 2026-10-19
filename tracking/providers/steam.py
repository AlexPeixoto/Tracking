"""PC games: Steam user reviews (% positive), plus Steam's own description,
header art and Metacritic score. Public store endpoints, no key."""

from __future__ import annotations

from .base import Metadata, RateLimiter, ScoreEntry, best_match, cached_json, session

SEARCH_URL = "https://store.steampowered.com/api/storesearch/"
DETAILS_URL = "https://store.steampowered.com/api/appdetails"
REVIEWS_URL = "https://store.steampowered.com/appreviews/{appid}"

_limit = RateLimiter(1.0)  # the store API throttles at ~200 requests / 5 minutes


def _get(url: str, params: dict) -> dict:
    _limit.wait()
    r = session().get(url, params=params, timeout=20)
    r.raise_for_status()
    return r.json()


def lookup(title: str) -> Metadata | None:
    found = cached_json("steam", f"search-{title.lower()}", 30,
                        lambda: _get(SEARCH_URL, {"term": title, "l": "english", "cc": "US"}))
    best = best_match(title, None, found.get("items") or [], names=lambda g: [g.get("name")])
    if not best:
        return None
    appid = best["id"]
    url = f"https://store.steampowered.com/app/{appid}/"

    reviews = cached_json("steam", f"reviews-{appid}", 7, lambda: _get(
        REVIEWS_URL.format(appid=appid),
        {"json": 1, "language": "all", "purchase_type": "all", "num_per_page": 0}))
    summary = reviews.get("query_summary") or {}
    scores = []
    total = summary.get("total_reviews") or 0
    if total >= 10:
        pct = 100 * (summary.get("total_positive") or 0) / total
        scores.append(ScoreEntry("Steam", round(pct, 1), 100, url))

    details = cached_json("steam", f"details-{appid}", 30,
                          lambda: _get(DETAILS_URL, {"appids": appid, "l": "english"}))
    data = (details.get(str(appid)) or {}).get("data") or {}
    mc = data.get("metacritic") or {}
    if mc.get("score"):
        scores.append(ScoreEntry("Metacritic", mc["score"], 100, mc.get("url")))
    date = (data.get("release_date") or {}).get("date") or ""
    year = next((int(w) for w in date.replace(",", " ").split() if w.isdigit() and len(w) == 4), None)
    return Metadata(
        title=data.get("name") or best.get("name"),
        year=year,
        external_url=url,
        # Steam's capsule art is landscape; only used when nothing else has a cover.
        poster_url=data.get("header_image"),
        summary=data.get("short_description") or None,
        scores=scores,
    )
