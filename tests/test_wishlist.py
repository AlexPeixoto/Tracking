"""Wishlist features: Steam in the library view, completion hours, libretro covers,
play sessions, episodes / continue watching, copy & move."""

import os
from pathlib import Path

import pytest

from tracking import completions, db, launcher, library, providers, scan, transfer
from tracking.providers import libretro


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("TRACKING_DATA_DIR", str(tmp_path / "data"))
    c = db.connect()
    yield c
    c.close()


@pytest.fixture
def window(conn, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from tracking.ui import main_window

    monkeypatch.setattr(main_window.MainWindow, "manage_locations", lambda self: None)
    return main_window


def _item(conn, title, category="game", platform="PC", source="disk", **cols):
    iid = conn.execute(
        "INSERT INTO items(volume_uid, rel_path, is_dir, category, platform, title, source) "
        "VALUES(?, ?, 1, ?, ?, ?, ?)",
        ("steam:1" if source == "steam" else "v", f"x/{title}", category, platform, title, source)).lastrowid
    for k, v in cols.items():
        conn.execute(f"UPDATE items SET {k}=? WHERE id=?", (v, iid))
    return iid


def touch(root: Path, rel: str, size: int = 10) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x" * size)


def test_include_steam_shows_steam_games_once(conn, window):
    _item(conn, "Hades", steam_appid=1145360)
    _item(conn, "Hades", source="steam", steam_appid=1145360)      # also on disk: once
    _item(conn, "Celeste", source="steam", steam_appid=504230)
    conn.commit()
    w = window.MainWindow()
    w.nav = ("game", None)
    w.reload()
    assert [i.title for i in w.model.items] == ["Hades"]
    w.include_steam.setChecked(True)
    assert sorted(i.title for i in w.model.items) == ["Celeste", "Hades"]


def test_completion_keeps_steam_hours(conn):
    a = _item(conn, "Elden Ring", playtime_min=60 * 140)
    library.set_completed(conn, [a], True)
    assert conn.execute("SELECT hours FROM completions").fetchone()[0] == 140
    assert completions.year_stats(conn, int(completions.today()[:4])).hours == 140


def test_libretro_box_art(monkeypatch):
    monkeypatch.setattr(libretro, "names", lambda system: [
        "Ookami (Japan)", "Ookami (Europe) (En,Fr,De)", "Ookami (USA)", "Ookami (USA) (Beta)",
        "Incredible Hulk, The (USA) (En,Fr)", "God of War (USA)"])
    md = libretro.lookup(providers.Query("Okami", category="game", platform="PS2"))
    assert md.poster_url.endswith("/Named_Boxarts/Ookami%20%28USA%29.png")
    md = libretro.lookup(providers.Query("The Incredible Hulk", category="game", platform="PS2"))
    assert "Incredible%20Hulk%2C%20The" in md.poster_url
    assert libretro.lookup(providers.Query("Okami", category="game", platform="PC")) is None


def test_sessions_add_to_play_time(conn, tmp_path):
    a = _item(conn, "Hades")
    conn.commit()
    path = tmp_path / "data" / "library.sqlite3"
    assert not launcher.add_session(a, 20, path)               # a launcher that handed over
    assert launcher.add_session(a, 45 * 60, path)
    assert launcher.add_session(a, 15 * 60, path)
    assert db.get_item(conn, a).playtime_min == 60


def test_next_episode_and_continue_watching(tmp_path, conn, window):
    for e in (1, 2, 3):
        touch(tmp_path, f"Anime/Frieren/[Grp] Frieren - {e:02d}.mkv")
    rid = scan.add_root(conn, str(tmp_path))
    scan.scan_root(conn, rid)
    item = db.list_items(conn)[0]
    assert launcher.next_episode(conn, item).episode == 1
    la = launcher.plan_video(conn, item, scan.mount_for(conn, item.volume_uid),
                             launcher.next_episode(conn, item))
    assert la.url.endswith("Frieren - 01.mkv") and la.episode == (1, 1)
    launcher.record_watch(conn, item.id, 1, 2)
    conn.commit()
    item = db.get_item(conn, item.id)
    assert launcher.next_episode(conn, item).episode == 3
    w = window.MainWindow()
    assert window.watching(item)
    w.nav = (window.WATCHING, None)
    w.reload()
    assert [i.title for i in w.model.items] == ["Frieren"]
    launcher.record_watch(conn, item.id, 1, 3)
    assert launcher.next_episode(conn, db.get_item(conn, item.id)) is None


def test_move_follows_and_copy_links(tmp_path, conn, monkeypatch):
    touch(tmp_path, "ext/Games/PC/Hades/Hades.exe", 100)
    touch(tmp_path, "ext/Games/PC/Hades/data/pack.bin", 300)
    rid = scan.add_root(conn, str(tmp_path / "ext"))
    scan.scan_root(conn, rid)
    item = db.list_items(conn)[0]
    conn.execute("UPDATE items SET notes='keep me', completed=1 WHERE id=?", (item.id,))
    mount = scan.mount_for(conn, item.volume_uid)
    ssd = tmp_path / "ssd"
    ssd.mkdir()

    p = transfer.plan(conn, item, mount, str(ssd), move=False)
    assert p.size == 400
    transfer.copy_files(p)
    copy_id = transfer.apply(conn, p)
    copy = db.get_item(conn, copy_id)
    assert (ssd / "Hades" / "data" / "pack.bin").exists()
    assert copy.launch_path.endswith("ssd/Hades/Hades.exe") and copy.notes == "keep me"
    copies = {copy_id: item.id}
    assert library.find_duplicates(db.list_items(conn), copies) == []   # copies aren't duplicates
    assert "Copied to" in transfer.history(conn, item.id)[0]["detail"]

    dest2 = tmp_path / "ssd2"
    dest2.mkdir()
    p = transfer.plan(conn, db.get_item(conn, item.id), mount, str(dest2), move=True)
    transfer.copy_files(p)
    transfer.apply(conn, p)
    transfer.remove_sources(p)
    moved = db.get_item(conn, item.id)
    assert not (tmp_path / "ext" / "Games" / "PC" / "Hades").exists()
    assert moved.rel_path.endswith("ssd2/Hades") and moved.completed and moved.notes == "keep me"
    assert moved.launch_path.endswith("ssd2/Hades/Hades.exe")
    assert transfer.history(conn, item.id)[0]["action"] == "moved"
    with pytest.raises(transfer.TransferError, match="already exists"):
        transfer.plan(conn, moved, scan.mount_for(conn, moved.volume_uid), str(dest2), move=True)
