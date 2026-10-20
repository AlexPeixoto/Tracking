"""The scanner's view of the disk: the real one, or a saved snapshot of it.

A real scan records what it sees (every folder listing with sizes and dates, and what
disc headers said) as the location's snapshot. "Re-process from snapshot" then runs the
very same scanner over that record, so new detection rules apply without the drive
being plugged in.

The scanner never calls os.* directly for listings / sizes / disc headers; it asks
``current()``, which is the real disk unless a snapshot is being replayed.
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass


@dataclass(frozen=True)
class Stat:
    st_size: int
    st_mtime: float


class Entry:
    """What os.scandir gives, as far as the scanner uses it."""

    __slots__ = ("_dir", "_stat", "name", "path")

    def __init__(self, name: str, path: str, is_dir: bool, stat: Stat | None):
        self.name, self.path, self._dir, self._stat = name, path, is_dir, stat

    def is_dir(self, follow_symlinks: bool = True) -> bool:
        return self._dir

    def is_file(self, follow_symlinks: bool = True) -> bool:
        return not self._dir

    def stat(self) -> Stat:
        return self._stat or Stat(0, 0.0)


class RealFS:
    """The disk itself."""

    def scandir(self, path: str) -> list[Entry]:
        out = []
        with os.scandir(path) as it:
            for e in it:
                try:
                    d = e.is_dir(follow_symlinks=False)
                    st = None if d else e.stat()
                except OSError:
                    continue
                out.append(Entry(e.name, e.path, d,
                                 None if d else Stat(st.st_size, st.st_mtime)))
        return out

    def walk(self, top: str) -> Iterator[tuple[str, list[str], list[str]]]:
        """Like os.walk (top-down; prune by editing ``dirs``), errors skipped."""
        try:
            entries = self.scandir(top)
        except OSError:
            return
        dirs = [e.name for e in entries if e.is_dir()]
        files = [e.name for e in entries if not e.is_dir()]
        yield top, dirs, files
        for d in dirs:
            yield from self.walk(os.path.join(top, d))

    def stat(self, path: str) -> Stat:
        st = os.stat(path)
        return Stat(st.st_size, st.st_mtime)

    def getsize(self, path: str) -> int:
        try:
            return os.path.getsize(path)
        except OSError:
            return 0

    def exists(self, path: str) -> bool:
        return os.path.exists(path)

    def sniff(self, path: str) -> str | None:
        from .classify import sniff_disc_real
        return sniff_disc_real(path)

    def disc_info(self, path: str):
        from . import discid
        return discid.identify_real(path)


class RecordingFS(RealFS):
    """The disk, noting everything under ``root`` it was asked about (for the snapshot)."""

    def __init__(self, root: str):
        self.root = root
        self.dirs: dict[str, list[list]] = {}     # rel folder -> [[name, is_dir, size, mtime]]
        self.probes: dict[str, dict] = {}         # rel file -> {"sniff": ..., "disc": [...]}

    def _rel(self, path: str) -> str | None:
        try:
            rel = os.path.relpath(path, self.root)
        except ValueError:  # another drive
            return None
        if rel.startswith(".."):
            return None
        return "" if rel == "." else rel.replace("\\", "/")

    def scandir(self, path: str) -> list[Entry]:
        entries = super().scandir(path)
        rel = self._rel(path)
        if rel is not None:
            self.dirs[rel] = [[e.name, e.is_dir(), e.stat().st_size, e.stat().st_mtime] for e in entries]
        return entries

    def sniff(self, path: str) -> str | None:
        result = super().sniff(path)
        rel = self._rel(path)
        if rel is not None:
            self.probes.setdefault(rel, {})["sniff"] = result
        return result

    def disc_info(self, path: str):
        info = super().disc_info(path)
        rel = self._rel(path)
        if rel is not None:
            self.probes.setdefault(rel, {})["disc"] = (
                [info.platform, info.serial, info.title] if info else None)
        return info


class SnapshotFS(RealFS):
    """A recorded location, replayed: listings and disc headers come from the record.
    Paths outside the location (other drives) still go to the real disk."""

    def __init__(self, root: str, dirs: dict[str, list[list]], probes: dict[str, dict]):
        self.root, self.dirs, self.probes = root, dirs, probes

    def _rel(self, path: str) -> str | None:
        rel = os.path.relpath(path, self.root)
        if rel.startswith(".."):
            return None
        return "" if rel == "." else rel.replace("\\", "/")

    def scandir(self, path: str) -> list[Entry]:
        rel = self._rel(path)
        if rel is None:
            return super().scandir(path)
        if rel not in self.dirs:
            raise FileNotFoundError(path)
        return [Entry(n, os.path.join(path, n), bool(d), None if d else Stat(s, m))
                for n, d, s, m in self.dirs[rel]]

    def _file(self, path: str):
        rel = self._rel(path)
        if rel is None:
            return None
        folder, _, name = rel.rpartition("/")
        return next((e for e in self.dirs.get(folder, []) if e[0] == name), None)

    def stat(self, path: str) -> Stat:
        if self._rel(path) is None:
            return super().stat(path)
        e = self._file(path)
        if e is None:
            raise FileNotFoundError(path)
        return Stat(e[2], e[3])

    def getsize(self, path: str) -> int:
        if self._rel(path) is None:
            return super().getsize(path)
        e = self._file(path)
        return e[2] if e else 0

    def exists(self, path: str) -> bool:
        rel = self._rel(path)
        if rel is None:
            return super().exists(path)
        return rel in self.dirs or self._file(path) is not None

    def sniff(self, path: str) -> str | None:
        rel = self._rel(path)
        return super().sniff(path) if rel is None else (self.probes.get(rel) or {}).get("sniff")

    def disc_info(self, path: str):
        rel = self._rel(path)
        if rel is None:
            return super().disc_info(path)
        found = (self.probes.get(rel) or {}).get("disc")
        if not found:
            return None
        from .discid import DiscInfo
        return DiscInfo(*found)


_local = threading.local()


def current() -> RealFS:
    return getattr(_local, "fs", None) or _REAL


@contextmanager
def using(fs: RealFS):
    """Scan through ``fs`` (on this thread) for the duration of the block."""
    prev = getattr(_local, "fs", None)
    _local.fs = fs
    try:
        yield fs
    finally:
        _local.fs = prev


_REAL = RealFS()


# --------------------------------------------------------------------------- storage

def save(conn, root_id: int, rec: RecordingFS, taken_at: str) -> int:
    """Replace the location's snapshot with what a scan just saw. Returns files recorded."""
    conn.execute("DELETE FROM snapshot_dirs WHERE root_id=?", (root_id,))
    conn.executemany("INSERT INTO snapshot_dirs(root_id, rel, entries) VALUES(?,?,?)",
                     [(root_id, rel, json.dumps(entries, separators=(",", ":")))
                      for rel, entries in rec.dirs.items()])
    files = sum(1 for entries in rec.dirs.values() for e in entries if not e[1])
    conn.execute("INSERT INTO snapshots(root_id, taken_at, files, probes) VALUES(?,?,?,?) "
                 "ON CONFLICT(root_id) DO UPDATE SET taken_at=excluded.taken_at, "
                 "files=excluded.files, probes=excluded.probes",
                 (root_id, taken_at, files, json.dumps(rec.probes, separators=(",", ":"))))
    return files


def load(conn, root_id: int, root_abs: str) -> SnapshotFS | None:
    meta = conn.execute("SELECT probes FROM snapshots WHERE root_id=?", (root_id,)).fetchone()
    if not meta:
        return None
    dirs = {r["rel"]: json.loads(r["entries"]) for r in conn.execute(
        "SELECT rel, entries FROM snapshot_dirs WHERE root_id=?", (root_id,))}
    return SnapshotFS(root_abs, dirs, json.loads(meta["probes"] or "{}"))


def info(conn, root_id: int) -> tuple[str, int] | None:
    """(taken at, files) of a location's snapshot, if it has one."""
    r = conn.execute("SELECT taken_at, files FROM snapshots WHERE root_id=?", (root_id,)).fetchone()
    return (r["taken_at"], r["files"]) if r else None
