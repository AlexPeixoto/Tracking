"""The full sweep of a real library (titles vs folders vs files): every case found, as a test."""

from pathlib import Path

import pytest

from tracking import scan, titles
from tracking.providers import anilist, variants
from tracking.scan import classify
from tracking.scan.classify import Hints
from tracking.scan.videos import display_title, is_extra_dir, is_extra_file


def touch(root: Path, rel: str, data: bytes = b"x") -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)


@pytest.mark.parametrize("name, title, season, episode", [
    # "v2" re-releases are still episodes (guessit called them movies)
    ("[SubsPlus+] I'm Dating a Dark Summoner - S01E01v2 (AMZN WEB-DL 1080p).mkv",
     "I'm Dating a Dark Summoner", 1, 1),
    ("[Judas] Gundam GQuuuuuuX - S01E01v2.mkv", "Gundam GQuuuuuuX", 1, 1),
    # the end of a name guessit drops ("Ru" read as Russian, "Life" lost)
    ("[Judas] To Love-Ru - S01E01.mkv", "To Love-Ru", 1, 1),
    ("[Judas] To Love-Ru S1 OVAs - Trouble (2009)", "To Love-Ru", 1, None),
    ("No Game No Life [BD][1080p][HEVC 10bit x265][Dual Audio][Tenrai-Sensei]", "No Game No Life", None, None),
    # a subtitle before the episode number is part of the show's name
    ("Bleach - Thousand Year Blood War - S01E48 - The End Two Worlds.mkv",
     "Bleach: Thousand Year Blood War", 1, 48),
    ("Brotherhood - Final Fantasy XV, E01-E05 (1080p JAP Audio - 2016 Web-DL).mp4",
     "Brotherhood: Final Fantasy XV", None, 1),
    # a number that names the show
    ("[Exiled-Destiny]_Megazone_23_Movie1_Part1_(954F4E1A).mkv", "Megazone 23", None, None),
    # specials written "S01OVA01" / "S01OVA" (season 0 joins the show's first season)
    ("S01OVA01-After Washing Your Body... [1EE70EE8].mkv", "", 0, 1),
    ("[Judas] Sora no Otoshimono - S01OVA.mkv", "Sora no Otoshimono", 0, 1),
    # "06.5" isn't an order number with a title "5 ..."
    ("06.5 Kara No Kyoukai Remix - Gate Of Seventh Heaven.mkv", "Kara No Kyoukai Remix", None, 6),
    # what must not change
    ("Kara No Kyoukai - Full Series [All Movie][1080p][HEVC 10bit x265][AAC]", "Kara No Kyoukai", None, None),
    ("Ghost in the Shell Stand Alone Complex 1st GIG v2 (BD 690p)",
     "Ghost in the Shell Stand Alone Complex 1st GIG", None, None),
    ("[Judas] Mushishi S1", "Mushishi", 1, None),
    ("CITY THE ANIMATION S01 (WEB 1080p AV1 Opus) [Multi Subs]", "CITY THE ANIMATION", 1, None),
    ("[Judas] 86 - S01E01.mkv", "86", 1, 1),
])
def test_release_names(name, title, season, episode):
    v = titles.parse_video(name)
    assert (v.title, v.season, v.episode) == (title, season, episode)


def test_stray_special_folds_into_its_show(tmp_path):
    for n in range(1, 4):
        touch(tmp_path, f"anime/86 (Eighty Six)/[Judas] 86 - S01E0{n}.mkv")
    touch(tmp_path, "anime/86 (Eighty Six)/[Judas] 86 - Eighty-Six - S01E11.5.mkv")
    found = scan.collect(str(tmp_path), "", Hints())
    assert [(i.title, len(i.videos)) for i in found] == [("86", 4)]


def test_ova_specials_join_their_show(tmp_path):
    folder = "anime/Isekai Meikyuu de Harem wo S01+OVA 1080p BDRip"
    for n in range(1, 4):
        touch(tmp_path, f"{folder}/S01E0{n}-Episode {n} [1624B4DA].mkv")
    touch(tmp_path, f"{folder}/S01OVA01-After Washing Your Body... [1EE70EE8].mkv")
    found = scan.collect(str(tmp_path), "", Hints())
    assert [(i.title, len(i.videos)) for i in found] == [("Isekai Meikyuu de Harem wo", 4)]


def test_season_number_isnt_said_twice():
    assert display_title("Tenchi Muyo! Ryo-Ohki 2", "series", 2) == "Tenchi Muyo! Ryo-Ohki 2"
    assert display_title("Laid-Back Camp", "series", 2) == "Laid-Back Camp Season 2"


def test_extras_in_collections():
    assert is_extra_dir("2b. FF Extras") and is_extra_dir("4c. Extras") and not is_extra_dir("2a. Fatal Fury")
    assert is_extra_file("Fatal Fury (TMP) - Extras - Big Bear vs Cheng Sinzan (480p).mkv")
    assert not is_extra_file("Fatal Fury - S00 E01 - Legend of the Hungry Wolf.mkv")


def test_games_sweep(tmp_path):
    assert titles.clean_game_title("V.I.P. (USA)") == "V.I.P"
    assert titles.clean_game_title("A.I.L.A. [FitGirl Repack]") == "A.I.L.A"
    assert classify.platform_from_name("DOS") == "DOS"
    assert classify.is_tool_name("Godot_v4.3-stable_win64.exe")
    assert classify.is_tool_name("ps3-disc-dumper_windows_v4.4.1")
    touch(tmp_path, "EMULATION/3ds/log/citra.log")
    touch(tmp_path, "EMULATION/3ds/Pokemon X (USA).3ds")
    touch(tmp_path, "EMULATION/Drivers/driver.inf")
    found = {i.title for i in scan.collect(str(tmp_path), "", Hints())}
    assert "Log" not in found and "Drivers" not in found


def test_search_wording_from_the_sweep():
    v = variants.search_variants
    assert "Mobile Suit ZETA Gundam" in v("MS ZETA Gundam")
    assert "Final Fantasy VI" in v("FF6")
    assert "Fatal Fury" in v("Fatal Fury Series")
    assert v("Batman - The Animated Series")[-1] != "Batman - The Animated"
    assert v("CITY THE ANIMATION") == ["CITY THE ANIMATION"]
    assert v("Turing Complete") == ["Turing Complete"]


def test_series_hint_is_a_preference(monkeypatch):
    movies = [{"title": {"romaji": "Kizumonogatari Part 1: Tekketsu"}, "format": "MOVIE", "seasonYear": 2016,
               "siteUrl": "https://anilist.co/anime/9260", "coverImage": {"large": "https://img/k.jpg"}}]
    monkeypatch.setattr(anilist, "_media", lambda title, fetch: movies)
    monkeypatch.setattr(anilist, "_fetcher", lambda title: None)
    md = anilist._search("Kizumonogatari", None, "series")
    assert md and md.poster_url == "https://img/k.jpg"
