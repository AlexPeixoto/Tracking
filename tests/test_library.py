from tracking import categories, db, library, paths, platforms, providers, scores
from tracking.scan import classify


def make(conn, title="Heat", category="movie", poster=None, root_id=None):
    iid = conn.execute(
        "INSERT INTO items(root_id, volume_uid, rel_path, is_dir, category, title, poster) "
        "VALUES(?, 'v', ?, 1, ?, ?, ?)", (root_id, title, category, title, poster)).lastrowid
    if poster:
        (paths.posters_dir() / poster).write_bytes(b"x")
    return iid


def test_edit_item(tmp_path, monkeypatch):
    monkeypatch.setenv("TRACKING_DATA_DIR", str(tmp_path))
    with db.session() as conn:
        iid = make(conn)
        item = db.get_item(conn, iid)
        # Same title, only notes: no new lookup needed, no title override stored.
        assert not library.edit_item(conn, item, title="Heat ", category="movie", platform="PS2",
                                     year=None, notes="  great  ")
        item = db.get_item(conn, iid)
        assert (item.notes, item.platform, item.title) == ("great", None, "Heat")
        assert conn.execute("SELECT user_title FROM items").fetchone()[0] is None
        # New title: stored as an override and flagged for a new lookup.
        assert library.edit_item(conn, item, title="Heat (1995)", category="movie", platform=None,
                                 year=1995, notes="great")
        assert db.get_item(conn, iid).title == "Heat (1995)"


def test_remove_items_and_locations_delete_covers(tmp_path, monkeypatch):
    monkeypatch.setenv("TRACKING_DATA_DIR", str(tmp_path))
    with db.session() as conn:
        conn.execute("INSERT INTO volumes(uid) VALUES('v')")
        rid = conn.execute("INSERT INTO roots(volume_uid, rel_path) VALUES('v', '')").lastrowid
        a = make(conn, "A", poster="a.jpg", root_id=rid)
        make(conn, "B", poster="b.jpg", root_id=rid)
        library.remove_items(conn, [a])
        assert not (paths.posters_dir() / "a.jpg").exists()
        library.remove_locations(conn, [rid], keep_items=True)
        assert [i.title for i in db.list_items(conn)] == ["B"]  # kept, no location
        assert (paths.posters_dir() / "b.jpg").exists()


def test_move_items_drops_platform_outside_games(tmp_path, monkeypatch):
    monkeypatch.setenv("TRACKING_DATA_DIR", str(tmp_path))
    with db.session() as conn:
        iid = make(conn)
        library.move_items(conn, [iid], "anime", "PS2")
        assert (db.get_item(conn, iid).category, db.get_item(conn, iid).platform) == ("anime", None)
        library.move_items(conn, [iid], "game", "PS2")
        assert db.get_item(conn, iid).platform == "PS2"


# --- the tables agree with each other ----------------------------------------

def test_rom_extensions_use_known_platforms():
    assert set(classify.ROM_EXT.values()) <= set(platforms.NAMES)
    assert set(platforms.FOLDER_ALIASES.values()) <= set(platforms.NAMES)
    assert set(platforms.TOKEN_ALIASES) <= set(platforms.FOLDER_ALIASES)


def test_providers_cover_known_categories_and_scores():
    for p in providers.REGISTRY:
        assert p.categories <= set(categories.KEYS), p.name
        assert set(p.required) <= {s.key for s in p.settings}, p.name
    assert set(scores.RATING_PREF) <= set(categories.KEYS)
    # Every category the classifier can pick has at least one service, except Unknown.
    for c in categories.KEYS:
        assert providers.for_category(c) or c == "unknown", c
