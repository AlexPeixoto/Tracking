"""Year stats, image presets / stamp, backlog, duplicates, library health, shortcuts."""

import os

import pytest

from tracking import completions, db, library


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("TRACKING_DATA_DIR", str(tmp_path / "data"))
    c = db.connect()
    yield c
    c.close()


@pytest.fixture
def qt(monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    from tracking.ui import main_window

    # An empty library opens Locations shortly after start: a modal dialog that would
    # block whichever later test processes events.
    monkeypatch.setattr(main_window.MainWindow, "manage_locations", lambda self: None)
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _item(conn, title, category="game", platform="PS2", path=None, **cols):
    iid = conn.execute(
        "INSERT INTO items(volume_uid, rel_path, is_dir, category, platform, title) "
        "VALUES('v', ?, 0, ?, ?, ?)", (path or f"x/{title}/{platform}", category, platform, title)).lastrowid
    for k, v in cols.items():
        conn.execute(f"UPDATE items SET {k}=? WHERE id=?", (v, iid))
    return iid


def test_year_stats(conn):
    g = _item(conn, "Okami", hltb_hours=33.0)
    p = _item(conn, "Hades", platform="PC", hltb_hours=20.0, playtime_min=60 * 45)
    a = _item(conn, "Frieren", category="anime", platform=None, duration_sec=28 * 24 * 60)
    completions.add(conn, "Okami", "2024-02-10", item_id=g)
    completions.add(conn, "Hades", "2024-02-20", item_id=p)
    completions.add(conn, "Frieren", "2024-05-01", "anime", item_id=a)
    completions.add(conn, "Celeste", "2024")                   # outside the library, no day
    completions.add(conn, "Old", "2019-01-01")
    s = completions.year_stats(conn, 2024)
    assert (s.count, s.by_category, s.timed, s.undated) == (4, {"game": 3, "anime": 1}, 3, 1)
    assert s.hours == pytest.approx(33 + 45 + 28 * 24 / 60)   # Steam play time beats HLTB
    assert s.by_month[1] == 2 and s.by_month[4] == 1
    from tracking.ui.completed_page import stats_text
    assert "4</b> finished · 3 games, 1 anime" in stats_text(s)


def test_presets_and_stamp(conn, qt):
    from tracking.ui import year_image

    opts = year_image.Options(cover_width=100, shape="Square", columns=2, background="#000000")
    year_image.save_preset(conn, "Twitter", opts)
    assert year_image.load_presets(conn)["Twitter"] == opts
    year_image.save_preset(conn, "Twitter", None)
    assert year_image.load_presets(conn) == {}

    entries = [completions.get(conn, completions.add(conn, f"G{n}", "2024-01-01")) for n in range(4)]
    plain = year_image.render(entries, 2024, opts)
    opts.stamp = "bottom-right"
    stamped = year_image.render(entries, 2024, opts)
    assert plain.size() == stamped.size()
    corner = lambda img: img.pixelColor(img.width() - 20, img.height() - 14)
    assert corner(plain) != corner(stamped)


def test_backlog_and_shortest_first(conn, qt):
    from tracking.ui import main_window

    _item(conn, "Long", hltb_hours=80.0)
    _item(conn, "Short", hltb_hours=5.0)
    _item(conn, "Unknown length")
    done = _item(conn, "Finished", hltb_hours=1.0, completed=1)
    _item(conn, "Gone", hltb_hours=2.0, missing=1)
    library.add_wanted(conn, "game", "Wanted", 2025)
    conn.commit()
    w = main_window.MainWindow()
    w.nav = (main_window.BACKLOG, None)
    w.sort.setCurrentIndex(w.sort.findData("short"))
    w.reload()
    assert [i.title for i in w.model.items] == ["Short", "Long", "Unknown length"]
    assert done not in {i.id for i in w.model.items}


def test_duplicates_and_health(conn):
    a = _item(conn, "Incredible Hulk, The", platform="PS2")
    b = _item(conn, "The Incredible Hulk", platform="PC")
    _item(conn, "Okami")
    _item(conn, "Shiki", category="anime", platform=None)
    library.add_wanted(conn, "game", "The Incredible Hulk")       # wish list: not a duplicate
    items = db.list_items(conn)
    groups = library.find_duplicates(items)
    assert [{i.id for i in g} for g in groups] == [{a, b}]
    checks = {c.key: c for c in library.health(items)}
    assert set(checks["duplicates"].ids) == {a, b}
    assert len(checks["no_cover"].ids) == 4 and checks["no_cover"].fetch
    assert checks["no_platform"].ids == []


def test_space_toggles_completed_and_focus_view(conn, qt):
    from PySide6.QtCore import QItemSelectionModel

    from tracking.ui import main_window

    a = _item(conn, "Okami")
    b = _item(conn, "Hades", platform="PC")
    conn.commit()
    w = main_window.MainWindow()
    w.show_focus("Picked", [a])
    assert [i.id for i in w.model.items] == [a] and w.heading.text() == "Picked"
    w.grid.selectionModel().select(w.model.index(0), QItemSelectionModel.Select)
    w._toggle_selected()
    with db.session() as c:
        assert db.get_item(c, a).completed and not db.get_item(c, b).completed
        assert len(completions.list_year(c)) == 1
    w.grid.selectionModel().select(w.model.index(0), QItemSelectionModel.Select)
    w._toggle_selected()
    with db.session() as c:
        assert not db.get_item(c, a).completed


def test_export_can_leave_out_entries_without_cover(conn, qt, tmp_path, monkeypatch):
    from tracking import paths
    from tracking.ui import year_image

    a = completions.add(conn, "Has cover", "2024-01-01")
    completions.add(conn, "No cover", "2024-02-01")
    (paths.posters_dir() / "done-x.jpg").write_bytes(b"x")
    conn.execute("UPDATE completions SET poster='done-x.jpg' WHERE id=?", (a,))
    entries = completions.list_year(conn, 2024)
    opts = year_image.Options(cover_width=100, shape="Square", columns=1)
    assert [c.title for c in year_image.shown(entries, opts)] == ["Has cover", "No cover"]
    opts.skip_no_cover = True
    assert [c.title for c in year_image.shown(entries, opts)] == ["Has cover"]
    assert year_image.render(entries, 2024, opts).height() == 100   # one row, not two


def test_big_covers_are_decoded_at_display_size(tmp_path, qt):
    from PySide6.QtGui import QColor, QImage

    from tracking.ui.grid import load_scaled

    big = QImage(2400, 3600, QImage.Format_RGB32)
    big.fill(QColor("#336699"))
    path = str(tmp_path / "big.jpg")
    assert big.save(path)
    pm = load_scaled(path, 170, 240)
    # Decoded just big enough to fill a 2x card (340 x 480), not at 2400 x 3600.
    assert pm is not None and pm.width() == 340 and 480 <= pm.height() < 600
    assert load_scaled(str(tmp_path / "missing.jpg"), 170, 240) is None


def test_export_sections_and_removing_coverless_entries(conn, qt, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from tracking import paths
    from tracking.ui import year_image
    from tracking.ui.completed_page import CompletedPage

    g = completions.add(conn, "Okami", "2024-01-01")
    completions.add(conn, "Frieren", "2024-02-01", "anime")
    completions.add(conn, "Heat", "2024-03-01", "movie")
    (paths.posters_dir() / "done-x.jpg").write_bytes(b"x")
    conn.execute("UPDATE completions SET poster='done-x.jpg' WHERE id=?", (g,))
    conn.commit()
    entries = completions.list_year(conn, 2024)
    opts = year_image.Options(sections=["game", "anime"])
    assert [c.title for c in year_image.shown(entries, opts)] == ["Okami", "Frieren"]

    dlg = year_image.ExportYearDialog(2024, entries)
    assert set(dlg.sections) == {"game", "anime", "movie"}
    dlg.sections["movie"].setChecked(False)
    assert sorted(dlg._read().sections) == ["anime", "game"]
    dlg.sections["movie"].setChecked(True)
    assert dlg._read().sections == []                                   # all ticked = everything

    page = CompletedPage()
    page.show_year(2024)
    page.hide_no_cover.setChecked(True)
    assert page.list.count() == 1
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    page.remove_no_cover()
    assert [c.title for c in completions.list_year(conn, 2024)] == ["Okami"]
