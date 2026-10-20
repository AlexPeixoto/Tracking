"""Movies / TV: Rotten Tomatoes and Metacritic scores without a key, from Wikidata
(open data, CC0). The IMDb id comes from IMDb's public title search; Wikidata's
"review score" statements for that id give the scores. Coverage is good for
well-known films and shows; OMDb (with a free key) covers the rest.
"""

from __future__ import annotations

import re

from . import omdb
from .base import Metadata, Provider, Query, RateLimiter, ScoreEntry, cached_json, session

SPARQL = "https://query.wikidata.org/sparql"
_QUERY = """SELECT ?score ?byLabel WHERE {{
  ?item wdt:P345 "{imdb}" . ?item p:P444 ?st . ?st ps:P444 ?score .
  OPTIONAL {{ ?st pq:P447 ?by }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
}}"""
_limit = RateLimiter(1.0)  # Wikidata asks for gentle, identified use


def review_scores(imdb_id: str) -> list[tuple[str, str]]:
    """(reviewer, score text) pairs Wikidata lists for an IMDb id, e.g. ("Metacritic", "74/100")."""
    def fetch():
        _limit.wait()
        r = session().get(SPARQL, params={"query": _QUERY.format(imdb=imdb_id)},
                          headers={"Accept": "application/sparql-results+json"}, timeout=30)
        r.raise_for_status()
        return [[(b.get("byLabel") or {}).get("value", ""), b["score"]["value"]]
                for b in r.json()["results"]["bindings"]]
    return [tuple(x) for x in cached_json("wikidata", imdb_id, 30, fetch)]


def parse(reviews: list[tuple[str, str]], url: str | None = None) -> list[ScoreEntry]:
    """Rotten Tomatoes (the % critics score) and Metacritic, the latest value of each."""
    found: dict[str, ScoreEntry] = {}
    for by, text in reviews:
        if by == "Rotten Tomatoes":
            m = re.fullmatch(r"\s*(\d{1,3})\s*%\s*", text)
            if m:
                found["Rotten Tomatoes"] = ScoreEntry("Rotten Tomatoes", float(m.group(1)), 100, url)
        elif by == "Metacritic":
            m = re.fullmatch(r"\s*(\d{1,3})\s*/\s*100\s*", text)
            if m:
                found["Metacritic"] = ScoreEntry("Metacritic", float(m.group(1)), 100, url)
    return list(found.values())


def lookup(q: Query) -> Metadata | None:
    kind = "series" if q.category == "tv" else "movie"
    title = re.sub(r"(?i)\s+season\s+\d+$", "", q.title)  # scores are per show
    hit = omdb._lookup_suggest(title, q.year, kind)
    if not hit or not hit.external_id:
        return None
    scores = parse(review_scores(hit.external_id), hit.external_url)
    if not scores:
        return None
    return Metadata(external_id=hit.external_id, external_url=hit.external_url, scores=scores)


PROVIDER = Provider("wikidata", "Wikidata (Rotten Tomatoes, Metacritic)", frozenset({"movie", "tv"}),
                    lookup, cover_rank=90)
