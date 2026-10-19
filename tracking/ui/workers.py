"""Background jobs: scanning, measuring durations, fetching metadata.

Jobs run one at a time on a QThreadPool thread (main_window queues them) and
report back through Qt signals, which are delivered on the GUI thread. A
metadata fetch fans out to several worker threads itself (see _fetch_parallel).
"""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from PySide6.QtCore import QObject, QRunnable, Signal

from .. import categories, db, enrich, scan, volumes
from ..providers import ProviderError

EPISODIC = [c.key for c in categories.CATEGORIES if c.episodic]

log = logging.getLogger(__name__)


class JobSignals(QObject):
    progress = Signal(str)
    item_changed = Signal(int)
    finished = Signal(str)        # summary for the status bar
    warning = Signal(str)


class Job(QRunnable):
    def __init__(self):
        super().__init__()
        self.signals = JobSignals()
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def run(self) -> None:
        conn = db.connect()
        try:
            summary = self.work(conn)
        except Exception as e:
            log.exception("%s failed", type(self).__name__)
            summary = f"Failed: {e}"
        finally:
            conn.close()
        self.signals.finished.emit("Cancelled" if self.cancelled() else summary)

    def work(self, conn) -> str:  # pragma: no cover - overridden
        raise NotImplementedError


def _mounts(conn) -> dict[str, str | None]:
    known = volumes.list_volumes()
    uids = [r["uid"] for r in conn.execute("SELECT uid FROM volumes")]
    return {uid: scan.mount_for(conn, uid, known) for uid in uids}


class ScanJob(Job):
    """Scan roots, then measure new video files and fetch metadata for new items."""

    def __init__(self, root_ids: list[int] | None = None, enrich_after: bool = True,
                 missing_completed: bool = False):
        super().__init__()
        self.root_ids = root_ids
        self.enrich_after = enrich_after
        self.missing_completed = missing_completed

    def work(self, conn) -> str:
        ids = self.root_ids or [r["id"] for r in conn.execute("SELECT id FROM roots")]
        found = new = missing = 0
        offline = []
        for rid in ids:
            if self.cancelled():
                break
            try:
                res = scan.scan_root(
                    conn, rid,
                    progress=lambda rel: self.signals.progress.emit(f"Scanning {rel}"),
                    cancelled=self.cancelled,
                    missing_completed=self.missing_completed,
                )
            except scan.VolumeOffline:
                row = conn.execute(
                    "SELECT v.label, r.rel_path FROM roots r JOIN volumes v ON v.uid=r.volume_uid "
                    "WHERE r.id=?", (rid,)).fetchone()
                offline.append(f"{row['label'] or 'drive'}:/{row['rel_path']}")
                continue
            except ProviderError as e:  # an online account: no key, private profile...
                self.signals.warning.emit(str(e))
                continue
            except requests.RequestException as e:
                self.signals.warning.emit(f"Couldn't reach Steam: {e}")
                continue
            found, new, missing = found + res.found, new + res.new, missing + res.missing
        self.signals.item_changed.emit(-1)
        if offline:
            self.signals.warning.emit("Offline, skipped: " + ", ".join(offline))
        summary = f"Scan done: {found} items ({new} new, {missing} missing"
        summary += ", marked completed)" if missing and self.missing_completed else ")"
        if self.enrich_after and not self.cancelled():
            summary += " - " + EnrichJob(only_pending=True).run_inline(conn, self)
        return summary


class EnrichJob(Job):
    """Measure durations and fetch metadata. ``item_ids=None`` means every item."""

    def __init__(self, item_ids: list[int] | None = None, only_pending: bool = True,
                 force: bool = False):
        super().__init__()
        self.item_ids = item_ids
        self.only_pending = only_pending
        self.force = force

    def work(self, conn) -> str:
        return self.run_inline(conn, self)

    def run_inline(self, conn, owner: Job) -> str:
        """Do the work, reporting through ``owner`` (lets ScanJob chain into this)."""
        mounts = _mounts(conn)
        if self.item_ids is not None:
            rows = [conn.execute("SELECT id, volume_uid, enriched_at FROM items WHERE id=?",
                                 (i,)).fetchone() for i in self.item_ids]
            rows = [r for r in rows if r]
        else:
            rows = conn.execute(
                "SELECT id, volume_uid, enriched_at FROM items WHERE missing=0 ORDER BY id"
            ).fetchall()

        # 1) durations: cheap, local, and shows up immediately on the cards
        need_dur = conn.execute(
            "SELECT DISTINCT item_id FROM media_files WHERE duration_sec IS NULL").fetchall()
        wanted = {r["id"] for r in rows}
        for r in need_dur:
            if owner.cancelled():
                break
            iid = r["item_id"]
            if iid not in wanted:
                continue
            uid = conn.execute("SELECT volume_uid, title FROM items WHERE id=?", (iid,)).fetchone()
            mount = mounts.get(uid["volume_uid"])
            if not mount:
                continue
            owner.signals.progress.emit(f"Measuring {uid['title']}")
            enrich.measure_durations(conn, iid, mount, owner.cancelled)
            owner.signals.item_changed.emit(iid)

        # 2) online metadata, several items at once
        # Series without an episode list count as pending too (older versions didn't
        # fetch one, or the service that has it was down).
        no_episodes = {r["id"] for r in conn.execute(
            "SELECT i.id FROM items i WHERE i.kind='series' AND i.category IN "
            f"({','.join('?' * len(EPISODIC))}) AND NOT EXISTS "
            "(SELECT 1 FROM episodes e WHERE e.item_id=i.id)", EPISODIC)}
        # So do items still without a cover: a service may have been down, or a newer
        # version may match the title better. Answers are cached, so retrying is cheap.
        no_cover = {r["id"] for r in conn.execute("SELECT id FROM items WHERE poster IS NULL")}
        todo = [r for r in rows if self.force or not self.only_pending or not r["enriched_at"]
                or r["id"] in no_episodes or r["id"] in no_cover]
        workers = metadata_workers(conn)
        done = self._fetch_parallel(todo, mounts, owner, workers)
        return f"metadata updated for {done} items"

    def _fetch_parallel(self, todo, mounts, owner: Job, workers: int) -> int:
        """Fetch metadata for ``todo`` with ``workers`` threads.

        Safe because every worker uses its own DB connection, and each service's
        RateLimiter is shared and thread-safe: more workers never hit a site
        faster than its limit; they overlap waiting on *different* sites
        (HLTB, Steam, AniList, cover downloads...).
        """
        lock = threading.Lock()
        state = {"started": 0, "done": 0}

        def one(row) -> None:
            if owner.cancelled():
                return
            with lock:
                state["started"] += 1
                n = state["started"]
            conn = db.connect()
            try:
                item = db.get_item(conn, row["id"])
                if not item:
                    return
                owner.signals.progress.emit(
                    f"Metadata {n}/{len(todo)}: {item.title}"
                    + (f"  ({workers} at a time)" if workers > 1 else ""))
                for w in enrich.enrich_item(conn, row["id"], mounts.get(row["volume_uid"]),
                                            force=self.force):
                    owner.signals.warning.emit(w)
            finally:
                conn.close()
            owner.signals.item_changed.emit(row["id"])
            with lock:
                state["done"] += 1

        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="metadata") as pool:
            futures = [pool.submit(one, r) for r in todo]
            for f in as_completed(futures):
                try:
                    f.result()
                except Exception as e:  # one bad item shouldn't stop the batch
                    log.exception("metadata worker failed")
                    owner.signals.warning.emit(f"Metadata failed for an item: {e}")
                if owner.cancelled():
                    pool.shutdown(wait=True, cancel_futures=True)
                    break
        return state["done"]


DEFAULT_WORKERS = 4
MAX_WORKERS = 8


def metadata_workers(conn) -> int:
    """How many items to fetch at once (Settings > General)."""
    try:
        n = int(db.get_setting(conn, "metadata_workers", str(DEFAULT_WORKERS)))
    except ValueError:
        n = DEFAULT_WORKERS
    return max(1, min(MAX_WORKERS, n))


class CompletionCoversJob(Job):
    """Covers for Completed entries that aren't in the library (imported / typed in)."""

    def __init__(self, ids: list[int] | None = None):
        super().__init__()
        self.ids = ids

    def work(self, conn) -> str:
        from .. import completions

        todo = self.ids if self.ids is not None else completions.needs_cover(conn)
        found = 0
        for n, cid in enumerate(todo, 1):
            if self.cancelled():
                break
            c = completions.get(conn, cid)
            if not c:
                continue
            self.signals.progress.emit(f"Cover {n}/{len(todo)}: {c.title}")
            try:
                if completions.fetch_cover(conn, cid):
                    found += 1
                    conn.commit()
                    self.signals.item_changed.emit(-1)
            except Exception as e:
                log.warning("cover for completed %r failed: %s", c.title, e)
        return f"Covers found for {found} of {len(todo)} completed entries"
