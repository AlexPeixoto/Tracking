"""Leftovers: dotted abbreviations, repack shorthand, keyless movie scores, card text."""

from tracking import titles
from tracking.providers import wikidata
from tracking.providers.base import search_variants


def test_dotted_abbreviations_keep_their_dots():
    assert titles.parse_video("B.C. 2033 (1987-91)").title == "B.C. 2033"
    assert titles.parse_video("A.D. Police Files (1990)").title == "A.D. Police Files"
    assert titles.parse_video("A.D.P, To Protect and Serve (1999)").title.startswith("A.D.P,")
    v = titles.parse_video("Mobile Suit GUNDAM 0079 - Ep. 01 - Gundam Rising (720p DUAL Audio).mkv")
    assert (v.title, v.episode) == ("Mobile Suit GUNDAM 0079", 1)


def test_repack_shorthand_is_spelled_out_for_searches():
    assert "Mobile Suit Gundam SEED BATTLE DESTINY REMASTERED" in search_variants(
        "MSG SEED BATTLE DESTINY REMASTERED")
    assert "The Hundred Line: Last Defense Academy" in search_variants("The Hundred Line - LDA")
    assert "Fatal Frame: Mask of the Lunar Eclipse" in search_variants("FF-PZ - Mask of the Lunar Eclipse")
    assert search_variants("MSGames Collection") == ["MSGames Collection"]   # whole words only
    assert len(search_variants("System Shock 2 - 25th AR")) <= 8


def test_wikidata_review_scores():
    reviews = [("Rotten Tomatoes", "83%"), ("Metacritic", "74/100"), ("Rotten Tomatoes", "7.6/10"),
               ("Rotten Tomatoes", "92%"), ("Some Magazine", "5/5")]
    got = {s.source: s.value for s in wikidata.parse(reviews)}
    assert got == {"Rotten Tomatoes": 92.0, "Metacritic": 74.0}
