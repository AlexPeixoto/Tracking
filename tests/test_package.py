"""Library package (docs/PACKAGE.md): what's in it, and that nothing private is."""

import json
import os
import zipfile

import pytest

from tracking import completions, db, library, package, paths


@pytest.fixture
def conn(tmp_path, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6.QtGui")
    monkeypatch.setenv("TRACKING_DATA_DIR", str(tmp_path / "data"))
    c = db.connect()
    yield c
    c.close()


def _cover(name: str) -> None:
    from PySide6.QtGui import QColor, QImage

    img = QImage(800, 1200, QImage.Format_RGB32)
    img.fill(QColor("#336699"))
    img.save(str(paths.posters_dir() / name), "JPEG")


def _library(conn) -> dict[str, int]:
    ids = {}
    ids["okami"] = conn.execute(
        "INSERT INTO items(volume_uid, rel_path, is_dir, category, platform, title, year, poster, notes, "
        "completed, completed_at, rating, rating_max, rating_source, summary, external_url, plan_state) "
        "VALUES('guid:{secret-drive}', 'Games/PS2/Okami (USA).iso', 0, 'game', 'PS2', 'Okami', 2006, "
        "'1.jpg', 'my private note', 1, '2024-05-01', 93, 100, 'Metacritic', 'A wolf.', "
        "'https://example.org/okami', 'next')").lastrowid
    ids["mushishi"] = conn.execute(
        "INSERT INTO items(volume_uid, rel_path, is_dir, category, title, kind, season, poster) "
        "VALUES('guid:{secret-drive}', 'anime/Mushishi S1', 1, 'anime', 'Mushishi', 'series', 1, '2.jpg')"
    ).lastrowid
    conn.execute("INSERT INTO scores(item_id, source, value, max_value, url) VALUES(?, 'AniList', 87, 100, NULL)",
                 (ids["mushishi"],))
    conn.execute("INSERT INTO media_files(item_id, rel_path, size_bytes, mtime, season, episode) "
                 "VALUES(?, 'anime/Mushishi S1/01.mkv', 1, 1, 1, 1)", (ids["mushishi"],))
    conn.execute("INSERT INTO episodes(item_id, season, number, title, source) VALUES(?, 1, 1, 'The Green Seat', 'MAL')",
                 (ids["mushishi"],))
    conn.execute("INSERT INTO episodes(item_id, season, number, title, source) VALUES(?, 1, 2, 'The Light of the Eyelid', 'MAL')",
                 (ids["mushishi"],))
    ids["gone"] = conn.execute(
        "INSERT INTO items(volume_uid, rel_path, is_dir, category, title, missing) "
        "VALUES('guid:{secret-drive}', 'x', 0, 'movie', 'Deleted Movie', 1)").lastrowid
    ids["wish"] = library.add_wanted(conn, "game", "Silksong", 2025)
    conn.execute("INSERT INTO completions(title, category, completed_on, item_id) VALUES('Okami', 'game', "
                 "'2024-05-01', ?)", (ids["okami"],))
    conn.execute("INSERT INTO completions(title, category, completed_on, poster) VALUES('Celeste', 'game', "
                 "'2023', '3.jpg')")
    conn.execute("INSERT INTO settings(key, value) VALUES('igdb_client_secret', 'TOPSECRET')")
    conn.commit()
    for n in (1, 2, 3):
        _cover(f"{n}.jpg")
    return ids


def test_package_has_the_library_and_nothing_private(conn, tmp_path):
    _library(conn)
    dest = tmp_path / "lib.trackinglib.zip"
    manifest = package.export_package(dest, name="Alex's library")
    assert manifest["counts"] == {"items": 3, "completions": 2, "covers": 3}

    with zipfile.ZipFile(dest) as zf:
        raw = zf.read(package.LIBRARY).decode("utf-8") + zf.read(package.MANIFEST).decode("utf-8")
        covers = [n for n in zf.namelist() if n.startswith("covers/")]
    for secret in ("secret-drive", "Okami (USA).iso", "Mushishi S1", "01.mkv", "my private note", "TOPSECRET",
                   "Deleted Movie"):
        assert secret not in raw, secret
    assert len(covers) == 3 and all(c.endswith(".webp") for c in covers)

    m, lib = package.read_package(dest)
    assert m["name"] == "Alex's library" and m["version"] == package.VERSION
    assert [c["key"] for c in lib["categories"]][:4] == ["anime", "movie", "tv", "game"]
    items = {i["title"]: i for i in lib["items"]}
    okami = items["Okami"]
    assert (okami["platform"], okami["year"], okami["completed"], okami["plan"], okami["source"]) == (
        "PS2", 2006, True, "next", "disk")
    assert okami["rating"] == {"value": 93, "max": 100, "source": "Metacritic"} and okami["cover"] in covers
    mushishi = items["Mushishi"]
    assert mushishi["episodes_owned"] == 1
    assert [(e["number"], e["owned"]) for e in mushishi["episodes"]] == [(1, True), (2, False)]
    assert mushishi["scores"] == [{"source": "AniList", "value": 87, "max": 100}]
    assert items["Silksong"]["source"] == "wish"
    done = {c["title"]: c for c in lib["completions"]}
    assert done["Okami"]["item"] == okami["id"] and done["Okami"]["cover"] == okami["cover"]  # shared, once
    assert done["Celeste"]["item"] is None and done["Celeste"]["date"] == "2023" and done["Celeste"]["cover"]


def test_covers_are_small_and_options(conn, tmp_path):
    from PySide6.QtGui import QImage

    _library(conn)
    dest = tmp_path / "small.zip"
    package.export_package(dest, covers=False, wishlist=False)
    m, lib = package.read_package(dest)
    assert m["counts"]["covers"] == 0 and all(i["cover"] is None for i in lib["items"])
    assert "Silksong" not in {i["title"] for i in lib["items"]}

    package.export_package(dest)
    with zipfile.ZipFile(dest) as zf:
        name = next(n for n in zf.namelist() if n.startswith("covers/"))
        img = QImage.fromData(zf.read(name))
    assert img.width() <= package.COVER_SIZE[0] and img.height() <= package.COVER_SIZE[1]


def test_read_package_refuses_other_files(tmp_path):
    bad = tmp_path / "x.zip"
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"format": "something-else"}))
    with pytest.raises(ValueError):
        package.read_package(bad)
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"format": package.FORMAT, "version": 99}))
        zf.writestr("library.json", "{}")
    with pytest.raises(ValueError, match="newer"):
        package.read_package(bad)
    with pytest.raises(ValueError):
        package.read_package(tmp_path / "missing.zip")


def test_completions_module_untouched(conn):
    # The package reads the Completed log; it never changes it.
    _library(conn)
    before = completions.years(conn)
    package.export_package(paths.data_dir() / "p.zip")
    assert completions.years(conn) == before


def test_share_dialog_and_job(conn, tmp_path):
    from PySide6.QtWidgets import QApplication

    from tracking.ui.dialogs import ShareDialog
    from tracking.ui.workers import ShareJob

    QApplication.instance() or QApplication([])
    _library(conn)
    dlg = ShareDialog()
    dlg.path.setText(str(tmp_path / "mine"))
    assert dlg.dest.name == "mine" + package.SUFFIX
    job = ShareJob(dlg.dest, "Mine", True, False)
    summary = job.work(conn)
    assert "2 items" in summary and dlg.dest.exists()
    job.cancelled = lambda: True
    assert "cancelled" in job.work(conn) and not dlg.dest.exists()
