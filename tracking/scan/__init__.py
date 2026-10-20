"""Scanning locations into the library.

  walk.py      folders / files -> FoundItems (container rules, game and video items)
  videos.py    episode / season / movie grouping
  classify.py  category and platform decisions (folder names, extensions, disc headers)
  merge.py     one item per series season, game (+ add-ons) and movie (+ copies)
  store.py     saving to the database, missing items, re-linking moved items
  fs.py        the disk, or a location's saved snapshot (re-process without the drive)
"""

from .merge import collect
from .model import GROUP_SEP, FileEntry, FoundItem, ScanResult, VolumeOffline, fs_path
from .store import NoSnapshot, add_root, mount_for, scan_root
from .walk import root_hints, walk

__all__ = ["GROUP_SEP", "FileEntry", "FoundItem", "NoSnapshot", "ScanResult", "VolumeOffline", "add_root",
           "collect", "fs_path", "mount_for", "root_hints", "scan_root", "walk"]
