"""Is a game ready to play, and what do we start?

  * installed: a PC folder with the game's own .exe (the one Play starts)
  * installer: a repack / setup folder (setup.exe, FitGirl / DODI / GOG installers) or a
    PC disc image / archive; Play runs the setup
  * image: a console ROM or disc; Play hands it to that platform's emulator

The launch target is chosen by scoring each candidate file; the user can always pick
another one on the item page.
"""

from __future__ import annotations

import os
import re

from .. import titles
from .classify import ARCHIVE_EXT, DISC_EXT, ROM_EXT

INSTALLED, INSTALLER, IMAGE = "installed", "installer", "image"
STATES = {INSTALLED: "Installed", INSTALLER: "Installer", IMAGE: "ROM / disc"}

_SETUP = re.compile(r"(?i)^(?:setup|install(?:er)?|autorun)(?:[_\-\s.].*)?\.exe$")
# Never what you want to start: uninstallers, redistributables, crash reporters, tools.
_NOT_THE_GAME = re.compile(
    r"(?i)(?:^unins\d*|redist|vc_?redist|vcredist|directx|dxsetup|dxwebsetup|dotnet|ndp\d|"
    r"physx|oalinst|crashreport|crashhandler|crashpad|unitycrashhandler|ue4prereq|"
    r"easyanticheat_setup|eac_setup|battleye_?installer|^dxsdk|^touchup|^cleanup|"
    r"^quicksfv|^7z|^unrar|^winrar|notification_helper|^dowser|^cefsharp|^zfgamebrowser)")
_SECONDARY = re.compile(r"(?i)(?:launcher|config|settings|setup|editor|server|benchmark|"
                        r"tool|helper|update|patch|modmanager|mod_manager|reporter|uploader)")
_PS3_EBOOT = "PS3_GAME/USRDIR/EBOOT.BIN"
# Disc formats in order of preference when a folder has several (a .cue names its .bins).
_DISC_PREF = [".m3u", ".cue", ".gdi", ".chd", ".iso", ".cdi", ".cso", ".pbp", ".rvz", ".wbfs",
              ".gcm", ".nsp", ".xci", ".wua", ".wux", ".wud", ".rpx", ".img", ".mds", ".ccd",
              ".bin", ".zip", ".7z"]


def _ext(path: str) -> str:
    return os.path.splitext(path)[1].lower()


def _exe_score(inner: str, size: int, title: str) -> float:
    """Higher = more likely the game's own executable."""
    name = os.path.basename(inner)
    stem = os.path.splitext(name)[0]
    depth = inner.count("/")
    score = 0.0
    score -= depth * 8
    score += min(size / (1024 * 1024), 200) / 10          # big binaries are usually the game
    key, skey = titles.normalize(title), titles.normalize(stem)
    if key and skey and (skey in key or key in skey):
        score += 40
    if _SECONDARY.search(stem):
        score -= 25
    if re.search(r"(?i)-win64-shipping$", stem):           # Unreal: prefer the root stub
        score -= 5
    return score


def detect(files: list[tuple[str, int]], platform: str | None, title: str) -> tuple[str | None, str | None]:
    """(state, inner path to launch) for a game made of ``files`` ((inner rel path, size),
    paths inside the item; a loose file is a single entry with its own name)."""
    if not files:
        return None, None
    if platform and platform != "PC":
        return IMAGE, _console_target(files, platform)
    exes = [(f, s) for f, s in files if _ext(f) == ".exe" and not _NOT_THE_GAME.search(os.path.basename(f))]
    setups = [(f, s) for f, s in exes if _SETUP.match(os.path.basename(f)) and f.count("/") <= 1]
    games = [(f, s) for f, s in exes if (f, s) not in setups]
    if games:
        best = max(games, key=lambda fs: _exe_score(fs[0], fs[1], title))
        return INSTALLED, best[0]
    if setups:
        return INSTALLER, min(setups, key=lambda fs: (fs[0].count("/"), len(fs[0])))[0]
    images = [(f, s) for f, s in files if _ext(f) in DISC_EXT | ARCHIVE_EXT]
    if images:  # a PC game still on its disc image / in its archive
        return INSTALLER, max(images, key=lambda fs: fs[1])[0]
    return None, None


def _console_target(files: list[tuple[str, int]], platform: str) -> str | None:
    paths = {f for f, _ in files}
    if platform == "PS3":
        eboot = next((f for f in paths if f.upper().endswith(_PS3_EBOOT)), None)
        if eboot:
            return eboot
    candidates = [(f, s) for f, s in files
                  if _ext(f) in ROM_EXT or _ext(f) in DISC_EXT or _ext(f) in ARCHIVE_EXT]
    if not candidates:
        return None

    def rank(fs):
        ext = _ext(fs[0])
        pref = _DISC_PREF.index(ext) if ext in _DISC_PREF else (0 if ext in ROM_EXT else len(_DISC_PREF))
        return (fs[0].count("/"), pref, -fs[1], fs[0])
    return min(candidates, key=rank)[0]
