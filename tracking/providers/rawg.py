"""Games: Metacritic score, RAWG user score, art and description. Free key: rawg.io/apidocs."""

from __future__ import annotations

from .. import platforms
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
    platform_matches,
    session,
    year_of,
)

API_URL = "https://api.rawg.io/api/games"

_limit = RateLimiter(0.3)


def lookup(q: Query) -> Metadata | None:
    key = q.setting("rawg_key")

    def get(url: str, params: dict):
        _limit.wait()
        r = session().get(url, params={"key": key, **params}, timeout=20)
        if r.status_code in (401, 403):
            raise ProviderError("RAWG rejected the API key")
        r.raise_for_status()
        return r.json()

    data = cached_json("rawg", q.title.lower(), 30,
                       lambda: get(API_URL, {"search": q.title, "page_size": 10}))
    want = platforms.service_name(q.platform, "rawg")
    best = best_match(
        q.title, None, data.get("results") or [], names=lambda g: [g.get("name")],
        bonus=lambda g: 8 if platform_matches(
            want, [(p.get("platform") or {}).get("name") for p in g.get("platforms") or []]) else 0)
    if not best:
        return None

    # The detail call carries the description.
    detail = cached_json("rawg", f"game-{best['id']}", 30, lambda: get(f"{API_URL}/{best['id']}", {}))
    url = f"https://rawg.io/games/{best.get('slug')}"
    scores = []
    if best.get("metacritic"):
        scores.append(ScoreEntry("Metacritic", best["metacritic"], 100, detail.get("metacritic_url") or None))
    if best.get("rating"):
        scores.append(ScoreEntry("RAWG users", best["rating"], 5, url))
    return Metadata(
        title=best.get("name"),
        year=year_of(best.get("released")),
        external_id=str(best.get("id")),
        external_url=url,
        poster_url=best.get("background_image"),
        summary=(detail.get("description_raw") or "").strip() or None,
        scores=scores,
    )


PROVIDER = Provider(
    "rawg", "RAWG", frozenset({"game"}), lookup,
    settings=(Setting("rawg_key", "RAWG API key",
                      "Metacritic score, RAWG user score, description. Free: rawg.io/apidocs"),),
    required=("rawg_key",), cover_rank=30,
)
