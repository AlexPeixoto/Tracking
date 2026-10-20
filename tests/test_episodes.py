"""Episode lists: parsing each service's answer (no network), choosing a source,
and matching episodes to files on disk."""

import xml.etree.ElementTree as ET

from tracking import db, enrich
from tracking.providers import Metadata, Query, anidb, mal, tvmaze


def fake_cache(responses):
    """Stand-in for cached_json: answers by key prefix, never calls fetch()."""
    def cached_json(provider, key, max_age_days, fetch):
        for prefix, value in responses.items():
            if key.startswith(prefix):
                return value
        raise AssertionError(f"unexpected request {provider}:{key}")
    return cached_json


def test_tvmaze_episodes_for_the_items_season(monkeypatch):
    monkeypatch.setattr(tvmaze, "cached_json", fake_cache({"episodes-": [
        {"season": 1, "number": 1, "name": "Pilot", "airdate": "2024-05-08", "runtime": 55,
         "rating": {"average": 7.3}, "url": "u1"},
        {"season": 1, "number": None, "name": "Special"},
        {"season": 2, "number": 1, "name": "Next", "rating": {"average": None}},
    ]}))
    eps = tvmaze.episodes(Query("Dark Matter", season=1), "123")
    assert [(e.season, e.number, e.title, e.score) for e in eps] == [(1, 1, "Pilot", 7.3)]
    eps2 = tvmaze.episodes(Query("Dark Matter", season=2), "123")
    assert [(e.number, e.score) for e in eps2] == [(1, None)]


def test_mal_episodes_follow_pages(monkeypatch):
    page = lambda n, more, eps: {"data": eps, "pagination": {"has_next_page": more}}
    monkeypatch.setattr(mal, "cached_json", fake_cache({
        "episodes-5-1": page(1, True, [{"mal_id": 1, "title": "A", "score": 4.5, "aired": "2013-10-04T00:00:00"}]),
        "episodes-5-2": page(2, False, [{"mal_id": 2, "title": "B", "score": None, "filler": True}]),
    }))
    eps = mal.episodes(Query("Kill la Kill"), "5")
    assert [(e.number, e.score, e.score_max, e.aired, e.note) for e in eps] == [
        (1, 4.5, 5, "2013-10-04", None), (2, None, 5, None, "filler")]


def test_anidb_regular_episodes_only(monkeypatch):
    xml = """<anime id="1"><episodes>
      <episode id="11"><epno type="1">2</epno><length>24</length><airdate>2013-10-11</airdate>
        <rating votes="9">8.12</rating><title xml:lang="en">Second</title></episode>
      <episode id="10"><epno type="1">1</epno><length>24</length>
        <title xml:lang="x-jat">Ichi</title></episode>
      <episode id="99"><epno type="2">S1</epno><title xml:lang="en">Special</title></episode>
    </episodes></anime>"""
    monkeypatch.setattr(anidb, "_anime_xml", lambda aid, c, v: ET.fromstring(xml))
    eps = anidb.episodes(Query("Kill la Kill", settings={"anidb_client": "x"}), "9")
    assert [(e.number, e.title, e.score, e.url) for e in eps] == [
        (1, "Ichi", None, "https://anidb.net/episode/10"),
        (2, "Second", 8.12, "https://anidb.net/episode/11")]


def test_episode_source_priority_and_on_disk(tmp_path, monkeypatch):
    monkeypatch.setenv("TRACKING_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(tvmaze, "cached_json", fake_cache({"episodes-": [
        {"season": 1, "number": n, "name": f"E{n}", "rating": {"average": 7 + n / 10}} for n in (1, 2, 3)]}))
    q = Query("Show", category="tv", kind="series", season=1)
    # TMDB has no key, so it found nothing; TVmaze did.
    source, eps = enrich.fetch_episodes(q, {"tvmaze": Metadata(external_id="77")})
    assert source == "TVmaze" and len(eps) == 3

    with db.session() as conn:
        iid = conn.execute("INSERT INTO items(volume_uid, rel_path, is_dir, category, title) "
                           "VALUES('v', 'Show', 1, 'tv', 'Show')").lastrowid
        conn.execute("INSERT INTO media_files(item_id, rel_path, size_bytes, mtime, season, episode) "
                     "VALUES(?, 'Show/S01E02.mkv', 1, 0, 1, 2)", (iid,))
        enrich.save_episodes(conn, iid, source, eps)
        rows = db.get_episodes(conn, iid)
        assert [(e.number, e.on_disk) for e in rows] == [(1, False), (2, True), (3, False)]


def test_scanner_records_episode_numbers(tmp_path):
    from tracking import scan
    from tracking.scan.classify import Hints

    p = tmp_path / "TV Shows" / "Severance" / "Severance.S02E03.mkv"
    p.parent.mkdir(parents=True)
    p.write_bytes(b"x")
    item = scan.collect(str(tmp_path), "", Hints())[0]
    assert [(v.season, v.episode) for v in item.videos] == [(2, 3)]


def test_kitsu_episodes_paginate(monkeypatch):
    from tracking.providers import kitsu

    def page(numbers):
        return {"data": [{"attributes": {"number": n, "canonicalTitle": f"Ep {n}", "airdate": "2015-07-08",
                                         "length": 4}} for n in numbers]}
    monkeypatch.setattr(kitsu, "cached_json", fake_cache({
        "episodes-7-0": page(range(1, 21)), "episodes-7-1": page(range(21, 25))}))
    eps = kitsu.episodes(Query("Bikini Warriors", season=1), "7")
    assert [e.number for e in eps] == list(range(1, 25))
    assert (eps[0].title, eps[0].runtime_min, eps[0].score) == ("Ep 1", 4, None)


def test_episode_list_falls_back_to_files(tmp_path, monkeypatch):
    monkeypatch.setenv("TRACKING_DATA_DIR", str(tmp_path))
    with db.session() as conn:
        iid = conn.execute("INSERT INTO items(volume_uid, rel_path, is_dir, category, title, kind) "
                           "VALUES('v', 'Show', 1, 'anime', 'Obscure Show', 'series')").lastrowid
        for e in (1, 2, 4):
            conn.execute("INSERT INTO media_files(item_id, rel_path, size_bytes, mtime, season, episode) "
                         "VALUES(?, ?, 1, 0, 1, ?)", (iid, f"Show/{e:02d}.mkv", e))
        item = db.get_item(conn, iid)
        source, eps = enrich.episodes_from_files(conn, item)
        assert source == enrich.FILES_SOURCE and [e.number for e in eps] == [1, 2, 4]
        enrich.save_episodes(conn, iid, source, eps)
        assert all(e.on_disk for e in db.get_episodes(conn, iid))
