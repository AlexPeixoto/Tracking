"""Android devices as locations (devices.py), emulator front-end folder names, ROM piles.
The device itself is faked: its listing is what Windows' device API would return."""

import pytest

from tracking import db, devices, scan
from tracking.scan import classify, fs
from tracking.scan.classify import Hints

THOR_SD = devices.Storage("AYN Thor", "disk", r"\\?\usb#vid_18d1&pid_4ee1#2160a1cb#{x}", "s20001",
                          "mtp:ad0a7fd4:disk", 511801556992)


def _listing() -> dict[str, list[list]]:
    rom = lambda n: [n, False, 1_000_000, 0.0]
    return {
        "": [["roms", True, 0, 0.0], ["bios", True, 0, 0.0]],
        "bios": [rom("scph5501.bin")],
        "roms": [["retro", True, 0, 0.0], ["n64", True, 0, 0.0], ["gc", True, 0, 0.0]],
        "roms/retro": [["SFC", True, 0, 0.0], ["FC", True, 0, 0.0], ["GB", True, 0, 0.0]],
        "roms/retro/SFC": [rom("Chrono Trigger (USA).zip"), rom("Super Metroid (USA).zip"),
                           rom("._Super Metroid (USA).zip")],
        "roms/retro/FC": [rom("Mega Man 2 (USA).zip")],
        "roms/retro/GB": [["old_non_zip", True, 0, 0.0], rom("Tetris (World).zip")],
        "roms/retro/GB/old_non_zip": [rom(f"{t} (USA).gb") for t in
                                      ("Kirby's Dream Land", "Metroid II", "Wario Land", "Link's Awakening",
                                       "Pokemon Red")],
        "roms/n64": [rom("Super Mario 64 (USA).z64")],
        "roms/gc": [rom("F-Zero GX (USA).rvz")],
    }


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("TRACKING_DATA_DIR", str(tmp_path / "data"))
    c = db.connect()
    yield c
    c.close()


def test_front_end_folder_names():
    for name, platform in (("SFC", "SNES"), ("FC", "NES"), ("SMS", "Master System"), ("MD", "Mega Drive"),
                           ("GG", "Game Gear"), ("PCE", "PC Engine"), ("DC", "Dreamcast"),
                           ("GC", "GameCube"), ("NEOGEO", "Neo Geo"), ("psx", "PS1"), ("n64", "N64")):
        assert classify.platform_from_name(name) == platform, name


def test_device_location_scans_its_listing_and_keeps_it(conn, monkeypatch):
    calls = []
    monkeypatch.setattr(devices, "find", lambda uid, storages=None: THOR_SD if uid == THOR_SD.uid else None)

    def read_tree(st, folder="", progress=None, cancelled=lambda: False):
        calls.append(folder)
        return _listing()

    monkeypatch.setattr(devices, "read_tree", read_tree)
    rid = scan.add_device_root(conn, THOR_SD, "Emulation", "game")
    res = scan.scan_root(conn, rid)
    assert calls == ["Emulation"]
    games = {(i.title, i.platform) for i in db.list_items(conn)}
    assert {("Chrono Trigger", "SNES"), ("Super Metroid", "SNES"), ("Mega Man 2", "NES"),
            ("Super Mario 64", "N64"), ("F-Zero GX", "GameCube"), ("Pokemon Red", "Game Boy"),
            ("Tetris", "Game Boy")} <= games
    assert not any("old non zip" in t.lower() or "bios" in t.lower() for t, _ in games)
    assert res.found == len(games)
    assert fs.info(conn, rid)[1] > 0                                     # kept as its snapshot

    # Unplugged: the scan falls back to that snapshot, like an unplugged drive.
    monkeypatch.setattr(devices, "find", lambda uid, storages=None: None)
    with pytest.raises(scan.VolumeOffline):
        scan.scan_root(conn, rid)
    again = scan.scan_root(conn, rid, from_snapshot=True)
    assert again.found == res.found and again.missing == 0


def test_rom_pile_inside_a_platform_folder_is_many_games(tmp_path):
    for t in ("Kirby's Dream Land", "Metroid II", "Wario Land", "Link's Awakening", "Pokemon Red"):
        (tmp_path / "GB" / "old_non_zip").mkdir(parents=True, exist_ok=True)
        (tmp_path / "GB" / "old_non_zip" / f"{t} (USA).gb").write_bytes(b"x")
    # One multi-disc game in its own folder stays one game.
    for n in (1, 2):
        p = tmp_path / "PS1" / "Final Fantasy VII (USA)" / f"Final Fantasy VII (USA) (Disc {n}).chd"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
    found = scan.collect(str(tmp_path), "", Hints())
    titles = sorted(i.title for i in found)
    assert "Old Non Zip" not in titles and len([i for i in found if i.platform == "Game Boy"]) == 5
    assert [i.title for i in found if i.platform == "PS1"] == ["Final Fantasy VII"]


def test_snapshot_fs_ignores_paths_on_other_drives(tmp_path):
    snap = fs.SnapshotFS(r"\\device\thor\Emulation", {"": []}, {})
    assert snap._rel("D:\\Games\\x") is None


def test_storage_label_and_uid():
    assert THOR_SD.label == "AYN Thor · disk"
    assert THOR_SD.uid.startswith(devices.PREFIX)
    assert devices.find("mtp:nope", [THOR_SD]) is None
    assert devices.find(THOR_SD.uid, [THOR_SD]) is THOR_SD


def test_no_devices_off_windows(monkeypatch):
    monkeypatch.setattr(devices, "available", lambda: False)
    assert devices.list_storages() == []


def test_rename_a_drive_and_keep_the_name(conn, tmp_path):
    from tracking import library, transfer

    lib = tmp_path / "lib"
    (lib / "Games").mkdir(parents=True)
    rid = scan.add_root(conn, str(lib / "Games"))
    uid = conn.execute("SELECT volume_uid FROM roots WHERE id=?", (rid,)).fetchone()[0]
    library.rename_volume(conn, uid, "  Big HDD  ")
    scan.add_root(conn, str(lib))                                   # added again: name stays
    assert conn.execute("SELECT name FROM volumes WHERE uid=?", (uid,)).fetchone()[0] == "Big HDD"
    assert transfer.describe(conn, uid, "Games/x").startswith("Big HDD:")
    library.rename_volume(conn, uid, "")                            # back to its own label
    assert conn.execute("SELECT name FROM volumes WHERE uid=?", (uid,)).fetchone()[0] is None

    rid2 = scan.add_device_root(conn, THOR_SD, "Emulation")
    library.rename_volume(conn, THOR_SD.uid, "Thor SD")
    scan.add_device_root(conn, THOR_SD, "Emulation/roms")
    assert conn.execute("SELECT name FROM volumes WHERE uid=?", (THOR_SD.uid,)).fetchone()[0] == "Thor SD"
    assert rid2
