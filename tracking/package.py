"""Library package: a shareable, read-only copy of the library (docs/PACKAGE.md).

What the Android app imports, and what you can send to someone to browse your library:
titles, covers (made small), scores, episodes, completion, the Completed log, the Planning
board and play time. Nothing private: no paths, drives, folder snapshots, notes, settings
or keys. Not a backup (that's ``archive.py``).
"""

from __future__ import annotations

import json
import logging
import tempfile
import zipfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from . import __version__, categories, db, devices, paths

log = logging.getLogger(__name__)

FORMAT = "tracking-library-package"
VERSION = 1
MANIFEST = "manifest.json"
LIBRARY = "library.json"
COVER_SIZE = (360, 540)   # covers are scaled down to fit this (phones, not posters)
COVER_QUALITY = 75  # WebP: ~24 KB a cover, 40% smaller than JPEG at the same look
SUFFIX = ".trackinglib.zip"


def _source(item: db.Item) -> str:
    if item.volume_uid.startswith(devices.PREFIX):
        return "device"
    return item.source


def item_entry(item: db.Item, episodes: list[db.EpisodeRow], cover: str | None) -> dict:
    """One item as the package describes it (no paths, no notes)."""
    hltb = ({"main": item.hltb_hours, "extra": item.hltb_extra, "complete": item.hltb_complete}
            if any((item.hltb_hours, item.hltb_extra, item.hltb_complete)) else None)
    rating = ({"value": item.rating, "max": item.rating_max, "source": item.rating_source}
              if item.rating is not None else None)
    return {
        "id": item.id, "title": item.title, "category": item.category, "kind": item.kind,
        "season": item.season, "platform": item.platform, "year": item.year,
        "source": _source(item), "store": item.store,
        "completed": bool(item.completed), "completed_at": item.completed_at,
        "plan": item.plan_state, "plan_position": item.plan_position,
        "playtime_min": item.playtime_min, "last_played": item.last_played or item.last_launched,
        "hltb": hltb, "duration_min": round(item.duration_sec / 60, 1) if item.duration_sec else None,
        "episodes_owned": item.video_count, "last_episode": item.last_episode,
        "rating": rating,
        "scores": [{"source": s.source, "value": s.value, "max": s.max_value,
                    **({"url": s.url} if s.url else {})} for s in item.scores],
        "summary": item.summary, "url": item.external_url, "cover": cover,
        "episodes": [{"season": e.season, "number": e.number, "title": e.title, "aired": e.aired,
                      "score": e.score, "max": e.score_max, "owned": e.on_disk} for e in episodes],
    }


def _small_cover(src: Path, dest: Path) -> bool:
    """``src`` scaled to fit COVER_SIZE, as WebP. False when it can't be read."""
    from PySide6.QtCore import QSize, Qt
    from PySide6.QtGui import QImageReader

    reader = QImageReader(str(src))
    size = reader.size()
    if size.isValid() and (size.width() > COVER_SIZE[0] or size.height() > COVER_SIZE[1]):
        reader.setScaledSize(size.scaled(QSize(*COVER_SIZE), Qt.KeepAspectRatio))
    img = reader.read()
    return not img.isNull() and img.save(str(dest), "WEBP", COVER_QUALITY)


def export_package(dest: Path, name: str = "", covers: bool = True, wishlist: bool = True,
                   progress: Callable[[str], None] | None = None,
                   cancelled: Callable[[], bool] = lambda: False) -> dict:
    """Write a library package to ``dest``; returns its manifest."""
    with db.session() as conn:
        items = [i for i in db.list_items(conn) if not i.missing and (wishlist or i.source != db.WANTED)]
        episodes = {i.id: db.get_episodes(conn, i.id) for i in items if i.kind == "series"}
        done = conn.execute(
            "SELECT c.title, c.category, c.platform, c.completed_on, c.position, c.item_id, "
            "COALESCE(c.poster, i.poster) AS poster, c.id FROM completions c "
            "LEFT JOIN items i ON i.id = c.item_id ORDER BY c.completed_on, c.id").fetchall()
    kept = {i.id for i in items}
    posters = paths.posters_dir()
    written: dict[str, str] = {}  # poster file -> its path inside the zip

    with tempfile.TemporaryDirectory() as tmp, \
            zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        def cover(poster: str | None) -> str | None:
            if not covers or not poster:
                return None
            if poster not in written:
                src = posters / poster
                out = Path(tmp) / f"{len(written)}.webp"
                if not src.exists() or not _small_cover(src, out):
                    return None
                arc = f"covers/{len(written)}.webp"
                zf.write(out, arc, zipfile.ZIP_STORED)  # already compressed
                out.unlink()
                written[poster] = arc
            return written[poster]

        entries = []
        for n, item in enumerate(items):
            if cancelled():
                raise InterruptedError("cancelled")
            if progress and n % 50 == 0:
                progress(f"Packing {n}/{len(items)}: {item.title}")
            entries.append(item_entry(item, episodes.get(item.id, []), cover(item.poster)))
        completions = [{
            "title": r["title"], "category": r["category"], "platform": r["platform"],
            "date": r["completed_on"], "position": r["position"],
            "item": r["item_id"] if r["item_id"] in kept else None, "cover": cover(r["poster"]),
        } for r in done]
        library = {
            "categories": [{"key": c.key, "label": c.label, "singular": c.singular, "done": c.done,
                            "episodic": c.episodic, "platforms": c.platforms, "colors": list(c.colors)}
                           for c in categories.CATEGORIES],
            "items": entries,
            "completions": completions,
        }
        manifest = {
            "format": FORMAT, "version": VERSION, "name": name.strip() or "My library",
            "created": datetime.now(UTC).isoformat(timespec="seconds"),
            "app": f"Tracking {__version__}",
            "counts": {"items": len(entries), "completions": len(completions), "covers": len(written)},
        }
        zf.writestr(LIBRARY, json.dumps(library, ensure_ascii=False, separators=(",", ":")))
        zf.writestr(MANIFEST, json.dumps(manifest, ensure_ascii=False, indent=2))
    log.info("library package %s: %s", dest, manifest["counts"])
    return manifest


def read_package(path: Path) -> tuple[dict, dict]:
    """(manifest, library) of a package; ValueError when it isn't one this version reads."""
    try:
        with zipfile.ZipFile(path) as zf:
            manifest = json.loads(zf.read(MANIFEST))
            if manifest.get("format") != FORMAT:
                raise ValueError("not a Tracking library package")
            if int(manifest.get("version", 0)) > VERSION:
                raise ValueError("made by a newer Tracking")
            return manifest, json.loads(zf.read(LIBRARY))
    except (OSError, KeyError, zipfile.BadZipFile, json.JSONDecodeError) as e:
        raise ValueError(f"can't read {path.name}: {e}") from e
