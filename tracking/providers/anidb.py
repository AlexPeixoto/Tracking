"""Anime: AniDB rating + picture.

AniDB's HTTP API needs a registered client (https://anidb.net/software/add,
"HTTP API" client, any name / version 1). Their rules: at most one request every
2 seconds, cache everything, and don't download the title dump more than once
a day — all of which this module does. Getting banned is temporary but annoying.

MyAnimeList (through Jikan, no key) is looked up alongside it as a second score.
"""

from __future__ import annotations

import gzip
import threading
import time
import xml.etree.ElementTree as ET

from rapidfuzz import fuzz, process

from .. import titles
from .anilist import format_bonus
from .base import (
    Metadata, ProviderError, RateLimiter, ScoreEntry, cache_file, cached_json, session, similarity,
)

TITLES_URL = "https://anidb.net/api/anime-titles.xml.gz"
API_URL = "http://api.anidb.net:9001/httpapi"
IMAGE_URL = "https://cdn-eu.anidb.net/images/main/{}"
JIKAN_URL = "https://api.jikan.moe/v4/anime"

_anidb_limit = RateLimiter(2.5)
_jikan_limit = RateLimiter(1.0)

_index_lock = threading.Lock()
_index: tuple[list[str], list[int]] | None = None  # parallel lists: normalized title, aid


def _xml(data: bytes) -> ET.Element:
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    return ET.fromstring(data)


def _title_index() -> tuple[list[str], list[int]]:
    global _index
    with _index_lock:
        if _index is not None:
            return _index
        f = cache_file("anidb", "anime-titles", ".xml.gz")
        if not f.exists() or time.time() - f.stat().st_mtime > 86400 * 7:
            try:
                r = session().get(TITLES_URL, timeout=60)
                r.raise_for_status()
                f.write_bytes(r.content)
            except Exception as e:
                if not f.exists():
                    raise ProviderError(f"Couldn't download AniDB title list: {e}") from e
        root = _xml(f.read_bytes())
        names, aids = [], []
        for anime in root.iter("anime"):
            aid = int(anime.get("aid"))
            for t in anime.iter("title"):
                if t.get("type") in ("main", "official", "syn", "short") and t.text:
                    names.append(titles.normalize(t.text))
                    aids.append(aid)
        _index = (names, aids)
        return _index


def find_aid(title: str) -> int | None:
    names, aids = _title_index()
    key = titles.normalize(title)
    if not key:
        return None
    try:
        return aids[names.index(key)]
    except ValueError:
        pass
    hit = process.extractOne(key, names, scorer=fuzz.ratio, score_cutoff=88)
    return aids[hit[2]] if hit else None


def _anime_xml(aid: int, client: str, clientver: str) -> ET.Element:
    f = cache_file("anidb", f"anime-{aid}", ".xml")
    if f.exists() and time.time() - f.stat().st_mtime < 86400 * 14:
        return ET.fromstring(f.read_bytes())
    _anidb_limit.wait()
    r = session().get(API_URL, params={
        "request": "anime", "client": client, "clientver": clientver,
        "protover": 1, "aid": aid,
    }, timeout=30)
    r.raise_for_status()
    root = _xml(r.content)
    if root.tag == "error":
        raise ProviderError(f"AniDB: {root.text}")
    f.write_bytes(ET.tostring(root))
    return root


def _lookup_anidb(title: str, client: str, clientver: str) -> Metadata | None:
    aid = find_aid(title)
    if not aid:
        return None
    root = _anime_xml(aid, client, clientver)
    main = next((t.text for t in root.iter("title") if t.get("type") == "main"), None)
    rating = root.findtext("ratings/permanent") or root.findtext("ratings/temporary")
    start = root.findtext("startdate") or ""
    pic = root.findtext("picture")
    desc = root.findtext("description")
    url = f"https://anidb.net/anime/{aid}"
    scores = [ScoreEntry("AniDB", float(rating), 10, url)] if rating else []
    return Metadata(
        scores=scores,
        title=main,
        year=int(start[:4]) if start[:4].isdigit() else None,
        rating=float(rating) if rating else None,
        rating_max=10,
        rating_source="AniDB",
        external_id=str(aid),
        external_url=f"https://anidb.net/anime/{aid}",
        poster_url=IMAGE_URL.format(pic) if pic else None,
        summary=desc,
    )


def _lookup_jikan(title: str, year: int | None, kind: str | None = None) -> Metadata | None:
    def fetch():
        _jikan_limit.wait()
        r = session().get(JIKAN_URL, params={"q": title, "limit": 10, "sfw": "false"}, timeout=20)
        if r.status_code == 429:
            time.sleep(2)
            r = session().get(JIKAN_URL, params={"q": title, "limit": 10}, timeout=20)
        r.raise_for_status()
        return r.json()

    data = cached_json("jikan", title.lower(), 14, fetch)
    best, best_score = None, 0.0
    for a in data.get("data") or []:
        names = [a.get("title") or "", a.get("title_english") or ""] + [
            t.get("title", "") for t in a.get("titles") or []]
        score = max(similarity(title, n) for n in names if n) if any(names) else 0
        score += format_bonus(a.get("type"), kind)
        if year and a.get("year") == year:
            score += 5
        if score > best_score:
            best, best_score = a, score
    if not best or best_score < 75:
        return None
    img = ((best.get("images") or {}).get("jpg") or {})
    scores = [ScoreEntry("MAL", best["score"], 10, best.get("url"))] if best.get("score") else []
    return Metadata(
        scores=scores,
        title=best.get("title_english") or best.get("title"),
        year=best.get("year"),
        rating=best.get("score"),
        rating_max=10,
        rating_source="MAL",
        external_id=str(best.get("mal_id")),
        external_url=best.get("url"),
        poster_url=img.get("large_image_url") or img.get("image_url"),
        summary=best.get("synopsis"),
    )


def lookup_anidb(title: str, client: str, clientver: str) -> Metadata | None:
    """AniDB, only when an HTTP client is configured."""
    return _lookup_anidb(title, client, clientver or "1") if client else None


def lookup_mal(title: str, year: int | None, kind: str | None = None) -> Metadata | None:
    """MyAnimeList through Jikan, no key needed."""
    return _lookup_jikan(title, year, kind)
