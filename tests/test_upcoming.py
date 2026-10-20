"""Coming up: next episodes, airing anime, releases and Steam sales (canned answers)."""

from datetime import date

from tracking import db, upcoming
from tracking.db import Item
from tracking.providers import anilist

BASE = dict(id=1, volume_uid="v", rel_path="x", is_dir=True, platform=None, year=None, file_count=0,
            size_bytes=0, duration_sec=None, hltb_hours=None, hltb_extra=None, hltb_complete=None,
            completed=False, completed_at=None, notes=None, kind="series", season=1,
            detected_how=None, source="disk", steam_appid=None, playtime_min=None, last_played=None,
            rating=None, rating_max=None, rating_source=None, external_url=None, poster=None,
            summary=None, missing=False, detected_title="t", video_count=0)
TODAY = date(2026, 10, 20)


def item(**kw):
    return Item(**{**BASE, "title": "x", "category": "tv", **kw})


def fake_cache(monkeypatch, answers):
    from tracking.providers import base

    def fake(provider, key, age, fetch):
        for prefix, data in answers.items():
            if key.startswith(prefix):
                return data
        raise AssertionError(key)
    monkeypatch.setattr(base, "cached_json", fake)


def test_next_episode_by_imdb_id(monkeypatch):
    fake_cache(monkeypatch, {
        "imdb-tt1": {"id": 44933},
        "next-44933": {"status": "Running", "_embedded": {"nextepisode": {
            "season": 3, "number": 1, "name": "Return", "airdate": "2026-10-23", "url": "u"}}},
    })
    [u] = upcoming.tv(item(external_id="tt1"), TODAY)
    assert u.text == "Next episode: S3E1 “Return” · Fri 23 Oct (in 3 days)" and u.highlight


def test_show_states(monkeypatch):
    fake_cache(monkeypatch, {"next-5": {"status": "Ended"}, "next-6": {"status": "Running"}})
    assert upcoming.tv(item(external_id="5"), TODAY)[0].text == "The show has ended"
    assert "no next episode" in upcoming.tv(item(external_id="6"), TODAY)[0].text
    assert upcoming.tv(item(external_id=None), TODAY) == []


def test_anime_airing(monkeypatch):
    monkeypatch.setattr(anilist, "airing", lambda aid: {
        "status": "RELEASING", "nextAiringEpisode": {"episode": 8, "airingAt": 1793000000}})
    [u] = upcoming.anime(item(category="anime", external_url="https://anilist.co/anime/99"), TODAY)
    assert u.text.startswith("Episode 8 airs") and u.url.endswith("/99")
    monkeypatch.setattr(anilist, "airing", lambda aid: {"status": "FINISHED", "episodes": 28})
    monkeypatch.setattr(anilist, "find", lambda *a: {"id": 7})
    assert upcoming.anime(item(category="anime"), TODAY)[0].text == "Finished airing (28 episodes)"
    assert upcoming.anime(item(category="anime", kind="movie"), TODAY) == []


def test_steam_release_and_sale(monkeypatch):
    fake_cache(monkeypatch, {
        "price-1": {"1": {"data": {"release_date": {"coming_soon": True, "date": "Q1 2027"},
                                   "price_overview": {"discount_percent": 0, "final_formatted": "$29.99"}}}},
        "price-2": {"2": {"data": {"price_overview": {"discount_percent": 50, "final_formatted": "$9.99",
                                                      "initial_formatted": "$19.99"}}}},
    })
    wanted = upcoming.game(item(category="game", steam_appid=1, source=db.WANTED), TODAY)
    assert [u.text for u in wanted] == ["Releases: Q1 2027", "Steam price: $29.99"]
    sale = upcoming.game(item(category="game", external_url="https://store.steampowered.com/app/2/"), TODAY)
    assert sale[0].text == "On sale on Steam: −50% · $9.99 (was $19.99)" and sale[0].highlight


def test_for_item_never_raises(monkeypatch):
    def boom(*a, **k):
        raise OSError("offline")
    monkeypatch.setattr(upcoming, "tv", boom)
    assert upcoming.for_item(item()) == []
    assert upcoming.for_item(item(category="movie")) == []
