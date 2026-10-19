"""Back up the library to a single .zip (safe while the app is running).

The zip holds library.sqlite3 (a consistent snapshot via SQLite's backup API)
and the posters folder. To restore or move to another PC: close Tracking and
unzip into its data folder (Settings > General shows where that is), or into
a "data" folder next to Tracking.exe for portable mode.
"""

from __future__ import annotations

import sqlite3
import tempfile
import zipfile
from pathlib import Path

from . import paths


def backup(zip_path: Path) -> int:
    """Write the backup; returns the number of files in it."""
    with tempfile.TemporaryDirectory() as tmp:
        snapshot = Path(tmp) / "library.sqlite3"
        src = sqlite3.connect(paths.db_path())
        dst = sqlite3.connect(snapshot)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        count = 1
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(snapshot, "library.sqlite3")
            for f in sorted(paths.posters_dir().iterdir()):
                if f.is_file():
                    # Images are already compressed; storing them is faster.
                    z.write(f, f"posters/{f.name}", compress_type=zipfile.ZIP_STORED)
                    count += 1
    return count
