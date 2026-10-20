"""The Planning board: columns, ordering, automatic moves, Done, Dropped."""

import os

import pytest

from tracking import completions, db, library


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("TRACKING_DATA_DIR", str(tmp_path / "data"))
    c = db.connect()
    yield c
    c.close()


def _item(conn, title, category="game", **cols):
    iid = conn.execute("INSERT INTO items(volume_uid, rel_path, is_dir, category, platform, title) "
                       "VALUES('v', ?, 1, ?, 'PC', ?)", (f"x/{title}", category, title)).lastrowid
    for k, v in cols.items():
        conn.execute(f"UPDATE items SET {k}=? WHERE id=?", (v, iid))
    return iid


def _state(conn, iid):
    i = db.get_item(conn, iid)
    return i.plan_state, i.plan_position


def test_up_next_keeps_your_order(conn):
    a, b, c = (_item(conn, t) for t in "ABC")
    library.set_plan(conn, [a, b], library.PLAN_NEXT)
    library.set_plan(conn, [c], library.PLAN_NEXT, position=0)     # dropped at the top
    order = [r[0] for r in conn.execute(
        "SELECT title FROM items WHERE plan_state='next' ORDER BY plan_position")]
    assert order == ["C", "A", "B"]


def test_playing_finishing_and_back(conn):
    a = _item(conn, "Hades")
    library.set_plan(conn, [a], library.PLAN_NEXT)
    library.started(conn, a)                                         # Play / an episode
    assert _state(conn, a) == ("playing", None)
    library.set_completed(conn, [a], True)                           # finished: off the board
    assert _state(conn, a) == (None, None)
    assert len(completions.list_year(conn)) == 1
    library.set_plan(conn, [a], library.PLAN_PLAYING)                # dragged back out of Done
    assert not db.get_item(conn, a).completed and completions.list_year(conn) == []
    library.started(conn, a)
    assert _state(conn, a)[0] == "playing"


def test_dropped_stays_dropped_and_leaves_backlog(conn, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6.QtWidgets")
    from tracking.ui import main_window

    a = _item(conn, "Meh")
    library.set_plan(conn, [a], library.PLAN_DROPPED)
    library.started(conn, a)                                         # launching doesn't undo it
    item = db.get_item(conn, a)
    assert item.plan_state == "dropped" and not main_window.in_backlog(item)


def test_board_columns_filters_and_drops(conn, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from tracking.ui.planning_page import PlanningPage, progress, remaining_hours

    g = _item(conn, "Elden Ring", hltb_hours=60.0, playtime_min=12 * 60)
    s = _item(conn, "Mushishi", category="anime", kind="series", duration_sec=26 * 24 * 60,
              last_episode=7)
    for e in range(1, 27):
        conn.execute("INSERT INTO media_files(item_id, rel_path, size_bytes, mtime, episode) "
                     "VALUES(?, ?, 1, 0, ?)", (s, f"x/Mushishi/{e:02d}.mkv", e))
    library.set_plan(conn, [g], library.PLAN_PLAYING)
    library.set_plan(conn, [s], library.PLAN_NEXT)
    conn.commit()
    gi, si = db.get_item(conn, g), db.get_item(conn, s)
    assert progress(gi) == "12.0h of ~60h" or progress(gi).endswith("of ~60h")
    assert progress(si) == "Ep 7 / 26"
    assert remaining_hours(gi) == 48 and round(remaining_hours(si), 1) == 7.6

    page = PlanningPage()
    page.refresh()
    assert page.lists["playing"].count() == 1 and page.lists["next"].count() == 1
    page.toggle_section("anime")                                       # only anime
    assert page.lists["playing"].count() == 0 and page.lists["next"].count() == 1
    page.toggle_section("game")                                        # anime + games
    assert page.lists["playing"].count() == 1
    assert PlanningPage().sections == {"anime", "game"}                # remembered
    page.toggle_section("")                                            # All
    assert page.sections == set() and page.chips[""].isChecked()
    page.drop([g], "done")                                            # dropped into Done
    assert page.lists["done"].count() == 1 and page.lists["playing"].count() == 0
    assert "~" in page.titles["next"].text()                           # column hours
