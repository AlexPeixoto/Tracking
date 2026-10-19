"""Read video durations from file headers with MediaInfo (bundled with pymediainfo on Windows)."""

from __future__ import annotations


def probe(path: str) -> float | None:
    """Duration in seconds, or None if the file can't be read."""
    try:
        from pymediainfo import MediaInfo

        info = MediaInfo.parse(path, parse_speed=0)
    except Exception:
        return None
    for track in info.tracks:
        if track.track_type == "General" and track.duration:
            return float(track.duration) / 1000.0
    for track in info.tracks:
        if track.track_type == "Video" and track.duration:
            return float(track.duration) / 1000.0
    return None
