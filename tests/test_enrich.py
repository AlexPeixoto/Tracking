from tracking.enrich import combine
from tracking.providers.base import Metadata, ScoreEntry, best_match


def test_headline_rating_falls_back_when_primary_missing():
    # No OMDb key: no IMDb score, so TMDB becomes the card rating.
    md = combine("movie", {
        "omdb": Metadata(title="Heat", external_url="https://www.imdb.com/title/tt0113277/"),
        "tmdb": Metadata(scores=[ScoreEntry("TMDB", 7.9, 10)], poster_url="tmdb.jpg"),
        "trakt": Metadata(scores=[ScoreEntry("Trakt", 8.1, 10)]),
    })
    assert (md.rating, md.rating_source) == (7.9, "TMDB")
    assert md.external_url.startswith("https://www.imdb.com")
    assert md.poster_url == "tmdb.jpg"
    assert [s.source for s in md.scores] == ["TMDB", "Trakt"]


def test_game_scores_dedupe_and_prefer_metacritic():
    md = combine("game", {
        "hltb": Metadata(hltb_hours=22.0, poster_url="hltb.jpg", scores=[ScoreEntry("HLTB users", 88, 100)]),
        "steam": Metadata(poster_url="steam.jpg", summary="From Steam",
                          scores=[ScoreEntry("Steam", 98, 100), ScoreEntry("Metacritic", 93, 100)]),
        "igdb": Metadata(poster_url="igdb.jpg", scores=[ScoreEntry("Metacritic", 1, 100)]),
    })
    assert (md.rating, md.rating_source) == (93, "Metacritic")
    assert [s.source for s in md.scores].count("Metacritic") == 1
    assert md.poster_url == "igdb.jpg"
    assert md.hltb_hours == 22.0 and md.summary == "From Steam"


def test_nothing_found():
    assert combine("anime", {}) is None


def test_best_match_uses_year_and_threshold():
    films = [{"t": "Dune", "y": 1984}, {"t": "Dune", "y": 2021}]
    pick = best_match("Dune", 2021, films, names=lambda f: [f["t"]], cand_year=lambda f: f["y"])
    assert pick["y"] == 2021
    assert best_match("Completely Different", None, films, names=lambda f: [f["t"]]) is None


def test_numbers_must_agree():
    from tracking.providers.base import search_variants, similarity

    gits = "Ghost in the Shell Stand Alone Complex 1st GIG"
    assert similarity(gits, "Ghost in the Shell: Stand Alone Complex 2nd GIG") < 75
    assert similarity("Ghost in the Shell Stand Alone Complex", "Ghost in the Shell: Stand Alone Complex") >= 90
    assert similarity("Okami", "Okami 2") < 75
    assert search_variants(gits)[:2] == [gits, "Ghost in the Shell Stand Alone Complex"]
    assert "TLoH: Trails through Daybreak" not in search_variants("TLoH - Trails through Daybreak II")
    assert search_variants("Show Season 2") == ["Show Season 2"]
    assert "INAZUMA ELEVEN: Victory Road" in search_variants("INAZUMA ELEVEN - Victory Road")
    assert "RoboCop: Rogue City" in search_variants("RoboCop - Rogue City UB")
    assert "RAIN CODE Plus" in search_variants("MDA - RAIN CODE Plus")
    assert search_variants("Riven") == ["Riven"]
