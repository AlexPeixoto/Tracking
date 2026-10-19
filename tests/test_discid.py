"""Reading a game disc's identity (SYSTEM.CNF / PARAM.SFO / Nintendo header)."""

import struct
import zipfile
from pathlib import Path

from tracking.scan import discid
from tracking.scan.classify import Hints


def _record(name: bytes, lba: int, size: int, is_dir: bool = False) -> bytes:
    rec = bytearray(33 + len(name) + (len(name) + 1) % 2)
    rec[0] = len(rec)
    struct.pack_into("<I", rec, 2, lba)
    struct.pack_into("<I", rec, 10, size)
    rec[25] = 2 if is_dir else 0
    rec[32] = len(name)
    rec[33:33 + len(name)] = name
    return bytes(rec)


def make_iso(files: dict[str, bytes]) -> bytes:
    """A tiny ISO9660 image with ``files`` ("SYSTEM.CNF", "PSP_GAME/PARAM.SFO")."""
    sectors: dict[int, bytes] = {}
    root, subdirs, next_lba = [], {}, 20
    for path, data in files.items():
        parts = path.split("/")
        sectors[next_lba] = data
        rec = _record(parts[-1].encode() + b";1", next_lba, len(data))
        next_lba += 1
        if len(parts) == 1:
            root.append(rec)
        else:
            subdirs.setdefault(parts[0], []).append(rec)
    for name, recs in subdirs.items():
        sectors[next_lba] = b"".join(recs)
        root.append(_record(name.encode(), next_lba, 2048, is_dir=True))
        next_lba += 1
    sectors[18] = b"".join(root)
    pvd = bytearray(2048)
    pvd[0:6] = b"\x01CD001"
    pvd[156:156 + 34] = _record(b"\0", 18, 2048, is_dir=True)[:34]
    sectors[16] = bytes(pvd)
    out = bytearray(2048 * next_lba)
    for lba, data in sectors.items():
        out[lba * 2048:lba * 2048 + len(data)] = data
    return bytes(out)


def make_sfo(entries: dict[str, str]) -> bytes:
    keys = b"".join(k.encode() + b"\0" for k in entries)
    values = [v.encode() + b"\0" for v in entries.values()]
    keys_at = 20 + 16 * len(entries)
    data_at = keys_at + len(keys) + (-len(keys)) % 4
    head = b"\0PSF" + struct.pack("<IIII", 0x101, keys_at, data_at, len(entries))
    table, key_off, data_off = b"", 0, 0
    for k, v in zip(entries, values):
        table += struct.pack("<HHIII", key_off, 0x0204, len(v), len(v), data_off)
        key_off += len(k) + 1
        data_off += len(v)
    return head + table + keys + b"\0" * ((-len(keys)) % 4) + b"".join(values)


def test_ps2_serial_and_name(tmp_path: Path):
    iso = tmp_path / "copy.iso"
    iso.write_bytes(make_iso({"SYSTEM.CNF": b"BOOT2 = cdrom0:\\SLUS_212.21;1\r\nVER = 1.00\r\n"}))
    info = discid.identify(str(iso), name_lookup={"SLUS-21221": "Okami"}.get)
    assert info == discid.DiscInfo("PS2", "SLUS-21221", "Okami")


def test_ps1_inside_zip(tmp_path: Path):
    z = tmp_path / "game.zip"
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as f:
        f.writestr("game.iso", make_iso({"SYSTEM.CNF": b"BOOT = cdrom:\\SCUS_944.55;1\r\n"}))
    assert discid.identify(str(z)) == discid.DiscInfo("PS1", "SCUS-94455", None)


def test_psp_param_sfo(tmp_path: Path):
    iso = tmp_path / "x.iso"
    sfo = make_sfo({"DISC_ID": "ULUS10566", "TITLE": "Afro Samurai"})
    iso.write_bytes(make_iso({"PSP_GAME/PARAM.SFO": sfo}))
    assert discid.identify(str(iso)) == discid.DiscInfo("PSP", "ULUS10566", "Afro Samurai")


def test_wii_header(tmp_path: Path):
    head = bytearray(0x400)
    head[0:6] = b"SOUE01"
    head[0x18:0x1C] = b"\x5D\x1C\x9E\xA3"
    head[0x20:0x20 + 26] = b"Zelda: Skyward Sword\0\0\0\0\0\0"
    (tmp_path / "x.iso").write_bytes(bytes(head))
    assert discid.identify(str(tmp_path / "x.iso")) == discid.DiscInfo("Wii", "SOUE01", "Zelda: Skyward Sword")


def test_scan_names_a_nameless_disc(tmp_path: Path, monkeypatch):
    """A "copy" folder / an image with no platform words: the disc says what it is."""
    from tracking import scan

    monkeypatch.setattr(discid, "ps2_name", {"SLUS-21221": "Okami"}.get)
    game = tmp_path / "Games" / "copy"
    game.mkdir(parents=True)
    (game / "image.iso").write_bytes(make_iso({"SYSTEM.CNF": b"BOOT2 = cdrom0:\\SLUS_212.21;1\r\n"}))
    items = scan.collect(str(tmp_path), "", Hints())
    assert [(i.title, i.platform) for i in items] == [("Okami", "PS2")]
    assert "SLUS-21221" in items[0].reason
