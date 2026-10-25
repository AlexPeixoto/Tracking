"""What a video folder's item is called: its folder's name or its files' show name?

Release folders and their files disagree all the time, and each side is right sometimes:

    Season 2/                             -> the files ("Mahouka ... S01E01.mkv")
    1a. MSG 0079/  (in a collection pack) -> the files ("Mobile Suit GUNDAM 0079 - 01.mkv")
    05 - Nekomonogatari (White)/          -> the folder: every sibling's files say
                                             "Monogatari Series Second Season"
    Hare + Guu/DELUXE/                    -> both: "Hare + Guu DELUXE"
    86 (Eighty Six)/[Judas] 86 - 01.mkv   -> the folder ("86" really is the name)
    4b. OVA (2005)/01.mkv                 -> the collection above ("OVA" names nothing)

``show_title`` makes that choice in those steps, in that order; ``group_title`` does the
same for one of several shows / seasons found in one folder; ``unique_slugs`` gives each
of those its own item path. Every case above has a test (tests/test_scanner.py).
"""

from __future__ import annotations

import os

from .. import titles
from . import fs
from .classify import VIDEO_EXT
from .videos import _Group, is_extra_file


def show_title(name: str, dir_abs: str, dir_rel: str, folder: titles.VideoInfo, g: _Group | None) -> str:
    """The title of a folder holding one show / season / movie (``g``: its video group)."""
    # 1. A folder named "Season 2" (or just a number, "OVA") says nothing about the show.
    weak = titles.season_from_dir(name) is not None or titles.is_weak_title(folder.title)
    use_files = g is not None and _files_name_it(name, dir_abs, folder, g, weak)
    base = g.title if use_files else folder.title
    # 2. "Hare + Guu/DELUXE": a part of the show the parent folder names.
    if g and not use_files and not weak and part_of_parent(g.title, folder.title, dir_rel):
        base = f"{g.title} {folder.title}"
    # 3. "86 (Eighty Six)" holding "86 - 01.mkv": the same name, written the folder's way.
    if g and titles.normalize(g.title) and titles.normalize(g.title) == titles.normalize(folder.title):
        base = folder.title
    # 4. Still no real name ("01.mkv" in "4b. OVA (2005)"): the collection's.
    elif titles.is_weak_title(base):
        base = collection_title(base, dir_rel)
    return base


def _files_name_it(name: str, dir_abs: str, folder: titles.VideoInfo, g: _Group, weak: bool) -> bool:
    """Should the files' show name win over the folder's?"""
    if titles.is_weak_title(g.title):
        return False
    if weak:
        return True
    # Inside a numbered collection pack ("1a. MSG 0079"), a longer name from the files is
    # usually the full one - unless every sibling's files share it (a generic series name).
    in_pack = titles.strip_order_prefix(name) != name
    return (in_pack and len(g.title) > len(folder.title)
            and (abbreviated(folder.title) or not shared_by_siblings(dir_abs, g.title)))


def group_title(g: _Group, folder: titles.VideoInfo, dir_rel: str) -> str:
    """The title of one of several shows / seasons / movies found in one folder."""
    if titles.is_weak_title(g.title):
        return folder.title if not titles.is_weak_title(folder.title) else collection_title(g.title, dir_rel)
    if part_of_parent(g.title, folder.title, dir_rel):
        return f"{g.title} {folder.title}"
    return g.title


def unique_slugs(groups: list[_Group]) -> dict[int, str]:
    """Each group's item path suffix (by ``id(group)``). "Sekirei" S1 and "Sekirei Pure
    Engagement" S1 side by side are both "s1", and the second would overwrite the first
    when saved: the biggest group keeps the plain one, the others get their title added."""
    slugs: dict[int, str] = {}
    for g in sorted(groups, key=lambda g: -len(g.files)):
        slug = g.slug
        if slug in slugs.values():
            slug = f"{slug}-{titles.normalize(g.title)[:40]}"
        n = 2
        while slug in slugs.values():
            slug, n = f"{g.slug}-{n}", n + 1
        slugs[id(g)] = slug
    return slugs


# --------------------------------------------------------------------------- the checks

def part_of_parent(files_title: str, folder_title: str, dir_rel: str) -> bool:
    """ "Hare + Guu/DELUXE" holding "Hare + Guu DELUXE Ep1.1.mkv": the folder is a part of the
    show its parent names, so the item is "Hare + Guu DELUXE", not "DELUXE" or "Hare + Guu"."""
    parts = dir_rel.split("/")
    if len(parts) < 2 or titles.is_weak_title(folder_title):
        return False
    parent = titles.parse_video(titles.strip_order_prefix(parts[-2]), keep_plus=True).title
    show = titles.normalize(files_title)
    return bool(show) and show == titles.normalize(parent) and show not in titles.normalize(folder_title)


def abbreviated(title: str) -> bool:
    """ "FF Extras", "KoF - Destiny", "SamSho", "MSG 0079": a short word with 2+ capitals."""
    return any(len(w) <= 6 and sum(ch.isupper() for ch in w) >= 2
               for w in title.replace(".", "").split())


def _first_video_title(dir_abs: str) -> str | None:
    try:
        for e in sorted(fs.current().scandir(dir_abs), key=lambda e: e.name.lower()):
            is_video = os.path.splitext(e.name)[1].lower() in VIDEO_EXT
            if e.is_file(follow_symlinks=False) and is_video and not is_extra_file(e.name):
                return titles.normalize(titles.parse_video(e.name).title)
    except OSError:
        pass
    return None


def shared_by_siblings(dir_abs: str, files_title: str) -> bool:
    """Do other folders next to this one hold files with the same show name?"""
    parent, me = os.path.dirname(dir_abs), os.path.basename(dir_abs)
    want = titles.normalize(files_title)
    try:
        siblings = [e for e in fs.current().scandir(parent)
                    if e.is_dir(follow_symlinks=False) and e.name != me]
    except OSError:
        return False
    return any(_first_video_title(e.path) == want for e in siblings)


def collection_title(fallback: str, dir_rel: str) -> str:
    """The nearest folder above ``dir_rel`` with a real name ("1b" / "OVA" don't count)."""
    for part in reversed(dir_rel.split("/")[:-1]):
        t = titles.parse_video(titles.strip_order_prefix(part)).title
        if not titles.is_weak_title(t):
            return t
    return fallback
