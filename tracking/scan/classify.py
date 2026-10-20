"""Decide whether something is anime, a movie or a game, and for games which platform.

Signals, strongest first:
  1. What the user told us (root category / per-item override).
  2. Folder names above the item ("Anime", "Games/PS2", "Sony - PlayStation 2").
  3. What's inside: ROM / disc-image extensions, disc header bytes, executables,
     video files and fansub-style names.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, replace

from .. import platforms

VIDEO_EXT = {
    ".mkv", ".mp4", ".avi", ".m4v", ".mov", ".wmv", ".ts", ".m2ts", ".webm",
    ".flv", ".mpg", ".mpeg", ".ogm", ".rmvb", ".divx", ".vob",
}

# Extensions that pin down the platform on their own (No-Intro / Redump / scene
# formats, as listed on the Emulation General wiki's "List of filetypes").
ROM_EXT = {
    # Nintendo
    ".nes": "NES", ".unf": "NES", ".unif": "NES", ".fds": "NES",
    ".sfc": "SNES", ".smc": "SNES", ".fig": "SNES", ".swc": "SNES", ".bs": "SNES",
    ".n64": "N64", ".z64": "N64", ".v64": "N64", ".ndd": "N64",
    ".gb": "Game Boy", ".gbc": "Game Boy Color", ".gba": "GBA", ".agb": "GBA",
    ".vb": "Virtual Boy", ".min": "Pokemon Mini",
    ".nds": "DS", ".dsi": "DS", ".ids": "DS",
    ".3ds": "3DS", ".cia": "3DS", ".cxi": "3DS", ".cci": "3DS", ".3dsx": "3DS",
    ".gcm": "GameCube", ".gcz": "GameCube", ".rvz": "GameCube", ".dol": "GameCube",
    ".wbfs": "Wii", ".wad": "Wii", ".wia": "Wii",
    ".wud": "Wii U", ".wux": "Wii U", ".rpx": "Wii U", ".wua": "Wii U",
    ".nsp": "Switch", ".xci": "Switch", ".nsz": "Switch", ".xcz": "Switch", ".nro": "Switch",
    # Sega
    ".md": "Mega Drive", ".gen": "Mega Drive", ".smd": "Mega Drive", ".32x": "32X",
    ".sms": "Master System", ".gg": "Game Gear", ".sg": "SG-1000",
    ".gdi": "Dreamcast", ".cdi": "Dreamcast",
    # Sony
    ".cso": "PSP", ".zso": "PSP", ".pbp": "PSP", ".vpk": "PS Vita",
    # Microsoft
    ".xex": "Xbox 360", ".xbe": "Xbox", ".xiso": "Xbox",
    # Others
    ".pce": "PC Engine", ".sgx": "PC Engine", ".ngp": "Neo Geo Pocket", ".ngc": "Neo Geo Pocket",
    ".ws": "WonderSwan", ".wsc": "WonderSwan", ".a26": "Atari 2600", ".a52": "Atari 5200",
    ".a78": "Atari 7800", ".lnx": "Atari Lynx", ".j64": "Atari Jaguar", ".jag": "Atari Jaguar",
    ".col": "ColecoVision", ".int": "Intellivision", ".vec": "Vectrex",
    ".d64": "Commodore 64", ".t64": "Commodore 64", ".crt": "Commodore 64",
    ".adf": "Amiga", ".ipf": "Amiga", ".tzx": "ZX Spectrum", ".z80": "ZX Spectrum",
}

# Disc images and packages: the platform has to come from the path, the file
# name, or by peeking at the disc header.
DISC_EXT = {
    ".iso", ".bin", ".cue", ".chd", ".img", ".mdf", ".mds", ".nrg", ".ccd", ".ecm", ".toc",
    ".m3u", ".pkg",
}
# Archives only count as games when something says which platform they're for.
ARCHIVE_EXT = {".zip", ".7z", ".rar"}

PC_EXT = {".exe"}

# PC repack / scene groups: a release tagged with one of these is a PC game.
PC_RELEASE_TAGS = {
    "fitgirl", "dodi", "elamigos", "gog", "codex", "plaza", "empress", "tenoki", "rune",
    "flt", "steamrip", "skidrow", "razor1911", "reloaded", "cpy", "hoodlum", "tinyiso",
    "darksiders", "kaos", "xatab",
}
_SWITCH_TITLE_ID = re.compile(r"\b0100[0-9A-F]{12}\b", re.I)

# Disc / store serials in names ("Folklore_BCES00050", "[NPEB-00553]", "ULUS10123").
_SERIALS = [
    (re.compile(r"(?i)(?<![A-Za-z0-9])B[CL][EUAJK][SDM][-_ ]?\d{5}(?!\d)"), "PS3"),
    (re.compile(r"(?i)(?<![A-Za-z0-9])NP[EUJHAK][ABCD][-_ ]?\d{5}(?!\d)"), "PS3"),
    (re.compile(r"(?i)(?<![A-Za-z0-9])NP[EUJHAK][GHJZ][-_ ]?\d{5}(?!\d)"), "PSP"),
    (re.compile(r"(?i)(?<![A-Za-z0-9])U[CL][EUJAK][SMD][-_ ]?\d{5}(?!\d)"), "PSP"),
    (re.compile(r"(?i)(?<![A-Za-z0-9])CUSA[-_ ]?\d{5}(?!\d)"), "PS4"),
    (re.compile(r"(?i)(?<![A-Za-z0-9])PPSA[-_ ]?\d{5}(?!\d)"), "PS5"),
    (re.compile(r"(?i)(?<![A-Za-z0-9])PCS[EAGBH][-_ ]?\d{5}(?!\d)"), "PS Vita"),
]
# PS1 / PS2 serials (SLUS, SCES...) prove "console game" but not which one.
_PS12_SERIAL = re.compile(r"(?i)(?<![A-Za-z0-9])S[CL][EUPAKC][SMD][-_ .]?\d{3}\.?\d{2}(?!\d)")
# Redump / No-Intro region tags: "(USA)", "(Europe)", "(Japan) (En,Ja)".
_REGION_TAG = re.compile(r"(?i)\((?:USA|Europe|Japan|World|Asia|Korea|Brazil|Germany|France|"
                         r"Spain|Italy|Australia|En(?:,[A-Z][a-z])+)(?:,\s*[A-Za-z]+)*\)")
_GOG_GAMES_NAME = re.compile(r"(?i)^game-.+-\(\d+\)")


_TOOL_NAMES = re.compile(
    r"(?i)(?:^|[\s._\-\[(])(?:firmware|prod\.?keys|title\.?keys|keys|bios|emulator|yuzu|ryujinx|"
    r"suyu|sudachi|citra|cemu|rpcs3|pcsx2|duckstation|retroarch|dolphin\s*emu)(?:$|[\s._\-\])])")


def is_tool_name(stem: str) -> bool:
    """Emulators, firmware, key dumps and BIOS files aren't games."""
    return bool(_TOOL_NAMES.search(stem))


def looks_random(stem: str) -> bool:
    """"IePErNcHFCMaNrLDqsH": no separators and long, i.e. a scrambled name."""
    return len(stem) >= 15 and not re.search(r"[\s._\-\[\]()]", stem)


def game_name_evidence(name: str) -> tuple[bool, str | None]:
    """Does a file name itself say "this is a game" (and maybe for what)?

    Serials and Switch title ids give the platform; Redump region tags and
    GOG-Games naming say it's a game (GOG: PC).
    """
    if _SWITCH_TITLE_ID.search(name):
        return True, "Switch"
    for rx, plat in _SERIALS:
        if rx.search(name):
            return True, plat
    if _GOG_GAMES_NAME.search(name):
        return True, "PC"
    if _PS12_SERIAL.search(name) or _REGION_TAG.search(name):
        return True, None
    return False, None


def switch_addon(name: str) -> bool:
    """Switch title ids end in 000 for the game; 800 is its update, others DLC."""
    m = _SWITCH_TITLE_ID.search(name)
    return bool(m) and not m.group(0).upper().endswith("000")

SKIP_DIRS = {
    "$recycle.bin", "system volume information", "recovery", "windows", "program files",
    "program files (x86)", "programdata", "$windows.~bt", "$windows.~ws", "msocache",
    "lost+found", ".trash", ".trashes", "@eadir", "#recycle", ".git",
}

# Folder name -> platform, and the aliases safe inside longer names: see platforms.py.
PLATFORM_KEYS = platforms.FOLDER_ALIASES
_TOKEN_PLATFORMS = platforms.TOKEN_ALIASES
CATEGORY_KEYS = {
    "anime": "anime", "animes": "anime", "animation": "anime", "animemovies": "anime",
    "animeseries": "anime", "tvanime": "anime",
    "movies": "movie", "movie": "movie", "films": "movie", "film": "movie", "filmes": "movie",
    "cinema": "movie",
    "tv": "tv", "tvshows": "tv", "tvseries": "tv", "series": "tv", "shows": "tv",
    "television": "tv", "seriados": "tv", "seriestv": "tv",
    "games": "game", "game": "game", "jogos": "game", "roms": "game", "isos": "game",
    "emulation": "game", "pcgames": "game", "videogames": "game",
}

# Folders that just hold other folders.
GENERIC_CONTAINERS = {
    "media", "library", "collection", "downloads", "download", "completed", "torrents",
    "videos", "video", "data", "storage", "backup", "backups",
}

_ANIME_FILE = re.compile(
    r"^\[[^\]]+\]"                     # [SubGroup] Title - 01
    r"|\[[0-9A-F]{8}\]"                # CRC32 tag
    r"|\s-\s\d{2,4}(v\d)?(\s|\[|\(|$)",  # absolute episode numbering
    re.I,
)
_EPISODE = re.compile(r"\bS\d{1,2}E\d{1,3}\b|\bE\d{2,3}\b|\s-\s\d{2,4}\b", re.I)


def _norm(name: str) -> str:
    return re.sub(r"[^0-9a-z]+", "", name.lower())


_TRAILING_FILLER = {"games", "roms", "rom", "isos", "iso", "collection", "library", "jogos", "set"}
_VENDORS = {"sony", "sega", "nintendo", "microsoft", "nec", "snk", "atari"}


def platform_from_name(name: str) -> str | None:
    """Exact matches only, so a game called "PlayStation All-Stars" isn't a platform.

    Accepts "PS2", "PS2 Games", "Saturn ISOs", "Sony - PlayStation 2", "Nintendo 64".
    """
    words = [w for w in re.split(r"[^0-9a-z]+", name.lower()) if w]
    while len(words) > 1 and words[-1] in _TRAILING_FILLER:
        words = words[:-1]
    candidates = ["".join(words)]
    if len(words) > 1 and words[0] in _VENDORS:
        candidates.append("".join(words[1:]))
    for c in candidates:
        if c in PLATFORM_KEYS:
            return PLATFORM_KEYS[c]
    return None


def category_from_name(name: str) -> str | None:
    return CATEGORY_KEYS.get(_norm(name))


def is_generic_container(name: str) -> bool:
    return _norm(name) in GENERIC_CONTAINERS


def is_skipped(name: str) -> bool:
    return name.lower() in SKIP_DIRS or name.startswith(".")


def is_junk_file(name: str) -> bool:
    """macOS metadata ("._Game.nsp", ".DS_Store") and Windows thumbnails."""
    return name.startswith(".") or name.lower() in {"thumbs.db", "desktop.ini"}


# Sub-folders that belong to one release rather than being releases themselves.
JUNK_SUBDIRS = {
    "md5", "bin", "binaries", "data", "engine", "content", "redist", "_commonredist",
    "commonredist", "directx", "dotnet", "vcredist", "support", "docs", "manual", "manuals",
    "saves", "save", "mods", "config", "localization", "lang", "languages", "sound", "sounds",
    "audio", "music", "shaders", "x64", "x86", "win64", "win32", "plugins", "lib", "libs",
    "update", "updates", "dlc", "crack", "patch", "fix", "installer", "_installer", "setup",
    "artbook", "soundtrack", "ost", "bonus", "extras", "subs", "subtitles", "featurettes",
    "sample", "samples", "scans", "fonts", "nfo", "covers", "artwork", "screens", "screenshots",
    "monobleedingedge", "d3d12", "redistributables", "_redist", "prerequisites",
}


def is_junk_subdir(name: str) -> bool:
    low = name.lower()
    return low in JUNK_SUBDIRS or low.endswith("_data") or low.startswith(("_", "."))


@dataclass(frozen=True)
class Hints:
    category: str | None = None
    platform: str | None = None

    def child(self, folder_name: str) -> tuple[Hints, bool]:
        """Hints for a sub-folder, and whether the folder name itself was a keyword."""
        # Inside a platform folder, sub-folders are game titles, not more platforms.
        # Inside an anime / movie / TV folder, a title like "Saturn" is just a title.
        video_ctx = self.category in ("anime", "movie", "tv")
        plat = None if (self.platform or video_ctx) else platform_from_name(folder_name)
        cat = category_from_name(folder_name)
        h = self
        if plat:
            h = replace(h, platform=plat, category="game")
        elif cat and not (self.category and self.category != cat and cat != "game"):
            h = replace(h, category=cat)
        return h, bool(plat or cat)


# --------------------------------------------------------------------------- disc sniffing

def sniff_disc(path: str) -> str | None:
    """Peek at a disc image header to guess its platform."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            head = f.read(0x9400)
    except OSError:
        return None
    if b"SEGA SEGASATURN" in head[:0x40]:
        return "Saturn"
    if b"SEGA SEGAKATANA" in head[:0x40]:
        return "Dreamcast"
    if b"SEGADISCSYSTEM" in head[:0x40] or b"SEGA MEGA DRIVE" in head[:0x200]:
        return "Sega CD"
    if head[0x1C:0x20] == b"\xC2\x33\x9F\x3D":
        return "GameCube"
    if head[0x18:0x1C] == b"\x5D\x1C\x9E\xA3":
        return "Wii"
    # ISO9660 primary volume descriptor: cooked (2048) or raw (2352) sectors.
    for pvd in (0x8000, 0x9318):
        block = head[pvd: pvd + 0x48]
        if block[1:6] != b"CD001":
            continue
        system_id = block[8:40].strip(b" \0")
        volume_id = block[40:72].strip(b" \0")
        if system_id == b"PLAYSTATION":
            return "PS2" if size > 900 * 1024 * 1024 else "PS1"
        if system_id.startswith(b"PSP GAME") or volume_id.startswith(b"UMD"):
            return "PSP"
        if volume_id.startswith(b"PS3VOLUME"):
            return "PS3"
    return None


# --------------------------------------------------------------------------- name / path hints

def _tokens(text: str) -> list[str]:
    return [w for w in re.split(r"[^0-9a-z]+", text.lower()) if w]


def platform_from_tokens(*texts: str) -> str | None:
    """Spot a platform word anywhere in paths / names: "Okami (PS2).iso",
    "PS2 stuff/x.zip", "Sony PlayStation 2 ISOs". Two-word names are joined
    ("playstation 2", "game cube", "xbox 360")."""
    for text in texts:
        toks = _tokens(text)
        for i, t in enumerate(toks):
            if i + 1 < len(toks) and t + toks[i + 1] in _TOKEN_PLATFORMS:
                return _TOKEN_PLATFORMS[t + toks[i + 1]]
            if t in _TOKEN_PLATFORMS:
                return _TOKEN_PLATFORMS[t]
    return None


def platform_from_tags(name: str) -> str | None:
    """Platform from release tags in a name.

    "[FitGirl PC Repack]" / "Game-CODEX" -> PC; "[01004E90149AA000]" (a Switch
    title id) -> Switch; "(PS2)" -> PS2.
    """
    _, plat = game_name_evidence(name)
    if plat:
        return plat
    tags = " ".join(re.findall(r"[\[(]([^\])]*)[\])]", name))
    words = set(_tokens(tags))
    # "[FitGirl Switch Repack]": a platform named in the tag beats the group's usual PC.
    for w in words - {"pc"}:
        if w in PLATFORM_KEYS and w not in {"ds", "gb", "sw", "game"}:
            return PLATFORM_KEYS[w]
    tail = _tokens(name.rsplit("-", 1)[-1]) if "-" in name else []
    if words & PC_RELEASE_TAGS or "pc" in words or (len(tail) == 1 and tail[0] in PC_RELEASE_TAGS):
        return "PC"
    return platform_from_tokens(tags) if tags else None


# --------------------------------------------------------------------------- content

@dataclass
class ContentSignals:
    videos: int = 0
    anime_like: int = 0
    episodic: int = 0
    game_platform: str | None = None
    game_files: int = 0
    exes: int = 0
    archives: int = 0
    disc_platform: str | None = None   # from disc headers / .pkg


def inspect_files(files: list[tuple[str, str]]) -> ContentSignals:
    """``files`` is a list of (relative path inside the item, absolute path)."""
    sig = ContentSignals()
    disc_candidates: list[str] = []
    for rel, abs_path in files:
        name = os.path.basename(rel)
        ext = os.path.splitext(name)[1].lower()
        parts = {p.upper() for p in rel.replace("\\", "/").split("/")}
        if ext in VIDEO_EXT:
            sig.videos += 1
            if _ANIME_FILE.search(name):
                sig.anime_like += 1
            if _EPISODE.search(name):
                sig.episodic += 1
        elif "PS3_GAME" in parts or name.upper() == "PS3_DISC.SFB":
            sig.game_platform, sig.game_files = "PS3", sig.game_files + 1
        elif ext in ROM_EXT:
            sig.game_platform = sig.game_platform or ROM_EXT[ext]
            sig.game_files += 1
        elif ext in DISC_EXT:
            sig.game_files += 1
            if ext in {".iso", ".bin", ".img"}:
                disc_candidates.append(abs_path)
            if ext == ".pkg":
                sig.disc_platform = sig.disc_platform or "PS3"
        elif ext in ARCHIVE_EXT:
            sig.archives += 1
        elif ext in PC_EXT:
            sig.exes += 1
    if not sig.game_platform and disc_candidates and not sig.exes:
        for p in disc_candidates[:3]:
            plat = sniff_disc(p)
            if plat:
                sig.disc_platform = plat
                break
    return sig


@dataclass
class Decision:
    category: str            # anime | movie | tv | game | unknown
    platform: str | None
    reason: str              # human-readable, shown in the log and on the item page


def decide(hints: Hints, sig: ContentSignals, name_platform: str | None = None,
           path_platform: str | None = None, name_is_game: bool = False) -> Decision | None:
    """Pick a category (and platform for games), or None when it isn't media.

    ``name_platform`` comes from tags in the item's own name ("[FitGirl PC Repack]"),
    ``path_platform`` from platform words anywhere in its path.
    Platform priority: folder keyword > unambiguous ROM extension > name tags >
    .exe (PC) > disc header > words in the path.
    """
    def game_platform() -> tuple[str | None, str]:
        if hints.platform:
            return hints.platform, f"platform folder {hints.platform}"
        if sig.game_platform:
            return sig.game_platform, f"{sig.game_platform} file extension"
        if name_platform:
            return name_platform, f"release tags say {name_platform}"
        if sig.exes:
            return "PC", ".exe present"
        if sig.disc_platform:
            return sig.disc_platform, f"disc header / package looks like {sig.disc_platform}"
        if path_platform:
            return path_platform, f"'{path_platform}' in the path"
        return None, "platform unknown"

    has_game = sig.game_files > 0 or sig.exes > 0
    # Archives are games only when we know what for.
    if sig.archives and (hints.category == "game" or hints.platform or name_platform
                         or path_platform or name_is_game):
        has_game = True

    if hints.category == "game" or hints.platform:
        if not has_game and sig.videos == 0:
            return None
        plat, why = game_platform()
        return Decision("game", plat, f"inside a games folder; {why}")
    if hints.category in ("anime", "movie", "tv"):
        if sig.videos:
            if hints.category == "movie" and sig.episodic and not sig.anime_like:
                return Decision("tv", None, "episodes inside a Movies folder")
            return Decision(hints.category, None, f"video files inside {hints.category} folder")
        if has_game:
            plat, why = game_platform()
            return Decision("game", plat, f"game files; {why}")
        return None
    # Auto: no folder hints, go by contents.
    if has_game and sig.videos <= max(2, sig.game_files + sig.exes):
        plat, why = game_platform()
        return Decision("game", plat, f"game files; {why}")
    if sig.videos:
        if sig.anime_like and sig.anime_like * 2 >= sig.videos:
            return Decision("anime", None, "fansub-style file names")
        if sig.episodic and sig.episodic * 2 >= sig.videos:
            return Decision("tv", None, "episodic video without anime markers")
        return Decision("movie", None, "video file(s) without episode numbers")
    return None
