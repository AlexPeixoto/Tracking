"""Re-processing a location from its snapshot, without the drive."""

import importlib
import shutil
from pathlib import Path

import pytest
from test_discid import make_iso

from tracking import db, scan
from tracking.scan import discid, fs
from tracking.scan.store import NoSnapshot

walk = importlib.import_module("tracking.scan.walk")  # the module, not the function


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("TRACKING_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(discid, "ps2_name", {"SLUS-21221": "Okami"}.get)
    c = db.connect()
    yield c
    c.close()


def touch(root: Path, rel: str, data: bytes = b"x") -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)


def _library(tmp_path: Path) -> Path:
    lib = tmp_path / "lib"
    for e in (1, 2, 3):
        touch(lib, f"Anime/Mahouka S01 1080p/S01E0{e}-Enrollment Part {e}.mkv")
    touch(lib, "Anime/Mahouka S01 1080p/NC/NCOP1.mkv")
    touch(lib, "Games/PC/Hades/Hades.exe", b"x" * 5000)
    touch(lib, "Games/PC/Hades/unins000.exe")
    touch(lib, "Games/PC/Elden Ring [FitGirl Repack]/setup.exe")
    touch(lib, "Games/copy/image.iso", make_iso({"SYSTEM.CNF": b"BOOT2 = cdrom0:\\SLUS_212.21;1\r\n"}))
    touch(lib, "Movies/Blade Runner 2049 (2017) [1080p]/Blade Runner 2049 (2017).mkv")
    return lib


def _items(conn):
    return sorted((i.category, i.platform, i.title, i.install_state, i.rel_path.split("/lib/", 1)[-1],
                   i.video_count, i.missing) for i in db.list_items(conn))


def test_snapshot_replays_the_same_library_without_the_drive(tmp_path, conn):
    lib = _library(tmp_path)
    rid = scan.add_root(conn, str(lib))
    scan.scan_root(conn, rid)
    before = _items(conn)
    taken, files = fs.info(conn, rid)
    assert files == 9 and taken
    assert ("game", "PS2", "Okami", "image") in [r[:4] for r in before]     # read from the disc

    shutil.rmtree(lib)                                                       # "unplug the drive"
    res = scan.scan_root(conn, rid, from_snapshot=True)
    assert res.found == len(before) and res.missing == 0
    assert _items(conn) == before
    # Replaying doesn't overwrite the snapshot (it's still the last real look).
    assert fs.info(conn, rid) == (taken, files)


def test_new_rules_apply_on_replay(tmp_path, conn, monkeypatch):
    lib = _library(tmp_path)
    rid = scan.add_root(conn, str(lib))
    scan.scan_root(conn, rid)
    shutil.rmtree(lib)
    # A "rule change": PC games get a marker in their title.
    real = walk.titles.clean_game_title
    monkeypatch.setattr(walk.titles, "clean_game_title", lambda n: real(n) + " (rule v2)")
    scan.scan_root(conn, rid, from_snapshot=True)
    assert "Hades (rule v2)" in {i.title for i in db.list_items(conn)}
    item = next(i for i in db.list_items(conn) if i.title.startswith("Hades"))
    assert item.launch_path.endswith("Games/PC/Hades/Hades.exe")


def test_no_snapshot_yet(tmp_path, conn):
    (tmp_path / "lib").mkdir()
    rid = scan.add_root(conn, str(tmp_path / "lib"))
    with pytest.raises(NoSnapshot):
        scan.scan_root(conn, rid, from_snapshot=True)


def test_snapshot_fs_answers_like_the_disk(tmp_path):
    lib = _library(tmp_path)
    rec = fs.RecordingFS(str(lib))
    with fs.using(rec):
        list(fs.current().walk(str(lib)))
    snap = fs.SnapshotFS(str(lib), rec.dirs, rec.probes)
    real, fake = fs.RealFS(), snap
    for path in (lib / "Games" / "PC" / "Hades", lib / "Anime"):
        assert sorted((e.name, e.is_dir()) for e in real.scandir(str(path))) == \
               sorted((e.name, e.is_dir()) for e in fake.scandir(str(path)))
    exe = str(lib / "Games" / "PC" / "Hades" / "Hades.exe")
    assert fake.getsize(exe) == 5000 and fake.exists(exe) and not fake.exists(exe + "x")


def test_rescan_job_replays_unplugged_locations(tmp_path, conn, monkeypatch):
    from tracking.scan import store
    from tracking.ui import workers

    lib = _library(tmp_path)
    rid = scan.add_root(conn, str(lib))
    scan.scan_root(conn, rid)
    conn.commit()
    shutil.rmtree(lib)
    monkeypatch.setattr(store, "mount_for", lambda c, uid, known=None: None)   # drive unplugged
    summary = workers.ScanJob([rid], enrich_after=False).work(conn)
    assert "1 location from snapshots" in summary
    assert not any(i.missing for i in db.list_items(conn))
