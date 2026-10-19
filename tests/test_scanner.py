import shutil
from pathlib import Path

import pytest

from tracking import classify, db, scanner, titles
from tracking.classify import Hints


def touch(root: Path, rel: str, data: bytes = b"x") -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)


def scan(root: Path):
    return {i.rel_path: i for i in scanner.walk(str(root), "", Hints())}


def test_platform_names():
    assert classify.platform_from_name("PS2") == "PS2"
    assert classify.platform_from_name("Sony - PlayStation 2") == "PS2"
    assert classify.platform_from_name("Saturn ISOs") == "Saturn"
    assert classify.platform_from_name("Nintendo 64") == "N64"
    assert classify.platform_from_name("PC Games") == "PC"
    assert classify.platform_from_name("PlayStation All-Stars Battle Royale") is None
    assert classify.platform_from_name("Super Mario World") is None


def test_game_titles():
    assert titles.clean_game_title("Final Fantasy X (USA) (Disc 1)") == "Final Fantasy X"
    assert titles.clean_game_title("Some.Game.v1.2.3-CODEX") == "Some Game"
    assert titles.clean_game_title("Super Mario World (USA) [!]") == "Super Mario World"
    assert titles.clean_game_title("Rune II") == "Rune II"


def test_layout(tmp_path: Path):
    touch(tmp_path, "Anime/[Grp] Cowboy Bebop/[Grp] Cowboy Bebop - 01 [ABCD1234].mkv")
    touch(tmp_path, "Anime/[Grp] Cowboy Bebop/[Grp] Cowboy Bebop - 02 [ABCD1235].mkv")
    touch(tmp_path, "Movies/Heat (1995)/Heat.1995.1080p.BluRay.mkv")
    touch(tmp_path, "Movies/Heat (1995)/Sample/heat-sample.mkv")
    touch(tmp_path, "Movies/Arrival.2016.1080p.mkv")
    touch(tmp_path, "Games/PS2/Shadow of the Colossus (USA).iso")
    touch(tmp_path, "Games/Saturn/Panzer Dragoon (Disc 1).cue")
    touch(tmp_path, "Games/Saturn/Panzer Dragoon (Disc 2).cue")
    touch(tmp_path, "Games/PC/Hades/Hades.exe")
    touch(tmp_path, "Documents/notes.txt")

    items = scan(tmp_path)
    bebop = items["Anime/[Grp] Cowboy Bebop"]
    assert (bebop.category, bebop.title, len(bebop.videos)) == ("anime", "Cowboy Bebop", 2)

    heat = items["Movies/Heat (1995)"]
    assert (heat.category, heat.title, heat.year, len(heat.videos)) == ("movie", "Heat", 1995, 1)
    assert items["Movies/Arrival.2016.1080p.mkv"].title == "Arrival"

    sotc = items["Games/PS2/Shadow of the Colossus (USA).iso"]
    assert (sotc.category, sotc.platform, sotc.title) == ("game", "PS2", "Shadow of the Colossus")

    panzer = [i for i in items.values() if i.title == "Panzer Dragoon"]
    assert len(panzer) == 1 and panzer[0].platform == "Saturn" and panzer[0].file_count == 2

    assert items["Games/PC/Hades"].platform == "PC"
    assert not any(k.startswith("Documents") for k in items)


def test_auto_detect_without_folder_hints(tmp_path: Path):
    header = b"SEGA SEGASATURN " + b"\0" * 64
    touch(tmp_path, "Stuff/Nights into Dreams/nights.bin", header)
    touch(tmp_path, "Stuff/Some Show/[Sub] Some Show - 01.mkv")
    items = {i.rel_path: i for i in scanner.walk(str(tmp_path / "Stuff"), "Stuff", Hints())}
    assert items["Stuff/Nights into Dreams"].platform == "Saturn"
    assert items["Stuff/Some Show"].category == "anime"


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("TRACKING_DATA_DIR", str(tmp_path / "data"))
    c = db.connect(tmp_path / "test.sqlite3")
    yield c
    c.close()


def test_rescan_marks_missing_completed(tmp_path: Path, conn):
    lib = tmp_path / "lib"
    touch(lib, "Movies/Heat (1995)/Heat.1995.mkv")
    touch(lib, "Movies/Arrival (2016)/Arrival.2016.mkv")
    rid = scanner.add_root(conn, str(lib))
    scanner.scan_root(conn, rid)
    shutil.rmtree(lib / "Movies" / "Heat (1995)")

    res = scanner.scan_root(conn, rid, missing_completed=True)
    assert res.missing == 1
    items = {i.title: i for i in db.list_items(conn)}
    assert items["Heat"].missing and items["Heat"].completed and items["Heat"].completed_at
    assert not items["Arrival"].completed


def test_items_survive_location_removal_and_follow_moves(tmp_path: Path, conn):
    lib = tmp_path / "lib"
    touch(lib, "Games/PS2/Okami (USA).iso")
    rid = scanner.add_root(conn, str(lib / "Games"))
    scanner.scan_root(conn, rid)
    okami = db.list_items(conn)[0]
    db.set_completed(conn, [okami.id], True)
    conn.execute("UPDATE items SET notes='great' WHERE id=?", (okami.id,))

    # Remove the location: the entry stays.
    conn.execute("DELETE FROM roots WHERE id=?", (rid,))
    assert [i.id for i in db.list_items(conn)] == [okami.id]

    # The file shows up somewhere else: same entry, new path, state kept.
    (lib / "Games" / "PS2" / "Okami (USA).iso").unlink()
    touch(lib, "Backup/PS2/Okami (USA).iso")
    rid2 = scanner.add_root(conn, str(lib / "Backup"), "game")
    scanner.scan_root(conn, rid2)
    items = db.list_items(conn)
    assert len(items) == 1 and items[0].id == okami.id
    assert items[0].rel_path.endswith("Backup/PS2/Okami (USA).iso")
    assert items[0].completed and items[0].notes == "great"


# --- grouping ----------------------------------------------------------------

def items_by_title(root: Path, hints=Hints()):
    return {i.title: i for i in scanner.collect(str(root), "", hints)}


def test_scattered_episodes_merge_into_one_series(tmp_path: Path):
    for ep in range(1, 9):
        touch(tmp_path, f"Anime/Kill la Kill/[HorribleSubs] Kill la Kill - {ep:02d} [1080p].mkv")
    # A few more episodes lying loose next to the folder.
    for ep in (9, 10):
        touch(tmp_path, f"Anime/[HorribleSubs] Kill la Kill - {ep:02d} [1080p].mkv")
    items = items_by_title(tmp_path)
    assert list(items) == ["Kill la Kill"]
    kill = items["Kill la Kill"]
    assert kill.rel_path == "Anime/Kill la Kill" and len(kill.videos) == 10


def test_seasons_split_and_movies_stay_apart(tmp_path: Path):
    for s in (1, 2):
        for ep in (1, 2, 3):
            touch(tmp_path, f"Anime/Show/Season {s}/[Grp] Show - S{s:02d}E{ep:02d}.mkv")
    touch(tmp_path, "Anime/Show/[Grp] Show The Movie (2019) [ABCD1234].mkv")
    touch(tmp_path, "Anime/Show/NCOP/[Grp] Show - NCOP 01.mkv")  # extra, not an episode
    items = items_by_title(tmp_path)
    assert items["Show"].season == 1 and len(items["Show"].videos) == 3
    assert items["Show Season 2"].season == 2 and len(items["Show Season 2"].videos) == 3
    movie = [i for i in items.values() if i.kind == "movie"]
    assert len(movie) == 1 and len(movie[0].videos) == 1
    assert items["Show"].rel_path == "Anime/Show::s1"


def test_anime_movie_with_crc_is_not_a_series(tmp_path: Path):
    touch(tmp_path, "Anime/Redline [BDRip]/[Arid] Redline [BDRip 1864x1048 HEVC] [943DC65B].mkv")
    touch(tmp_path, "Anime/[naiyas] Vampire Hunter D - Bloodlust (2000) (BD1080p HEVC).mkv")
    items = items_by_title(tmp_path)
    assert items["Redline"].kind == "movie"
    assert items["Vampire Hunter D: Bloodlust"].kind == "movie"
    assert items["Vampire Hunter D: Bloodlust"].year == 2000


def test_live_action_tv_goes_to_unknown(tmp_path: Path):
    for ep in (1, 2, 3):
        touch(tmp_path, f"Bad.Sisters.S01.1080p.WEBRip.x265[eztv.re]/Bad.Sisters.S01E0{ep}.1080p.mkv")
    touch(tmp_path, "Ballerina.From.the.World.of.John.Wick.2025.1080p.WEBRip.AAC5.1.10bits.x265-Rapta/"
                    "Ballerina.2025.1080p.mkv")
    items = items_by_title(tmp_path)
    assert items["Bad Sisters"].category == "unknown"
    assert items["Ballerina From the World of John Wick"].category == "movie"
    assert items["Ballerina From the World of John Wick"].year == 2025


def test_game_release_names(tmp_path: Path):
    touch(tmp_path, "Ace Attorney Investigations Collection [FitGirl PC Repack]/setup.exe")
    touch(tmp_path, "Ace Attorney Investigations Collection [FitGirl PC Repack]/fg-01.bin")
    touch(tmp_path, "Azure Striker GUNVOLT 3 [01004E90149AA000][v0].nsp")
    touch(tmp_path, "ps2 isos/Okami (USA).iso")
    touch(tmp_path, "random/Okami.zip")  # archive, no platform anywhere: skipped
    items = items_by_title(tmp_path)
    assert items["Ace Attorney Investigations Collection"].platform == "PC"
    assert items["Azure Striker GUNVOLT 3"].platform == "Switch"
    assert items["Okami"].platform == "PS2" and items["Okami"].rel_path.startswith("ps2 isos")
    assert len([i for i in items.values() if i.title == "Okami"]) == 1


def test_db_migration_adds_columns(tmp_path: Path):
    import sqlite3
    old = sqlite3.connect(tmp_path / "old.sqlite3")
    old.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, root_id INTEGER, volume_uid TEXT, "
                "rel_path TEXT, is_dir INTEGER, category TEXT, platform TEXT, title TEXT)")
    old.commit()
    old.close()
    c = db.connect(tmp_path / "old.sqlite3")
    cols = {r["name"] for r in c.execute("PRAGMA table_info(items)")}
    assert {"kind", "season", "detected_how"} <= cols
    c.close()


# --- cases from a real drive -------------------------------------------------

def test_messy_game_folder(tmp_path: Path):
    tp = tmp_path / "tpex"
    for n in ("Albatroz [FitGirl Repack]", "Riven [FitGirl Repack]", "Viewfinder [FitGirl Repack]"):
        touch(tp, f"{n}/setup.exe")
        touch(tp, f"{n}/fg-01.bin")
        touch(tp, f"{n}/MD5/fitgirl-bins.md5")
    # A Switch game someone dropped into a PC repack folder.
    touch(tp, "Fatal Frame - Maiden of Black Water [FitGirl Repack]/setup.exe")
    touch(tp, "Fatal Frame - Maiden of Black Water [FitGirl Repack]/"
              "The Legend of Zelda Echoes of Wisdom [01008CF01BAAC000][v0][US].nsp")
    touch(tp, "sw/The Legend of Zelda Echoes of Wisdom [01008CF01BAAC000][v1.0.1][US](nsw2u.com).xci")
    touch(tp, "sw/TRON Catalyst [01002B102116A000][v0][US].nsp")
    touch(tp, "sw/TRON Catalyst [01002B102116A800][v65536][US].nsp")       # its update
    touch(tp, "sw/._TRON Catalyst [01002B102116A000][v0][US].nsp")         # macOS junk
    touch(tp, "sw/Firmware.19.0.0.zip")
    touch(tp, "SHINOBI - Art of Vengeance [FitGirl Switch Repack]/setup.exe")
    touch(tp, "Riven.Update.v1.2-RUNE.rar")                                 # joins Riven
    touch(tp, "Folklore_BCES00050.rar")
    touch(tp, "game-chains.of.freedom-(81127).rar")
    touch(tp, "game-chains.of.freedom-(81127)/setup_chains.exe")
    touch(tp, "IePErNcHFCMaNrLDqsH.rar")

    items = scanner.collect(str(tmp_path), "", Hints())
    got = {(i.title, i.platform): i for i in items}
    assert ("Fatal Frame - Maiden of Black Water", "PC") in got
    zelda = got[("The Legend of Zelda Echoes of Wisdom", "Switch")]
    assert zelda.file_count == 2
    assert got[("TRON Catalyst", "Switch")].file_count == 2
    assert got[("Riven", "PC")].file_count == 4
    assert ("SHINOBI - Art of Vengeance", "Switch") in got
    assert ("Folklore", "PS3") in got
    assert got[("Chains of Freedom", "PC")].file_count == 2
    assert not any("Firmware" in t or "IePEr" in t for t, _ in got)
    assert len(items) == 9


def test_duplicate_movies_count_runtime_once(tmp_path: Path):
    touch(tmp_path, "Alien Romulus (2024) [1080p] [WEBRip] [5.1] [YTS.MX]/Alien.Romulus.2024.1080p.mp4", b"x" * 10)
    touch(tmp_path, "Alien.Romulus.2024.2160p.AMZN.WEB-DL.DDP5.1.Atmos.H.265-FLUX[TGx]/Alien.Romulus.2024.2160p.mkv",
          b"x" * 100)
    touch(tmp_path, "Dune Part Two (2024) [1080p] [WEBRip] [YTS.MX]/Dune.Part.Two.2024.mp4")
    items = items_by_title(tmp_path)
    assert len(items["Alien Romulus"].videos) == 1
    assert items["Alien Romulus"].videos[0].size == 100
    assert "Dune Part Two" in items
