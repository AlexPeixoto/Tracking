"""Regression tests for the code review: move safety, launch arguments, and refetches
that hit a failing service."""

from pathlib import Path

import pytest

from tracking import db, enrich, launcher, providers, scan, transfer
from tracking.providers.base import Metadata, Provider, ProviderError, ScoreEntry


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("TRACKING_DATA_DIR", str(tmp_path / "data"))
    c = db.connect()
    yield c
    c.close()


def touch(root: Path, rel: str, size: int = 10) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x" * size)


# --- moving never deletes more than the item ------------------------------------

def test_move_refuses_folders_holding_other_items_and_locations(tmp_path, conn):
    touch(tmp_path, "lib/Games/PC/Fatal Frame/game.exe")
    touch(tmp_path, "lib/Games/PC/Fatal Frame/Zelda [01007EF00011E000].nsp")  # a stray ROM item
    rid = scan.add_root(conn, str(tmp_path / "lib"))
    scan.scan_root(conn, rid)
    items = {i.title: i for i in db.list_items(conn)}
    assert len(items) == 2, list(items)
    folder = next(i for i in items.values() if i.is_dir)
    mount = scan.mount_for(conn, folder.volume_uid)
    (tmp_path / "ssd").mkdir()
    with pytest.raises(transfer.TransferError, match="other item"):
        transfer.plan(conn, folder, mount, str(tmp_path / "ssd"), move=True)

    root = conn.execute("SELECT volume_uid, rel_path FROM roots WHERE id=?", (rid,)).fetchone()
    fake = db.Item(**{**folder.__dict__, "rel_path": root["rel_path"]})
    with pytest.raises(transfer.TransferError, match="whole drive or location"):
        transfer.plan(conn, fake, mount, str(tmp_path / "ssd"), move=True)


def test_destination_check_is_by_folder_not_by_text(tmp_path, conn):
    touch(tmp_path, "lib/Games/PC/Hades/Hades.exe")
    rid = scan.add_root(conn, str(tmp_path / "lib"))
    scan.scan_root(conn, rid)
    item = db.list_items(conn)[0]
    mount = scan.mount_for(conn, item.volume_uid)
    sibling = tmp_path / "lib" / "Games" / "PC" / "Hades2"     # "…Hades" + "2": not inside
    sibling.mkdir()
    assert transfer.plan(conn, item, mount, str(sibling), move=False)
    with pytest.raises(transfer.TransferError, match="inside the item"):
        transfer.plan(conn, item, mount, str(tmp_path / "lib" / "Games" / "PC" / "Hades" / "x"), move=False)


# --- launch arguments --------------------------------------------------------------

def test_quoted_arguments_lose_their_quotes():
    assert launcher.split_args('-config "C:\\My Games\\x.ini" -windowed') == [
        "-config", "C:\\My Games\\x.ini", "-windowed"]
    assert launcher.split_args(None) == []


# --- refetch with a service down ------------------------------------------------------

def test_refetch_keeps_cover_and_scores_when_a_service_fails(conn, monkeypatch):
    iid = conn.execute("INSERT INTO items(volume_uid, rel_path, is_dir, category, platform, title, "
                       "poster, enriched_at) VALUES('v', 'g/Okami', 1, 'game', 'PS2', 'Okami', "
                       "'7.jpg', 'x')").lastrowid
    conn.executemany("INSERT INTO scores(item_id, source, value, max_value) VALUES(?,?,?,100)",
                     [(iid, "Metacritic", 93), (iid, "HLTB users", 88)])
    conn.commit()

    def down(q):
        raise ProviderError("HowLongToBeat didn't answer")

    good = Provider("good", "Good", frozenset({"game"}), lambda q: Metadata(
        scores=[ScoreEntry("Metacritic", 94, 100)]))
    bad = Provider("bad", "Bad", frozenset({"game"}), down)
    monkeypatch.setattr(providers, "REGISTRY", [good, bad])
    warnings = enrich.enrich_item(conn, iid, None, force=True)
    assert warnings
    item = db.get_item(conn, iid)
    assert item.poster == "7.jpg"                                   # no new cover: old one stays
    assert {s.source: s.value for s in item.scores} == {"Metacritic": 94, "HLTB users": 88}
