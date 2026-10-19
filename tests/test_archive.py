import zipfile

import pytest

from tracking import archive, db, paths


def make_library(root, items, monkeypatch):
    """A library in ``root`` with items: (rel_path, title, completed, notes, poster bytes)."""
    monkeypatch.setenv("TRACKING_DATA_DIR", str(root))
    with db.session() as conn:
        conn.execute("INSERT OR IGNORE INTO volumes(uid, serial, label) VALUES('guid:{x}', 'S', 'Media')")
        conn.execute("INSERT OR IGNORE INTO roots(volume_uid, rel_path) VALUES('guid:{x}', '')")
        for rel, title, completed, notes, cover in items:
            iid = conn.execute(
                "INSERT INTO items(root_id, volume_uid, rel_path, is_dir, category, title, completed, "
                "notes, enriched_at) VALUES(1, 'guid:{x}', ?, 1, 'movie', ?, ?, ?, '2026-01-01')",
                (rel, title, completed, notes)).lastrowid
            conn.execute("INSERT INTO scores VALUES(?, 'IMDb', 7.5, 10, NULL)", (iid,))
            if cover:
                (paths.posters_dir() / f"{iid}.jpg").write_bytes(cover)
                conn.execute("UPDATE items SET poster=? WHERE id=?", (f"{iid}.jpg", iid))
        conn.execute("INSERT OR REPLACE INTO settings VALUES('omdb_key', ?)", (str(root.name),))


@pytest.fixture
def libs(tmp_path, monkeypatch):
    """Library A (exported) and library B (imported into)."""
    a, b = tmp_path / "a", tmp_path / "b"
    make_library(a, [("Heat", "Heat", 1, None, b"heat"), ("Dune", "Dune", 0, "theirs", b"dune")],
                 monkeypatch)
    zip_full, zip_db = tmp_path / "full.zip", tmp_path / "db.zip"
    archive.export_zip(zip_full, images=True)
    archive.export_zip(zip_db, images=False)
    make_library(b, [("Dune", "Dune", 0, "mine", None), ("Up", "Up", 0, None, b"up")], monkeypatch)
    return zip_full, zip_db


def titles(conn):
    return {i.title: i for i in db.list_items(conn)}


def test_export_contents(libs):
    zip_full, zip_db = libs
    with zipfile.ZipFile(zip_full) as z:
        assert sorted(z.namelist()) == ["library.sqlite3", "manifest.json", "posters/1.jpg",
                                        "posters/2.jpg"]
    with zipfile.ZipFile(zip_db) as z:
        assert sorted(z.namelist()) == ["library.sqlite3", "manifest.json"]
    assert archive.read_manifest(zip_db)["images"] is False


def test_replace(libs):
    zip_full, _ = libs
    result = archive.import_zip(zip_full, "replace")
    with db.session() as conn:
        items = titles(conn)
        assert set(items) == {"Heat", "Dune"}
        assert items["Heat"].completed and items["Dune"].notes == "theirs"
        assert (paths.posters_dir() / items["Heat"].poster).read_bytes() == b"heat"
        # The export left API keys out, so mine survive the replace.
        assert db.get_setting(conn, "omdb_key") == "b"
    assert result.covers == 2
    # Only the export's covers remain (my "Up" cover went with my library).
    assert {f.name: f.read_bytes() for f in paths.posters_dir().iterdir()} == {
        "1.jpg": b"heat", "2.jpg": b"dune"}


def test_replace_database_only_marks_covers_for_refetch(libs):
    _, zip_db = libs
    result = archive.import_zip(zip_db, "replace")
    assert result.need_metadata == 2
    with db.session() as conn:
        assert all(i.poster is None for i in db.list_items(conn))
        assert conn.execute("SELECT COUNT(*) n FROM items WHERE enriched_at IS NULL").fetchone()["n"] == 2


def test_merge_keeps_mine_and_adds_theirs(libs):
    zip_full, _ = libs
    result = archive.import_zip(zip_full, "merge")
    assert (result.added, result.covers) == (1, 2)  # Heat added; Heat + Dune covers
    with db.session() as conn:
        items = titles(conn)
        assert set(items) == {"Heat", "Dune", "Up"}
        assert items["Dune"].notes == "mine"            # my notes win
        assert items["Heat"].completed                  # their completion comes along
        assert len(items["Heat"].scores) == 1
        assert (paths.posters_dir() / items["Heat"].poster).read_bytes() == b"heat"
        assert (paths.posters_dir() / items["Dune"].poster).read_bytes() == b"dune"  # I had none
        assert (paths.posters_dir() / items["Up"].poster).read_bytes() == b"up"      # untouched
        assert db.get_setting(conn, "omdb_key") == "b"  # my settings win


def test_old_backup_without_manifest_and_bad_zip(tmp_path, libs):
    zip_full, _ = libs
    old = tmp_path / "old.zip"
    with zipfile.ZipFile(zip_full) as src, zipfile.ZipFile(old, "w") as dst:
        for n in src.namelist():
            if n != "manifest.json":
                dst.writestr(n, src.read(n))
    assert archive.read_manifest(old)["images"] is True
    (tmp_path / "not.zip").write_bytes(b"nope")
    with pytest.raises(archive.ArchiveError):
        archive.read_manifest(tmp_path / "not.zip")


def test_credentials_only_when_asked(tmp_path, libs, monkeypatch):
    import sqlite3

    # libs ends with library B active; export it both ways.
    plain, with_keys = tmp_path / "plain.zip", tmp_path / "keys.zip"
    archive.export_zip(plain, images=False)
    archive.export_zip(with_keys, images=False, credentials=True)
    for zip_path, expected in ((plain, None), (with_keys, ("b",))):
        with zipfile.ZipFile(zip_path) as z:
            z.extract("library.sqlite3", tmp_path / zip_path.stem)
        c = sqlite3.connect(tmp_path / zip_path.stem / "library.sqlite3")
        assert c.execute("SELECT value FROM settings WHERE key='omdb_key'").fetchone() == expected
        c.close()
    assert archive.read_manifest(with_keys)["credentials"] is True
