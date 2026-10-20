"""Anime: AniDB rating + picture.

AniDB's HTTP API needs a registered client (https://anidb.net/software/add,
"HTTP API" client, any name / version 1). Their rules: at most one request every
2 seconds, cache everything, and don't download the title dump more than once
a day — all of which this module does. Getting banned is temporary but annoying.

"""

from __future__ import annotations

import gzip
import threading
import time
import xml.etree.ElementTree as ET

from rapidfuzz import fuzz, process

from .. import titles
from .base import (
    Episode,
    Metadata,
    Provider,
    ProviderError,
    Query,
    RateLimiter,
    ScoreEntry,
    Setting,
    cache_file,
    session,
)

TITLES_URL = "https://anidb.net/api/anime-titles.xml.gz"
API_URL = "http://api.anidb.net:9001/httpapi"
IMAGE_URL = "https://cdn-eu.anidb.net/images/main/{}"

_anidb_limit = RateLimiter(2.5)

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


def lookup(q: Query) -> Metadata | None:
    aid = find_aid(q.title)
    if not aid:
        return None
    root = _anime_xml(aid, q.setting("anidb_client"), q.setting("anidb_clientver") or "1")
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
        external_id=str(aid),
        external_url=url,
        poster_url=IMAGE_URL.format(pic) if pic else None,
        summary=desc,
    )


def episodes(q: Query, aid: str) -> list[Episode]:
    """Regular episodes from the anime record we already fetched (cached), with
    AniDB's per-episode rating."""
    root = _anime_xml(int(aid), q.setting("anidb_client"), q.setting("anidb_clientver") or "1")
    season, out = q.season or 1, []
    for ep in root.iter("episode"):
        epno = ep.find("epno")
        if epno is None or epno.get("type") != "1" or not (epno.text or "").isdigit():
            continue  # specials, credits, trailers
        titles_ = {t.get("{http://www.w3.org/XML/1998/namespace}lang"): t.text for t in ep.iter("title")}
        rating = ep.findtext("rating")
        length = ep.findtext("length")
        out.append(Episode(season, int(epno.text), titles_.get("en") or titles_.get("x-jat"),
                           ep.findtext("airdate"), float(length) if length else None,
                           float(rating) if rating else None, 10,
                           f"https://anidb.net/episode/{ep.get('id')}"))
    return sorted(out, key=lambda e: e.number)


PROVIDER = Provider(
    "anidb", "AniDB", frozenset({"anime"}), lookup, episodes=episodes,
    settings=(
        Setting("anidb_client", "AniDB client name",
                "AniDB score (the headline anime rating when set). Register an HTTP API client "
                "at anidb.net/software/add.", secret=False),
        Setting("anidb_clientver", "AniDB client version",
                "The version you registered, usually 1.", secret=False),
    ),
    required=("anidb_client",), cover_rank=10,
)
