# Workflow: changing the code safely

## Setup and running

| Command | What it does |
|---|---|
| `build.bat` | creates `.venv` on first use, installs `requirements.txt` (every build), builds `dist\Tracking\Tracking.exe` with PyInstaller and runs its self-test |
| `run.bat` | starts the built exe (builds it first if missing) |
| `check.bat` | lint (ruff), tests with coverage (`build\coverage\index.html`), and the replay of your library when you have a baseline |
| `python -m tracking` | runs from source (inside `.venv`) |

Don't rebuild while the app is running: the exe and its files are in use.

## The checks

1. **ruff**: `.venv\Scripts\python -m ruff check tracking tests` (line length 110).
2. **tests**: `.venv\Scripts\python -m pytest -q`. No network: services are faked with
   recorded answers, folder trees are built in a temp folder, the library is a temp
   `TRACKING_DATA_DIR`. Name tests after the real case they protect.
3. **replay** (anything touching the scanner): re-process every saved snapshot of your real
   library with the current rules and compare with a baseline.

   ```bat
   .venv\Scripts\python -m tracking.scan.replay --save   :: before the change: record what you have
   .venv\Scripts\python -m tracking.scan.replay          :: after: every line that differs
   ```

   It only reads the library. Each line is one item (location, category, platform, kind,
   season, files, title, path); `-` lines are gone, `+` lines are new. Read every one: a rule
   should move exactly the items it's meant for. A refactor should show no lines at all.
   Re-save the baseline once you're happy with a change.

For lookup rules (search wording, matching), compare the output for every title in a library
before and after instead (`search_variants` over `SELECT title FROM items`), and spot-check
real lookups. Services are slow and change, so these aren't in the automated tests.

## How rules are written here

- **Find the real case first**, and write it as a test.
- **Put the rule where its kind lives** ([SCANNING.md](SCANNING.md), [MATCHING.md](MATCHING.md)):
  a row in a table (`platforms.py`, `ABBREVIATIONS`, the edition words, the tool names) beats
  new code; a new rule gets a name (`unglued`, `part_of_parent`) rather than another `if` in a
  long function.
- **Say why, with the example**: `"GalGun 2" is "Gal*Gun 2" to the services`. The next person
  needs to know what breaks without it.
- **Thresholds are named constants** at the top of the module (`ROM_PILE_GAMES`,
  `INDEX_MIN_FILES`, `SHORT_EXTRA_SECS`, `MAX_VARIANTS`…).
- **Prefer "try this too" over "change this"**: search variants are tried after the name as
  written; fallbacks (borrowed covers) only apply when nothing else worked. Rules that can be
  wrong should be the ones that only add chances, not the ones that replace good answers.
- **Fail softly**: a service that's down, a device unplugged mid-scan or an unreadable folder
  logs a warning and the rest goes on.
- **Update the docs** page for the area in the same change.

## Conventions

- The UI never writes SQL: `library.py` (user actions), `scan/store.py` (scans), `enrich.py`
  (lookups), `archive.py` (export / import).
- Each thread opens its own database connection (`db.connect()` / `db.session()`).
- Commit messages: a one-line summary, then what changed and why, in plain words.
- Commit / push only when the owner asks.
- Screenshots in the README use the demo library, never a real one (no personal paths).
- The README says the project is made with AI; keep that notice.

## Things not to do

- Don't use IMDb's internal APIs with spoofed browser headers (against its terms); IMDb data
  comes from OMDb, Wikidata and public pages.
- Don't commit a replay baseline, a library, logs or anything else from a data folder.
- Don't bypass a service's rate limit (`RateLimiter`) or its cache.
- Don't make a scan write to the scanned drive, or a replay write to the library.
