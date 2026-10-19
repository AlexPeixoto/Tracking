"""Logging to <data dir>/logs/tracking.log (rotating) and stderr.

Levels: DEBUG explains every scanner decision and provider match, INFO gives
scan / fetch summaries, WARNING only problems. Set it in Settings > General.
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from . import paths

LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR"]
FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"

_file_handler: RotatingFileHandler | None = None


def log_dir() -> Path:
    d = paths.data_dir() / "logs"
    d.mkdir(exist_ok=True)
    return d


def log_file() -> Path:
    return log_dir() / "tracking.log"


def setup(level: str = "INFO") -> None:
    global _file_handler
    root = logging.getLogger("tracking")
    if _file_handler is None:
        _file_handler = RotatingFileHandler(log_file(), maxBytes=2_000_000, backupCount=5,
                                            encoding="utf-8")
        _file_handler.setFormatter(logging.Formatter(FORMAT))
        console = logging.StreamHandler()
        console.setFormatter(logging.Formatter(FORMAT))
        root.addHandler(_file_handler)
        root.addHandler(console)
        root.propagate = False
    set_level(level)


def set_level(level: str) -> None:
    logging.getLogger("tracking").setLevel(getattr(logging, level.upper(), logging.INFO))


def tail(lines: int = 2000) -> list[str]:
    try:
        with open(log_file(), encoding="utf-8", errors="replace") as f:
            return f.readlines()[-lines:]
    except OSError:
        return []
