"""Games: HowLongToBeat play times (main / +extras / completionist), user score and
cover. No key, via the howlongtobeatpy library (which breaks now and then when
HLTB changes their site; errors are reported, not fatal)."""

from __future__ import annotations

from .. import platforms
from .base import (
    Candidate, Metadata, Provider, ProviderError, Query, RateLimiter, ScoreEntry, best_match, cached_json,
    platform_matches,
)

_limit = RateLimiter(1.0)


def _results(q: Query) -> list[dict]:
    def fetch():
        from howlongtobeatpy import HowLongToBeat

        _limit.wait()
        results = HowLongToBeat().search(q.title, similarity_case_sensitive=False)
        if results is None:  # the library's way of saying the request failed: don't cache that
            raise ProviderError("HowLongToBeat didn't answer (try Update metadata again later)")
        return [
            {"name": r.game_name, "main": r.main_story, "extra": r.main_extra,
             "complete": r.completionist, "image": r.game_image_url, "url": r.game_web_link,
             "id": r.game_id, "year": r.release_world, "score": r.review_score,
             "platforms": r.profile_platforms or []}
            for r in results
        ]

    try:
        # "hltb3": older caches stored failed requests as "no results"
        return cached_json("hltb3", q.title.lower(), 30, fetch)
    except ProviderError:
        raise
    except Exception as e:
        raise ProviderError(f"HowLongToBeat lookup failed: {e}") from e


def search(q: Query) -> list[Candidate]:
    """Every game HowLongToBeat has under that name (any platform), for Find match."""
    out = []
    for r in _results(q):
        plats = ", ".join(r.get("platforms") or [])
        hours = f" · {r['main']:.0f} h" if r.get("main") else ""
        out.append(Candidate(r.get("name") or "?", r.get("year") or None,
                             f"HowLongToBeat · {plats or 'game'}{hours}", r.get("image"), r.get("url")))
    return out


def lookup(q: Query) -> Metadata | None:
    results = _results(q)
    want = platforms.service_name(q.platform, "hltb")
    # The platform bonus picks the PS3 "Demon's Souls" over the PS5 remake.
    best = best_match(q.title, None, results, names=lambda r: [r.get("name")],
                      bonus=lambda r: 8 if platform_matches(want, r.get("platforms") or []) else 0)
    if not best:
        return None
    url = best.get("url")
    return Metadata(
        title=best.get("name"),
        year=best.get("year") or None,
        hltb_hours=best.get("main") or None,
        hltb_extra=best.get("extra") or None,
        hltb_complete=best.get("complete") or None,
        poster_url=best.get("image"),
        external_url=url,
        scores=[ScoreEntry("HLTB users", best["score"], 100, url)] if best.get("score") else [],
    )


PROVIDER = Provider("hltb", "HowLongToBeat", frozenset({"game"}), lookup, cover_rank=20, search=search)
