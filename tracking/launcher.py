"""Play: start a game from the library.

  * PC (installed): its .exe, in its own folder, with your extra arguments
  * PC (installer): the setup
  * consoles: the emulator set for the platform in Settings › Emulators, with an
    argument template ("{file}" is the ROM / disc). Zipped images are unpacked to a
    cache first for emulators that can't read zips (the last few are kept).
  * Steam: steam://rungameid/<appid>

Every launch is recorded (last_launched, launch_count) for the "Recently played" sort.
"""

from __future__ import annotations

import logging
import os
import shlex
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from . import db, paths, platforms, scan, volumes

log = logging.getLogger(__name__)

# Settings › Emulators: "emulator.<platform>" (path), "emulator_args.<platform>",
# "emulator_unzip.<platform>" ("1"/"0").
DEFAULT_ARGS = {
    "PS1": '-batch "{file}"',          # DuckStation
    "PS2": '-batch "{file}"',          # PCSX2 (Qt)
    "PS3": '--no-gui "{file}"',        # RPCS3
    "PSP": '"{file}"',                 # PPSSPP
    "GameCube": '-b -e "{file}"',      # Dolphin
    "Wii": '-b -e "{file}"',
    "Wii U": '-g "{file}"',            # Cemu
    "Switch": '"{file}"',              # Ryujinx and friends
}
SUGGESTED = {
    "PS1": "DuckStation", "PS2": "PCSX2", "PS3": "RPCS3", "PSP": "PPSSPP", "GameCube": "Dolphin",
    "Wii": "Dolphin", "Wii U": "Cemu", "Switch": "Ryujinx", "Dreamcast": "Flycast",
    "Saturn": "Mednafen / Ymir", "3DS": "Azahar", "DS": "melonDS", "N64": "RetroArch",
    "SNES": "RetroArch", "NES": "RetroArch", "GBA": "mGBA", "Xbox": "xemu", "Xbox 360": "Xenia",
    "PS Vita": "Vita3K",
}
KEEP_EXTRACTED = 2


class LaunchError(Exception):
    """Why a game can't be started (shown to the user)."""


def emulator_key(platform: str) -> str:
    return f"emulator.{platform}"


def emulator_args_key(platform: str) -> str:
    return f"emulator_args.{platform}"


def emulator_unzip_key(platform: str) -> str:
    return f"emulator_unzip.{platform}"


def console_platforms() -> list[str]:
    return [p.name for p in platforms.PLATFORMS if p.name != "PC"]


@dataclass
class Launch:
    """What Play will do: a command (or a URL), where, and what to say about it."""
    item_id: int
    what: str                               # "Hades", "Okami in PCSX2"...
    command: list[str] = field(default_factory=list)
    cwd: str | None = None
    url: str | None = None
    unzip: str | None = None                # a .zip to unpack first; {file} becomes the image
    episode: tuple | None = None            # videos: (season, episode) to remember as watched


def target_path(item: db.Item, mount: str | None) -> str | None:
    """The file Play uses, on disk, or None if the drive isn't there."""
    p = item.launch_path
    if not p:
        return None
    if os.path.isabs(p) or p.startswith("\\\\"):
        return p
    return volumes.join(mount, p) if mount else None


def plan(conn: sqlite3.Connection, item: db.Item, mount: str | None) -> Launch:
    """Work out how to start ``item``; LaunchError says what's missing."""
    if item.category != "game":
        raise LaunchError("Only games can be played from here for now.")
    if item.steam_appid and (item.source == db.STEAM or not item.launch_path):
        return Launch(item.id, f"{item.title} in Steam", url=f"steam://rungameid/{item.steam_appid}")
    if not item.launch_path:
        raise LaunchError(f"No game file found for {item.title}. Pick one on its page "
                          "(Launch options › Choose file…).")
    path = target_path(item, mount)
    if not path:
        raise LaunchError(f"{item.title} is on a drive that isn't connected.")
    if not os.path.exists(path):
        raise LaunchError(f"{path} isn't there anymore. Rescan, or pick the file again.")
    extra = split_args(item.launch_args)

    if not item.platform or item.platform == "PC":
        if not path.lower().endswith((".exe", ".bat", ".cmd", ".lnk")):
            raise LaunchError(f"{os.path.basename(path)} is an image or archive: extract or mount "
                              "it and install the game, then rescan.")
        return Launch(item.id, item.title, [path, *extra], cwd=os.path.dirname(path))

    exe = db.get_setting(conn, emulator_key(item.platform))
    if not exe:
        suggestion = SUGGESTED.get(item.platform)
        raise LaunchError(f"No emulator set for {item.platform}. Add one in Settings › Emulators"
                          + (f" (for example {suggestion})." if suggestion else "."))
    if not os.path.exists(exe):
        raise LaunchError(f"The {item.platform} emulator isn't at {exe} anymore (Settings › Emulators).")
    template = db.get_setting(conn, emulator_args_key(item.platform)) or DEFAULT_ARGS.get(
        item.platform, '"{file}"')
    unzip = path.lower().endswith(".zip") and db.get_setting(
        conn, emulator_unzip_key(item.platform), "1") == "1"
    name = os.path.splitext(os.path.basename(exe))[0]
    return Launch(item.id, f"{item.title} in {name}", build_command(exe, template, path, extra),
                  cwd=os.path.dirname(exe), unzip=path if unzip else None)


def split_args(text: str | None) -> list[str]:
    """Windows-style arguments: spaces separate, "quotes" group (and are removed - the
    command list is quoted again when it's started)."""
    out = []
    for part in shlex.split(text or "", posix=False):
        out.append(part[1:-1] if len(part) >= 2 and part[0] == part[-1] == '"' else part)
    return out


def build_command(exe: str, template: str, file: str, extra: list[str] | None = None) -> list[str]:
    """``exe`` + the template's arguments with {file} filled in (quotes keep spaces)."""
    parts = split_args(template)
    args = [p.replace("{file}", file) for p in parts]
    if not any("{file}" in p for p in parts):
        args.append(file)
    return [exe, *args, *(extra or [])]


def unzip_image(zip_path: str, progress: Callable[[str], None] = lambda s: None,
                cancelled: Callable[[], bool] = lambda: False) -> str:
    """Unpack the biggest file of ``zip_path`` into the cache (reused if already there)."""
    cache = paths.data_dir() / "extracted"
    cache.mkdir(exist_ok=True)
    stamp = f"{os.path.getsize(zip_path)}-{int(os.path.getmtime(zip_path))}"
    folder = cache / f"{os.path.splitext(os.path.basename(zip_path))[0][:60]}-{stamp}"
    with zipfile.ZipFile(zip_path) as z:
        members = [m for m in z.infolist() if not m.is_dir()]
        if not members:
            raise LaunchError(f"{zip_path} is empty.")
        main = max(members, key=lambda m: m.file_size)
        target = folder / os.path.basename(main.filename)
        if target.exists() and target.stat().st_size == main.file_size:
            os.utime(folder)
            return _entry(folder, target)
        folder.mkdir(exist_ok=True)
        # A .cue needs its .bins, a .gdi its tracks: unpack everything, biggest last.
        for m in sorted(members, key=lambda m: m.file_size):
            dest = folder / os.path.basename(m.filename)
            with z.open(m) as src, open(dest, "wb") as out:
                done, total = 0, m.file_size or 1
                while chunk := src.read(4 * 1024 * 1024):
                    if cancelled():
                        out.close()
                        shutil.rmtree(folder, ignore_errors=True)
                        raise LaunchError("Cancelled.")
                    out.write(chunk)
                    done += len(chunk)
                    progress(f"Unpacking {os.path.basename(m.filename)}: {100 * done // total}%")
    _trim_cache(cache, keep=folder)
    return _entry(folder, target)


def _entry(folder, biggest) -> str:
    """What to open in an unpacked folder: its .cue / .gdi / .m3u, else the biggest file."""
    sheet = next((p for p in folder.iterdir() if p.suffix.lower() in (".cue", ".gdi", ".m3u")), None)
    return str(sheet or biggest)


def _trim_cache(cache, keep) -> None:
    folders = sorted((p for p in cache.iterdir() if p.is_dir() and p != keep),
                     key=lambda p: p.stat().st_mtime, reverse=True)
    for old in folders[KEEP_EXTRACTED - 1:]:
        shutil.rmtree(old, ignore_errors=True)


def start(launch: Launch) -> subprocess.Popen | None:
    """Start it, detached from Tracking (closing the app doesn't close the game).
    Returns the process to time the session with (None for Steam URLs)."""
    if launch.url:
        if sys.platform == "win32":
            os.startfile(launch.url)
        else:
            subprocess.Popen(["xdg-open", launch.url])
        return None
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    log.info("launching %s: %s", launch.what, subprocess.list2cmdline(launch.command))
    return subprocess.Popen(launch.command, cwd=launch.cwd, creationflags=flags, close_fds=True)


MIN_SESSION_S = 60  # shorter = a launcher that handed over to the real game, not a session


def watch_session(proc: subprocess.Popen, item_id: int, db_file=None) -> threading.Thread:
    """Time the game until it exits (on a background thread) and add the minutes to the
    item's play time. Launchers that exit right away don't count."""
    def run():
        t0 = time.monotonic()
        proc.wait()
        add_session(item_id, time.monotonic() - t0, db_file)

    t = threading.Thread(target=run, name=f"session-{item_id}", daemon=True)
    t.start()
    return t


def add_session(item_id: int, seconds: float, db_file=None) -> bool:
    if seconds < MIN_SESSION_S:
        return False
    with db.session(db_file) as conn:
        conn.execute("UPDATE items SET playtime_local_min=COALESCE(playtime_local_min, 0) + ? "
                     "WHERE id=?", (round(seconds / 60, 1), item_id))
    log.info("session for item %d: %.0f min", item_id, seconds / 60)
    return True


def record(conn: sqlite3.Connection, item_id: int) -> None:
    conn.execute("UPDATE items SET last_launched=?, launch_count=launch_count+1 WHERE id=?",
                 (datetime.now().isoformat(timespec="seconds"), item_id))


def set_launch(conn: sqlite3.Connection, item_id: int, path: str | None, args: str | None) -> None:
    """Your own file / arguments for Play (None path = back to the detected one). A file on
    the item's own drive is stored relative to it, so a new drive letter doesn't break it."""
    item = db.get_item(conn, item_id)
    stored = path
    if path and item:
        mount = scan.mount_for(conn, item.volume_uid)
        if mount and os.path.normcase(path).startswith(os.path.normcase(mount)):
            stored = volumes._rel(path, mount)
    conn.execute("UPDATE items SET user_launch=?, launch_args=? WHERE id=?",
                 (stored, (args or "").strip() or None, item_id))


# --------------------------------------------------------------------------- videos

PLAYER_KEY, PLAYER_ARGS_KEY = "video_player", "video_player_args"


@dataclass
class EpisodeFile:
    season: int | None
    episode: int | None
    rel_path: str


def episode_files(conn: sqlite3.Connection, item_id: int) -> list[EpisodeFile]:
    """An item's video files in watching order (season, episode, then name)."""
    rows = conn.execute("SELECT season, episode, rel_path FROM media_files WHERE item_id=?",
                        (item_id,)).fetchall()
    files = [EpisodeFile(r["season"], r["episode"], r["rel_path"]) for r in rows]
    return sorted(files, key=lambda f: (f.season or 0, f.episode is None, f.episode or 0, f.rel_path))


def next_episode(conn: sqlite3.Connection, item: db.Item) -> EpisodeFile | None:
    """The one after the last played (the first if none); None once all were played."""
    files = episode_files(conn, item.id)
    if not files:
        return None
    if item.kind == "movie" or item.category == "movie":
        return max(files, key=lambda f: conn.execute(
            "SELECT size_bytes FROM media_files WHERE item_id=? AND rel_path=?",
            (item.id, f.rel_path)).fetchone()["size_bytes"] or 0)
    if item.last_episode is None:
        return files[0]
    last = (item.last_episode_season or 0, item.last_episode)
    later = [f for f in files if f.episode is not None and ((f.season or 0), f.episode) > last]
    return later[0] if later else None


def plan_video(conn: sqlite3.Connection, item: db.Item, mount: str | None,
               file: EpisodeFile) -> Launch:
    if not mount:
        raise LaunchError(f"{item.title} is on a drive that isn't connected.")
    path = volumes.join(mount, file.rel_path)
    if not os.path.exists(path):
        raise LaunchError(f"{path} isn't there anymore. Rescan the location.")
    label = item.title + (f" · episode {file.episode}" if file.episode is not None
                          and item.kind != "movie" else "")
    player = db.get_setting(conn, PLAYER_KEY)
    if player:
        if not os.path.exists(player):
            raise LaunchError(f"The video player isn't at {player} anymore (Settings › General).")
        template = db.get_setting(conn, PLAYER_ARGS_KEY) or '"{file}"'
        la = Launch(item.id, label, build_command(player, template, path), cwd=os.path.dirname(path))
    else:
        la = Launch(item.id, label, url=path)  # the system's default player
    la.episode = (file.season, file.episode)
    return la


def record_watch(conn: sqlite3.Connection, item_id: int, season: int | None, episode: int | None) -> None:
    conn.execute("UPDATE items SET last_episode=?, last_episode_season=?, last_watched=? WHERE id=?",
                 (episode, season, datetime.now().isoformat(timespec="seconds"), item_id))
