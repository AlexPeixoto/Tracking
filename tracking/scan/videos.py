"""Video grouping: series seasons vs movies.

  * Every video file is parsed for title, season and episode. Files without
    episode numbers are movies and are never merged with a series.
  * One group per (series, season): "Season 1" + "Season 2" folders, or S01Exx +
    S02Exx files, become separate items; specials (season 0) join season 1.
  * Extras (NCOP / NCED / menus / trailers...) are not counted as episodes.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from rapidfuzz import fuzz

from .. import titles
from .model import FileEntry

EXTRA_DIRS = {
    "extras", "extra", "bonus", "bonuses", "nc", "ncop", "nced", "ncop & nced", "creditless",
    "menu", "menus", "trailers", "trailer", "pv", "cm", "cms", "previews", "sample", "samples",
    "featurettes", "behind the scenes", "deleted scenes", "interviews", "scans", "fonts",
    "subs", "subtitles", "ost", "soundtrack", "other", "others",
}


def is_extra_path(inner_rel: str) -> bool:
    parts = inner_rel.lower().split("/")[:-1]
    return any(p in EXTRA_DIRS or p.startswith(("nc ", "extras", "bonus")) for p in parts)


@dataclass
class _Group:
    kind: str
    title: str
    season: int | None
    year: int | None
    files: list[tuple[FileEntry, str]] = field(default_factory=list)  # (entry, inner rel)

    @property
    def key(self) -> str:
        if self.kind == "series":
            return f"{titles.normalize(self.title)}|s{self.season or 1}"
        return f"{titles.normalize(self.title)}|m{self.year or ''}"

    @property
    def slug(self) -> str:
        if self.kind == "series":
            return f"s{self.season or 1}"
        return f"movie-{titles.normalize(self.title)[:40]}"


def _video_info(inner_rel: str) -> titles.VideoInfo:
    name = inner_rel.rsplit("/", 1)[-1]
    info = titles.parse_video(name)
    if info.season is None:
        parent = inner_rel.rsplit("/", 2)[-2] if "/" in inner_rel else ""
        season = titles.season_from_dir(parent) if parent else None
        if season is not None:
            info = titles.VideoInfo(info.title, info.year, season, info.episode, "series")
    return info


def group_videos(videos: list[tuple[FileEntry, str]]) -> list[_Group]:
    """Split video files into series seasons and movies."""
    groups: list[_Group] = []
    for entry, inner in videos:
        info = _video_info(inner)
        season = (info.season or 1) if info.kind == "series" else None
        entry.season, entry.episode = season, info.episode
        target = None
        for g in groups:
            if g.kind != info.kind or (info.kind == "series" and g.season != season):
                continue
            same = titles.normalize(g.title) == titles.normalize(info.title)
            if same or fuzz.ratio(titles.normalize(g.title), titles.normalize(info.title)) >= 90:
                target = g
                break
        if target is None:
            target = _Group(info.kind, info.title, season, info.year)
            groups.append(target)
        target.files.append((entry, inner))
    # Season 0 / specials ride along with the first season of the same show.
    for g in [g for g in groups if g.kind == "series" and g.season == 0]:
        host = next((h for h in groups if h is not g and h.kind == "series" and h.season
                     and titles.normalize(h.title) == titles.normalize(g.title)), None)
        if host:
            host.files += g.files
            groups.remove(g)
    groups.sort(key=lambda g: (g.kind != "series", g.season or 0, g.title))
    return groups


def season_tag(season: int | None) -> str:
    return f" S{season}" if season else ""


def display_title(base: str, kind: str | None, season: int | None) -> str:
    if kind == "series" and season and season > 1 and titles.season_from_dir(base) is None:
        return f"{base} Season {season}"
    return base
