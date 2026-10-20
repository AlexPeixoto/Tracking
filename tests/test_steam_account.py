"""Steam account import: finding the account, owned games with play time (HTTP
mocked), play time landing on disk copies, the wish list, and the "Time played" sort."""

import pytest

from tracking import db, library, scan
from tracking.providers.base import RateLimiter
from tracking.sources import steam

ID64 = "76561197960287930"

PROFILE_XML = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<profile>
  <steamID64>{ID64}</steamID64>
  <steamID><![CDATA[Rabscuttle]]></steamID>
  <onlineState>offline</onlineState>
  <privacyState>public</privacyState>
  <visibilityState>3</visibilityState>
</profile>"""

OWNED = {"response": {"game_count": 3, "games": [
    {"appid": 1145360, "name": "Hades", "playtime_forever": 3125, "rtime_last_played": 1700000000},
    {"appid": 374320, "name": "DARK SOULS™ III", "playtime_forever": 45, "rtime_last_played": 0},
    {"appid": 620, "name": "Portal 2", "playtime_forever": 0},
]}}


class FakeResponse:
    def __init__(self, status=200, json=None, text=""):
        self.status_code, self._json, self.text = status, json, text

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    """Answers by URL; records each request."""

    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params))
        for prefix, answer in self.routes.items():
            if url.startswith(prefix):
                return answer
        raise AssertionError(f"unexpected request {url}")


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("TRACKING_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(steam, "_limit", RateLimiter(0))
    c = db.connect()
    yield c
    c.close()


def fake_http(monkeypatch, routes) -> FakeSession:
    s = FakeSession(routes)
    monkeypatch.setattr(steam, "session", lambda: s)
    return s


def add(conn, key="KEY", wishlist=False) -> int:
    if key:
        db.set_setting(conn, steam.KEY_SETTING, key)
    return steam.add_account(conn, steam.Account(ID64, "Rabscuttle"), wishlist=wishlist)


# --------------------------------------------------------------------------- finding the account

@pytest.mark.parametrize("typed, expected", [
    ("rabscuttle", ("id", "rabscuttle")),
    ("  rabscuttle  ", ("id", "rabscuttle")),
    ("https://steamcommunity.com/id/rabscuttle/", ("id", "rabscuttle")),
    ("steamcommunity.com/id/rabscuttle/games/?tab=all", ("id", "rabscuttle")),
    (f"https://steamcommunity.com/profiles/{ID64}", ("profiles", ID64)),
    (ID64, ("profiles", ID64)),
])
def test_parse_account(typed, expected):
    assert steam.parse_account(typed) == expected


@pytest.mark.parametrize("typed", ["", "a", "two words", "steamcommunity.com/profiles/123"])
def test_parse_account_rejects(typed):
    with pytest.raises(steam.SteamError):
        steam.parse_account(typed)


def test_profile_xml():
    acc = steam.parse_profile_xml(PROFILE_XML)
    assert (acc.id64, acc.name, acc.private) == (ID64, "Rabscuttle", False)
    private = steam.parse_profile_xml(PROFILE_XML.replace(">public<", ">private<"))
    assert private.private


def test_profile_not_found_explains_what_to_type():
    xml = "<response><error><![CDATA[The specified profile could not be found.]]></error></response>"
    with pytest.raises(steam.SteamError, match=r"could not be found.*profile URL"):
        steam.parse_profile_xml(xml)


def test_resolve_name_without_key(monkeypatch):
    s = fake_http(monkeypatch, {"https://steamcommunity.com/id/rabscuttle/": FakeResponse(text=PROFILE_XML)})
    assert steam.resolve("rabscuttle").id64 == ID64
    assert s.calls[0][0] == "https://steamcommunity.com/id/rabscuttle/?xml=1"


# --------------------------------------------------------------------------- owned games

def test_import_owned_games(conn, monkeypatch):
    s = fake_http(monkeypatch, {steam.OWNED_GAMES: FakeResponse(json=OWNED)})
    rid = add(conn)
    res = scan.scan_root(conn, rid)                    # Rescan of the account location
    assert (res.found, res.new) == (3, 3)
    assert s.calls[0][1]["steamid"] == ID64 and s.calls[0][1]["key"] == "KEY"
    items = {i.title: i for i in db.list_items(conn)}
    assert set(items) == {"Hades", "DARK SOULS III", "Portal 2"}
    hades = items["Hades"]
    assert (hades.source, hades.platform, hades.category, hades.steam_appid) == ("steam", "PC", "game", 1145360)
    assert (hades.playtime_min, hades.last_played) == (3125, "2023-11-14")
    assert hades.volume_uid == f"steam:{ID64}" and hades.rel_path == "app/1145360"
    assert items["Portal 2"].playtime_min == 0 and items["Portal 2"].last_played is None
    # An account is never "offline", so its games aren't shown as disconnected.
    assert scan.mount_for(conn, hades.volume_uid)
    # Their own section: not in the library counts or under Games.
    assert db.source_count(conn, db.STEAM) == 3
    assert db.category_counts(conn) == {} and db.platform_counts(conn) == {}

    # A rescan updates play time instead of adding again; a game no longer owned is flagged.
    owned = {"response": {"game_count": 1, "games": [
        {"appid": 1145360, "name": "Hades", "playtime_forever": 4000}]}}
    fake_http(monkeypatch, {steam.OWNED_GAMES: FakeResponse(json=owned)})
    res = scan.scan_root(conn, rid)
    assert (res.found, res.new, res.missing) == (1, 0, 2)
    items = {i.title: i for i in db.list_items(conn)}
    assert items["Hades"].playtime_min == 4000 and items["Portal 2"].missing


def test_bad_key_and_private_games(conn, monkeypatch):
    rid = add(conn)
    fake_http(monkeypatch, {steam.OWNED_GAMES: FakeResponse(status=401)})
    with pytest.raises(steam.SteamError, match="refused the Web API key"):
        scan.scan_root(conn, rid)
    fake_http(monkeypatch, {steam.OWNED_GAMES: FakeResponse(json={"response": {}})})
    with pytest.raises(steam.SteamError, match="private"):
        scan.scan_root(conn, rid)


def test_no_key_still_reads_wish_list(conn, monkeypatch):
    wish = {"response": {"items": [{"appid": 2, "priority": 2}, {"appid": 1, "priority": 1}]}}
    s = fake_http(monkeypatch, {steam.WISHLIST: FakeResponse(json=wish)})
    names = {1: {"name": "Hollow Knight: Silksong", "release_date": {"date": "4 Sep, 2025"}},
             2: {"name": "Hades II™"}}
    monkeypatch.setattr(steam.steam_store, "app_details", lambda appid: names[appid])
    rid = add(conn, key="", wishlist=True)
    with pytest.raises(steam.SteamError, match="apikey"):
        scan.scan_root(conn, rid)
    assert all(url != steam.OWNED_GAMES for url, _ in s.calls)
    wanted = [(i.title, i.year, i.steam_appid, i.source) for i in db.list_items(conn)]
    assert sorted(wanted) == [("Hades II", None, 2, "wish"),
                              ("Hollow Knight: Silksong", 2025, 1, "wish")]


def test_owned_game_takes_over_wish_list_entry(conn, monkeypatch):
    a = library.add_wanted(conn, "game", "Hades", steam_appid=1145360)
    conn.execute("UPDATE items SET notes='buy on sale' WHERE id=?", (a,))
    fake_http(monkeypatch, {steam.OWNED_GAMES: FakeResponse(json=OWNED)})
    scan.scan_root(conn, add(conn))
    hades = db.get_item(conn, a)
    assert (hades.source, hades.notes, hades.playtime_min) == ("steam", "buy on sale", 3125)
    assert db.category_counts(conn, wanted=True) == {}


# --------------------------------------------------------------------------- disk copies

def _disk_hades(tmp_path, conn) -> int:
    game = tmp_path / "lib" / "Games" / "PC" / "Hades"
    game.mkdir(parents=True)
    (game / "Hades.exe").write_bytes(b"x")
    return scan.add_root(conn, str(tmp_path / "lib"))


def test_play_time_copied_to_disk_copy(tmp_path, conn, monkeypatch):
    scan.scan_root(conn, _disk_hades(tmp_path, conn))
    fake_http(monkeypatch, {steam.OWNED_GAMES: FakeResponse(json=OWNED)})
    res = scan.scan_root(conn, add(conn))
    assert res.new == 3                                    # the Steam section lists all owned games
    hades = {i.source: i for i in db.list_items(conn) if i.title == "Hades"}
    assert set(hades) == {"disk", "steam"}
    assert (hades["disk"].steam_appid, hades["disk"].playtime_min) == (1145360, 3125)
    assert hades["disk"].rel_path.endswith("Games/PC/Hades")
    assert db.category_counts(conn) == {"game": 1} and db.platform_counts(conn) == {"PC": 1}


def test_disk_game_found_later_gets_play_time_on_next_sync(tmp_path, conn, monkeypatch):
    fake_http(monkeypatch, {steam.OWNED_GAMES: FakeResponse(json=OWNED)})
    srid = add(conn)
    scan.scan_root(conn, srid)
    steam_hades = next(i.id for i in db.list_items(conn) if i.title == "Hades")

    res = scan.scan_root(conn, _disk_hades(tmp_path, conn))
    assert res.new == 1                                    # a disk scan leaves Steam items alone
    disk_hades = next(i for i in db.list_items(conn) if i.title == "Hades" and i.source == "disk")
    assert db.get_item(conn, steam_hades).source == "steam"

    owned = {"response": {"game_count": 1, "games": [
        {"appid": 1145360, "name": "Hades", "playtime_forever": 5000}]}}
    fake_http(monkeypatch, {steam.OWNED_GAMES: FakeResponse(json=owned)})
    scan.scan_root(conn, srid)
    assert db.get_item(conn, disk_hades.id).playtime_min == 5000
    assert db.get_item(conn, steam_hades).playtime_min == 5000


# --------------------------------------------------------------------------- sort

def test_time_played_sort(conn, monkeypatch):
    pytest.importorskip("PySide6")
    from tracking.ui.main_window import SORTS, played_order

    assert "played" in dict(SORTS)
    fake_http(monkeypatch, {steam.OWNED_GAMES: FakeResponse(json=OWNED)})
    scan.scan_root(conn, add(conn))
    library.add_wanted(conn, "game", "Celeste")            # no play time known
    items = sorted(db.list_items(conn), key=played_order)
    assert [i.title for i in items] == ["Hades", "DARK SOULS III", "Portal 2", "Celeste"]


def test_steam_section_in_window(conn, monkeypatch):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    from tracking.ui import main_window as mw

    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    fake_http(monkeypatch, {steam.OWNED_GAMES: FakeResponse(json=OWNED)})
    scan.scan_root(conn, add(conn))
    library.add_wanted(conn, "game", "Celeste")
    conn.commit()

    w = mw.MainWindow()
    try:
        tops = [w.sidebar.topLevelItem(n) for n in range(w.sidebar.topLevelItemCount())]
        assert [t.text(0) for t in tops if t.data(0, mw.NavRole)[0] == mw.STEAM] == ["Steam   3"]
        assert tops[0].text(0) == "All items   0"         # not counted in the library
        w.nav = (None, None)
        w.apply_filter()
        assert w.model.items == []
        w.nav = (mw.STEAM, None)
        w.sort.setCurrentIndex(w.sort.findData("played"))
        w.apply_filter()
        assert [i.title for i in w.model.items] == ["Hades", "DARK SOULS III", "Portal 2"]
        assert w.heading.text() == "Steam" and "Steam account" in w.add_btn.text()
        assert not w.add_btn.isHidden()
    finally:
        w.close()
