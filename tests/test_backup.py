import sqlite3
import zipfile

from tracking import backup, db, paths


def test_backup_zip_has_library_and_posters(tmp_path, monkeypatch):
    monkeypatch.setenv("TRACKING_DATA_DIR", str(tmp_path / "data"))
    with db.session() as conn:  # an open connection (WAL) like the running app has
        conn.execute("INSERT INTO settings(key, value) VALUES('x', 'kept')")
        conn.commit()
        (paths.posters_dir() / "1.jpg").write_bytes(b"jpg")
        n = backup.backup(tmp_path / "b.zip")

    assert n == 2
    with zipfile.ZipFile(tmp_path / "b.zip") as z:
        assert sorted(z.namelist()) == ["library.sqlite3", "posters/1.jpg"]
        z.extract("library.sqlite3", tmp_path / "restored")
    restored = sqlite3.connect(tmp_path / "restored" / "library.sqlite3")
    assert restored.execute("SELECT value FROM settings WHERE key='x'").fetchone() == ("kept",)
    restored.close()


def test_portable_data_folder(tmp_path, monkeypatch):
    monkeypatch.delenv("TRACKING_DATA_DIR", raising=False)
    monkeypatch.setattr(paths, "app_dir", lambda: tmp_path)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    assert paths.data_dir() == tmp_path / "local" / "Tracking"
    (tmp_path / "data").mkdir()
    assert paths.data_dir() == tmp_path / "data"
