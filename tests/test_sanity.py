"""Library sanity fixes: installed games with many folders, emulator / tool folders,
openings and endings counted as episodes, later seasons without a cover."""

from pathlib import Path

import pytest

from tracking import db, enrich, paths, scan
from tracking.providers.base import search_variants
from tracking.scan import classify
from tracking.scan.classify import Hints
from tracking.scan.videos import is_extra_file, is_short_extra


def touch(root: Path, rel: str, data: bytes = b"x") -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("TRACKING_DATA_DIR", str(tmp_path / "data"))
    c = db.connect()
    yield c
    c.close()


def test_installed_game_with_many_folders_is_one_game(tmp_path):
    # 30XX: its own .exe at the top, and lots of folders (data, editor, music...).
    touch(tmp_path, "Games/30XX/30XX.exe", b"MZ" + b"x" * 100)
    for d in ("data", "editor", "fmod", "music", "30XX_OST", "shaders2"):
        touch(tmp_path, f"Games/30XX/{d}/a.bin")
    # Trails: no matching .exe name, but engine files only an installed game has.
    touch(tmp_path, "Games/TLoH - Trails through Daybreak/ed9.exe", b"MZ" + b"x" * 100)
    touch(tmp_path, "Games/TLoH - Trails through Daybreak/EOSSDK-Win64-Shipping.dll")
    for d in ("bgm", "cursors", "movie", "scene", "table"):
        touch(tmp_path, f"Games/TLoH - Trails through Daybreak/{d}/x.webm")
    found = {i.rel_path: i for i in scan.collect(str(tmp_path), "", Hints())}
    assert set(found) == {"Games/30XX", "Games/TLoH - Trails through Daybreak"}
    assert all(i.category == "game" for i in found.values())


def test_emulator_tool_and_redist_folders_are_skipped(tmp_path):
    touch(tmp_path, "Downloads/rpcs3/rpcs3.exe")
    touch(tmp_path, "Downloads/rpcs3/dev_hdd0/game/BLUS30161/USRDIR/EBOOT.BIN")
    touch(tmp_path, "Downloads/Cinebench2024_win_x86_64/Cinebench.exe")
    touch(tmp_path, "Downloads/Cemu_2.6/Cemu.exe")
    touch(tmp_path, "Games/FF Bundle/_Redist/vc_redist.x64.exe")
    for n in (1, 2, 3):
        touch(tmp_path, f"Games/FF Bundle/FF{n}/FINAL FANTASY.exe")
    found = {i.rel_path for i in scan.collect(str(tmp_path), "", Hints())}
    assert not any("rpcs3" in p or "Cinebench" in p or "Cemu" in p or "Redist" in p for p in found)
    assert {"Games/FF Bundle/FF1", "Games/FF Bundle/FF2", "Games/FF Bundle/FF3"} <= found
    assert classify.is_tool_name("Cinebench2024_win_x86_64") and not classify.is_tool_name("Okami 2")


@pytest.mark.parametrize("name", [
    "[polished] Hanamaru Youchien NCED 01 [BD 1080p][7F92F20E].mkv",
    "[Judas] High School DxD S2 - NCOP 1.mkv",
    "[Judas] Dororo - Opening 01.mkv",
    "[INDEX] Your Being Summoned, Azezal - Clean Ending [329DBA66].mkv",
    "Boogiepop Phantom - MV [Ending 2] (BDRip)[sxales].mkv",
    "Boogiepop Phantom - TV-SPOT (BDRip)[sxales].mkv",
    "a. Mobile Suit GUNDAM 0079 - OP (Opening-Intro) Video.mkv",
    "[Judas] Dakara Boku wa, H ga Dekinai - ED.mkv",
])
def test_openings_and_endings_arent_episodes(name):
    assert is_extra_file(name)


@pytest.mark.parametrize("name", [
    "Room Camp - S01E01.mkv", "[Judas] High School DxD S1 - SP01.mkv", "Steins;Gate - 01.mkv",
    "Mobile Suit Gundam SEED - 01.mkv", "The Neverending Story.mkv",
])
def test_short_episodes_stay(name):
    assert not is_extra_file(name) and not is_short_extra(name, 200)


def test_short_video_named_like_an_ending_is_dropped_once_measured(conn):
    iid = conn.execute("INSERT INTO items(volume_uid, rel_path, is_dir, category, title, kind) "
                       "VALUES('v', 'Show', 1, 'anime', 'Show', 'series')").lastrowid
    rows = [("Show/Show - 01.mkv", 1420.0), ("Show/Show - Ending Theme Full.mkv", 90.0),
            ("Show/Show - Ending Theme Full (long cut).mkv", 600.0)]
    conn.executemany("INSERT INTO media_files(item_id, rel_path, size_bytes, mtime, duration_sec) "
                     "VALUES(?,?,1,1,?)", [(iid, p, d) for p, d in rows])
    assert enrich.drop_short_extras(conn, iid) == 1      # only the 90-second one
    assert len(conn.execute("SELECT 1 FROM media_files WHERE item_id=?", (iid,)).fetchall()) == 2


def test_numbers_inside_words_stay_digits():
    assert search_variants("Narcissu 10th Anniversary Anthology Project") == [
        "Narcissu 10th Anniversary Anthology Project"]
    assert "Fatal Frame II Remake" in search_variants("Fatal Frame 2 Remake")
    assert search_variants("Ys 10") == ["Ys 10"]


def _show(conn, title, rel, season=None, poster=None, kind="series"):
    iid = conn.execute(
        "INSERT INTO items(volume_uid, rel_path, is_dir, category, title, kind, season, poster) "
        "VALUES('v', ?, 1, 'anime', ?, ?, ?, ?)", (rel, title, kind, season, poster)).lastrowid
    if poster:
        (paths.posters_dir() / poster).write_bytes(b"cover of " + title.encode())
    return iid


def test_later_season_borrows_the_first_seasons_cover(conn):
    _show(conn, "Agent AIKa", "AIKa (1997)::s1", 1, "1.jpg")
    s2 = _show(conn, "Agent AIKa Season 2", "AIKa (1997)::s2", 2)
    _show(conn, "Blood Blockade Battlefront", "Kekkai Sensen::s1", 1, "3.jpg")
    kk2 = _show(conn, "Blood Blockade Season 2", "Kekkai Sensen::s2", 2)
    lone = _show(conn, "Nobody Season 2", "Nobody::s2", 2)
    first = _show(conn, "No Cover", "No Cover", 1)
    assert enrich.borrow_season_cover(conn, s2)
    assert enrich.borrow_season_cover(conn, kk2)                       # same release folder
    assert not enrich.borrow_season_cover(conn, lone)                  # no first season
    assert not enrich.borrow_season_cover(conn, first)                 # a first season itself
    poster = conn.execute("SELECT poster FROM items WHERE id=?", (s2,)).fetchone()[0]
    assert (paths.posters_dir() / poster).read_bytes() == b"cover of Agent AIKa"
    assert not enrich.borrow_season_cover(conn, s2)                    # has one now


def test_windows_boot_and_recovery_partitions_arent_offered():
    from tracking import volumes

    guid = r"\\?\Volume{2af49aa6-d090-4e16-beea-078c1256d94b}" + "\\"
    recovery = volumes.Volume("guid:x", None, "", guid)
    assert volumes.is_system_partition(recovery, 985_657_344)            # 0.9 GB, no letter
    assert not volumes.is_system_partition(recovery, 500 * 1024**3)       # a big letterless disk
    assert not volumes.is_system_partition(volumes.Volume("guid:y", None, "SNES", guid), 10**8)
    assert not volumes.is_system_partition(volumes.Volume("guid:z", None, "", "E:\\"), 10**8)


def test_search_wording_is_less_picky():
    assert "Gal Gun 2" in search_variants("GalGun 2")
    assert search_variants("DmC Devil May Cry") == ["DmC Devil May Cry"]
    assert "White Knight Chronicles" in search_variants("White Knight Chronicles - International Edition")
    assert "Death Stranding" in search_variants("Death Stranding Directors Cut")
    assert "Atelier Ryza - Secret Trilogy Deluxe" in search_variants("Atelier Ryza - Secret Trilogy DX")
    assert search_variants("Edition") == ["Edition"]


def test_libretro_looks_in_the_sibling_set(monkeypatch):
    from tracking.providers import Query, libretro

    sets = {"Nintendo - Game Boy": ["Tetris (World)"],
            "Nintendo - Game Boy Color": ["Rayman 2 - The Great Escape (USA)"]}
    monkeypatch.setattr(libretro, "names", lambda system: sets.get(system, []))
    md = libretro.lookup(Query("Rayman 2 - The Great Escape", category="game", platform="Game Boy"))
    assert md and "Game%20Boy%20Color" in md.poster_url
    assert libretro.lookup(Query("Tetris", category="game", platform="Game Boy")).title == "Tetris"


def test_same_title_elsewhere_lends_its_cover(conn):
    steam = _show(conn, "Gal*Gun 2", "app/851890", None, "steam.jpg", kind=None)
    switch = _show(conn, "GalGun 2", "tpex/sw/GalGun 2.xci", None, None, kind=None)
    conn.execute("UPDATE items SET category='game' WHERE id IN (?, ?)", (steam, switch))
    other = _show(conn, "Gal", "x/Gal", None, None, kind=None)        # too short to trust
    assert enrich.borrow_cover(conn, switch)
    poster = conn.execute("SELECT poster FROM items WHERE id=?", (switch,)).fetchone()[0]
    assert (paths.posters_dir() / poster).read_bytes() == b"cover of Gal*Gun 2"
    assert not enrich.borrow_cover(conn, other)
