"""Search cover on IGDB: the search, keeping a picked cover through refetch, Completed entries."""

import os
from pathlib import Path

import pytest

from tracking import completions, db, enrich, library, paths, providers
from tracking.providers import base, igdb
from tracking.providers.base import Metadata, Provider

KEYS = {"igdb_client_id": "id", "igdb_client_secret": "secret"}


class _Resp:
    def __init__(self, data=None, content=b"", ctype="image/jpeg"):
        self._data, self.content, self.status_code = data, content, 200
        self.headers = {"Content-Type": ctype}

    def json(self):
        return self._data

    def raise_for_status(self):
        pass


class _Session:
    def __init__(self, games=None, image=b"img"):
        self.games, self.image, self.bodies = games or [], image, []

    def post(self, url, data=None, **_):
        if url == igdb.TOKEN_URL:
            return _Resp({"access_token": "t", "expires_in": 3600})
        self.bodies.append(data.decode())
        return _Resp(self.games)

    def get(self, url, **_):
        return _Resp(content=self.image)


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("TRACKING_DATA_DIR", str(tmp_path / "data"))
    c = db.connect()
    yield c
    c.close()


def _web(monkeypatch, sess):
    for mod in (igdb, enrich, base):
        monkeypatch.setattr(mod, "session", lambda: sess)
    monkeypatch.setattr(igdb, "_token", None)


def _item(conn, title):
    iid = conn.execute("INSERT INTO items(volume_uid, rel_path, is_dir, category, platform, title) "
                       "VALUES('v', ?, 0, 'game', 'PS2', ?)", (f"x/{title}", title)).lastrowid
    conn.commit()
    return iid


def test_search_covers_lists_every_edition_with_a_cover(monkeypatch):
    sess = _Session([
        {"name": "Okami", "first_release_date": 1145000000, "cover": {"image_id": "co1"},
         "platforms": [{"abbreviation": "PS2"}], "url": "https://igdb.com/games/okami"},
        {"name": "Okami HD", "cover": {"image_id": "co2"}, "platforms": [{"name": "Switch"}]},
        {"name": "No art"},
    ])
    _web(monkeypatch, sess)
    found = igdb.search_covers('Okami "HD"', KEYS)
    assert [(c.title, c.year, c.detail) for c in found] == [("Okami", 2006, "PS2"), ("Okami HD", None, "Switch")]
    assert found[0].image_url == igdb.COVER_URL.format("co1")
    assert 'search "Okami  HD "' in sess.bodies[0] and "cover != null" in sess.bodies[0]
    with pytest.raises(providers.ProviderError):
        igdb.search_covers("Okami", {})
    assert igdb.configured(KEYS) and not igdb.configured({"igdb_client_id": "id"})


def test_picked_cover_survives_refetch_until_find_match(conn, monkeypatch):
    _web(monkeypatch, _Session(image=b"picked"))
    iid = _item(conn, "Okami")
    name = library.set_cover_url(conn, iid, "https://images.igdb.com/co1.jpg")
    conn.commit()
    assert (paths.posters_dir() / name).read_bytes() == b"picked"

    _web(monkeypatch, _Session(image=b"fetched"))
    monkeypatch.setattr(providers, "REGISTRY", [Provider("p", "P", frozenset({"game"}),
                                                         lambda q: Metadata(poster_url="http://x"))])
    enrich.enrich_item(conn, iid, None, force=True)
    item = db.get_item(conn, iid)
    assert (paths.posters_dir() / item.poster).read_bytes() == b"picked"  # kept

    library.use_match(conn, item, "Okami HD")                             # a new match unpins
    enrich.enrich_item(conn, iid, None, force=True)
    item = db.get_item(conn, iid)
    assert (paths.posters_dir() / item.poster).read_bytes() == b"fetched"


def test_picked_cover_replaces_old_file_unless_completed_uses_it(conn, monkeypatch):
    _web(monkeypatch, _Session())
    iid = _item(conn, "Okami")
    (paths.posters_dir() / "old.webp").write_bytes(b"old")
    conn.execute("UPDATE items SET poster='old.webp' WHERE id=?", (iid,))
    library.set_cover_url(conn, iid, "https://x/co.jpg")
    assert not (paths.posters_dir() / "old.webp").exists()

    (paths.posters_dir() / "kept.webp").write_bytes(b"kept")
    conn.execute("UPDATE items SET poster='kept.webp' WHERE id=?", (iid,))
    conn.execute("INSERT INTO completions(title, category, completed_on, poster) "
                 "VALUES('Okami', 'game', '2024', 'kept.webp')")
    library.set_cover_url(conn, iid, "https://x/co.jpg")
    assert (paths.posters_dir() / "kept.webp").exists()


def test_completed_entry_cover_from_url(conn, monkeypatch):
    _web(monkeypatch, _Session(image=b"igdb"))
    cid = conn.execute("INSERT INTO completions(title, category, completed_on) "
                       "VALUES('Okami', 'game', '2024-05-01')").lastrowid
    completions.set_cover_url(conn, cid, "https://images.igdb.com/co1.jpg")
    path = completions.get(conn, cid).poster_path()
    assert path and Path(path).read_bytes() == b"igdb"


@pytest.fixture
def qt():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_cover_dialog(conn, qt, monkeypatch):
    from tracking.ui.dialogs import CoverDialog

    dlg = CoverDialog("Okami")                        # no keys: says how to get them
    assert "Client ID" in dlg.status.text() and dlg.results.count() == 0

    db.set_setting(conn, "igdb_client_id", "id")
    db.set_setting(conn, "igdb_client_secret", "secret")
    conn.commit()
    monkeypatch.setattr(CoverDialog, "_search", lambda self: None)
    dlg = CoverDialog("Okami")
    dlg._no = 1
    found = [providers.Candidate("Okami", 2006, "PS2", "https://img/co1.jpg"),
             providers.Candidate("Okami HD", 2017, "Switch", "https://img/co2.jpg")]
    dlg._show(1, "IGDB", found)
    assert dlg.results.count() == 2 and "2 covers" in dlg.status.text()
    dlg._show(0, "IGDB", [])                           # an older search's answer is ignored
    assert dlg.results.count() == 2
    dlg.results.setCurrentRow(1)
    dlg._use()
    assert dlg.url == "https://img/co2.jpg"


def test_on_platform_result_wins_over_exact_name():
    from tracking.providers.base import best_match

    found = [{"name": "Wolverine", "year": 1991, "plats": ["NES"]},
             {"name": "Marvel's Wolverine", "year": 2026, "plats": ["PlayStation 5"]}]

    def pick(plat, year=None):
        on = lambda r: plat in r["plats"]
        r = best_match("Wolverine", year, found, names=lambda r: [r["name"]],
                       cand_year=lambda r: r["year"], bonus=lambda r: 8 if on(r) else 0, prefer=on)
        return r and r["name"]

    assert pick("PlayStation 5") == "Marvel's Wolverine"
    assert pick("NES") == "Wolverine"
    assert pick("PC") == "Wolverine"                     # nothing on PC: the best name overall
    assert pick("PC", 2026) == "Wolverine"               # a year alone doesn't beat the name


def test_title_and_year():
    assert completions.title_and_year("Wolverine 2026") == ("Wolverine", 2026)
    assert completions.title_and_year("Wolverine (2026)") == ("Wolverine", 2026)
    assert completions.title_and_year("1979") == ("1979", None)
    assert completions.title_and_year("Okami") == ("Okami", None)


def test_completed_cover_tries_the_name_without_its_year(conn, monkeypatch):
    seen = []

    def lookup(q):
        seen.append((q.title, q.year, q.platform))
        return Metadata(poster_url="http://cover") if q.title == "Wolverine" else None

    monkeypatch.setattr(providers, "REGISTRY", [Provider("p", "P", frozenset({"game"}), lookup)])
    _web(monkeypatch, _Session(image=b"ps5"))
    cid = conn.execute("INSERT INTO completions(title, category, platform, completed_on) "
                       "VALUES('Wolverine 2026', 'game', 'PS5', '2026-10-01')").lastrowid
    assert completions.fetch_cover(conn, cid)
    assert seen[0] == ("Wolverine 2026", None, "PS5") and ("Wolverine", 2026, "PS5") in seen

    seen.clear()                                         # a year after it is finished: part of the name
    cid = conn.execute("INSERT INTO completions(title, category, completed_on) "
                       "VALUES('Cyberpunk 2077', 'game', '2024-01-01')").lastrowid
    completions.fetch_cover(conn, cid)
    assert all(y is None for _, y, _ in seen)
