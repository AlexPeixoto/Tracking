"""Turn messy folder / file names into searchable titles."""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache

log = logging.getLogger(__name__)

_BRACKETS = re.compile(r"\[[^\]]*\]|\{[^}]*\}")
_YEAR_RANGE = re.compile(r"\((19\d\d|20\d\d)\s*-\s*(?:\d{2}|\d{4})\)")
_DOTTED = re.compile(r"\b(?:[A-Z]\.){2,}[A-Z]?\.?")
_LEAD_EPISODE = re.compile(
    r"(?i)^(?:s(\d{1,2})\s*e(\d{1,4})|(?:episode|ep\.?)\s*(\d{1,4}))(?:v\d)?(?:\s*[-_.:]|\s|$)")
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


# Scene release names glue tags on with dots: "Disgaea.2.Cursed.Memories.USA.PS2.DVD-WaLMaRT".
# Everything from the first of these tags on is not part of the title.
_SCENE_TAGS = {
    "usa", "us", "eur", "eu", "pal", "ntsc", "ntscu", "ntscj", "jpn", "jap", "jp", "uk", "ger", "fra",
    "ps1", "psx", "ps2", "ps3", "ps4", "ps5", "psp", "psv", "vita", "xbox", "x360", "xbox360",
    "xboxone", "nsw", "switch", "wii", "wiiu", "ngc", "gc", "nds", "3ds", "gba", "dc", "pc",
    "dvd", "dvd5", "dvd9", "cd", "iso", "rip", "repack", "proper", "readnfo", "internal",
    "multi", "multi2", "multi3", "multi5", "en", "eng", "nsp", "xci", "cia", "update", "dlc",
}


def _cut_scene_tags(name: str) -> str:
    """Only for dot-separated scene names (no spaces), so a real title word like
    "Wii" in "Wii Sports" or "PC" in "Disgaea 2 PC" is never cut."""
    if " " in name.strip() or name.count(".") < 2:
        return name
    words = re.sub(r"-[A-Za-z0-9]+$", "", name).split(".")   # drop the "-GROUP" tail
    for i, w in enumerate(words[1:], 1):
        if w.lower() in _SCENE_TAGS or re.fullmatch(r"(?i)multi\d*|v\d+(\.\d+)*", w):
            return ".".join(words[:i])
    return ".".join(words)


def clean_game_title(name: str) -> str:
    """``name`` is a folder name or a file stem (extension already removed)."""
    name = _PART.sub("", strip_site(name))
    gog = _GOG_GAMES.match(name)
    if gog:
        name = re.sub(r"(?i)\.game$", "", gog.group(1))
    name = _cut_scene_tags(name)
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
    return front_article(smart_title(s)) if s else name


# "Incredible Hulk, The" / "Lord of the Rings, The - The Third Age" (No-Intro / library
# sort order): the article goes back in front, where every store and database has it.
_TRAILING_ARTICLE = re.compile(r"^(?P<head>[^,]+?),\s+(?P<art>The|A|An)(?P<rest>(?:\s+[-:(].*)?)$")


def front_article(title: str) -> str:
    m = _TRAILING_ARTICLE.match(title)
    if not m:
        return title
    return f"{m.group('art')} {m.group('head')}{m.group('rest')}"


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
    # "01.mkv", "E05 [ABCD1234].mkv": a file named only by its number is an
    # episode of whatever show its folder is (guessit would call it a movie "01").
    bare = _BRACKETS.sub("", strip_ext(name)).strip()
    num = re.fullmatch(r"(?i)(?:ep?\.?\s*|episode\s*)?(\d{1,4})(?:v\d)?", bare)
    if num:
        return VideoInfo("", None, None, int(num.group(1)), "series")
    # "S01E01.mkv", "S01E01-Enrollment Part I.mkv", "Episode 10 - Before the War.mkv":
    # what follows the number is the *episode's* title, not the show's.
    lead = _LEAD_EPISODE.match(bare)
    if lead:
        season = int(lead.group(1)) if lead.group(1) else None
        return VideoInfo("", None, season, int(lead.group(2) or lead.group(3)), "series")
    try:
        from guessit import guessit  # slow import, keep it lazy

        # "(1987-91)" / "(1998-1999)": keep the first year (guessit reads "-91" as an episode)
        g = guessit(_YEAR_RANGE.sub(r"(\1)", strip_site(name)))
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
            for abbr in _DOTTED.findall(name):  # guessit turns "A.D.P" into "A D P"
                title = title.replace(" ".join(re.findall(r"[A-Za-z]", abbr)), abbr.rstrip("."), 1)
            title = front_article(title)
            year = _as_int(g.get("year"))
            if season and season >= 1900:  # "(1987-91)": a year range, not season 1987
                year, season = year or season, None
                is_series = episode is not None
            return VideoInfo(title, year, season, episode,
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


# "14 - Owarimonogatari S2", "03. Kizumonogatari", "05) Show", "1a. OVA - B.C. 2033": an
# ordering number in front of a folder name. guessit reads it as the title or an episode number.
_ORDER_PREFIX = re.compile(r"^\s*\d{1,3}(?:[a-z](?=[\s.)_-]))?\s*(?:[-.)_]|\s-\s)\s*(?=\S*[A-Za-z])")
# "OVA - Bubblegum CRASH", "Series - A.D.P", "ONA Series": a release type in front of
# (or instead of) the title in collection folders.
_TYPE_PREFIX = re.compile(
    r"(?i)^(?:(?:tv|ona|ova|oav|ovas|oavs|movies?|films?|series|specials?|spin-?offs?|"
    r"compilation|live[- ]action)\s*)+(?:\s-\s|:\s*|$)")


def strip_order_prefix(folder_name: str) -> str:
    """Drop a leading "14 - " / "03. " from a *folder* name (files keep theirs:
    "01 - Title.mkv" really is episode 1)."""
    stripped = _ORDER_PREFIX.sub("", folder_name, count=1)
    if stripped != folder_name:
        stripped = _TYPE_PREFIX.sub("", stripped, count=1)
    return stripped if re.search(r"[A-Za-z]", stripped) else folder_name


def is_weak_title(title: str | None) -> bool:
    """No usable title: empty, only digits ("14"), an order tag ("1b") or a bare
    release type ("OVA", "Series", "Extras")."""
    key = normalize(title or "")
    return (not key or key.isdigit() or bool(re.fullmatch(r"\d{1,3}[a-z]", key))
            or key in _TYPE_WORDS)


_TYPE_WORDS = {"ova", "ovas", "oav", "ona", "tv", "movie", "movies", "film", "series", "special",
               "specials", "extras", "extra", "bonus", "spinoff", "spinoffova", "onaseries",
               "liveactionfilm", "compilationmovies"}


def normalize(title: str) -> str:
    """Lowercase alphanumeric key used to compare titles ("Ōkami" -> "okami")."""
    ascii_ = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    return re.sub(r"[^0-9a-z]+", "", ascii_.lower())
