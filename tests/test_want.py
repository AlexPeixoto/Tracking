"""The "Want to acquire" list: adding, "Got it", and scans claiming wanted items."""

import os
from pathlib import Path

import pytest

from tracking import db, library, providers, scan


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("TRACKING_DATA_DIR", str(tmp_path / "data"))
    c = db.connect()
    yield c
    c.close()


def test_add_wanted_once_and_kept_out_of_library_counts(conn):
    a = library.add_wanted(conn, "game", "Hades", 2020, steam_appid=1145360,
                           url="https://store.steampowered.com/app/1145360/")
    assert library.add_wanted(conn, "game", "hades") == a          # no duplicates
    library.add_wanted(conn, "anime", "Frieren", 2023)
    item = db.get_item(conn, a)
    assert (item.source, item.platform, item.steam_appid) == (db.WANTED, "PC", 1145360)
    assert db.category_counts(conn) == {}                          # library sections
    assert db.category_counts(conn, wanted=True) == {"game": 1, "anime": 1}


def test_got_it_moves_to_library(conn):
    a = library.add_wanted(conn, "movie", "Heat", 1995)
    library.acquire(conn, [a])
    assert db.get_item(conn, a).source == "manual"
    assert db.category_counts(conn) == {"movie": 1}


def test_scan_claims_wanted_item(tmp_path, conn):
    a = library.add_wanted(conn, "anime", "Kill la Kill")
    conn.execute("UPDATE items SET notes='finally' WHERE id=?", (a,))
    lib = tmp_path / "lib" / "Anime" / "Kill la Kill"
    lib.mkdir(parents=True)
    (lib / "[HorribleSubs] Kill la Kill - 01 [1080p].mkv").write_bytes(b"x")
    rid = scan.add_root(conn, str(tmp_path / "lib"))
    res = scan.scan_root(conn, rid)
    assert res.new == 0                                            # no duplicate entry
    item = db.get_item(conn, a)
    assert (item.source, item.notes, item.video_count) == ("disk", "finally", 1)
    assert item.rel_path.endswith("Anime/Kill la Kill")


def _fake_services(monkeypatch):
    """Every service answers "<name> hit" without the network."""
    fakes = [type(r)(**{**r.__dict__, "required": (),
                        "search": lambda q, n=r.name: [providers.Candidate(f"{n} hit")]})
             if r.search else r for r in providers.REGISTRY]
    monkeypatch.setattr(providers, "REGISTRY", fakes)


def test_search_asks_every_service_for_the_section(monkeypatch):
    _fake_services(monkeypatch)
    for category, expected in (("game", {"steam", "hltb"}), ("anime", {"anilist"}), ("movie", {"omdb"})):
        _, found = providers.search(providers.Query("x", category=category))
        assert expected <= {c.title.removesuffix(" hit") for c in found}, category


def test_search_for_a_console_game_skips_steam(monkeypatch):
    """Adding "Zelda Ocarina of Time" for Switch 2 searched only the Steam store."""
    _fake_services(monkeypatch)
    label, found = providers.search(providers.Query("x", category="game", platform="Switch 2"))
    titles = {c.title for c in found}
    assert "hltb hit" in titles and "steam hit" not in titles
    assert "Steam" not in label


def test_search_puts_the_closest_title_first(monkeypatch):
    names = {"steam": "Zelda Breath of the Wild", "hltb": "The Legend of Zelda: Ocarina of Time"}
    fakes = [type(r)(**{**r.__dict__, "required": (),
                        "search": lambda q, n=r.name: [providers.Candidate(names.get(n, "Something Else"))]})
             if r.search else r for r in providers.REGISTRY]
    monkeypatch.setattr(providers, "REGISTRY", fakes)
    _, found = providers.search(providers.Query("Zelda Ocarina of Time", category="game"))
    assert found[0].title == "The Legend of Zelda: Ocarina of Time"


def test_dialog_adds_selected_results(tmp_path, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    monkeypatch.setenv("TRACKING_DATA_DIR", str(tmp_path))
    from tracking.ui.dialogs import WantDialog

    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    dlg = WantDialog("game")
    dlg._search_no = 1
    dlg._show_results(1, "Steam", [providers.Candidate("Hades", None, "Steam · PC", None, None, 1145360),
                                   providers.Candidate("Hades II", None, "Steam · PC", None, None, 1145350)])
    dlg.results.item(1).setSelected(True)
    dlg._add_selected()
    with db.session() as c:
        assert [(i.title, i.steam_appid) for i in db.list_items(c)] == [("Hades II", 1145350)]
    assert Path(tmp_path).exists()


def test_add_owned_goes_straight_to_the_library(tmp_path, monkeypatch):
    monkeypatch.setenv("TRACKING_DATA_DIR", str(tmp_path / "data"))
    from tracking import db, library

    conn = db.connect()
    iid = library.add_owned(conn, "game", " Gears of War: E-Day ", 2026, None, None,
                            "Xbox Series", "Xbox app / Game Pass")
    item = db.get_item(conn, iid)
    assert (item.title, item.source, item.platform, item.store) == (
        "Gears of War: E-Day", "manual", "Xbox Series", "Xbox app / Game Pass")
    assert library.add_owned(conn, "game", "gears of war: e-day", platform="Xbox Series") == iid
    wish = library.add_wanted(conn, "game", "Okami")
    assert library.add_owned(conn, "game", "Okami", store="Physical disc / cartridge") == wish
    assert db.get_item(conn, wish).source == "manual"                     # bought it after all
    library.edit_item(conn, item, title=item.title, category="game", platform="PC", year=2026,
                      notes=None, store="")
    assert db.get_item(conn, iid).store is None
    movie = library.add_owned(conn, "movie", "Dune", platform="PC", store="Streaming")
    assert db.get_item(conn, movie).platform is None                      # movies have no platform
    conn.close()


def test_hand_added_game_plays_where_you_said(tmp_path, monkeypatch):
    monkeypatch.setenv("TRACKING_DATA_DIR", str(tmp_path / "data"))
    import pytest

    from tracking import db, launcher, library

    conn = db.connect()
    xbox = db.get_item(conn, library.add_owned(conn, "game", "Gears of War: E-Day", 2026, 1234560,
                                               None, "Xbox Series", "Xbox app / Game Pass"))
    assert not launcher.via_steam(xbox)                        # the Steam id is only for metadata
    with pytest.raises(launcher.LaunchError, match="Xbox app"):
        launcher.plan(conn, xbox, None)
    pc = db.get_item(conn, library.add_owned(conn, "game", "Hades", None, 1145360, None, "PC", "Steam"))
    assert launcher.via_steam(pc)
    assert launcher.plan(conn, pc, None).url == "steam://rungameid/1145360"
    conn.close()
