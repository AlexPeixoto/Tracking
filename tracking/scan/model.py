"""Types shared by the scanner modules."""

from __future__ import annotations

from dataclasses import dataclass, field

# Rel paths with "::" mark one group inside a folder ("Show::s2"); ':' can't
# appear in real file names on Windows, so these never clash with real paths.
GROUP_SEP = "::"


class VolumeOffline(Exception):
    """The location's drive isn't connected."""


@dataclass
class FileEntry:
    rel_path: str  # relative to the volume
    size: int
    mtime: float
    season: int | None = None   # episodic video: parsed from the name
    episode: int | None = None


@dataclass
class FoundItem:
    """One library item as the scanner sees it, before it's saved."""
    rel_path: str
    is_dir: bool
    category: str
    platform: str | None
    title: str
    year: int | None
    videos: list[FileEntry] = field(default_factory=list)
    file_count: int = 0
    size_bytes: int = 0
    kind: str | None = None        # 'series' | 'movie' for video items, 'addon' for DLC/updates
    season: int | None = None
    reason: str = ""
    series_key: str | None = None  # merge key for episodes found in several places


@dataclass
class ScanResult:
    found: int = 0
    new: int = 0
    missing: int = 0


def fs_path(rel_path: str) -> str:
    """The on-disk part of an item path (drops the "::group" suffix)."""
    return rel_path.split(GROUP_SEP, 1)[0]


def vol_rel(base_rel: str, name: str) -> str:
    return f"{base_rel}/{name}" if base_rel else name
