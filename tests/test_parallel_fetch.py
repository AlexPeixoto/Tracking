"""Metadata fetching runs several items at once, and still stops on cancel."""

import threading
import time

import pytest

pytest.importorskip("PySide6")

from tracking import db, enrich
from tracking.providers.base import RateLimiter
from tracking.ui import workers


@pytest.fixture
def library(tmp_path, monkeypatch):
    monkeypatch.setenv("TRACKING_DATA_DIR", str(tmp_path))
    with db.session() as conn:
        for n in range(12):
            conn.execute("INSERT INTO items(volume_uid, rel_path, is_dir, category, title) "
                         "VALUES('v', ?, 1, 'movie', ?)", (f"m{n}", f"Movie {n}"))
    return tmp_path


def fake_enrich(seen, delay=0.2):
    lock = threading.Lock()
    state = {"now": 0, "peak": 0}

    def enrich_item(conn, item_id, mount, force=False):
        with lock:
            state["now"] += 1
            state["peak"] = max(state["peak"], state["now"])
        time.sleep(delay)
        with lock:
            state["now"] -= 1
            seen.append(item_id)
        return []
    return enrich_item, state


def run(job):
    conn = db.connect()
    try:
        return job.run_inline(conn, job)
    finally:
        conn.close()


def test_items_are_fetched_in_parallel(library, monkeypatch):
    seen: list[int] = []
    fn, state = fake_enrich(seen)
    monkeypatch.setattr(enrich, "enrich_item", fn)
    with db.session() as conn:
        db.set_setting(conn, "metadata_workers", "4")
    t = time.time()
    summary = run(workers.EnrichJob(only_pending=True))
    took = time.time() - t
    assert summary == "metadata updated for 12 items"
    assert sorted(seen) == list(range(1, 13))       # every item, once
    assert state["peak"] == 4                         # really 4 at a time
    assert took < 12 * 0.2 / 2                        # well under the one-by-one time


def test_one_worker_means_one_by_one(library, monkeypatch):
    seen: list[int] = []
    fn, state = fake_enrich(seen, delay=0.01)
    monkeypatch.setattr(enrich, "enrich_item", fn)
    with db.session() as conn:
        db.set_setting(conn, "metadata_workers", "1")
    run(workers.EnrichJob(only_pending=True))
    assert state["peak"] == 1 and len(seen) == 12


def test_cancel_stops_starting_new_items(library, monkeypatch):
    seen: list[int] = []
    fn, _ = fake_enrich(seen, delay=0.2)
    monkeypatch.setattr(enrich, "enrich_item", fn)
    job = workers.EnrichJob(only_pending=True)
    threading.Timer(0.3, job.cancel).start()
    run(job)
    assert 0 < len(seen) < 12


def test_rate_limiter_is_shared_across_threads():
    """More workers must not hit one site faster than its limit."""
    limiter = RateLimiter(0.05)
    stamps: list[float] = []

    def call():
        limiter.wait()
        stamps.append(time.monotonic())

    threads = [threading.Thread(target=call) for _ in range(6)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    gaps = [b - a for a, b in zip(sorted(stamps), sorted(stamps)[1:], strict=False)]
    assert min(gaps) >= 0.045


def test_online_drive_does_not_block_parallel_writes(tmp_path, monkeypatch):
    """Resolving mounts writes last_mount; it must be committed before workers write."""
    import sqlite3

    from tracking import db
    from tracking.ui import workers

    monkeypatch.setenv("TRACKING_DATA_DIR", str(tmp_path))
    conn = db.connect()
    conn.execute("INSERT INTO volumes(uid, serial, label, last_mount) VALUES('v1', 'S', 'D', 'X:\\')")
    conn.commit()
    monkeypatch.setattr(workers.volumes, "list_volumes", lambda: [])
    monkeypatch.setattr(workers.scan, "mount_for",
                        lambda c, uid, known: c.execute(
                            "UPDATE volumes SET last_mount='Y:\\' WHERE uid=?", (uid,)) and "Y:\\")
    workers._mounts(conn)
    other = sqlite3.connect(tmp_path / "library.sqlite3", timeout=0.2)
    other.execute("UPDATE volumes SET label='E' WHERE uid='v1'")   # would raise "locked"
    other.commit()
