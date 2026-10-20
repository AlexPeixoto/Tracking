"""PC games: Steam user reviews (% positive), Steam's description, artwork and
Metacritic score. Public store endpoints, no key.

When the item came from a Steam account (or the wish list) its app id is known,
so no title search is needed, and Steam's tall library artwork becomes the cover.
The Steam Web API key declared here is used by sources/steam.py to read an
account's owned games and play time.
"""

from __future__ import annotations

import re

from .base import (
    Candidate,
    Metadata,
    Provider,
    Query,
    RateLimiter,
    ScoreEntry,
    Setting,
    best_match,
    cached_json,
    session,
)

SEARCH_URL = "https://store.steampowered.com/api/storesearch/"
DETAILS_URL = "https://store.steampowered.com/api/appdetails"
REVIEWS_URL = "https://store.steampowered.com/appreviews/{appid}"
# Portrait "library" artwork, the same images the Steam client's library shows.
LIBRARY_ART = "https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/{appid}/library_600x900_2x.jpg"

_limit = RateLimiter(1.0)  # the store API throttles at ~200 requests / 5 minutes


def _get(url: str, params: dict) -> dict:
    _limit.wait()
    r = session().get(url, params=params, timeout=20)
    r.raise_for_status()
    return r.json()


def _store_search(term: str) -> list[dict]:
    found = cached_json("steam", f"search-{term.lower()}", 30,
                        lambda: _get(SEARCH_URL, {"term": term, "l": "english", "cc": "US"}))
    return found.get("items") or []


def app_details(appid: int) -> dict:
    """Store details for one app (name, release date, description, art...). Cached."""
    details = cached_json("steam", f"details-{appid}", 30,
                          lambda: _get(DETAILS_URL, {"appids": appid, "l": "english"}))
    return (details.get(str(appid)) or {}).get("data") or {}


def library_art(appid: int) -> str | None:
    """The portrait library image, if this app has one (older games don't)."""
    url = LIBRARY_ART.format(appid=appid)

    def check():
        try:
            return {"ok": session().head(url, timeout=10).status_code == 200}
        except Exception:
            return {"ok": False}

    return url if cached_json("steam", f"art-{appid}", 30, check)["ok"] else None


def _app_metadata(appid: int, name_hint: str | None, prefer_art: bool) -> Metadata:
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

    data = app_details(appid)
    mc = data.get("metacritic") or {}
    if mc.get("score"):
        scores.append(ScoreEntry("Metacritic", mc["score"], 100, mc.get("url")))
    date = (data.get("release_date") or {}).get("date") or ""
    year = next((int(w) for w in date.replace(",", " ").split() if w.isdigit() and len(w) == 4), None)
    art = library_art(appid) if prefer_art else None
    return Metadata(
        title=data.get("name") or name_hint,
        year=year,
        external_id=str(appid),
        external_url=url,
        # Portrait library art for known Steam games; the landscape header only as a last resort.
        poster_url=art or data.get("header_image"),
        cover_first=bool(art),
        summary=data.get("short_description") or None,
        scores=scores,
    )


def lookup(q: Query) -> Metadata | None:
    if q.steam_appid:
        return _app_metadata(q.steam_appid, q.title, prefer_art=True)
    games = [g for g in _store_search(q.title) if not _NOT_A_GAME.search(g.get("name") or "")]
    best = best_match(q.title, None, games, names=lambda g: [g.get("name")])
    if not best:
        return None
    return _app_metadata(best["id"], best.get("name"), prefer_art=False)


_NOT_A_GAME = re.compile(r"(?i)\b(soundtrack|ost|artbook|art book|wallpapers?|demo|season pass|dlc|"
                         r"costumes?|bgm|additional (?:maps?|characters?|scenarios?))\b")


def search(q: Query) -> list[Candidate]:
    return [Candidate(g.get("name") or "?", None, "Steam · PC", g.get("tiny_image"),
                      f"https://store.steampowered.com/app/{g['id']}/", g["id"])
            for g in _store_search(q.title)
            if g.get("id") and not _NOT_A_GAME.search(g.get("name") or "")]


PROVIDER = Provider(
    "steam", "Steam", frozenset({"game"}), lookup, cover_rank=50, search=search,
    applies=lambda q: q.platform in (None, "PC"),
    settings=(Setting("steam_api_key", "Steam Web API key",
                      "Only for importing a Steam account's games and play time (Locations › "
                      "Add Steam account). Free: steamcommunity.com/dev/apikey. Reviews, "
                      "artwork and your wish list work without it."),),
)
