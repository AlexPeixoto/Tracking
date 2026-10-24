"""Identify drives by a stable id instead of a drive letter.

Windows
    * Local volumes are keyed by their volume GUID (``guid:{...}``). We also keep
      the filesystem serial number so the same disk is recognised when plugged
      into another PC (where it gets a different GUID).
    * Volumes can be read through ``\\\\?\\Volume{GUID}\\`` even when they have
      no drive letter at all.
    * Mapped network drives are keyed by their UNC share (``unc:\\\\nas\\media``).
Linux
    * Keyed by filesystem UUID from ``/dev/disk/by-uuid``.
Elsewhere
    * Falls back to the mount point.
"""

from __future__ import annotations

import os
import shutil
import string
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Volume:
    uid: str
    serial: str | None
    label: str
    mount: str  # how to reach it right now, e.g. 'E:\\' or '\\\\?\\Volume{...}\\'


@dataclass(frozen=True)
class Location:
    volume: Volume
    rel_path: str  # forward slashes, no leading slash; '' for the volume root


def _rel(path: str, mount: str) -> str:
    rel = os.path.relpath(path, mount) if os.path.normcase(path) != os.path.normcase(mount) else ""
    rel = "" if rel == "." else rel
    return rel.replace("\\", "/").strip("/")


def join(mount: str, rel_path: str) -> str:
    return os.path.join(mount, *rel_path.split("/")) if rel_path else mount


# --------------------------------------------------------------------------- Windows

if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _mpr = ctypes.WinDLL("mpr")
    _BUF = 1024

    def _volume_path_name(path: str) -> str:
        buf = ctypes.create_unicode_buffer(_BUF)
        if not _k32.GetVolumePathNameW(path, buf, _BUF):
            raise ctypes.WinError(ctypes.get_last_error())
        return buf.value

    def _volume_guid(mount: str) -> str | None:
        buf = ctypes.create_unicode_buffer(_BUF)
        if _k32.GetVolumeNameForVolumeMountPointW(mount, buf, _BUF):
            return buf.value  # '\\?\Volume{...}\'
        return None

    def _volume_info(root: str) -> tuple[str, str | None]:
        label = ctypes.create_unicode_buffer(_BUF)
        fs = ctypes.create_unicode_buffer(_BUF)
        serial = wintypes.DWORD()
        if _k32.GetVolumeInformationW(root, label, _BUF, ctypes.byref(serial), None, None, fs, _BUF):
            s = serial.value
            return label.value, f"{s >> 16:04X}-{s & 0xFFFF:04X}"
        return "", None

    def _unc_for_drive(drive: str) -> str | None:
        buf = ctypes.create_unicode_buffer(_BUF)
        size = wintypes.DWORD(_BUF)
        if _mpr.WNetGetConnectionW(drive, buf, ctypes.byref(size)) == 0:
            return buf.value
        return None

    def _mount_points(guid_path: str) -> list[str]:
        buf = ctypes.create_unicode_buffer(_BUF)
        needed = wintypes.DWORD()
        if not _k32.GetVolumePathNamesForVolumeNameW(guid_path, buf, _BUF, ctypes.byref(needed)):
            return []
        raw = ctypes.wstring_at(ctypes.addressof(buf), needed.value)
        return [p for p in raw.split("\0") if p]

    def _guid_volumes() -> list[str]:
        buf = ctypes.create_unicode_buffer(_BUF)
        _k32.FindFirstVolumeW.restype = wintypes.HANDLE
        h = _k32.FindFirstVolumeW(buf, _BUF)
        if h in (None, wintypes.HANDLE(-1).value):
            return []
        out = [buf.value]
        try:
            while _k32.FindNextVolumeW(wintypes.HANDLE(h), buf, _BUF):
                out.append(buf.value)
        finally:
            _k32.FindVolumeClose(wintypes.HANDLE(h))
        return out

    def _volume_from_guid(guid_path: str) -> Volume:
        label, serial = _volume_info(guid_path)
        mounts = _mount_points(guid_path)
        guid = guid_path[guid_path.index("{"): guid_path.index("}") + 1]
        return Volume(uid=f"guid:{guid}", serial=serial, label=label,
                      mount=mounts[0] if mounts else guid_path)

    def list_volumes() -> list[Volume]:
        vols = []
        for g in _guid_volumes():
            try:
                vols.append(_volume_from_guid(g))
            except (OSError, ValueError):
                pass
        for letter in string.ascii_uppercase:
            unc = _unc_for_drive(f"{letter}:")
            if unc:
                label, serial = _volume_info(f"{letter}:\\")
                vols.append(Volume(uid=f"unc:{unc.lower()}", serial=serial,
                                   label=label or unc, mount=f"{letter}:\\"))
        return vols

    def identify(path: str | os.PathLike) -> Location:
        path = os.path.abspath(path)
        if path.startswith("\\\\") and not path.startswith("\\\\?\\"):
            # Plain UNC path: \\server\share\rest
            parts = path.lstrip("\\").split("\\")
            share = "\\\\" + "\\".join(parts[:2])
            label, serial = _volume_info(share + "\\")
            vol = Volume(uid=f"unc:{share.lower()}", serial=serial, label=label or share,
                         mount=share + "\\")
            return Location(vol, "/".join(parts[2:]))

        drive = os.path.splitdrive(path)[0]
        unc = _unc_for_drive(drive) if drive else None
        if unc:
            label, serial = _volume_info(drive + "\\")
            vol = Volume(uid=f"unc:{unc.lower()}", serial=serial, label=label or unc,
                         mount=drive + "\\")
            return Location(vol, _rel(path, vol.mount))

        mount = _volume_path_name(path)
        guid_path = _volume_guid(mount)
        if guid_path:
            v = _volume_from_guid(guid_path)
            vol = Volume(v.uid, v.serial, v.label, mount)
        else:
            label, serial = _volume_info(mount)
            vol = Volume(uid=f"serial:{serial}", serial=serial, label=label, mount=mount)
        return Location(vol, _rel(path, mount))

# --------------------------------------------------------------------------- POSIX

else:

    def _mount_of(path: str) -> str:
        path = os.path.realpath(path)
        while not os.path.ismount(path):
            path = os.path.dirname(path)
        return path

    def _uuid_by_device() -> dict[str, str]:
        out = {}
        by_uuid = Path("/dev/disk/by-uuid")
        if by_uuid.is_dir():
            for link in by_uuid.iterdir():
                out[os.path.realpath(link)] = link.name
        return out

    def _mounts() -> list[tuple[str, str]]:
        try:
            with open("/proc/mounts", encoding="utf-8") as f:
                return [(ln.split()[0], ln.split()[1].replace("\\040", " ")) for ln in f]
        except OSError:
            return []

    def _volume_for_mount(mount: str) -> Volume:
        uuids = _uuid_by_device()
        for dev, mnt in _mounts():
            if mnt == mount and os.path.realpath(dev) in uuids:
                u = uuids[os.path.realpath(dev)]
                return Volume(uid=f"uuid:{u}", serial=u, label=os.path.basename(mount) or "/",
                              mount=mount)
        return Volume(uid=f"mount:{mount}", serial=None, label=os.path.basename(mount) or "/",
                      mount=mount)

    def list_volumes() -> list[Volume]:
        return [_volume_for_mount(m) for _, m in _mounts() if m.startswith(("/", "/media", "/mnt"))]

    def identify(path: str | os.PathLike) -> Location:
        path = os.path.realpath(path)
        vol = _volume_for_mount(_mount_of(path))
        return Location(vol, _rel(path, vol.mount))


# --------------------------------------------------------------------------- common

SYSTEM_PARTITION_BYTES = 2 * 1024**3


def size_of(v: Volume) -> int | None:
    try:
        return shutil.disk_usage(v.mount).total
    except OSError:
        return None


def is_system_partition(v: Volume, size: int | None = None) -> bool:
    """Windows' hidden boot (EFI) / recovery partitions: no drive letter, no label and
    small. Not somewhere you keep a library, so the drive list leaves them out."""
    if not v.mount.startswith("\\\\?\\") or v.label:
        return False
    size = size_of(v) if size is None else size
    return size is not None and size < SYSTEM_PARTITION_BYTES

def resolve(uid: str, serial: str | None = None, volumes: list[Volume] | None = None) -> Volume | None:
    """Find where a known volume is mounted right now, or None if it's offline."""
    vols = volumes if volumes is not None else list_volumes()
    for v in vols:
        if v.uid == uid:
            return v
    if serial:
        # Same filesystem, different machine / different GUID.
        for v in vols:
            if v.serial == serial and not v.uid.startswith("unc:"):
                return v
    if uid.startswith("unc:") and os.path.isdir(uid[4:]):
        share = uid[4:]
        return Volume(uid, serial, share, share + os.sep)
    return None
