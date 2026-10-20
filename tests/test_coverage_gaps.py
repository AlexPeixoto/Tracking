"""Item page for every kind of item, local cover art, a cancelled copy, and fetching a
Completed entry's cover."""

import os
from pathlib import Path

import pytest

from tracking import completions, db, enrich, library, providers, scan, transfer
from tracking.providers.base import Metadata, Provider

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("TRACKING_DATA_DIR", str(tmp_path / "data"))
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    c = db.connect()
    yield c
    c.close()


def touch(root: Path, rel: str, data: bytes = b"x") -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)


def _library(conn, tmp_path):
    touch(tmp_path, "lib/Games/PC/Hades/Hades.exe")
    for e in (1, 2, 3):
        touch(tmp_path, f"lib/Anime/Mushishi/[Grp] Mushishi - {e:02d}.mkv")
    rid = scan.add_root(conn, str(tmp_path / "lib"))
    scan.scan_root(conn, rid)
    library.add_wanted(conn, "game", "Silksong", 2025, 1030300)
    conn.execute("INSERT INTO items(volume_uid, rel_path, is_dir, category, platform, title, source, "
                 "steam_appid, playtime_min) VALUES('steam:1', 'app/1', 0, 'game', 'PC', 'Celeste', "
                 "'steam', 504230, 600)")
    conn.commit()
    return {i.title: i for i in db.list_items(conn)}


def test_item_page_shows_every_kind_and_saves(conn, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from tracking.ui.item_page import ItemPage

    items = _library(conn, tmp_path)
    page = ItemPage()
    events = []
    page.changed.connect(lambda i: events.append(("changed", i)))
    page.refetch.connect(lambda i: events.append(("refetch", i)))
    page.deleted.connect(lambda i: events.append(("deleted", i)))
    mount = str(tmp_path)
    for title in ("Hades", "Mushishi", "Silksong", "Celeste"):
        page.show_item(items[title], mount, "Media")
    assert page.play_btn.text().startswith("▶")                       # Steam: Play in Steam

    page.show_item(items["Mushishi"], mount, "Media")
    assert "Play episode 1" in page.play_btn.text()
    page.show_item(items["Hades"], mount, "Media")
    page.e_title.setText("Hades II")
    page._save()
    assert ("refetch", items["Hades"].id) in events
    assert db.get_item(conn, items["Hades"].id).title == "Hades II"
    page._toggle_done(True)
    assert db.get_item(conn, items["Hades"].id).completed

    page.show_item(items["Silksong"], mount, "Media")
    page._toggle_done(True)                                            # "Got it"
    assert db.get_item(conn, items["Silksong"].id).source == "manual"

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    page._delete()
    assert ("deleted", items["Silksong"].id) in events and not db.get_item(conn, items["Silksong"].id)


def test_cover_jpg_in_the_folder_wins(conn, tmp_path, monkeypatch):
    items = _library(conn, tmp_path)
    touch(tmp_path, "lib/Games/PC/Hades/cover.jpg", b"local art")
    monkeypatch.setattr(providers, "REGISTRY", [Provider("p", "P", frozenset({"game"}),
                                                         lambda q: Metadata(poster_url="http://x"))])
    enrich.enrich_item(conn, items["Hades"].id, scan.mount_for(conn, items["Hades"].volume_uid))
    from tracking import paths
    poster = db.get_item(conn, items["Hades"].id).poster
    assert (paths.posters_dir() / poster).read_bytes() == b"local art"   # nothing downloaded


def test_cancelled_copy_leaves_nothing_behind(conn, tmp_path):
    items = _library(conn, tmp_path)
    touch(tmp_path, "lib/Games/PC/Hades/data/big.bin", b"x" * 1000)
    hades = items["Hades"]
    dest = tmp_path / "ssd"
    dest.mkdir()
    p = transfer.plan(conn, hades, scan.mount_for(conn, hades.volume_uid), str(dest), move=True)
    calls = {"n": 0}

    def cancel_on_second_chunk():
        calls["n"] += 1
        return calls["n"] > 1

    with pytest.raises(transfer.TransferError, match="Cancelled"):
        transfer.copy_files(p, cancelled=cancel_on_second_chunk)
    assert not (dest / "Hades").exists()                               # partial copy removed
    assert (tmp_path / "lib/Games/PC/Hades/data/big.bin").exists()     # originals untouched


def test_fetch_cover_for_a_completed_entry(conn, monkeypatch):
    cid = completions.add(conn, "Celeste", "2024-02-03", link=False)
    monkeypatch.setattr(providers, "REGISTRY", [Provider("p", "P", frozenset({"game"}),
                                                         lambda q: Metadata(poster_url="http://c"))])

    class Resp:
        content, headers = b"png!", {"Content-Type": "image/png"}

        def raise_for_status(self):
            pass

    from tracking.providers import base
    monkeypatch.setattr(base.session(), "get", lambda url, timeout=30: Resp())
    assert completions.fetch_cover(conn, cid)
    entry = completions.get(conn, cid)
    assert entry.poster.endswith(".png") and Path(entry.poster_path()).read_bytes() == b"png!"
    monkeypatch.setattr(providers, "REGISTRY", [Provider("p", "P", frozenset({"game"}), lambda q: None)])
    assert not completions.fetch_cover(conn, cid)                      # nothing found: keeps it
    assert completions.get(conn, cid).poster == entry.poster


def test_coming_up_section_shows_only_for_the_current_item(conn, tmp_path):
    from tracking.ui.item_page import ItemPage
    from tracking.upcoming import Upcoming

    items = _library(conn, tmp_path)
    page = ItemPage()
    page.show_item(items["Mushishi"], str(tmp_path), "Media")
    page._show_upcoming(items["Hades"].id, [Upcoming("stale")])      # an older answer: ignored
    assert page.upcoming.isHidden()
    page._show_upcoming(items["Mushishi"].id, [Upcoming("Episode 8 airs Sat", "u", True)])
    assert not page.upcoming.isHidden() and "<b>" in page.upcoming.text()
