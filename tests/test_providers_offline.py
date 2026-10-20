"""Services' response parsing and matching, offline: canned answers stand in for the
network (every service fetches through ``cached_json``)."""

import pytest

from tracking.providers import Query, anilist, hltb, omdb, steam, tvmaze, wikidata


def canned(module, monkeypatch, answers: dict):
    """Make ``module.cached_json`` answer from ``answers`` (key prefix -> data)."""
    def fake(provider, key, age, fetch):
        for prefix, data in answers.items():
            if key.startswith(prefix):
                return data
        raise AssertionError(f"unexpected fetch {provider}:{key}")
    monkeypatch.setattr(module, "cached_json", fake)


def test_anilist_prefers_the_right_format(monkeypatch):
    media = [
        {"title": {"romaji": "Sousou no Frieren", "english": "Frieren: Beyond Journey's End"},
         "synonyms": [], "seasonYear": 2023, "format": "TV", "averageScore": 91,
         "siteUrl": "https://anilist.co/anime/154587", "coverImage": {"large": "f.jpg"},
         "description": "<b>After</b> the party..."},
        {"title": {"romaji": "Sousou no Frieren: Mini", "english": None}, "synonyms": [],
         "seasonYear": 2023, "format": "ONA", "averageScore": 70, "siteUrl": "x", "coverImage": {}},
    ]
    canned(anilist, monkeypatch, {"": {"data": {"Page": {"media": media}}}})
    md = anilist.lookup(Query("Sousou no Frieren", category="anime", kind="series"))
    assert (md.title, md.year, md.poster_url) == ("Frieren: Beyond Journey's End", 2023, "f.jpg")
    assert md.summary == "After the party..." and md.scores[0].value == 91
    found = anilist.search(Query("Frieren", category="anime"))
    assert [c.detail for c in found] == ["Anime · Tv", "Anime · Ona"]


def test_tvmaze_show_episodes_and_search(monkeypatch):
    show = {"id": 44933, "name": "Severance", "premiered": "2022-02-18", "type": "Scripted",
            "rating": {"average": 8.4}, "url": "https://www.tvmaze.com/shows/44933",
            "image": {"original": "s.jpg"}, "summary": "<p>Mark leads a team</p>",
            "network": None, "webChannel": {"name": "Apple TV+"}, "status": "Running"}
    eps = [{"season": 1, "number": 1, "name": "Good News About Hell", "airdate": "2022-02-18",
            "runtime": 57, "rating": {"average": 8.1}, "url": "e1"},
           {"season": 1, "number": None, "name": "Special"},
           {"season": 2, "number": 1, "name": "Hello, Ms. Cobel", "airdate": "2025-01-17"}]
    canned(tvmaze, monkeypatch, {"episodes-": eps, "": [{"show": show}]})
    md = tvmaze.lookup(Query("Severance", category="tv"))
    assert (md.title, md.year, md.external_id, md.scores[0].value) == ("Severance", 2022, "44933", 8.4)
    assert md.summary == "Mark leads a team"
    got = tvmaze.episodes(Query("Severance", category="tv", season=1), "44933")
    assert [(e.number, e.title, e.score) for e in got] == [(1, "Good News About Hell", 8.1)]
    assert tvmaze.search(Query("Severance", category="tv"))[0].detail == "TV series · Apple TV+ · Running"


def test_steam_lookup_by_search_and_by_app_id(monkeypatch):
    answers = {
        "search-": {"items": [{"id": 2, "name": "Hades Soundtrack"}, {"id": 1145360, "name": "Hades"}]},
        "details-1145360": {"1145360": {"data": {
            "name": "Hades", "release_date": {"date": "17 Sep, 2020"}, "short_description": "Defy",
            "header_image": "header.jpg", "metacritic": {"score": 93, "url": "mc"}}}},
        "reviews-": {"query_summary": {"total_reviews": 200, "total_positive": 196}},
        "art-": {"ok": True},
    }
    canned(steam, monkeypatch, answers)
    md = steam.lookup(Query("Hades", category="game", platform="PC"))
    assert (md.title, md.year, md.poster_url, md.cover_first) == ("Hades", 2020, "header.jpg", False)
    assert {s.source: s.value for s in md.scores} == {"Steam": 98.0, "Metacritic": 93}
    md = steam.lookup(Query("anything", category="game", steam_appid=1145360))
    assert md.cover_first and md.poster_url.endswith("/1145360/library_600x900_2x.jpg")
    assert [c.title for c in steam.search(Query("Hades", category="game"))] == ["Hades"]


def test_imdb_suggestion_picks_kind_and_year_and_a_sized_poster(monkeypatch):
    data = {"d": [
        {"id": "tt0087182", "l": "Dune", "y": 1984, "qid": "movie",
         "i": {"imageUrl": "https://m.media-amazon.com/images/M/x._V1_.jpg"}},
        {"id": "tt1160419", "l": "Dune", "y": 2021, "qid": "movie",
         "i": {"imageUrl": "https://m.media-amazon.com/images/M/y._V1_.jpg"}},
        {"id": "tt10466872", "l": "Dune: Prophecy", "y": 2024, "qid": "tvSeries"},
    ]}
    canned(omdb, monkeypatch, {"": data})
    md = omdb._lookup_suggest("Dune", 2021)
    assert md.external_id == "tt1160419"
    assert md.poster_url.endswith("y._V1_UX600_.jpg")                 # not the 10 MB original
    found = omdb.search(Query("Dune", category="tv"))
    assert [c.title for c in found] == ["Dune: Prophecy"]


def test_hltb_platform_bonus_and_search(monkeypatch):
    results = [
        {"name": "Demon's Souls", "main": 25.0, "extra": 40.0, "complete": 60.0, "image": "ps5.jpg",
         "url": "u5", "id": 5, "year": 2020, "score": 88, "platforms": ["PlayStation 5"]},
        {"name": "Demon's Souls", "main": 27.0, "extra": 42.0, "complete": 70.0, "image": "ps3.jpg",
         "url": "u3", "id": 3, "year": 2009, "score": 84, "platforms": ["PlayStation 3"]},
    ]
    monkeypatch.setattr(hltb, "_results", lambda q: results)
    md = hltb.lookup(Query("Demon's Souls", category="game", platform="PS3"))
    assert (md.poster_url, md.hltb_hours, md.scores[0].value) == ("ps3.jpg", 27.0, 84)
    found = hltb.search(Query("Demon's Souls", category="game"))
    assert found[0].detail == "HowLongToBeat · PlayStation 5 · 25 h"


def test_wikidata_needs_an_imdb_id(monkeypatch):
    monkeypatch.setattr(omdb, "_lookup_suggest", lambda t, y, k: None)
    assert wikidata.lookup(Query("Unknown film", category="movie")) is None
    from tracking.providers.base import Metadata
    monkeypatch.setattr(omdb, "_lookup_suggest", lambda t, y, k: Metadata(
        external_id="tt1160419", external_url="https://www.imdb.com/title/tt1160419/"))
    monkeypatch.setattr(wikidata, "review_scores", lambda i: [("Metacritic", "74/100")])
    md = wikidata.lookup(Query("Dune Season 2", category="tv"))
    assert md.scores[0].value == 74 and md.external_id == "tt1160419"


@pytest.mark.parametrize("module", [anilist, tvmaze, steam])
def test_empty_answers_find_nothing(module, monkeypatch):
    canned(module, monkeypatch, {"": {"items": [], "data": {}} if module is not tvmaze else []})
    assert module.lookup(Query("Nothing", category="game" if module is steam else "tv")) is None
