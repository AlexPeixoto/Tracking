# Tracking: how the code is organised

A PySide6 desktop app over a SQLite library. Data flows one way:

```
drives / folders ─┐
Android devices ──┼─scan─▶ snapshot (every folder listing) ─▶ items in SQLite ─enrich─▶ scores,
Steam account ────┘        └─ replayable without the drive          covers, times, episodes ─▶ UI
```

Where to go next: rules for *what* the scanner finds → [SCANNING.md](SCANNING.md); how
titles are looked up and covers chosen → [MATCHING.md](MATCHING.md); tables, settings
and files → [DATA.md](DATA.md); how to work on the code safely → [WORKFLOW.md](WORKFLOW.md).

## Layout

| Path | What it does |
|---|---|
| `tracking/__main__.py` | Entry point (`python -m tracking`, `Tracking.exe`); `--selftest` for the frozen build |
| `paths.py` | Where data lives: `TRACKING_DATA_DIR`, portable `data\` next to the exe, or `%LOCALAPPDATA%\Tracking` |
| `log.py` | Rotating log file + level |
| `db.py` | Schema, migrations, `Item` / `Score` / `EpisodeRow` and the read queries |
| `library.py` | Changes the *user* makes (edit, move, remove, complete). The UI calls these; it never writes SQL |
| `archive.py` | Export / import of the library as a zip (replace or merge) |
| `launcher.py` | Play: the command for an item (PC .exe, emulator + argument template, steam://), unzipping images to a cache, recording launches |
| `transfer.py` | Copy / move an item's files to another folder or drive (verified before deleting), re-link or link the copy (`copy_of`), and keep `item_history` |
| `exporters.py` | Your library in other trackers' formats: MyAnimeList XML (MAL ids via AniList), Letterboxd diary / watchlist CSV, one CSV of everything |
| `upcoming.py` | "Coming up" for an item page: TVmaze next episode, AniList airing, Steam release date and price / sale (keyless, cached a few hours) |
| `completions.py` | The Completed log: one row per time you finished something (marking an item completed adds today; year CSVs `name, DD/MM/YYYY` and manual entries add rows outside the library, with their own cover). Editing, ordering, CSV import/export |
| `volumes.py` | Drive identity: volume GUID + serial, UNC shares, Linux UUIDs; resolves where a drive is mounted now; hides Windows' boot / recovery partitions |
| `devices.py` | Android phones / handhelds over USB (MTP): lists their storages and reads a folder tree through Windows' device API (WPD, via `comtypes`); the listing is scanned like a snapshot |
| **Tables** | |
| `categories.py` | The sections (Anime, Movies, TV Shows, Games, Unknown): label, colours, "Watched/Beaten", episodic? has platform? |
| `platforms.py` | Every game platform: folder aliases, in-name tokens, each service's spelling |
| `scores.py` | Comparing scores across sites: headline order per category, short names, 0–100 scale, average / best |
| `titles.py` | Text: cleaning release names, guessit parsing, seasons, normalising for comparisons |
| **Scanning** `scan/` | |
| `scan/walk.py` | Walks a location: container rules, folder items, loose files |
| `scan/classify.py` | Category / platform decisions: folder names, ROM extensions, disc headers, release tags, serials |
| `scan/discid.py` | A disc's own identity, read like an emulator does (also inside .zip): SYSTEM.CNF serial for PS1/PS2 (named via PCSX2's GameIndex), PARAM.SFO for PSP/PS3, Wii/GameCube headers. Used only when the name or platform says too little |
| `scan/fs.py` | The scanner's disk: the real one (recording a snapshot of every listing and disc header), or a saved snapshot replayed (Rescan of an unplugged drive). The scanner never calls `os` directly for listings, sizes or headers |
| `scan/install.py` | Installed / installer / ROM-disc, and which file Play starts (scored .exe candidates, preferred disc formats, PS3 EBOOT.BIN) |
| `scan/videos.py` | Episodes vs movies, one group per series season, extras (NC / AMV / PV folders and files, short openings / endings) skipped |
| `scan/naming.py` | What a video folder's item is called: the folder's name or its files' show name (season folders, collection packs, abbreviations, parts of a parent show), and unique item paths for several shows in one folder |
| `scan/replay.py` | `python -m tracking.scan.replay`: re-process every saved snapshot with the current rules, read-only, and diff against a baseline (the safety net for rule changes, see WORKFLOW.md) |
| `scan/merge.py` | One item per series season, per game (updates/DLC folded in), per movie (copies folded in) |
| `scan/store.py` | Saving: upsert, missing items, re-linking moved items, mark-missing-as-completed |
| `duration.py` | Video runtime from file headers (MediaInfo) |
| **Accounts** `sources/` | |
| `sources/steam.py` | A Steam account as a location (volume `steam:<SteamID64>`, always online): resolves a profile name / URL via the public profile XML, reads owned games + play time with the Web API key (`steam_api_key`), syncs them on Rescan as `source='steam'` items, which have their own sidebar section ("Steam") outside the library counts, copies play time onto a disk PC copy of the same game, and optionally imports the wish list into "Want to acquire" |
| **Metadata** | |
| `enrich.py` | Runs the registered services for an item, merges results, saves scores / cover / episodes |
| `providers/__init__.py` | The registry: which services exist and their priority |
| `providers/base.py` | `Query`, `Provider`, `Metadata`, `Episode`, HTTP session, cache, rate limiter, fuzzy matching (`similarity`, `best_match`) |
| `providers/variants.py` | Search wording: the ordered rules that suggest other ways to search for a title (index numbers, glued words, " - " → ": ", edition suffixes, abbreviations, numerals) |
| `providers/<service>.py` | One module per service (anidb, mal, anilist, kitsu, omdb, tmdb, tvmaze, trakt, rawg, igdb, hltb, libretro, opencritic, steam, wikidata) |
| **UI** `ui/` | |
| `ui/main_window.py` | Sidebar (library sections, Steam, Want to acquire), grid, job queue, toolbar |
| `ui/grid.py` | Poster cards (score chips, average, badges) |
| `ui/item_page.py` | Item page: completion, scores, time, episodes, summary, edits |
| `ui/dialogs/want.py` | Search a title and add it to *Want to acquire*, or (`to_library`) straight to the library with platform and "Played on" |
| `ui/dialogs/cover.py` | Search cover on IGDB: pick one of a game's covers; it's kept through refetches |
| `ui/dialogs/match.py` | Find match: search every service (`providers.search_all`) and use a result as the item's search title (`library.use_match`) |
| `ui/dialogs/pick.py` | What should I play? (`library.pick_candidates` / `library.pick`) |
| `ui/dialogs/trackers.py` | Export for other trackers (formats + folder; runs as a job) |
| `ui/dialogs/health.py` | Library health: checks from `library.health()` (no cover / score / platform, duplicates, missing), each with Show / Fetch again |
| `ui/planning_page.py` | Planning board: Up next / Playing / On hold (items.plan_state, `library.set_plan`) and Done this year (the Completed log); drag & drop, section chips |
| `ui/completed_page.py` | Completed section: a year's covers (drag to reorder), add / edit / remove, CSV import / export |
| `ui/year_image.py` | "Export year as image": the year's covers as one grid picture (columns, cover size / shape, gap, margin, background, heading, labels), drawn with Qt |
| `ui/dialogs/` | `locations.py` (add / manage / rescan / rename, add Android device, add Steam account), `settings.py` (keys, export/import), `log_viewer.py` |
| `ui/workers.py` | Background jobs (scan, enrich) on a thread pool, each with its own DB connection |
| `ui/theme.py` | Dark theme, colours, app icon |
| `packaging/` | PyInstaller spec, entry script, icon renderer (used by `build.ps1`) |

Rules of thumb:
- **UI code never writes SQL.** User actions go through `library.py`, scans through `scan/store.py`, lookups through `enrich.py`, archives through `archive.py`.
- **No category, platform or service names hard-coded outside their table.** Read `categories.py`, `platforms.py` and the provider registry instead.
- **Each thread opens its own connection** (`db.connect()` / `db.session()`); jobs run one at a time from a queue.
- **Services fail softly:** a down service logs a warning and the others still run; `ProviderError` messages reach the user.

## How to…

### Add a metadata service
1. Create `tracking/providers/<name>.py`:
   ```python
   def lookup(q: Query) -> Metadata | None:   # None = no match
       ...                                     # raise ProviderError("...") for user-facing problems
       return Metadata(title=..., year=..., external_id=..., external_url=..., poster_url=...,
                       summary=..., scores=[ScoreEntry("MyService", 8.1, 10, url)])

   PROVIDER = Provider("myservice", "My Service", frozenset({"movie", "tv"}), lookup,
                       settings=(Setting("myservice_key", "My Service API key", "Where to get it"),),
                       required=("myservice_key",), cover_rank=30)
   ```
   Use `best_match()` for picking a result and `cached_json()` + `RateLimiter` for requests.
   Optionally pass `episodes=fn(q, external_id) -> list[Episode]` for per-episode scores.
2. Add the module to `REGISTRY` in `providers/__init__.py`. Its position = its priority for titles, summaries, links and episode lists.
3. If it introduces a new score name, add it to `scores.RATING_PREF` (headline order) and optionally `scores.SHORT` (chip label).

Settings tabs, "works without a key" lists, skipping when unconfigured, logging and export credential stripping all follow from the declaration.

### Add a section (category)
1. Add a `Category(...)` row in `categories.py`.
2. Teach `scan/classify.py` when to pick it (`CATEGORY_KEYS` for folder names, `decide()` for contents).
3. Register providers for it (their `categories` sets).
Sidebar, item page, location dialog, colours and wording come from the table.

### Add a platform
Add a `Platform(...)` row in `platforms.py` (folder aliases, safe in-name tokens, service spellings). If it has its own ROM extensions, add them to `ROM_EXT` in `scan/classify.py`. A test checks every extension points at a known platform.

### Change the database
Add the column to `SCHEMA` in `db.py` **and** to `MIGRATIONS` so existing libraries get it on next start. Imports run the same migrations on older exports.

## Tests

See [WORKFLOW.md](WORKFLOW.md) for how to run them and the replay check. `pytest` (no network needed): scanner rules on synthetic folder trees, real-world release names, archive export/import, library operations, episode parsers with recorded responses, Steam account import with mocked HTTP, job queue and window close, and consistency checks between the tables.
