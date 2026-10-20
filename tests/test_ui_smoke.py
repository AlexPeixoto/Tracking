"""Every dialog opens and its main buttons work; the Launch and Transfer jobs run end to end."""

import os
import zipfile
from pathlib import Path

import pytest

from tracking import db, launcher, library, providers, scan

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


def _game(conn, tmp_path):
    touch(tmp_path, "lib/Games/PS2/Okami (USA).zip")
    touch(tmp_path, "lib/Games/PC/Hades/Hades.exe", b"x" * 100)
    rid = scan.add_root(conn, str(tmp_path / "lib"))
    scan.scan_root(conn, rid)
    conn.commit()
    return {i.title: i for i in db.list_items(conn)}, rid


def test_settings_saves_emulators_and_player(conn, tmp_path):
    from tracking.ui.dialogs import SettingsDialog

    dlg = SettingsDialog()
    path, args, unzip = dlg.emulators["PS2"]
    path.setText(str(tmp_path / "pcsx2-qt.exe"))
    args.setText('-fullscreen "{file}"')
    unzip.setChecked(False)
    dlg.player.setText(r"C:\mpv\mpv.exe")
    dlg._save()
    assert db.get_setting(conn, launcher.emulator_key("PS2")).endswith("pcsx2-qt.exe")
    assert db.get_setting(conn, launcher.emulator_args_key("PS2")) == '-fullscreen "{file}"'
    assert db.get_setting(conn, launcher.emulator_unzip_key("PS2")) == "0"
    assert db.get_setting(conn, launcher.PLAYER_KEY) == r"C:\mpv\mpv.exe"


def test_locations_rescan_log_and_health_open(conn, tmp_path):
    from tracking.ui.dialogs import HealthDialog, LocationsDialog, LogDialog, RescanDialog

    items, rid = _game(conn, tmp_path)
    LocationsDialog()
    rescan = RescanDialog()
    assert rescan.root_ids() == [rid]
    LogDialog()
    health = HealthDialog(list(items.values()))
    shown, fetched = [], []
    health.show_items.connect(lambda h, ids: shown.append(ids))
    health.refetch.connect(fetched.append)
    no_cover = next(c for c in library.health(list(items.values())) if c.key == "no_cover")
    health._show(no_cover)
    health._refetch(no_cover)
    assert shown == [no_cover.ids] and fetched == [no_cover.ids]


def test_find_match_dialog_uses_the_picked_result(conn, tmp_path, monkeypatch):
    from tracking.ui.dialogs import MatchDialog

    items, _ = _game(conn, tmp_path)
    monkeypatch.setattr(providers, "search_all", lambda q, limit=12: [
        providers.Candidate("OKAMI HD", 2017, "Steam · PC", None, None, 587620)])
    dlg = MatchDialog(items["Okami"])
    dlg._show(dlg._no, "", providers.search_all(None))
    dlg.results.item(0).setSelected(True)
    dlg._use()
    got = db.get_item(conn, items["Okami"].id)
    assert (got.title, got.steam_appid) == ("OKAMI HD", 587620)


def test_launch_job_unzips_starts_and_records(conn, tmp_path, monkeypatch):
    from tracking.ui import workers

    items, _ = _game(conn, tmp_path)
    with zipfile.ZipFile(tmp_path / "lib/Games/PS2/Okami (USA).zip", "w") as z:
        z.writestr("Okami (USA).iso", b"\0" * 4096)
    emu = tmp_path / "pcsx2-qt.exe"
    emu.write_bytes(b"x")
    db.set_setting(conn, launcher.emulator_key("PS2"), str(emu))
    conn.commit()
    okami = db.get_item(conn, items["Okami"].id)
    la = launcher.plan(conn, okami, scan.mount_for(conn, okami.volume_uid))
    started = []
    monkeypatch.setattr(launcher, "start", lambda launch: started.append(launch.command) or None)
    summary = workers.LaunchJob(la).work(conn)
    assert summary.startswith("Started") and started[0][-1].endswith("Okami (USA).iso")
    got = db.get_item(conn, okami.id)
    assert got.launch_count == 1 and got.plan_state == library.PLAN_PLAYING


def test_transfer_job_moves_and_reports(conn, tmp_path):
    from tracking.ui import workers

    items, _ = _game(conn, tmp_path)
    hades = items["Hades"]
    dest = tmp_path / "ssd"
    dest.mkdir()
    mounts = {hades.volume_uid: scan.mount_for(conn, hades.volume_uid)}
    summary = workers.TransferJob([hades.id], str(dest), True, mounts).work(conn)
    assert summary.startswith("Moved 1 of 1")
    assert (dest / "Hades" / "Hades.exe").exists()
    assert not (tmp_path / "lib/Games/PC/Hades").exists()
