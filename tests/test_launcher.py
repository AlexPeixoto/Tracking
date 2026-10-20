"""Launcher basics: installed / installer / ROM detection, Play plans, recently played,
hide offline; and finding matches (II = 2, subtitles in the middle, Find match)."""

import os
import zipfile
from pathlib import Path

import pytest

from tracking import db, launcher, library, providers, scan
from tracking.providers.base import search_variants, similarity
from tracking.scan import install


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("TRACKING_DATA_DIR", str(tmp_path / "data"))
    c = db.connect()
    yield c
    c.close()


def touch(root: Path, rel: str, size: int = 1) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x" * size)


# --- detection ---------------------------------------------------------------

def test_detect_installed_picks_the_game_exe():
    files = [("unins000.exe", 1_000_000), ("Hades.exe", 900_000), ("x64/Hades.exe", 30_000_000),
             ("CrashReporter.exe", 5_000_000), ("redist/vc_redist.x64.exe", 20_000_000),
             ("Launcher.exe", 40_000_000)]
    assert install.detect(files, "PC", "Hades") == (install.INSTALLED, "Hades.exe")


def test_detect_installer_and_image():
    repack = [("setup.exe", 3_000_000), ("fg-01.bin", 4_000_000_000), ("MD5/QuickSFV.EXE", 100)]
    assert install.detect(repack, "PC", "Elden Ring") == (install.INSTALLER, "setup.exe")
    gog = [("setup_hades_1.38.exe", 2_000_000), ("setup_hades_1.38-1.bin", 9_000_000_000)]
    assert install.detect(gog, "PC", "Hades") == (install.INSTALLER, "setup_hades_1.38.exe")
    assert install.detect([("Game.iso", 7_000_000_000)], "PC", "Game")[0] == install.INSTALLER
    disc = [("Game (Track 1).bin", 600_000_000), ("Game (Track 2).bin", 20_000_000), ("Game.cue", 300)]
    assert install.detect(disc, "PS1", "Game") == (install.IMAGE, "Game.cue")
    ps3 = [("PS3_GAME/USRDIR/EBOOT.BIN", 20_000_000), ("PS3_DISC.SFB", 100)]
    assert install.detect(ps3, "PS3", "Demon's Souls") == (install.IMAGE, "PS3_GAME/USRDIR/EBOOT.BIN")


def test_scan_stores_install_state_and_launch_path(tmp_path, conn):
    touch(tmp_path, "Games/PC/Hades/Hades.exe", 5000)
    touch(tmp_path, "Games/PC/Hades/unins000.exe", 9000)
    touch(tmp_path, "Games/PC/Elden Ring [FitGirl Repack]/setup.exe")
    touch(tmp_path, "Games/PC/Elden Ring [FitGirl Repack]/fg-01.bin", 9000)
    touch(tmp_path, "Games/PS2/Okami (USA).iso", 9000)
    rid = scan.add_root(conn, str(tmp_path))
    scan.scan_root(conn, rid)
    got = {i.title: (i.install_state, (i.launch_path or "").rsplit("/", 1)[-1]) for i in db.list_items(conn)}
    assert got == {"Hades": ("installed", "Hades.exe"), "Elden Ring": ("installer", "setup.exe"),
                   "Okami": ("image", "Okami (USA).iso")}


# --- launching ---------------------------------------------------------------

def _game(conn, title, platform, launch, **cols):
    iid = conn.execute(
        "INSERT INTO items(volume_uid, rel_path, is_dir, category, platform, title, launch_path) "
        "VALUES('v', ?, 1, 'game', ?, ?, ?)", (f"g/{title}", platform, title, launch)).lastrowid
    for k, v in cols.items():
        conn.execute(f"UPDATE items SET {k}=? WHERE id=?", (v, iid))
    return db.get_item(conn, iid)


def test_plan_pc_emulator_and_steam(tmp_path, conn):
    touch(tmp_path, "g/Hades/Hades.exe")
    touch(tmp_path, "g/Okami/Okami.iso")
    touch(tmp_path, "emu/pcsx2-qt.exe")
    mount = str(tmp_path)
    pc = _game(conn, "Hades", "PC", "g/Hades/Hades.exe", launch_args="-windowed")
    la = launcher.plan(conn, pc, mount)
    assert la.command == [os.path.join(mount, "g", "Hades", "Hades.exe"), "-windowed"]
    assert la.cwd == os.path.join(mount, "g", "Hades")

    ps2 = _game(conn, "Okami", "PS2", "g/Okami/Okami.iso")
    with pytest.raises(launcher.LaunchError, match="No emulator set for PS2"):
        launcher.plan(conn, ps2, mount)
    db.set_setting(conn, launcher.emulator_key("PS2"), str(tmp_path / "emu" / "pcsx2-qt.exe"))
    la = launcher.plan(conn, ps2, mount)
    assert la.command == [str(tmp_path / "emu" / "pcsx2-qt.exe"), "-batch",
                          os.path.join(mount, "g", "Okami", "Okami.iso")]
    with pytest.raises(launcher.LaunchError, match="isn't connected"):
        launcher.plan(conn, ps2, None)

    steam = _game(conn, "Celeste", "PC", None, source="steam", steam_appid=504230)
    assert launcher.plan(conn, steam, None).url == "steam://rungameid/504230"


def test_build_command_templates():
    assert launcher.build_command("e.exe", '--no-gui "{file}"', r"D:\My Games\x.iso") == [
        "e.exe", "--no-gui", r"D:\My Games\x.iso"]
    assert launcher.build_command("e.exe", "-fullscreen", "x.iso") == ["e.exe", "-fullscreen", "x.iso"]


def test_unzip_image_keeps_cue_and_bins(tmp_path, monkeypatch):
    monkeypatch.setenv("TRACKING_DATA_DIR", str(tmp_path / "data"))
    z = tmp_path / "Game.zip"
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("Game.cue", 'FILE "Game.bin" BINARY')
        f.writestr("Game.bin", b"\0" * 5000)
    out = launcher.unzip_image(str(z))
    assert out.endswith("Game.cue") and Path(out).with_name("Game.bin").exists()
    assert launcher.unzip_image(str(z)) == out  # reused, not unpacked again


def test_set_launch_is_drive_relative(tmp_path, conn, monkeypatch):
    item = _game(conn, "Hades", "PC", "g/Hades/Hades.exe")
    monkeypatch.setattr(launcher.scan, "mount_for", lambda c, uid, known=None: str(tmp_path))
    launcher.set_launch(conn, item.id, str(tmp_path / "g" / "Hades" / "x64" / "Hades.exe"), " -dx11 ")
    item = db.get_item(conn, item.id)
    assert (item.launch_path, item.launch_args) == ("g/Hades/x64/Hades.exe", "-dx11")
    launcher.set_launch(conn, item.id, None, "")
    assert db.get_item(conn, item.id).launch_path == "g/Hades/Hades.exe"   # back to detected


def test_recently_played_install_filter_and_hide_offline(conn, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from tracking.ui import main_window

    monkeypatch.setattr(main_window.MainWindow, "manage_locations", lambda self: None)
    a = _game(conn, "Old", "PC", "a.exe", install_state="installed", last_launched="2026-01-01T10:00:00")
    b = _game(conn, "New", "PC", "b.exe", install_state="installed", last_launched="2026-10-01T10:00:00")
    c = _game(conn, "Steamy", "PC", "c.exe", install_state="installed", last_played="2026-05-01T00:00:00+00:00")
    _game(conn, "Never", "PC", "setup.exe", install_state="installer")
    conn.execute("INSERT INTO volumes(uid, serial, label) VALUES('v', 'S', 'Ext')")
    conn.commit()
    w = main_window.MainWindow()
    w.nav = ("game", None)
    w.sort.setCurrentIndex(w.sort.findData("launched"))
    w.reload()
    assert [i.title for i in w.model.items][:3] == ["New", "Steamy", "Old"]
    w.install.setCurrentIndex(w.install.findData("installer"))
    assert [i.title for i in w.model.items] == ["Never"]
    w.install.setCurrentIndex(0)
    w.mounts = {"v": None}
    w.hide_offline.setChecked(True)
    assert w.model.items == []
    w.hide_offline.setChecked(False)
    assert {a.id, b.id, c.id} <= {i.id for i in w.model.items}


# --- matching ----------------------------------------------------------------

def test_roman_numerals_and_subtitles_in_the_middle():
    assert similarity("Fatal Frame 2 Remake", "FATAL FRAME II: Crimson Butterfly REMAKE") >= 75
    assert similarity("Grand Theft Auto V", "Grand Theft Auto 5") == 100
    assert similarity("Dark Souls", "Dark Souls III") < 75
    assert similarity("Mega Man X", "Mega Man 10") < 75
    assert "Fatal Frame II Remake" in search_variants("Fatal Frame 2 Remake")
    assert "Fatal Frame 2: Crimson Butterfly" in search_variants("Fatal Frame II - Crimson Butterfly")
    assert search_variants("Show Season 2") == ["Show Season 2"]


def test_find_match_uses_the_picked_title(conn, monkeypatch):
    item = _game(conn, "Fatal Frame 2 Remake", "PC", None, enriched_at="x", rating=5.0)
    conn.execute("INSERT INTO scores(item_id, source, value, max_value) VALUES(?, 'X', 1, 10)", (item.id,))
    library.use_match(conn, item, "FATAL FRAME II: Crimson Butterfly REMAKE", 2026, 2638370)
    got = db.get_item(conn, item.id)
    assert (got.title, got.year, got.steam_appid, got.scores) == (
        "FATAL FRAME II: Crimson Butterfly REMAKE", 2026, 2638370, [])
    assert conn.execute("SELECT enriched_at FROM items WHERE id=?", (item.id,)).fetchone()[0] is None

    calls = []
    for name in ("steam", "hltb"):
        mod = getattr(providers, name)
        fake = type(mod.PROVIDER)(**{**mod.PROVIDER.__dict__, "search": lambda q, n=name: calls.append(n)
                                     or [providers.Candidate(f"{n} hit")]})
        monkeypatch.setattr(providers, "REGISTRY",
                            [fake if r.name == name else r for r in providers.REGISTRY])
    found = providers.search_all(providers.Query("x", category="game", platform="PC"))
    assert {c.title for c in found} == {"steam hit", "hltb hit"}
    assert providers.search(providers.Query("x", category="game", platform="PC"))[1][0].title == "steam hit"


def test_rescan_keeps_a_looked_up_year(tmp_path, conn):
    touch(tmp_path, "Games/PC/Hades/Hades.exe")
    rid = scan.add_root(conn, str(tmp_path))
    scan.scan_root(conn, rid)
    conn.execute("UPDATE items SET year=2020")
    scan.scan_root(conn, rid)
    assert db.list_items(conn)[0].year == 2020
