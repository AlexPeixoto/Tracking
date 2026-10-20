"""Read a game disc's own identity: platform, serial and (when the disc has one) title.

Works on plain images and on images inside a .zip, without unpacking:

  * PS1 / PS2: SYSTEM.CNF says which (BOOT vs BOOT2) and gives the serial, as
    PCSX2 does ("cdrom0:\\SLUS_212.21;1" -> SLUS-21221). PS2 serials are named
    from PCSX2's GameIndex (downloaded once, cached).
  * PSP / PS3: PARAM.SFO holds the title and title ID.
  * GameCube / Wii (.iso / .gcm / .wbfs): the disc header holds the ID and title.

Only used when a file's name or platform doesn't say enough, since reading a
zipped image costs some decompression.
"""

from __future__ import annotations

import logging
import os
import re
import struct
import zipfile
from dataclasses import dataclass

log = logging.getLogger(__name__)

IMAGE_EXT = (".iso", ".bin", ".img", ".gcm", ".wbfs")
GAMEINDEX_URL = "https://raw.githubusercontent.com/PCSX2/pcsx2/master/bin/resources/GameIndex.yaml"


@dataclass(frozen=True)
class DiscInfo:
    platform: str
    serial: str | None = None
    title: str | None = None

    def describe(self) -> str:
        return f"disc {self.serial}" if self.serial else f"{self.platform} disc header"


# --------------------------------------------------------------------------- opening

def _open(path: str):
    """A readable, seekable stream of the disc image (inside a zip if need be) and its size."""
    if path.lower().endswith(".zip"):
        z = zipfile.ZipFile(path)
        members = [m for m in z.infolist() if m.filename.lower().endswith(IMAGE_EXT)]
        if not members:
            z.close()
            return None, 0
        m = max(members, key=lambda m: m.file_size)
        return z.open(m), m.file_size
    if path.lower().endswith(IMAGE_EXT):
        return open(path, "rb"), os.path.getsize(path)
    return None, 0


class _Disc:
    """ISO9660 reader over 2048-byte (cooked) or 2352-byte (raw CD) sectors."""

    MAX_BYTES = 256 * 1024 * 1024  # never read deeper than this into a zipped image

    def __init__(self, f, sector: int, offset: int):
        self.f, self.sector, self.offset = f, sector, offset

    def read(self, lba: int, length: int) -> bytes:
        out = b""
        while len(out) < length:
            pos = lba * self.sector + self.offset
            if pos > self.MAX_BYTES:
                break
            self.f.seek(pos)
            out += self.f.read(min(2048, length - len(out)))
            lba += 1
        return out

    def _entries(self, lba: int, size: int):
        data = self.read(lba, min(size, 64 * 1024))
        i = 0
        while i < len(data):
            n = data[i]
            if n == 0:  # records don't cross sectors: skip to the next one
                i = (i // 2048 + 1) * 2048
                continue
            rec = data[i:i + n]
            name_len = rec[32]
            name = rec[33:33 + name_len].decode("ascii", "replace").split(";")[0].upper()
            yield name, struct.unpack_from("<I", rec, 2)[0], struct.unpack_from("<I", rec, 10)[0]
            i += n

    def find(self, path: str) -> bytes | None:
        pvd = self.read(16, 2048)
        if pvd[1:6] != b"CD001":
            return None
        lba, size = struct.unpack_from("<I", pvd, 156 + 2)[0], struct.unpack_from("<I", pvd, 156 + 10)[0]
        for part in path.upper().split("/"):
            hit = next(((at, sz) for n, at, sz in self._entries(lba, size) if n == part), None)
            if not hit:
                return None
            lba, size = hit
        return self.read(lba, min(size, 64 * 1024))[:size]


def _iso(f) -> _Disc | None:
    for sector, offset in ((2048, 0), (2352, 24), (2352, 16)):
        f.seek(16 * sector + offset)
        if f.read(6)[1:6] == b"CD001":
            return _Disc(f, sector, offset)
    return None


# --------------------------------------------------------------------------- formats

_BOOT = re.compile(rb"BOOT(2)?\s*=\s*cdrom0?:\\*([A-Z]{4})[_-](\d{3})\.?(\d{2})", re.I)


def parse_system_cnf(data: bytes) -> tuple[str, str] | None:
    """(platform, serial) from SYSTEM.CNF: BOOT2 means PS2, BOOT means PS1."""
    m = _BOOT.search(data)
    if not m:
        return None
    serial = f"{m.group(2).decode().upper()}-{m.group(3).decode()}{m.group(4).decode()}"
    return ("PS2" if m.group(1) else "PS1"), serial


def parse_sfo(data: bytes) -> dict[str, str]:
    """PARAM.SFO (PSP / PS3 / Vita) as {key: value} for its text entries."""
    if data[:4] != b"\0PSF":
        return {}
    keys_at, data_at, count = struct.unpack_from("<III", data, 8)
    out = {}
    for i in range(count):
        key_off, fmt, length, _max, data_off = struct.unpack_from("<HHIII", data, 20 + i * 16)
        key = data[keys_at + key_off:].split(b"\0", 1)[0].decode("ascii", "replace")
        if fmt in (0x0204, 0x0004):  # utf-8 text
            raw = data[data_at + data_off:data_at + data_off + length]
            out[key] = raw.split(b"\0", 1)[0].decode("utf-8", "replace").strip()
    return out


def _nintendo(head: bytes) -> DiscInfo | None:
    if head[:4] == b"WBFS":
        head = head[0x200:]
    plat = ("Wii" if head[0x18:0x1C] == b"\x5D\x1C\x9E\xA3"
            else "GameCube" if head[0x1C:0x20] == b"\xC2\x33\x9F\x3D" else None)
    if not plat:
        return None
    title = head[0x20:0x80].split(b"\0", 1)[0].decode("latin-1").strip()
    return DiscInfo(plat, head[:6].decode("ascii", "replace"), title or None)


# --------------------------------------------------------------------------- entry point

def identify(path: str, name_lookup=None) -> DiscInfo | None:
    """What the disc image at ``path`` (or the image inside that .zip) says about itself.
    ``name_lookup(serial)`` names PS2 discs (defaults to PCSX2's GameIndex)."""
    try:
        f, _size = _open(path)
    except (OSError, zipfile.BadZipFile, NotImplementedError) as e:
        log.debug("can't open %s: %s", path, e)
        return None
    if f is None:
        return None
    try:
        with f:
            head = f.read(0x300)
            found = _nintendo(head)
            if found:
                return found
            disc = _iso(f)
            if not disc:
                return None
            for where, plat in (("PSP_GAME/PARAM.SFO", "PSP"), ("PS3_GAME/PARAM.SFO", "PS3")):
                sfo = disc.find(where)
                if sfo:
                    p = parse_sfo(sfo)
                    return DiscInfo(plat, p.get("DISC_ID") or p.get("TITLE_ID"), p.get("TITLE"))
            cnf = disc.find("SYSTEM.CNF")
            if cnf:
                found = parse_system_cnf(cnf)
                if found:
                    plat, serial = found
                    title = (name_lookup or ps2_name)(serial) if plat == "PS2" else None
                    return DiscInfo(plat, serial, title)
    except (OSError, ValueError, struct.error, zipfile.BadZipFile, EOFError) as e:
        log.debug("can't read disc %s: %s", path, e)
    return None


def ps2_name(serial: str) -> str | None:
    """English name of a PS2 serial from PCSX2's GameIndex (cached for 90 days)."""
    from ..providers.base import cached_json, session

    def fetch():
        text = session().get(GAMEINDEX_URL, timeout=60).text
        names, cur = {}, None
        for line in text.splitlines():
            if line and not line[0].isspace() and line.endswith(":"):
                cur = line[:-1]
            elif cur and line.startswith(("  name-en:", "  name:")):
                value = line.split(":", 1)[1].strip().strip('"')
                if line.startswith("  name-en:") or cur not in names:
                    names[cur] = value
        return names

    try:
        return cached_json("pcsx2", "gameindex", 90, fetch).get(serial)
    except Exception as e:  # offline: the serial alone still identifies the platform
        log.debug("PCSX2 GameIndex unavailable: %s", e)
        return None
