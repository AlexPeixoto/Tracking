"""Every game platform the app knows, in one table.

To add a platform, add a row. Fields:
  name    what the app shows (and stores)
  folders folder names that mean this platform (lowercase, letters/digits only:
          "Sony - PlayStation 2" is matched as "playstation2"), including the short
          names emulator front-ends use (ES-DE / RetroArch: sfc, fc, md, gg, pce...)
  tokens  the subset of ``folders`` that is safe to spot *inside* longer names and
          paths ("Okami (PS2).iso", "ps2 isos/"); everyday words such as "switch",
          "genesis" or "arcade" only count as a whole folder name
  rawg / igdb / hltb   how that service spells it; igdb / hltb fall back to the
          rawg spelling, which falls back to ``name``

ROM extensions live in scan/classify.py (ROM_EXT) and refer to these names.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Platform:
    name: str
    folders: tuple[str, ...] = ()
    tokens: tuple[str, ...] = ()
    rawg: str | None = None
    igdb: str | None = None
    hltb: str | None = None

    def service_name(self, service: str) -> str:
        # Services mostly agree with RAWG's full names ("PlayStation 2"), not our short ones.
        return getattr(self, service, None) or self.rawg or self.name


_P = Platform
PLATFORMS: list[Platform] = [
    _P("PC", ("pc", "pcgames", "windows", "steam", "gog"), (), "PC", "PC (Microsoft Windows)"),
    _P("PS1", ("ps1", "psx", "psone", "playstation", "playstation1"),
       ("ps1", "psx", "psone", "playstation1"), "PlayStation", "PlayStation", "PlayStation"),
    _P("PS2", ("ps2", "playstation2"), ("ps2", "playstation2"), "PlayStation 2"),
    _P("PS3", ("ps3", "playstation3"), ("ps3", "playstation3"), "PlayStation 3"),
    _P("PS4", ("ps4", "playstation4"), ("ps4", "playstation4"), "PlayStation 4"),
    _P("PS5", ("ps5", "playstation5"), ("ps5", "playstation5"), "PlayStation 5"),
    _P("PSP", ("psp", "playstationportable"), ("psp",), "PSP", "PlayStation Portable"),
    _P("PS Vita", ("vita", "psvita", "playstationvita"), ("psvita",), "PS Vita", "PlayStation Vita"),
    _P("Saturn", ("saturn", "segasaturn"), ("saturn", "segasaturn"), "SEGA Saturn", "Sega Saturn",
       "Sega Saturn"),
    _P("Dreamcast", ("dreamcast", "segadreamcast", "dc"), ("dreamcast",), "Dreamcast"),
    _P("Mega Drive", ("megadrive", "genesis", "segagenesis", "md", "gen"), ("megadrive", "segagenesis"),
       "Genesis", "Sega Mega Drive/Genesis", "Sega Mega Drive/Genesis"),
    _P("Sega CD", ("segacd", "megacd", "scd"), ("segacd", "megacd"), "SEGA CD"),
    _P("Master System", ("mastersystem", "sms"), (), "SEGA Master System", "Sega Master System/Mark III"),
    _P("Game Gear", ("gamegear", "gg"), (), "Game Gear", "Sega Game Gear"),
    _P("32X", ("sega32x",), (), "SEGA 32X", "Sega 32X"),
    _P("SG-1000"),
    _P("NES", ("nes", "famicom", "fc"), ("nes", "famicom"), "NES", "Nintendo Entertainment System"),
    _P("SNES", ("snes", "superfamicom", "supernintendo", "sfc"), ("snes", "superfamicom"), "SNES",
       "Super Nintendo Entertainment System", "Super Nintendo"),
    _P("N64", ("n64", "nintendo64"), ("n64", "nintendo64"), "Nintendo 64", "Nintendo 64",
       "Nintendo 64"),
    _P("GameCube", ("gamecube", "ngc", "gc"), ("gamecube", "ngc"), "GameCube", "Nintendo GameCube",
       "Nintendo GameCube"),
    _P("Wii", ("wii",), ("wii",), "Wii"),
    _P("Wii U", ("wiiu",), ("wiiu",), "Wii U"),
    _P("Switch", ("switch", "nintendoswitch", "sw", "nsw"), ("nintendoswitch",), "Nintendo Switch",
       "Nintendo Switch", "Nintendo Switch"),
    _P("Game Boy", ("gb", "gameboy"), ("gameboy",), "Game Boy"),
    _P("Game Boy Color", ("gbc", "gameboycolor"), ("gbc",), "Game Boy Color"),
    _P("GBA", ("gba", "gameboyadvance"), ("gba", "gameboyadvance"), "Game Boy Advance",
       "Game Boy Advance", "Game Boy Advance"),
    _P("DS", ("nds", "ds", "nintendods"), ("nds",), "Nintendo DS", "Nintendo DS", "Nintendo DS"),
    _P("3DS", ("3ds", "nintendo3ds"), ("3ds", "nintendo3ds"), "Nintendo 3DS", "Nintendo 3DS",
       "Nintendo 3DS"),
    _P("Virtual Boy", ("virtualboy", "vb")),
    _P("Pokemon Mini"),
    _P("Xbox", ("xbox",), (), "Xbox"),
    _P("Xbox 360", ("xbox360", "x360"), ("xbox360", "x360"), "Xbox 360"),
    _P("Xbox One", ("xboxone",), ("xboxone",), "Xbox One"),
    _P("PC Engine", ("pcengine", "turbografx", "turbografx16", "pce", "tg16"), ("pcengine", "turbografx16"),
       None, "TurboGrafx-16/PC Engine", "TurboGrafx-16"),
    _P("PC-FX", ("pcfx",)),
    _P("Neo Geo", ("neogeo",), ("neogeo",), "Neo Geo", "Neo Geo AES"),
    _P("Neo Geo Pocket", ("ngp", "ngpc", "neogeopocket", "neogeopocketcolor")),
    _P("WonderSwan", ("wonderswan", "ws", "wsc", "wonderswancolor")),
    _P("Arcade", ("arcade", "mame", "fbneo", "fba", "cps1", "cps2", "cps3"), ("mame",)),
    _P("Atari 2600", ("atari2600", "a2600")),
    _P("Atari 5200"),
    _P("Atari 7800", ("atari7800", "a7800")),
    _P("Atari Lynx", ("lynx",)),
    _P("Atari Jaguar", ("jaguar",)),
    _P("ColecoVision"),
    _P("Intellivision"),
    _P("Vectrex"),
    _P("Commodore 64", ("c64",)),
    _P("Amiga", ("amiga",)),
    _P("ZX Spectrum", ("zxspectrum",)),
    _P("MSX", ("msx",)),
    _P("3DO", ("3do",)),
]

BY_NAME: dict[str, Platform] = {p.name: p for p in PLATFORMS}
NAMES: list[str] = sorted(BY_NAME, key=str.lower)
# folder alias -> platform name; token alias -> platform name
FOLDER_ALIASES: dict[str, str] = {a: p.name for p in PLATFORMS for a in p.folders}
TOKEN_ALIASES: dict[str, str] = {a: p.name for p in PLATFORMS for a in p.tokens}


def service_name(platform: str | None, service: str) -> str | None:
    """How ``service`` ("rawg", "igdb", "hltb") spells our platform name."""
    p = BY_NAME.get(platform or "")
    return p.service_name(service) if p else platform
