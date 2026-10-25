# Scanning: what the library finds, and why

A scan turns folder listings into library items. It never opens files except to read a
disc header or video length, and never changes anything on disk. This page lists the rules
in the order the scanner applies them, each with the real case it exists for. The code is
in `tracking/scan/`; every rule here has a test (mostly `tests/test_scanner.py`,
`test_sanity.py`, `test_devices.py`, `test_snapshot.py`).

## Locations

A location is a drive, a folder, an Android device's folder or a Steam account
(`roots` + `volumes` tables, see [DATA.md](DATA.md)).

| Kind | Identified by | Scanned how |
|---|---|---|
| Drive / folder | volume GUID + serial (`volumes.py`), so a new drive letter doesn't matter | `fs.RecordingFS` reads the disk and records every listing |
| Android device | `mtp:<serial>:<storage name>` (`devices.py`) | Windows' device API lists the folder tree; that listing *is* the snapshot |
| Steam account | `steam:<SteamID64>` | the Steam Web API (`sources/steam.py`), not a file scan |

Windows' hidden boot / recovery partitions (no letter, no label, under 2 GB) aren't offered
as drives. Drives and devices can be renamed (`volumes.name`); the name survives rescans.

## Snapshots

Every real scan stores the location's whole folder tree (names, sizes, dates) plus any disc
headers it read (`snapshots`, `snapshot_dirs`). After the scan, folders it skipped
(extras, emulator folders) are listed too, so the snapshot is the complete tree; only system
and hidden folders are left out. Rescanning an unplugged drive re-processes its snapshot
(`fs.SnapshotFS`), which is also how new rules reach old drives, and what the replay check
uses ([WORKFLOW.md](WORKFLOW.md)).

## Walking a location (`walk.py`)

For each folder, in order:

1. **Skipped entirely**: system folders (`$RECYCLE.BIN`, `Windows`…), hidden folders,
   extras folders next to shows (`Extras`, `NC`, `Bonus`…), emulator / tool / benchmark
   folders (`rpcs3`, `Cemu_2.6`, `Cinebench2024…`: `classify.is_tool_name`) and
   redistributables (`_Redist`, `CommonRedist`).
2. **A container** (walk into it) when it is:
   - a keyword folder: a section (`Anime`, `Games`) or a platform (`PS2`, `SFC`); the platform
     becomes a hint for everything below;
   - a generic folder (`tpex`, `Downloads`);
   - a folder of releases: `CONTAINER_FOLDERS` real sub-folders and no `.exe`, or
     `CONTAINER_FOLDERS_WITH_EXE` despite one. **Never** when the `.exe` is named like the
     folder (`30XX/30XX.exe`) or game-engine files sit next to it (`steam_api64.dll`,
     `UnityPlayer.dll`, `EOSSDK…`): that's one installed game with many data folders;
   - a ROM pile: inside a console platform folder, `ROM_PILE_GAMES` or more ROM / disc /
     archive files with different titles (`GB/old_non_zip`, `PS 2/copy` with 59 discs). Not
     for PC (FitGirl repack parts would look like that).
3. **Otherwise one release**: `_dir_items` decides game or video from its files
   (`classify.decide`), and loose files in a folder become items by `_loose_items`.

## Games

- **Platform**: from the folder (`platforms.py` aliases, including emulator front-end names:
  `SFC` SNES, `FC` NES, `SMS`, `MD`, `GG`, `PCE`, `DC`, `GC`…), in-name tokens (`(PS2)`),
  ROM extensions (`classify.ROM_EXT`), disc headers (`discid.py`), store serials.
- **Title**: `titles.clean_game_title` drops release tags, regions, scene groups, serials and
  moves a trailing article to the front ("Legend of Zelda, The" → "The Legend of Zelda").
- **Numbered collections**: when a folder's files share an index style ("000 Sonic Blast",
  "0121 - Castlevania…"), at least `titles.INDEX_MIN_FILES` of them and one in
  `INDEX_ONE_IN`, the number is dropped. A lone "1942" or "007 - NightFire" keeps it.
- **Install state** (`install.py`): installed (an `.exe` to start), installer (setup), or
  image (ROM / disc); which file Play starts.
- **Add-ons**: updates / DLC fold into their game (`merge.merge_addons`).

## Videos (anime, movies, TV)

- **Episodes or movie** (`videos.group_videos`): files with episode numbers are a series,
  one item per season; files without are movies. Specials (season 0) join season 1.
- **Not episodes**: openings / endings / promos by name (`NCOP`, `NCED 01`, `Opening 01`,
  `Clean Ending`, `MV`, `CM`, `TV-SPOT`, `Preview`, `credits`, `splash screen`:
  `videos.is_extra_file`), and once lengths are measured, any video of
  `SHORT_EXTRA_SECS` (5 min) or less with such a word anywhere in its name. Short real
  episodes (Room Camp, specials) stay.
- **Several shows in one folder** each get their own item path (`naming.unique_slugs`):
  "Sekirei" S1 and "Sekirei Pure Engagement" S1 used to overwrite each other.
- **The item's name** (`naming.show_title`), in steps:
  1. a folder named "Season 2" / "OVA" / a number says nothing: use the files' show name;
  2. in a numbered pack ("1a. MSG 0079"), a longer name from the files wins, unless every
     sibling folder's files share it ("05 - Nekomonogatari (White)" keeps its name) or the
     folder name is abbreviated ("KoF - Destiny" takes "The King of Fighters Destiny");
  3. a part of the show the parent folder names: "Hare + Guu/DELUXE" → "Hare + Guu DELUXE";
  4. the same name written the folder's way: "86 (Eighty Six)" keeps "86";
  5. still nothing usable: the nearest collection folder's name.
- **A "+" inside a name** stays ("Hare + Guu"); "Death Parade + Special" and "Symphogear +
  G + GX" are lists of releases, not names (`titles._plus_title`).

## Saving (`store.py`)

Items are matched to existing ones by path; a moved item is re-linked instead of duplicated;
an item whose detected name / category changed gets its metadata fetched again; items no
longer found are flagged `missing` (never deleted), unless they only moved because the same
files are grouped differently now.

## Adding a rule

1. Find the real case (a folder from someone's library) and write it as a test first.
2. Put the rule where its kind lives (table above / this page); give it a name and the
   example in its docstring; put any threshold in a named constant.
3. Run the tests and the replay check ([WORKFLOW.md](WORKFLOW.md)); read every line of the
   diff. Anything that moved that you didn't mean to move is a regression.
4. Update this page.
