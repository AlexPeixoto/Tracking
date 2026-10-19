"""Games: IGDB critic + user ratings, cover and summary. Great coverage of old consoles.

Needs a free Twitch developer app (dev.twitch.tv/console/apps): its Client ID and
Client Secret. We exchange them for an app token and keep it until it expires.
"""

from __future__ import annotations

import threading
import time

from .. import platforms, titles
from .base import (
    Metadata, Provider, ProviderError, Query, RateLimiter, ScoreEntry, Setting, best_match,
    cached_json, session,
)

TOKEN_URL = "https://id.twitch.tv/oauth2/token"
API_URL = "https://api.igdb.com/v4/games"
COVER_URL = "https://images.igdb.com/igdb/image/upload/t_cover_big_2x/{}.jpg"

# Where IGDB's platform names differ from ours / RAWG's.
IGDB_PLATFORMS = {
    "PC": "PC (Microsoft Windows)", "PS1": "PlayStation", "Mega Drive": "Sega Mega Drive/Genesis",
    "Saturn": "Sega Saturn", "Sega CD": "Sega CD", "Master System": "Sega Master System/Mark III",
    "SNES": "Super Nintendo Entertainment System", "NES": "Nintendo Entertainment System",
    "GBA": "Game Boy Advance", "PSP": "PlayStation Portable", "PS Vita": "PlayStation Vita",
    "Neo Geo": "Neo Geo AES", "Game Gear": "Sega Game Gear", "32X": "Sega 32X",
}

_limit = RateLimiter(0.3)  # IGDB allows 4 requests/second
_token_lock = threading.Lock()
_token: tuple[str, str, float] | None = None  # (client_id, token, expires_at)


def _access_token(client_id: str, secret: str) -> str:
    global _token
    with _token_lock:
        if _token and _token[0] == client_id and _token[2] > time.time() + 60:
            return _token[1]
        r = session().post(TOKEN_URL, timeout=20, params={
            "client_id": client_id, "client_secret": secret, "grant_type": "client_credentials"})
        if r.status_code in (400, 401, 403):
            raise ProviderError("Twitch rejected the IGDB Client ID / Secret")
        r.raise_for_status()
        d = r.json()
        _token = (client_id, d["access_token"], time.time() + d.get("expires_in", 3600))
        return _token[1]


def _search(title: str, platform: str | None, client_id: str, secret: str) -> Metadata | None:
    if not (client_id and secret):
        return None

    def fetch():
        token = _access_token(client_id, secret)
        safe = title.replace('"', " ")
        body = (f'search "{safe}"; fields name,alternative_names.name,first_release_date,url,'
                "summary,cover.image_id,platforms.name,aggregated_rating,aggregated_rating_count,"
                "rating,rating_count; limit 15;")
        _limit.wait()
        r = session().post(API_URL, data=body.encode(), timeout=20, headers={
            "Client-ID": client_id, "Authorization": f"Bearer {token}"})
        if r.status_code in (401, 403):
            raise ProviderError("IGDB rejected the token")
        r.raise_for_status()
        return r.json()

    games = cached_json("igdb", title.lower(), 30, fetch)
    want = titles.normalize(platforms.service_name(platform, "igdb") or "")

    def on_platform(g) -> float:
        names = {titles.normalize(p.get("name", "")) for p in g.get("platforms") or []}
        return 8 if want and want in names else 0

    best = best_match(title, None, games,
                      names=lambda g: [g.get("name"), *(a.get("name") for a in g.get("alternative_names") or [])],
                      bonus=on_platform)
    if not best:
        return None
    url = best.get("url")
    scores = []
    if best.get("aggregated_rating") and (best.get("aggregated_rating_count") or 0) >= 1:
        scores.append(ScoreEntry("IGDB critics", round(best["aggregated_rating"]), 100, url))
    if best.get("rating") and (best.get("rating_count") or 0) >= 5:
        scores.append(ScoreEntry("IGDB users", round(best["rating"]), 100, url))
    released = best.get("first_release_date")
    cover = (best.get("cover") or {}).get("image_id")
    return Metadata(
        title=best.get("name"),
        year=time.gmtime(released).tm_year if released else None,
        external_url=url,
        poster_url=COVER_URL.format(cover) if cover else None,
        summary=best.get("summary") or None,
        scores=scores,
    )


def lookup(q: Query) -> Metadata | None:
    return _search(q.title, q.platform, q.setting("igdb_client_id"), q.setting("igdb_client_secret"))


PROVIDER = Provider(
    "igdb", "IGDB", frozenset({"game"}), lookup,
    settings=(
        Setting("igdb_client_id", "IGDB Client ID",
                "IGDB critic + user ratings, covers, summaries; good for retro consoles. "
                "Free Twitch app: dev.twitch.tv/console/apps.", secret=False),
        Setting("igdb_client_secret", "IGDB Client Secret", "From the same Twitch app."),
    ),
    required=("igdb_client_id", "igdb_client_secret"), cover_rank=10,
)
