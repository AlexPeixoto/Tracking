"""Search wording: other ways to ask a service for a title when the name as written finds
nothing. Store and database searches are literal ("GalGun 2" finds nothing, "Gal Gun 2"
finds "Gal*Gun 2"), and file / release names write titles their own way.

Each rule below is one small function: it gets the title and returns the variants it
suggests (none when it doesn't apply). ``search_variants`` runs RULES in order, then the
numeral rules over everything so far, and keeps the first MAX_VARIANTS: each variant can
cost one request per service, so the order is the priority. To add a rule: write the
function with its real-life example in the docstring, put it in RULES where it belongs,
add the example to tests, and run the replay / tests (docs/MATCHING.md).
"""

from __future__ import annotations

import re
from collections.abc import Callable

from .. import titles

MAX_VARIANTS = 8  # the title itself included

# Sequel numerals as words. "I" and "X" stay words ("Mega Man X" isn't "Mega Man 10").
ROMAN = {"ii": "2", "iii": "3", "iv": "4", "v": "5", "vi": "6", "vii": "7", "viii": "8",
         "ix": "9", "xi": "11", "xii": "12", "xiii": "13", "xiv": "14", "xv": "15", "xvi": "16"}
_TO_ROMAN = {d: r.upper() for r, d in ROMAN.items()}

_FIRST_PART = re.compile(r"(?i)\s*(?:\b1st\s+\w+|\bfirst\s+season|\bseason\s*0*1|\bpart\s*0*1|\bs0*1)\s*$")
_ABBR = r"(?![IVX]+\b)[A-Z][A-Za-z]{0,3}[A-Z]"  # "UB", "EE", "LDA", "TLoH", "LoTC"
# "... - International Edition", "... Director's Cut": the edition, not the game.
_EDITION = re.compile(
    r"(?i)(?:\s*[-:]\s*|\s+)(?:international|deluxe|complete|definitive|ultimate|gold|special|limited|"
    r"collector'?s|enhanced|remastered|anniversary|director'?s|directors|extended|standard|premium|"
    r"legendary|royal|digital|game of the year|goty)\s+(?:edition|cut|version)$")
# Shorthand that repack / release names use for long titles (case-sensitive, whole words).
ABBREVIATIONS = {
    "FF-PZ": "Fatal Frame", "MSG": "Mobile Suit Gundam", "LDA": "Last Defense Academy",
    "MDA": "Master Detective Archives", "DoA": "Dead or Alive", "AR": "Anniversary Remaster",
    "KH": "Kingdom Hearts", "SMT": "Shin Megami Tensei", "DQ": "Dragon Quest",
    "MGS": "Metal Gear Solid", "TLoH": "The Legend of Heroes", "DE": "Definitive Edition",
    "GOTY": "Game of the Year Edition", "CE": "Complete Edition", "DX": "Deluxe",
    "MS": "Mobile Suit", "FF": "Final Fantasy",
}
_NOT_SEQUEL = re.compile(r"(?i)(?:season|part|vol\.?|volume|episode|ep\.?|chapter|s)\s*$")


def _with_colon(t: str) -> list[str]:
    """The title, and with " - " as ": " when it has one (how stores write subtitles)."""
    return [t, t.replace(" - ", ": ")] if " - " in t else [t]


# --------------------------------------------------------------------------- the rules

def first_season(t: str) -> list[str]:
    """ "Show 1st GIG" / "Show Season 1" / "Part 1" -> "Show": first seasons often have no number."""
    return [_FIRST_PART.sub("", t)]


def without_index(t: str) -> list[str]:
    """ "002 Golden Axe" / "0121 - Castlevania - Dawn of Sorrow": a collection's index number."""
    bare = titles.drop_index(t)
    return _with_colon(bare) if bare != t else []


def unglued(t: str) -> list[str]:
    """ "GalGun 2" -> "Gal Gun 2" (it's "Gal*Gun 2" to the services): words glued together."""
    bare = titles.drop_index(t)
    split = re.sub(r"(?<=[a-z])(?=[A-Z][a-z])", " ", bare)
    return [split] if split != bare else []


def subtitle_colon(t: str) -> list[str]:
    """ "INAZUMA ELEVEN - Victory Road" -> "...: Victory Road" / "... Victory Road": release
    names swap ':' for ' - ', which store searches don't like."""
    return [t.replace(" - ", ": "), t.replace(" - ", " ")] if " - " in t else []


def without_edition(t: str) -> list[str]:
    """ "White Knight Chronicles - International Edition" -> "White Knight Chronicles":
    many services list a game under its plain name only."""
    plain = _EDITION.sub("", t)
    return [plain] if plain != t and len(plain) >= 3 else []


def without_trailing_abbreviation(t: str) -> list[str]:
    """ "RoboCop - Rogue City UB" -> "RoboCop - Rogue City" (a trailing edition abbreviation)."""
    trimmed = re.sub(rf"\s+{_ABBR}$", "", t)
    return _with_colon(trimmed) if trimmed != t else []


def without_leading_abbreviation(t: str) -> list[str]:
    """ "MDA - RAIN CODE Plus" -> "RAIN CODE Plus" (a leading series abbreviation)."""
    lead = re.match(rf"^{_ABBR}\s+-\s+(.+)$", t)
    return [lead.group(1)] if lead else []


def without_release_type(t: str) -> list[str]:
    """ "Fatal Fury Series" -> "Fatal Fury", "Queen's Blade OVA" -> "Queen's Blade": a release
    type at the end that services don't have in the name. Not "Batman - The Animated
    Series" (that's the name), and not "the animation" ("CITY THE ANIMATION")."""
    if re.search(r"(?i)\bthe\s+(?:\w+\s+){0,2}series$", t):
        return []  # "Batman - The Animated Series" is the name
    plain = re.sub(r"(?i)\s+(?:series|tv series|ovas?|oavs?|onas?)$", "", t)
    return [plain] if plain != t and len(plain) >= 3 else []


def numbered_abbreviations(t: str) -> list[str]:
    """ "FF6" -> "Final Fantasy 6", "DQ11" -> "Dragon Quest 11": an abbreviation glued to its
    number (ABBREVIATIONS); the numeral rules then add "Final Fantasy VI"."""
    def full(m: re.Match) -> str:
        return f"{ABBREVIATIONS[m.group(1)]} {m.group(2)}"
    keys = "|".join(re.escape(k) for k in ABBREVIATIONS if k.isalpha())
    expanded = re.sub(rf"\b({keys})(\d{{1,2}})\b", full, t)
    return [expanded] if expanded != t else []


def expanded_abbreviations(t: str) -> list[str]:
    """ "MSG SEED ..." -> "Mobile Suit Gundam SEED ...", "25th AR" -> "25th Anniversary
    Remaster", "DX" -> "Deluxe": repack shorthand (ABBREVIATIONS), whole words only."""
    expanded = t
    for short, full in ABBREVIATIONS.items():
        expanded = re.sub(rf"(?<![\w-]){re.escape(short)}(?![\w-])", full, expanded)
    return _with_colon(expanded) if expanded != t else []


RULES: list[Callable[[str], list[str]]] = [
    first_season,
    without_index,
    unglued,
    subtitle_colon,
    without_edition,
    without_trailing_abbreviation,
    without_leading_abbreviation,
    expanded_abbreviations,
    numbered_abbreviations,
    without_release_type,
]


# Run over the title *and* every variant above: "Fatal Frame 2 Remake" <-> "Fatal Frame II Remake".

def digits_to_roman(t: str) -> str:
    """ "Fatal Frame 2" -> "Fatal Frame II". Not "Season 2" / "Part 2" / "Vol. 2" (digits
    everywhere), not "10th" (whole numbers only), and 10 stays 10 ("X" is a word)."""
    def roman(m: re.Match) -> str:
        if _NOT_SEQUEL.search(m.string[:m.start()]):
            return m.group(1)
        return _TO_ROMAN.get(m.group(1), m.group(1))
    return re.sub(r"(?<![\w.])([2-9]|1[0-6])(?![\w.])", roman, t)


def roman_to_digits(t: str) -> str:
    """ "Fatal Frame II - Crimson Butterfly" -> "Fatal Frame 2 - Crimson Butterfly"."""
    return re.sub(r"\b(II|III|IV|V|VI|VII|VIII|IX|XI|XII|XIII|XIV|XV|XVI)\b",
                  lambda m: ROMAN[m.group(1).lower()], t)


NUMERAL_RULES: list[Callable[[str], str]] = [digits_to_roman, roman_to_digits]


def search_variants(title: str) -> list[str]:
    """The title, then other ways to search for it, best first (at most MAX_VARIANTS)."""
    out = [title]

    def add(v: str) -> None:
        v = re.sub(r"\s+", " ", v).strip(" -:")
        if len(v) >= 3 and v not in out:
            out.append(v)

    for rule in RULES:
        for v in rule(title):
            add(v)
    for v in list(out):
        for rule in NUMERAL_RULES:
            add(rule(v))
    return out[:MAX_VARIANTS]
