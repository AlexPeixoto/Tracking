"""The Completed log: marking items, year CSVs, editing, ordering, the year image."""

import os
from pathlib import Path

import pytest

from tracking import completions, db, library


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("TRACKING_DATA_DIR", str(tmp_path / "data"))
    c = db.connect()
    yield c
    c.close()


def _item(conn, title, category="game", platform="PS2"):
    return conn.execute(
        "INSERT INTO items(volume_uid, rel_path, is_dir, category, platform, title) "
        "VALUES('v', ?, 0, ?, ?, ?)", (f"x/{title}", category, platform, title)).lastrowid


def test_marking_completed_logs_today_and_unmarking_removes(conn):
    a = _item(conn, "Okami")
    library.set_completed(conn, [a], True)
    [c] = completions.list_year(conn, int(completions.today()[:4]))
    assert (c.title, c.item_id, c.completed_on, c.platform) == ("Okami", a, completions.today(), "PS2")
    library.set_completed(conn, [a], False)
    assert completions.list_year(conn) == []
    assert not db.get_item(conn, a).completed


def test_editing_the_date_moves_year_and_updates_item(conn):
    a = _item(conn, "Okami")
    library.set_completed(conn, [a], True)
    [c] = completions.list_year(conn)
    completions.update(conn, c.id, completed_on="2019-03-05")
    assert completions.years(conn) == {2019: 1}
    assert db.get_item(conn, a).completed_at == "2019-03-05"


def test_import_year_csv(tmp_path, conn):
    hulk = _item(conn, "The Incredible Hulk")
    f = tmp_path / "2023.csv"
    f.write_text("name,date\n"
                 "Incredible Hulk, The,05/02/2023\n"
                 "Elden Ring,\n"            # no date: the year from the file name
                 "Hades,31/12/2023\n"
                 "Broken,32/13/2023\n", encoding="utf-8")
    res = completions.import_csv(conn, str(f))
    assert (res.added, res.linked, len(res.errors), res.years) == (3, 1, 1, {2023})
    entries = {c.title: c for c in completions.list_year(conn, 2023)}
    assert entries["The Incredible Hulk"].item_id == hulk
    assert entries["The Incredible Hulk"].date_text() == "05/02/2023"
    assert entries["Elden Ring"].completed_on == "2023" and entries["Elden Ring"].date_text() == "2023"
    assert completions.import_csv(conn, str(f)).skipped == 3   # importing again adds nothing


def test_semicolon_csv_and_export_round_trip(tmp_path, conn):
    f = tmp_path / "games 2021.csv"
    f.write_text("Celeste;14/01/2021\nHollow Knight;02/06/2021\n", encoding="utf-8")
    assert completions.import_csv(conn, str(f)).added == 2
    out = tmp_path / "out.csv"
    assert completions.export_csv(conn, 2021, str(out)) == 2
    conn.execute("DELETE FROM completions")
    assert completions.import_csv(conn, str(out)).added == 2
    assert [c.title for c in completions.list_year(conn, 2021)] == ["Celeste", "Hollow Knight"]


def test_your_order_wins_over_dates(conn):
    ids = [completions.add(conn, t, d) for t, d in
           (("A", "2022-01-01"), ("B", "2022-02-01"), ("C", "2022-03-01"))]
    completions.set_order(conn, [ids[2], ids[0], ids[1]])
    assert [c.title for c in completions.list_year(conn, 2022)] == ["C", "A", "B"]
    completions.reset_order(conn, 2022)
    assert [c.title for c in completions.list_year(conn, 2022)] == ["A", "B", "C"]


def test_existing_completed_items_seed_the_log(tmp_path, monkeypatch):
    monkeypatch.setenv("TRACKING_DATA_DIR", str(tmp_path / "data"))
    path = tmp_path / "data" / "library.sqlite3"
    with db.session() as c:
        c.execute("DELETE FROM settings WHERE key='completions_seeded'")
        c.execute("INSERT INTO items(volume_uid, rel_path, is_dir, category, title, completed, "
                  "completed_at) VALUES('v', 'a', 0, 'movie', 'Heat', 1, '2020-07-01 10:00:00')")
    db._ready.discard(str(path.resolve()))
    with db.session() as c:
        [e] = completions.list_year(c)
        assert (e.title, e.category, e.completed_on) == ("Heat", "movie", "2020-07-01")


def test_parse_date():
    assert completions.parse_date("31/12/2023") == "2023-12-31"
    assert completions.parse_date("2023-12-31") == "2023-12-31"
    assert completions.parse_date("", 2020) == "2020"
    with pytest.raises(ValueError):
        completions.parse_date("12/31/2023")


def test_year_image_layout_and_render(tmp_path, monkeypatch, conn):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from tracking.ui import year_image

    for n in range(10):
        completions.add(conn, f"Game {n}", "2024-01-01")
    entries = completions.list_year(conn, 2024)
    opts = year_image.Options(cover_width=100, shape="Square", gap=10, margin=20)
    w, h, cols, rows, *_ = year_image.layout(len(entries), opts)
    assert (cols, rows) == (4, 3)                     # nearly square
    assert (w, h) == (20 * 2 + 4 * 100 + 3 * 10, 20 * 2 + 3 * 100 + 2 * 10)
    img = year_image.render(entries, 2024, opts)
    assert (img.width(), img.height()) == (w, h)
    assert img.pixelColor(0, 0).alpha() == 0          # transparent background
    opts.heading, opts.labels, opts.columns = True, "both", 5
    w2, h2, cols2, rows2, label_h, heading_h = year_image.layout(len(entries), opts)
    assert (cols2, rows2) == (5, 2) and label_h > 0 and heading_h > 0


def test_completed_page_shows_a_year(tmp_path, monkeypatch, conn):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from tracking.ui.completed_page import CompletedPage

    completions.add(conn, "Celeste", "2021-01-14")
    completions.add(conn, "Hades", "2022-05-01")
    conn.commit()
    page = CompletedPage()
    page.show_year(2021)
    assert page.list.count() == 1 and "Celeste" in page.list.item(0).text()
    page.show_year(None)
    assert page.list.count() == 2
    assert Path(tmp_path).exists()
