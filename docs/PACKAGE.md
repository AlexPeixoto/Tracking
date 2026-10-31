# Library package: a shareable, read-only copy of a library

A library package is what Tracking exports for *viewing* a library elsewhere: the Android app
(`android/`) imports it, and anyone you send it to can browse your library. It carries the
library itself (titles, covers, scores, episodes, completion, the Completed log, the Planning
board, play time) and nothing private: no file paths, drive names, folder snapshots, notes,
settings or API keys.

It is **not** a backup: to move a library between Tracking installs, use Settings › Export /
Import (`archive.py`), which keeps everything.

Written by `tracking/package.py`; read by `android/app/.../data/PackageReader.kt`. Both follow
this page: change the format here first, and bump `version` when a reader of the old version
would misread the new one (adding optional fields doesn't need a bump).

## The file

A `.zip` (extension `.trackinglib.zip` by default, any name works):

```
manifest.json      what this is
library.json       the library
covers/            WebP covers, at most 360 × 540, named in library.json
```

## manifest.json

```json
{
  "format": "tracking-library-package",
  "version": 1,
  "name": "Alex's library",
  "created": "2026-10-31T18:20:00+00:00",
  "app": "Tracking 0.9.0",
  "counts": {"items": 5815, "completions": 412, "covers": 4980}
}
```

A reader checks `format` and refuses a `version` higher than it knows.

## library.json

```json
{
  "categories": [
    {"key": "anime", "label": "Anime", "singular": "Anime", "done": "Watched", "episodic": true,
     "platforms": false, "colors": ["#7b3fe4", "#2b1659"]}
  ],
  "items": [ ITEM, … ],
  "completions": [ COMPLETION, … ]
}
```

`categories` are the library's sections, in sidebar order; items refer to them by `key`.

### ITEM

| Field | Type | Notes |
|---|---|---|
| `id` | int | unique within this package (not across packages) |
| `title` | string | as shown in the library |
| `category` | string | a `categories[].key` |
| `kind` | string? | `series` / `movie` (videos), `addon` (game DLC) |
| `season` | int? | series season |
| `platform` | string? | games: "PS2", "SNES", "PC"… |
| `year` | int? | release year |
| `source` | string | `disk` (on a drive), `steam` (Steam account), `device` (on an Android device), `manual` (added by hand), `wish` (Want to acquire) |
| `store` | string? | `manual`: where it's played ("Xbox app / Game Pass") |
| `completed` | bool | watched / beaten |
| `completed_at` | string? | ISO date (or date-time) |
| `plan` | string? | Planning board: `next`, `playing`, `hold`, `dropped` |
| `plan_position` | int? | order within "Up next" |
| `playtime_min` | number? | games: minutes played (Steam + launched from Tracking) |
| `last_played` | string? | ISO date-time |
| `hltb` | object? | games: `{"main": h, "extra": h, "complete": h}` (HowLongToBeat hours, any may be null) |
| `duration_min` | number? | videos: total running time |
| `episodes_owned` | int | video files of the item (0 for games) |
| `last_episode` | int? | series: last episode played from Tracking |
| `rating` | object? | the headline score: `{"value": 8.2, "max": 10, "source": "AniList"}` |
| `scores` | list | `{"source", "value", "max", "url"?}`, best-known first |
| `summary` | string? | plain text |
| `url` | string? | the matched service's page |
| `cover` | string? | path inside the zip (`covers/12.webp`), or null |
| `episodes` | list | `{"season", "number", "title"?, "aired"?, "score"?, "max"?, "owned"}` |

### COMPLETION

One entry of the Completed log (a time something was finished; may be outside the library).

| Field | Type | Notes |
|---|---|---|
| `title` | string | |
| `category` | string | a `categories[].key` |
| `platform` | string? | |
| `date` | string | `YYYY-MM-DD`, or just `YYYY` when the day isn't known |
| `position` | int? | your order within the year; null = by date |
| `item` | int? | the library item's `id`, if it's in the library |
| `cover` | string? | path inside the zip |

## What is left out, on purpose

Items not found on their last scan (`missing`), file paths and file names, drive / device
names and ids, folder snapshots, launch settings, notes, settings, API keys, scores' raw
service ids. A package can be passed around without revealing anything about the sender's
computer.
