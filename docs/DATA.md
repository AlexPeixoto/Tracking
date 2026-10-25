# Data: what is stored where

## The data folder

`paths.data_dir()`, in this order:

1. `TRACKING_DATA_DIR` (tests and tools point it at a temporary folder);
2. a `data\` folder next to `Tracking.exe` (portable install);
3. `%LOCALAPPDATA%\Tracking` (Windows; the Store / MSIX build may redirect it).

| Path | What |
|---|---|
| `library.sqlite3` (+ `-wal`, `-shm`) | the library (SQLite, WAL mode) |
| `posters/` | cover images, one file per item (`<id>.jpg`) or Completed entry (`done-<id>-<n>.jpg`) |
| `cache/` | service answers (`cached_json`), libretro file lists, game launch unpacking |
| `backups/` | automatic library backups (zip, see `archive.py`) |
| `logs/` | `tracking.log` (rotating) and the frozen build's `selftest.txt` |
| `replay-baseline.tsv` | your replay baseline, if you saved one ([WORKFLOW.md](WORKFLOW.md)); lists your own files, so it never goes in the repository |

## Tables (`db.py`)

| Table | One row per | Notes |
|---|---|---|
| `volumes` | drive / device / account | `uid` (`guid:{…}`, `serial:…`, `unc:…`, `mtp:<serial>:<storage>`, `steam:<id>`), `label` (the system's), `name` (yours), `last_mount` |
| `roots` | location | a volume + folder (`rel_path`, '' = whole drive), `category` (auto or fixed), optional `platform` |
| `items` | library entry | see below |
| `media_files` | video file of an item | size, mtime, measured `duration_sec`, parsed season / episode |
| `episodes` | episode of a series season | title, air date, score, from one service |
| `scores` | score of an item from one site | `value` / `max_value`; the headline order is `scores.RATING_PREF` |
| `completions` | time you finished something | the Completed log; may have no item (`item_id` NULL) and its own cover |
| `snapshots`, `snapshot_dirs` | location / folder in it | the saved tree (`[[name, is_dir, size, mtime]]` per folder) and disc header results |
| `item_history` | copy / move of an item's files | shown on the item page |
| `settings` | key | everything configurable, as text |

### `items`, the important columns

- **Where**: `volume_uid` + `rel_path` (unique; a path inside a folder can carry
  `::s1` / `::movie-…` when one folder holds several items), `root_id`, `is_dir`, `missing`.
- **What it is**: `category`, `platform`, `title`, `kind` (series / movie / addon), `season`,
  `year`. The scanner's guess; `user_category` / `user_platform` / `user_title` override it
  (always read through `db.ITEM_SELECT`, which applies the overrides).
- **Where it comes from**: `source` = `disk` (scanned), `steam` (account), `wish` (Want to
  acquire), `manual` (added by hand; `store` says where you play it).
- **Looked up**: `rating*`, `external_id` / `external_url`, `summary`, `poster`,
  `cover_pinned` (you picked it), `hltb_*`, `enriched_at` (NULL = look up again).
- **Yours**: `completed` / `completed_at`, `notes`, `plan_state` / `plan_position` /
  `plan_changed` (Planning board), play tracking (`launch_*`, `last_*`, `playtime_*`).

## Settings keys

Service keys are declared by each provider (`Setting(...)`) and shown in Settings; they're
left out of exports unless asked (`providers.credential_keys()`). The rest:

| Key | What |
|---|---|
| `hide_completed`, `hide_missing`, `hide_no_cover`, `hide_offline`, `include_steam`, `install_filter`, `location_filter` | library filters (`location_filter`: "" all, "-" none, else root ids) |
| `sidebar_open` | which sidebar sections are unfolded |
| `offline_style` | how offline items are marked |
| `metadata_workers` | parallel lookups |
| `log_level` | logging |
| `plan_sections` | Planning board columns shown |
| `pick_options` | "What should I play?" filters |
| `year_image`, `year_image_presets` | Completed year image options |
| `done_hide_no_cover` | Completed page filter |
| `auto_backup`, `auto_backup_images`, `auto_backup_keep`, `auto_backup_last` | automatic backups |
| `emulator.<platform>`, `emulator_args.<platform>`, `emulator_unzip.<platform>` | emulator per platform (Settings › Emulators, `launcher.py`) |

## Changing the schema

Add the column to `SCHEMA` **and** to `MIGRATIONS` in `db.py` (existing libraries get it on
next start; imports of older exports run the same migrations). If `db.Item` should carry it,
add it to the dataclass (with a default), to `ITEM_SELECT` and to the row mapping. If an
archive import should keep it, check `archive.py`.
