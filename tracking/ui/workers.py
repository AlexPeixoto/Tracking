"""Background jobs: scanning, measuring durations, fetching metadata.

Each job runs on a QThreadPool thread with its own SQLite connection and
reports back through Qt signals (which are delivered on the GUI thread).
"""

from __future__ import annotations

import logging
import threading

from PySide6.QtCore import QObject, QRunnable, Signal

from .. import db, enrich, scanner, volumes

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
    return {uid: scanner.mount_for(conn, uid, known) for uid in uids}


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
                res = scanner.scan_root(
                    conn, rid,
                    progress=lambda rel: self.signals.progress.emit(f"Scanning {rel}"),
                    cancelled=self.cancelled,
                    missing_completed=self.missing_completed,
                )
            except scanner.VolumeOffline:
                row = conn.execute(
                    "SELECT v.label, r.rel_path FROM roots r JOIN volumes v ON v.uid=r.volume_uid "
                    "WHERE r.id=?", (rid,)).fetchone()
                offline.append(f"{row['label'] or 'drive'}:/{row['rel_path']}")
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

        # 2) online metadata
        todo = [r for r in rows if self.force or not self.only_pending or not r["enriched_at"]]
        done = 0
        for n, r in enumerate(todo, 1):
            if owner.cancelled():
                break
            item = db.get_item(conn, r["id"])
            owner.signals.progress.emit(f"Metadata {n}/{len(todo)}: {item.title}")
            for w in enrich.enrich_item(conn, r["id"], mounts.get(r["volume_uid"]), force=self.force):
                owner.signals.warning.emit(w)
            owner.signals.item_changed.emit(r["id"])
            done += 1
        return f"metadata updated for {done} items"
