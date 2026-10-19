"""Games: Metacritic score + art via RAWG, play time via HowLongToBeat.

RAWG needs a free key (https://rawg.io/apidocs). HowLongToBeat needs nothing and
also gives us cover art, so games still get thumbnails without a RAWG key.
"""

from __future__ import annotations

from .. import titles
from .base import (
    Metadata, ProviderError, RateLimiter, ScoreEntry, cached_json, session, similarity,
)

RAWG_URL = "https://api.rawg.io/api/games"

# Our platform names -> how RAWG spells them.
RAWG_PLATFORMS = {
    "PC": "PC", "PS1": "PlayStation", "PS2": "PlayStation 2", "PS3": "PlayStation 3",
    "PS4": "PlayStation 4", "PS5": "PlayStation 5", "PSP": "PSP", "PS Vita": "PS Vita",
    "Saturn": "SEGA Saturn", "Dreamcast": "Dreamcast", "Mega Drive": "Genesis",
    "Sega CD": "SEGA CD", "Master System": "SEGA Master System", "Game Gear": "Game Gear",
    "32X": "SEGA 32X", "NES": "NES", "SNES": "SNES", "N64": "Nintendo 64",
    "GameCube": "GameCube", "Wii": "Wii", "Wii U": "Wii U", "Switch": "Nintendo Switch",
    "Game Boy": "Game Boy", "Game Boy Color": "Game Boy Color", "GBA": "Game Boy Advance",
    "DS": "Nintendo DS", "3DS": "Nintendo 3DS", "Xbox": "Xbox", "Xbox 360": "Xbox 360",
    "Xbox One": "Xbox One", "Neo Geo": "Neo Geo",
}

_rawg_limit = RateLimiter(0.3)
_hltb_limit = RateLimiter(1.0)


def _plat_match(want: str | None, names) -> bool:
    if not want:
        return False
    w = titles.normalize(want)
    return any(titles.normalize(n or "") == w for n in names)


def lookup_rawg(title: str, platform: str | None, api_key: str) -> Metadata | None:
    if not api_key:
        return None

    def get(url: str, params: dict):
        _rawg_limit.wait()
        r = session().get(url, params={"key": api_key, **params}, timeout=20)
        if r.status_code in (401, 403):
            raise ProviderError("RAWG rejected the API key")
        r.raise_for_status()
        return r.json()

    data = cached_json("rawg", title.lower(),
                       30, lambda: get(RAWG_URL, {"search": title, "page_size": 10}))
    want = RAWG_PLATFORMS.get(platform or "")
    best, best_score = None, 0.0
    for g in data.get("results") or []:
        score = similarity(title, g.get("name", ""))
        if _plat_match(want, [(p.get("platform") or {}).get("name") for p in g.get("platforms") or []]):
            score += 8
        if score > best_score:
            best, best_score = g, score
    if not best or best_score < 75:
        return None

    # The detail call carries the description.
    detail = cached_json("rawg", f"game-{best['id']}", 30,
                         lambda: get(f"{RAWG_URL}/{best['id']}", {}))
    url = f"https://rawg.io/games/{best.get('slug')}"
    released = best.get("released") or ""
    scores = []
    if best.get("metacritic"):
        mc_url = detail.get("metacritic_url") or None
        scores.append(ScoreEntry("Metacritic", best["metacritic"], 100, mc_url))
    if best.get("rating"):
        scores.append(ScoreEntry("RAWG users", best["rating"], 5, url))
    return Metadata(
        title=best.get("name"),
        year=int(released[:4]) if released[:4].isdigit() else None,
        rating=best.get("metacritic"),
        rating_max=100,
        rating_source="Metacritic" if best.get("metacritic") else None,
        external_id=str(best.get("id")),
        external_url=url,
        poster_url=best.get("background_image"),
        summary=(detail.get("description_raw") or "").strip() or None,
        scores=scores,
    )


def lookup_hltb(title: str, platform: str | None = None) -> Metadata | None:
    def fetch():
        from howlongtobeatpy import HowLongToBeat

        _hltb_limit.wait()
        results = HowLongToBeat().search(title, similarity_case_sensitive=False) or []
        return [
            {"name": r.game_name, "main": r.main_story, "extra": r.main_extra,
             "complete": r.completionist, "image": r.game_image_url, "url": r.game_web_link,
             "id": r.game_id, "year": r.release_world, "score": r.review_score,
             "platforms": r.profile_platforms or []}
            for r in results
        ]

    try:
        results = cached_json("hltb2", title.lower(), 30, fetch)
    except Exception as e:  # the library breaks whenever HLTB changes their site
        raise ProviderError(f"HowLongToBeat lookup failed: {e}") from e
    want = RAWG_PLATFORMS.get(platform or "", platform)
    best, best_score = None, 0.0
    for r in results:
        score = similarity(title, r.get("name") or "")
        if _plat_match(want, r.get("platforms") or []):
            score += 8  # PS3 "Demon's Souls" over the PS5 remake
        if score > best_score:
            best, best_score = r, score
    if not best or best_score < 75:
        return None
    scores = [ScoreEntry("HLTB users", best["score"], 100, best.get("url"))] if best.get("score") else []
    return Metadata(
        title=best.get("name"),
        year=best.get("year") or None,
        hltb_hours=best.get("main") or None,
        hltb_extra=best.get("extra") or None,
        hltb_complete=best.get("complete") or None,
        poster_url=best.get("image"),
        external_url=best.get("url"),
        scores=scores,
    )
