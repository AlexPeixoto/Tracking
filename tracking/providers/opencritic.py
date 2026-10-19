"""Games: OpenCritic top-critic average and % recommended.

OpenCritic's API is served through RapidAPI: subscribe to "OpenCritic API"
(free tier) at rapidapi.com and paste your RapidAPI key.
"""

from __future__ import annotations

from .base import Metadata, ProviderError, RateLimiter, ScoreEntry, best_match, cached_json, session

HOST = "opencritic-api.p.rapidapi.com"
API_URL = f"https://{HOST}"

_limit = RateLimiter(1.0)  # free tier is small; results are cached for a month


def _get(path: str, key: str, params: dict | None = None):
    _limit.wait()
    r = session().get(API_URL + path, params=params or {}, timeout=20,
                      headers={"X-RapidAPI-Key": key, "X-RapidAPI-Host": HOST})
    if r.status_code in (401, 403):
        raise ProviderError("OpenCritic (RapidAPI) rejected the key or you're not subscribed")
    if r.status_code == 429:
        raise ProviderError("OpenCritic (RapidAPI) quota used up for now")
    r.raise_for_status()
    return r.json()


def lookup(title: str, key: str) -> Metadata | None:
    if not key:
        return None
    found = cached_json("opencritic", f"search-{title.lower()}", 30,
                        lambda: _get("/game/search", key, {"criteria": title}))
    best = best_match(title, None, found or [], names=lambda g: [g.get("name")])
    if not best:
        return None
    game = cached_json("opencritic", f"game-{best['id']}", 30,
                       lambda: _get(f"/game/{best['id']}", key))
    slug = (game.get("url") or "").rstrip("/").rsplit("/", 1)[-1] or ""
    url = f"https://opencritic.com/game/{best['id']}/{slug}" if slug else f"https://opencritic.com/game/{best['id']}"
    scores = []
    if (game.get("topCriticScore") or -1) > 0:
        scores.append(ScoreEntry("OpenCritic", round(game["topCriticScore"]), 100, url))
    if (game.get("percentRecommended") or -1) > 0:
        scores.append(ScoreEntry("OpenCritic % rec.", round(game["percentRecommended"]), 100, url))
    return Metadata(title=game.get("name"), external_url=url,
                    summary=game.get("description") or None, scores=scores)
