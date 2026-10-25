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
    # "V.I.P. (USA).chd": the dots of an acronym aren't separators ("V I P").
    for abbr in _DOTTED.findall(name):
        s = s.replace(" ".join(re.findall(r"[A-Za-z]", abbr)), abbr.rstrip("."), 1)
    return front_article(smart_title(s)) if s else name


# "003 The Revenge of Shinobi", "0121 - Castlevania - Dawn of Sorrow": a collection's index
# number in front of the name (the name itself must follow, with letters in it).
_INDEX = re.compile(r"^(\d{1,4})\s*[-_.)]?\s+(?=[^\s(\[{][^(\[{]*[A-Za-z]{2})")


# A folder is a numbered collection when at least this many of its files carry the same
# index style, and at least one in INDEX_ONE_IN of them do.
INDEX_MIN_FILES = 5
INDEX_ONE_IN = 5


def index_width(stems: list[str]) -> int | None:
    """The digit count of the index numbers a folder's files carry ("000 Sonic Blast",
    "007 After Burner": 3), or None. Only when many files share it (5+, a fifth of them):
    "1942" or "007 - NightFire" alone are names, not indexes."""
    widths: dict[int, int] = {}
    for s in stems:
        m = _INDEX.match(s)
        if m:
            widths[len(m.group(1))] = widths.get(len(m.group(1)), 0) + 1
    if not widths:
        return None
    width, n = max(widths.items(), key=lambda kv: kv[1])
    return width if n >= INDEX_MIN_FILES and n * INDEX_ONE_IN >= len(stems) else None


def drop_index(stem: str, width: int | None = None) -> str:
    """The name without its index number (``width``: only an index that many digits long)."""
    m = _INDEX.match(stem)
    if not m or (width is not None and len(m.group(1)) != width):
        return stem
    return stem[m.end():]


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


def is_season_only(name: str) -> bool:
    """ "Season 2", "S02", "2nd Season [1080p]": a folder that names a season and nothing else,
    so the show's name has to come from elsewhere. Not "Muv-Luv Alternative - Total Eclipse
    (Season 1)", which names the show as well."""
    if season_from_dir(name) is None:
        return False
    rest = _SEASON_DIR.sub(" ", name.strip())
    return is_weak_title(_tidy(_PARENS.sub(" ", _BRACKETS.sub(" ", rest))))


def _as_int(v) -> int | None:
    if isinstance(v, list):
        v = v[0] if v else None
    return v if isinstance(v, int) else None


# Release-name spellings guessit misreads, rewritten before it sees them:
_FIX_VERSION = re.compile(r"(?i)\b(S\d{1,2}E\d{1,4})v\d+\b")            # "S01E01v2": a re-release, still E01
# "S01OVA01", "S01OVA": a special
_FIX_OVA = re.compile(r"(?i)\bS(\d{1,2})\s?OVA\s?(\d{1,3})?(?![A-Za-z0-9])")
_FIX_HALF = re.compile(r"^(\d{1,3})\.5(?=\s)")                          # "06.5 Kara no Kyoukai Remix"


def _fix_release_name(name: str) -> str:
    """ "[Judas] Gundam GQuuuuuuX - S01E01v2.mkv" -> "... S01E01" (guessit calls it a movie);
    "S01OVA01-After Washing Your Body.mkv" / "Sora no Otoshimono - S01OVA.mkv" -> season 0
    (a special, which joins the show's first season); "06.5 Title" -> "06 Title"."""
    name = _FIX_VERSION.sub(r"\1", name)
    name = _FIX_OVA.sub(lambda m: f"S00E{int(m.group(2) or 1):02d}", name)
    return _FIX_HALF.sub(r"\1", name)


# Words guessit is right to drop after a title (release details, not part of the name).
_RELEASE_WORDS = {
    "complete", "series", "season", "seasons", "batch", "bd", "bdrip", "bluray", "web", "webrip",
    "webdl", "dvd", "dvdrip", "dual", "audio", "multi", "subs", "sub", "subbed", "dub", "dubbed",
    "eng", "english", "jap", "raw", "uncensored", "hevc", "remux", "tv", "ova", "oav", "ona", "movie",
    "movies", "special", "specials", "extras", "vostfr", "french", "german", "spanish", "italian",
    "remastered", "proper", "repack", "internal", "limited", "collection", "pack", "hdr", "uhd"}
_TRAILING_TAGS = re.compile(
    r"(?i)(?:\s+(?:s\d{1,2}|ovas?|oads?|specials?|movies?|season\s*\d+|complete|batch))+$")
_CUT = re.compile(r"\s+-\s+|\s*[\[({]|\s+S\d{1,2}\s?E\d|\s+(?:ep?\.?\s?)?\d{1,4}(?:v\d)?\s*$", re.I)


def _uncut(name: str, title: str) -> str:
    """guessit sometimes drops the end of a name: "No Game No Life [BD]" -> "No Game No",
    "[Judas] To Love-Ru - S01E01" -> "To Love" ("Ru" read as Russian). When the name as
    written, up to its first separator, is guessit's title plus a word or two that aren't
    release details, those words are the name's."""
    stem = re.sub(r"^\s*(?:\[[^\]]*\]\s*)+", "", strip_ext(name)).replace("_", " ")
    if " " not in stem.strip():
        stem = stem.replace(".", " ")
    simple = _CUT.split(stem, maxsplit=1)[0].strip()
    # Release tags at the end go first: "To Love-Ru S1 OVAs" -> "To Love-Ru".
    simple = _TRAILING_TAGS.sub("", simple)
    if not simple.lower().startswith(title.lower()) or len(simple) == len(title):
        return title
    rest = simple[len(title):]
    if not re.fullmatch(r"(?:-[A-Za-z][\w'!?]*|\s+[A-Za-z][\w'!?]*){1,2}", rest):
        return title
    if any(w.lower() in _RELEASE_WORDS for w in re.findall(r"[A-Za-z]+", rest)) \
            or re.search(r"(?i)\b(?:v\d|s\d{1,2}\b|e\d)", rest):  # "1st GIG v2", "Mushishi S1": not words
        return title
    return title + rest


# Subtitles that describe the release, not the show: "Kara No Kyoukai - Full Series".
_RELEASE_SUBTITLE = re.compile(
    r"(?i)^(?:(?:full|complete)\s+(?:series|collection|seasons?|box)|complete|batch|uncensored|"
    r"remastered|dual[\s-]audio|multi[\s-]?subs?|season\s*\d+|s\d+|part\s*\d+|vol(?:ume)?\.?\s*\d+)$")
_EPISODE_MARK = re.compile(r"(?i)\bs\d{1,2}\s?e\d|\be\d{1,4}\b|\bep\.?\s?\d|\bepisode\s?\d|\s-\s\d{1,4}\b")


def _subtitle_before_episode(name: str, alt: str) -> bool:
    """ "Bleach - Thousand Year Blood War - S01E48 - The End.mkv", "Brotherhood - Final
    Fantasy XV, E01-E05": the subtitle comes before the episode number, so it's part of the
    show's name (after it, it would be the episode's title)."""
    text = re.sub(r"[._]", " ", strip_ext(name))
    at = text.lower().find(alt.lower())
    mark = _EPISODE_MARK.search(text)
    return at >= 0 and mark is not None and at < mark.start()


@lru_cache(maxsize=8192)
def parse_video(name: str, keep_plus: bool = False) -> VideoInfo:
    """Title / year / season / episode from a release name (file or folder).

    "[naiyas] Vampire Hunter D - Bloodlust (2000) (BD1080p...).mkv" ->
    movie "Vampire Hunter D: Bloodlust" (2000);
    "[SubsMix] Ghost in the Shell SAC 1st GIG - S01E05 - Decoy.mkv" -> series S1 E5.
    """
    name = _fix_release_name(name)
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
            title = _uncut(name, title)
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
            if is_series or keep_plus or name.lower().endswith(VIDEO_SUFFIXES):
                plus = _plus_title(name, title)
                if plus != title:
                    title, alt = plus, None  # "Hare + Guu": "Guu" isn't a subtitle
            # "Vampire Hunter D - Bloodlust": guessit splits the subtitle off movies. A series
            # *folder* has no episode number, and its subtitle is the show's name too:
            # "Muv-Luv Alternative - Total Eclipse (Season 1)" isn't "Muv-Luv Alternative".
            subtitle = (isinstance(alt, str) and re.search(r"[A-Za-z]{3}", alt) and len(alt) < 50
                        and not _RELEASE_SUBTITLE.match(alt.strip()) and not is_weak_title(alt))
            if subtitle and (not is_series or episode is None or _subtitle_before_episode(name, alt)):
                title = f"{title}: {alt}"
            for abbr in _DOTTED.findall(name):  # guessit turns "A.D.P" / "B.C." into "A D P" / "B C"
                spaced = " ".join(re.findall(r"[A-Za-z]", abbr))
                # "B.C. 2033" keeps its last dot inside a title; at the very end it's dropped.
                keep = abbr if title.find(spaced) + len(spaced) < len(title) else abbr.rstrip(".")
                title = title.replace(spaced, keep, 1)
            year = _as_int(g.get("year"))
            glued = _number_in_title(name, title)
            if glued:
                # "Blade Runner 2049 (2017)", "Mob Psycho 100 - 01": the number is part of the
                # name; guessit read it as an episode / season.
                title, rest = glued
                ep = re.match(r"\s*-\s*(?:ep?\.?\s*)?(\d{1,4})\b", rest, re.I)
                episode = int(ep.group(1)) if ep else None
                if not re.search(r"(?i)\bs\d{1,2}\s*e\d|\bseason\b", name):
                    season = None
                is_series = episode is not None or season is not None
            title = front_article(title)
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


VIDEO_SUFFIXES = (".mkv", ".mp4", ".avi", ".m4v", ".webm", ".wmv", ".mov", ".ogm", ".ts")
_PLUS_NOT_NAME = re.compile(
    r"(?i)^(?:s\d+|season|ova|oav|ona|movies?|films?|specials?|sp|extras?|g|gx|axz|xv)$")


def _plus_title(name: str, title: str) -> str:
    """ "Hare + Guu TV Ep01.mkv": guessit stops at the "+" ("Hare"); the name goes on.
    Not for "Death Parade + Special" or "Symphogear + G + GX": those join releases."""
    stem = _BRACKETS.sub(" ", strip_ext(name))
    m = re.search(re.escape(title) + r"\s*\+\s*([A-Za-z][\w'!.-]*)", stem)
    if not m or _PLUS_NOT_NAME.match(m.group(1)):
        return title
    return f"{title} + {m.group(1)}"


def _number_in_title(name: str, title: str) -> tuple[str, str] | None:
    """(title with its number, what follows) when a number right after guessit's title
    belongs to it: followed by "(year)", by " - <episode>", or ending a folder name with
    3+ digits ("Mob Psycho 100"). Plain "Show 05.mkv" stays episode 5."""
    bare = re.sub(r"^\s*(?:\[[^\]]*\]\s*)+", "", strip_ext(name)).replace("_", " ").replace(".", " ")
    m = re.match(rf"\s*{re.escape(title)}\s+(\d{{1,4}})\b(.*)$", bare, re.I)
    if not m:
        return None
    num, rest = m.group(1), m.group(2)
    if (re.match(r"\s*[(\[](?:19|20)\d\d[)\]]", rest)
            # "Megazone 23 Movie 1-4", "Megazone_23_Movie1_Part1": the number names the show
            or re.match(r"(?i)\s*(?:movie|part|ova|oav|special)\s*\d", rest)
            or re.match(r"\s*-\s*(?:ep?\s*)?\d{1,4}\b", rest, re.I)
            or (not rest.strip(" -") and len(num) >= 3 and strip_ext(name) == name)):
        return f"{title} {num}", rest
    return None


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
            or key in _TYPE_WORDS or bool(re.fullmatch(r"(?:tv|ova|ona|oav)\d{1,2}", key)))


_TYPE_WORDS = {"ova", "ovas", "oav", "ona", "tv", "movie", "movies", "film", "series", "special",
               "specials", "extras", "extra", "bonus", "spinoff", "spinoffova", "onaseries",
               "liveactionfilm", "compilationmovies"}


def normalize(title: str) -> str:
    """Lowercase alphanumeric key used to compare titles ("Ōkami" -> "okami")."""
    ascii_ = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    return re.sub(r"[^0-9a-z]+", "", ascii_.lower())
