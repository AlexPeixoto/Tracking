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


def test_search_uses_first_service_that_can(monkeypatch):
    calls = []
    for name in ("omdb", "steam", "anilist", "tvmaze"):
        mod = getattr(providers, name)
        p = mod.PROVIDER
        fake = type(p)(**{**p.__dict__, "search": lambda q, n=name: calls.append(n) or
                          [providers.Candidate(f"{n} hit")]})
        monkeypatch.setattr(mod, "PROVIDER", fake)
        monkeypatch.setattr(providers, "REGISTRY",
                            [fake if r.name == name else r for r in providers.REGISTRY])
    for category, expected in (("game", "steam"), ("movie", "omdb"), ("anime", "anilist"),
                               ("tv", "omdb")):
        _, found = providers.search(providers.Query("x", category=category))
        assert found[0].title == f"{expected} hit", category


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
