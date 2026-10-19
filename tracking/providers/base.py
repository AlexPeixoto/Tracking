"""Shared bits for metadata providers: result type, HTTP session, disk cache."""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import requests
from rapidfuzz import fuzz

from .. import paths, titles

USER_AGENT = "Tracking/0.1 (personal media library browser)"


class ProviderError(Exception):
    """Something went wrong talking to a service (bad key, banned, offline...)."""


@dataclass
class ScoreEntry:
    source: str          # 'IMDb', 'Rotten Tomatoes', 'Metacritic', 'AniDB', 'MAL', ...
    value: float
    max_value: float
    url: str | None = None


@dataclass
class Metadata:
    title: str | None = None
    year: int | None = None
    rating: float | None = None
    rating_max: float | None = None
    rating_source: str | None = None
    external_id: str | None = None
    external_url: str | None = None
    poster_url: str | None = None
    summary: str | None = None
    hltb_hours: float | None = None
    hltb_extra: float | None = None
    hltb_complete: float | None = None
    scores: list[ScoreEntry] = field(default_factory=list)


_session_lock = threading.Lock()
_session: requests.Session | None = None


def session() -> requests.Session:
    global _session
    with _session_lock:
        if _session is None:
            _session = requests.Session()
            _session.headers["User-Agent"] = USER_AGENT
        return _session


class RateLimiter:
    def __init__(self, min_interval: float):
        self.min_interval = min_interval
        self._last = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            delay = self._last + self.min_interval - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            self._last = time.monotonic()


def cache_file(provider: str, key: str, suffix: str = ".json") -> Path:
    d = paths.cache_dir() / provider
    d.mkdir(exist_ok=True)
    return d / (hashlib.sha1(key.encode("utf-8")).hexdigest() + suffix)


def cached_json(provider: str, key: str, max_age_days: float, fetch) -> object:
    """Return cached JSON for ``key`` or call ``fetch()`` and store its result."""
    f = cache_file(provider, key)
    if f.exists() and time.time() - f.stat().st_mtime < max_age_days * 86400:
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except ValueError:
            pass
    data = fetch()
    f.write_text(json.dumps(data), encoding="utf-8")
    return data


def best_match(title: str, year: int | None, candidates, names, cand_year=lambda c: None,
               bonus=lambda c: 0.0, threshold: float = 75.0):
    """Pick the candidate whose names look most like ``title``.

    ``names(c)`` lists a candidate's titles (main, English, synonyms...),
    ``cand_year(c)`` its release year, ``bonus(c)`` any extra points (platform...).
    """
    best, best_score = None, 0.0
    for c in candidates or []:
        ns = [n for n in names(c) if n]
        if not ns:
            continue
        score = max(similarity(title, n) for n in ns) + bonus(c)
        cy = cand_year(c)
        if year and cy:
            score += 6 if cy == year else (2 if abs(cy - year) == 1 else -4)
        if score > best_score:
            best, best_score = c, score
    return best if best is not None and best_score >= threshold else None


def year_of(date: str | None) -> int | None:
    return int(date[:4]) if date and date[:4].isdigit() else None


_NUMBER = re.compile(r"(?<![\d.])(\d{1,3})(?:st|nd|rd|th)?\b", re.I)


def _numbers(s: str) -> set[str]:
    """Sequel / season numbers in a title. "1" counts as no number (season 1 is implied)."""
    return {n.lstrip("0") for n in _NUMBER.findall(s)} - {"1", ""}


def similarity(a: str, b: str) -> float:
    """0-100 title similarity, tolerant of punctuation and word order.

    Titles whose numbers disagree ("1st GIG" vs "2nd GIG", "Okami" vs "Okami 2")
    are pushed below the match threshold.
    """
    na, nb = titles.normalize(a), titles.normalize(b)
    if na and na == nb:
        return 100.0
    score = max(fuzz.ratio(na, nb), fuzz.token_sort_ratio(a.lower(), b.lower()))
    if _numbers(a) != _numbers(b):
        score -= 25
    return score


_FIRST_PART = re.compile(r"(?i)\s*(?:\b1st\s+\w+|\bfirst\s+season|\bseason\s*0*1|\bpart\s*0*1|\bs0*1)\s*$")


_ABBR = r"(?![IVX]+\b)[A-Z][A-Za-z]{0,3}[A-Z]"  # "UB", "EE", "LDA", "TLoH", "LoTC"


def search_variants(title: str) -> list[str]:
    """Other ways to search for a title when the exact one finds nothing.

    * "Show 1st GIG" / "Season 1" / "Part 1" -> "Show" (first seasons often have no number)
    * "INAZUMA ELEVEN - Victory Road" -> "INAZUMA ELEVEN: Victory Road" / "... Victory Road"
      (release names swap ':' for ' - ', which store searches don't like)
    * "RoboCop - Rogue City UB" -> "RoboCop - Rogue City" (drop a trailing edition abbreviation)
    * "MDA - RAIN CODE Plus" -> "RAIN CODE Plus" (drop a leading series abbreviation)
    """
    out = [title]

    def add(v: str) -> None:
        v = re.sub(r"\s+", " ", v).strip(" -:")
        if len(v) >= 3 and v not in out:
            out.append(v)

    add(_FIRST_PART.sub("", title))
    if " - " in title:
        add(title.replace(" - ", ": "))
        add(title.replace(" - ", " "))
    trimmed = re.sub(rf"\s+{_ABBR}$", "", title)
    if trimmed != title:
        add(trimmed)
        if " - " in trimmed:
            add(trimmed.replace(" - ", ": "))
    lead = re.match(rf"^{_ABBR}\s+-\s+(.+)$", title)
    if lead:
        add(lead.group(1))
    return out
