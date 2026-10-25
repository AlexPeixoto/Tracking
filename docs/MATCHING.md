# Matching: looking titles up, and choosing covers

`enrich.py` asks the registered services (`providers/`) about each item, merges what they
say and stores scores, covers, times, summaries and episode lists. Services are picky: their
searches are literal and their names differ from file names, so most of the rules below are
about asking the right way and recognising the right answer.

## Services

`providers/__init__.py` lists them; the order is the priority for titles, summaries, links
and episode lists. Each declares the sections it serves, the settings (API keys) it needs and
a `cover_rank`. A service without its key is skipped; one that fails logs a warning and the
others still run (the item is retried on the next metadata update).

| Works without a key | Needs a free key |
|---|---|
| AniList, Kitsu, MyAnimeList (Jikan), TVmaze, IMDb posters, Wikidata, HowLongToBeat, Steam store, libretro box art | AniDB, Trakt, OMDb, TMDB, RAWG, IGDB, OpenCritic, Steam Web API |

Answers are cached on disk (`cached_json`, see [DATA.md](DATA.md)), so retries are cheap.

## Search wording (`providers/variants.py`)

If the title as written finds nothing, each service is asked again with other wordings, best
first, at most `MAX_VARIANTS` (each costs a request per service). `RULES`, in order:

| Rule | Example |
|---|---|
| `first_season` | "Show 1st GIG" / "Show Season 1" → "Show" |
| `without_index` | "002 Golden Axe" → "Golden Axe" |
| `unglued` | "GalGun 2" → "Gal Gun 2" (it's "Gal*Gun 2" to the services) |
| `subtitle_colon` | "INAZUMA ELEVEN - Victory Road" → "INAZUMA ELEVEN: Victory Road" |
| `without_edition` | "White Knight Chronicles - International Edition" → "White Knight Chronicles" |
| `without_trailing_abbreviation` | "RoboCop - Rogue City UB" → "RoboCop - Rogue City" |
| `without_leading_abbreviation` | "MDA - RAIN CODE Plus" → "RAIN CODE Plus" |
| `expanded_abbreviations` | "MSG SEED" → "Mobile Suit Gundam SEED", "MS ZETA Gundam" → "Mobile Suit ZETA Gundam", "DX" → "Deluxe" (`ABBREVIATIONS`) |
| `numbered_abbreviations` | "FF6" → "Final Fantasy 6" (then "Final Fantasy VI") |
| `without_release_type` | "Fatal Fury Series" → "Fatal Fury", "Queen's Blade OVA" → "Queen's Blade"; not "Batman - The Animated Series" |

Then over all of those: `digits_to_roman` ("Fatal Frame 2" → "Fatal Frame II", not "Season 2",
not "10th", 10 stays 10) and `roman_to_digits`.

To add a rule: a small function with its example in the docstring, placed in `RULES` by
priority, a test with the example, and the check that it doesn't push useful variants past
`MAX_VARIANTS` for real titles (dump `search_variants` for every title in a library before
and after; only the intended titles should change).

## Recognising the right result (`providers/base.py`)

- `similarity(a, b)`: 0–100 on words (Roman numerals as digits, "&" as "and", punctuation
  ignored), with a bonus when one title's words appear in order in the other and a penalty
  when their sequel numbers differ ("Persona 4" isn't "Persona 5").
- `best_match(...)`: the best candidate scoring at least 75, with bonuses:
  - **platform**: results on the item's platform are tried *first*; the rest only if none
    is close enough (a PS5 "Wolverine" is "Marvel's Wolverine", not the 1991 NES game);
  - **year**: +6 same year, +2 one off, −4 otherwise (games pass the item's year; a year
    typed after a Completed entry's name counts: "Wolverine 2026").
- **series / movie**: anime services prefer the item's kind (`media_format_bonus`), but when
  that leaves no match they accept the other ("Kizumonogatari" stored as episodes 01–03 is
  three movies).
- Find match (`ui/dialogs/match.py`) lets you search by hand and pin the item to a result.

## Covers

In order:

1. **A cover you picked** (Search cover on IGDB, `cover_pinned`) is never replaced by a
   refetch; Find match unpins it.
2. **Art next to the files** (`cover.jpg` / `folder.jpg` in the item's folder).
3. **The best-ranked service** that returned one (`cover_rank`; libretro box art for consoles
   looks in the sibling set too: Game Boy ↔ Game Boy Color, Master System ↔ Game Gear).
4. **Fallbacks when nothing found one** (`enrich.borrow_cover`):
   - a later season uses its first season's cover (same show, or the season-1 item in the
     same release folder: "Agent AIKa Season 2", "Blood Blockade Season 2");
   - otherwise the same title elsewhere in the library ("GalGun 2" on Switch shows the
     Steam "Gal*Gun 2" cover; names compared without spaces / symbols, 4+ characters).
   Borrowed covers are copies; a refetch that finds a real one replaces them.

A refetch that finds nothing keeps the old cover: a service being down shouldn't blank items.

## Known gaps (ideas, not done)

- A folder of several separate releases under one name ("2a. Fatal Fury Series" holding three
  TV specials, each its own entry on AniList) can't match one entry; Find match by hand.
- Games in a mixed folder with no platform in the name ("tpex/Alter Echo (USA).zip") need a
  platform set by hand (or a disc header the scanner can read).

- Neo Geo / arcade sets named by MAME short names ("kof2003.zip") need a short-name → title
  list (e.g. FinalBurn Neo's DAT) before any service can find them.
- Scene short names on DS ("b-dqhothb.nds") can't be identified from the name at all; reading
  the ROM header would be the way (like `discid.py` does for discs).
