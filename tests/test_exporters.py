"""Exports for MyAnimeList, Letterboxd and plain CSV."""

import csv
import xml.etree.ElementTree as ET

import pytest

from tracking import completions, db, exporters, library


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("TRACKING_DATA_DIR", str(tmp_path / "data"))
    c = db.connect()
    yield c
    c.close()


def _item(conn, title, category, **cols):
    iid = conn.execute("INSERT INTO items(volume_uid, rel_path, is_dir, category, title) "
                       "VALUES('v', ?, 1, ?, ?)", (f"x/{title}", category, title)).lastrowid
    for k, v in cols.items():
        conn.execute(f"UPDATE items SET {k}=? WHERE id=?", (v, iid))
    return iid


def test_statuses():
    from tracking.db import Item
    base = dict(id=1, volume_uid="v", rel_path="x", is_dir=True, category="anime", platform=None,
                title="t", year=None, file_count=0, size_bytes=0, duration_sec=None, hltb_hours=None,
                hltb_extra=None, hltb_complete=None, completed=False, completed_at=None, notes=None,
                kind=None, season=None, detected_how=None, source="disk", steam_appid=None,
                playtime_min=None, last_played=None, rating=None, rating_max=None,
                rating_source=None, external_url=None, poster=None, summary=None, missing=False,
                detected_title="t", video_count=0)
    assert exporters.status(Item(**base)) == "Plan to Watch"
    assert exporters.status(Item(**{**base, "last_episode": 3})) == "Watching"
    assert exporters.status(Item(**{**base, "plan_state": "hold"})) == "On-Hold"
    assert exporters.status(Item(**{**base, "plan_state": "dropped"})) == "Dropped"
    assert exporters.status(Item(**{**base, "completed": True, "plan_state": "hold"})) == "Completed"


def test_mal_xml_letterboxd_and_csv(conn, tmp_path):
    a = _item(conn, "Frieren", "anime")
    _item(conn, "Mushishi", "anime", last_episode=7)
    _item(conn, "Unknown Thing", "anime")
    m = _item(conn, "Dune Part Two", "movie", year=2024, external_id="tt15239678")
    _item(conn, "Heat", "movie", year=1995)
    library.set_completed(conn, [a, m], True)
    conn.execute("UPDATE completions SET completed_on='2024-06-02' WHERE item_id=?", (m,))
    completions.add(conn, "Alien", "2023-10-31", "movie", link=False)      # from a year CSV
    library.add_wanted(conn, "movie", "Sinners", 2025)
    conn.commit()
    ids = {"Frieren": (52991, 28), "Mushishi": (457, 26)}
    res = exporters.export(conn, tmp_path, {"mal", "letterboxd", "all"},
                           lookup=lambda i: ids.get(i.title))
    assert {p.name for p in res.files} == {"myanimelist-anime.xml", "letterboxd-diary.csv",
                                           "letterboxd-watchlist.csv", "tracking-library.csv"}
    assert res.skipped == ["Unknown Thing"] and "left out" in res.summary()

    root = ET.parse(tmp_path / "myanimelist-anime.xml").getroot()
    anime = {a.findtext("series_title"): a for a in root.iter("anime")}
    assert anime["Frieren"].findtext("my_status") == "Completed"
    assert anime["Frieren"].findtext("my_watched_episodes") == "28"
    assert anime["Mushishi"].findtext("my_status") == "Watching"
    assert anime["Mushishi"].findtext("my_watched_episodes") == "7"
    assert anime["Mushishi"].findtext("series_animedb_id") == "457"

    with open(tmp_path / "letterboxd-diary.csv", encoding="utf-8") as f:
        diary = list(csv.DictReader(f))
    assert {(r["Title"], r["imdbID"], r["WatchedDate"]) for r in diary} == {
        ("Dune Part Two", "tt15239678", "2024-06-02"), ("Alien", "", "2023-10-31")}
    with open(tmp_path / "letterboxd-watchlist.csv", encoding="utf-8") as f:
        watch = [r["Title"] for r in csv.DictReader(f)]
    assert watch == ["Heat", "Sinners"] or sorted(watch) == ["Heat", "Sinners"]
    with open(tmp_path / "tracking-library.csv", encoding="utf-8") as f:
        rows = {r["title"]: r for r in csv.DictReader(f)}
    assert rows["Sinners"]["status"] == "Want to acquire"
    assert rows["Dune Part Two"]["finished_on"] == "2024-06-02"
