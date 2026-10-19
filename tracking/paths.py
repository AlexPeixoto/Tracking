"""Where the app keeps its database, caches and downloaded artwork.

Order:
  1. TRACKING_DATA_DIR environment variable
  2. Portable mode: a "data" folder next to Tracking.exe (or next to the
     project when running from source) - create it and the library lives there
  3. %LOCALAPPDATA%\\Tracking (Windows) / ~/.local/share/tracking
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def app_dir() -> Path:
    """Folder of Tracking.exe, or the project root when running from source."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def portable_dir() -> Path:
    return app_dir() / "data"


def data_dir() -> Path:
    override = os.environ.get("TRACKING_DATA_DIR")
    if override:
        base = Path(override)
    elif portable_dir().is_dir():
        base = portable_dir()
    elif os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "Tracking"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "tracking"
    base.mkdir(parents=True, exist_ok=True)
    return base


def db_path() -> Path:
    return data_dir() / "library.sqlite3"


def posters_dir() -> Path:
    p = data_dir() / "posters"
    p.mkdir(exist_ok=True)
    return p


def cache_dir() -> Path:
    p = data_dir() / "cache"
    p.mkdir(exist_ok=True)
    return p
