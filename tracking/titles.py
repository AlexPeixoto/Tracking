"""Turn messy folder / file names into searchable titles."""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache

log = logging.getLogger(__name__)

_BRACKETS = re.compile(r"\[[^\]]*\]|\{[^}]*\}")
_PARENS = re.compile(r"\([^)]*\)")
_SPACES = re.compile(r"\s+")
_YEAR = re.compile(r"(?<!\d)(19[3-9]\d|20[0-4]\d)(?!\d)")

# Release-group suffix: "Some.Game-CODEX". Only stripped after a hyphen at the
# end so real titles like "Rune" or "Empress" survive.
_SCENE_SUFFIX = re.compile(
    r"(?i)[-.](codex|plaza|cpy|skidrow|reloaded|razor1911|flt|hoodlum|tenoki|rune|"
    r"empress|tinyiso|darksiders|gog|fitgirl|dodi|elamigos|steamrip|p2p|doge)\s*$"
)
# Version / dump tags that show up in game names outside brackets.
_GAME_JUNK = re.compile(
    r"""(?ix)\b(
        v+\d+(\.\d+)+[a-z]?|build\s*\d+|rev\s*\d+|readnfo|
        repack|multi\d+|proper|ntsc(-[uj])?|
        disc\s*\d+(\s*of\s*\d+)?|cd\s*\d+|track\s*\d+|
        sl[ue]s[-_ ]?\d+|scus[-_ ]?\d+|sces[-_ ]?\d+|blus\d+|bles\d+|bca[su]\d+|npu[ab]\d+
    )\b""",
)


def _tidy(s: str) -> str:
    s = s.replace("_", " ")
    # Dots as separators (Some.Movie.2010) but keep things like "Vol. 2" readable.
    if "." in s and " " not in s.strip():
        s = s.replace(".", " ")
    s = _SPACES.sub(" ", s)
    return s.strip(" -.,")


def strip_ext(name: str) -> str:
    return re.sub(r"\.[A-Za-z0-9]{2,4}$", "", name)


# "www.UIndex.org  -  Superman (2025)...", "Mark_of_the_Deep_--_fitgirl-repacks.site"
_SITE = re.compile(
    r"(?i)(?:^\s*(?:www\.)?[\w-]+\.(?:org|com|net|to|site|re|mx|ws|cc|io|info)\s*-+\s*"
    r"|[\s_-]*-+[\s_]*(?:www\.)?[\w-]+\.(?:org|com|net|to|site|re|mx|ws|cc|io|info)\s*$)")
_PART = re.compile(r"(?i)\.part\d+$")
# GOG-Games.to naming: "game-chains.of.freedom-(81127)"
_GOG_GAMES = re.compile(r"(?i)^game-(.+?)-\(\d+\)(?:\(\d+\))?$")
# Everything from an update / patch / DLC marker onwards.
ADDON_RE = re.compile(
    r"(?i)(?:^|[\s._\-(\[])(?:update|patch|hotfix|dlc|unlocker|crack(?:fix)?|fix)(?:$|[\s._\-)\]]).*$")


def strip_site(name: str) -> str:
    return _SITE.sub(" ", name).strip()


def is_addon_name(name: str) -> bool:
    """Updates, patches and DLC packs: "X.Update.v1.2-RUNE", "X_Patch_2-ElAmigos",
    "X DLC Pack [5 DLCs]", "X.DLC.Unlocker-RUNE"."""
    return bool(ADDON_RE.search(_PART.sub("", name)))


def addon_base_title(name: str) -> str:
    """The game an update / DLC belongs to: "The_Alters_Update_from_v1.0..." -> "The Alters"."""
    return clean_game_title(ADDON_RE.sub("", _PART.sub("", name)))


def clean_game_title(name: str) -> str:
    """``name`` is a folder name or a file stem (extension already removed)."""
    name = _PART.sub("", strip_site(name))
    gog = _GOG_GAMES.match(name)
    if gog:
        name = re.sub(r"(?i)\.game$", "", gog.group(1))
    s = _SCENE_SUFFIX.sub("", name)
    # "..._USA_READNFO_PSP-PLAYASiA": region + platform + group glued on with underscores
    s = re.sub(r"(?i)_(?:USA|EUR|JPN|PAL|NTSC)(?:_.*)?$", "", s)
    # Disc / store serials: "Folklore_BCES00050", "[NPEB-00553]" (brackets go anyway)
    s = re.sub(r"(?i)(?<![A-Za-z0-9])(?:B[CL][EUAJK][SDM]|NP[EUJHAK][A-Z]|U[CL][EUJAK][SMD]|CUSA|PPSA|"
               r"S[CL][EUPAKC][SMD])[-_ ]?\d{3}\.?\d{2}(?!\d)", " ", s)
    s = _GAME_JUNK.sub(" ", s)
    s = _BRACKETS.sub(" ", s)
    s = _PARENS.sub(" ", s)
    s = _tidy(s)
    s = re.sub(r"[-+]\s*$", "", _SPACES.sub(" ", s)).strip(" -.,+")
    return smart_title(s) if s else name


_SMALL_WORDS = {"a", "an", "and", "as", "at", "but", "by", "for", "in", "of", "on", "or", "the",
                "to", "vs", "with"}
_ROMAN = re.compile(r"(?i)^(?=[ivxlc]+$)c{0,3}(xc|xl|l?x{0,3})(ix|iv|v?i{0,3})$")


def smart_title(s: str) -> str:
    """Title-case names that arrive all lowercase ("chains of freedom" -> "Chains of Freedom",
    "breath of fire iv" -> "Breath of Fire IV"). Anything with capitals is left alone."""
    if not s.islower():
        return s
    words = s.split()
    out = []
    for i, w in enumerate(words):
        if _ROMAN.match(w):
            out.append(w.upper())
        elif i and w in _SMALL_WORDS:
            out.append(w)
        else:
            out.append(w[:1].upper() + w[1:])
    return " ".join(out)


@dataclass(frozen=True)
class VideoInfo:
    title: str
    year: int | None
    season: int | None
    episode: int | None
    kind: str  # 'series' (has episode / season numbers) or 'movie'


_SEASON_DIR = re.compile(
    r"(?i)^(?:season|series|s)\s*0*(\d{1,2})$|\b0*(\d{1,2})(?:st|nd|rd|th)\s+season\b|\bseason\s*0*(\d{1,2})\b")


def season_from_dir(name: str) -> int | None:
    """"Season 2", "S02", "2nd Season", "Show Season 3" -> season number."""
    m = _SEASON_DIR.search(name.strip())
    if not m:
        return None
    return int(next(g for g in m.groups() if g))


def _as_int(v) -> int | None:
    if isinstance(v, list):
        v = v[0] if v else None
    return v if isinstance(v, int) else None


@lru_cache(maxsize=8192)
def parse_video(name: str) -> VideoInfo:
    """Title / year / season / episode from a release name (file or folder).

    "[naiyas] Vampire Hunter D - Bloodlust (2000) (BD1080p...).mkv" ->
    movie "Vampire Hunter D: Bloodlust" (2000);
    "[SubsMix] Ghost in the Shell SAC 1st GIG - S01E05 - Decoy.mkv" -> series S1 E5.
    """
    try:
        from guessit import guessit  # slow import, keep it lazy

        g = guessit(strip_site(name))
        title = str(g.get("title") or "").strip()
        if title:
            episode = _as_int(g.get("episode")) or _as_int(g.get("absolute_episode"))
            season = _as_int(g.get("season"))
            # Not guessit's "type": it calls any fansub release (CRC tag) an episode,
            # even a movie like "[Arid] Redline [...] [943DC65B].mkv".
            is_series = episode is not None or season is not None
            if not is_series and g.get("part") is not None:
                part = re.search(r"(?i)\bpart[\s._]+([a-z]+|\d+)\b", name)
                if part and part.group(0).lower() not in title.lower():
                    title = f"{title} Part {part.group(1).capitalize()}"
            alt = g.get("alternative_title")
            # "Vampire Hunter D - Bloodlust": guessit splits the subtitle off movies.
            if not is_series and isinstance(alt, str) and re.search(r"[A-Za-z]{3}", alt) and len(alt) < 50:
                title = f"{title}: {alt}"
            return VideoInfo(title, _as_int(g.get("year")), season, episode,
                             "series" if is_series else "movie")
    except Exception as e:  # fall back to the simple parser below, but say so
        log.debug("guessit failed on %r: %s", name, e)
    s = strip_ext(name)
    m = _YEAR.search(s)
    year = int(m.group(1)) if m else None
    s = _BRACKETS.sub(" ", s)
    s = _PARENS.sub(" ", s)
    if m:
        s = s.split(m.group(1))[0]
    return VideoInfo(_tidy(s) or name, year, None, None, "movie")


def parse_video_name(name: str) -> tuple[str, int | None]:
    """Return (title, year) for a movie or anime folder / file name."""
    v = parse_video(name)
    return v.title, v.year


def normalize(title: str) -> str:
    """Lowercase alphanumeric key used to compare titles ("Ōkami" -> "okami")."""
    ascii_ = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    return re.sub(r"[^0-9a-z]+", "", ascii_.lower())
