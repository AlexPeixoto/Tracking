# Wishlist

Where Tracking is heading: **the one launcher for everything outside Steam**, covering
installed PC games, emulated consoles and the anime / shows / movies on your drives. Steam
stays Steam, but its games can show up alongside the rest.

Items are grouped by theme, roughly in the order they'd be built. Each has a short note on
how it could work, so picking one up doesn't start from zero.

---

## 🎮 Launcher

### ✅ Installed vs installer *(done)*
Tell installed games apart from installers and filter by it (*Installed*, *Installer*, *ROM / disc*).
- **Installer**: repack folders with `setup.exe` / `setup-*.bin` (FitGirl, DODI, GOG
  `setup_*.exe`), `.iso` / `.rar` PC images.
- **Installed**: a folder with the game's own `.exe` (biggest non-tool exe, not
  `unins*.exe` / `setup.exe` / redistributables), or a known store layout
  (`steamapps/common`, `GOG Galaxy/Games`, `Epic Games`).
- Store it as `items.install_state`; filter checkboxes next to *Hide completed*.

### ✅ Launch games from the app *(done: PC, emulators, Steam)*
A **Play** button on the item page and in the right-click menu.
- **PC**: run the detected exe (pick another once and it's remembered per item; optional
  arguments; working directory = the exe's folder).
- **Consoles**: one emulator per platform in Settings (path + argument template), e.g.
  PCSX2 `"{file}"`, RPCS3 `--no-gui "{file}"`, PPSSPP, Dolphin `-e "{file}"`,
  Ryujinx, DuckStation, Flycast, Mednafen (Saturn). Disc images inside `.zip` are
  extracted to a cache first when the emulator can't read zips.
- **Steam games**: `steam://rungameid/<appid>`.
- Drive offline: say which drive to plug in instead of failing.

### ✅ Recently played, for installed games *(sort done; session length still to do)*
Record each launch (`items.last_launched`, `launch_count`) and add a
**Recently played** sort and a *Continue playing* row at the top of Games. Optionally
track the session length while the process runs, which adds to play time for non-Steam games.

### Copy / move to another drive
Right-click › **Copy to…** / **Move to…**, asking for the destination folder (e.g. from an
external disk to the internal SSD before playing).
- Runs as a background job with progress and cancel; verifies sizes before deleting on
  a move.
- The library follows: a **move** re-links the item to the new location, keeping completion,
  notes and history. A **copy** keeps both, linked as copies of one item, so *Play* prefers
  the internal copy and *Library health* doesn't flag a duplicate.
- A per-item **history** ("Moved from Backup (E:) to SSD (C:) on 2026-10-20") on the item page.

### Steam games in All items / Games
A setting (and a quick toggle) to **include Steam games** in *All items* and *Games* as
well as in their own section. A Steam game that's also on disk shows once (the disk item,
with Steam play time attached).

---

## 📺 Watching

### Play episodes from the app
**Play** on a series opens the next unwatched episode in your player (system default,
or mpv / VLC / MPC-HC set in Settings); double-click any episode in the list to play it.
- Remember the **last played episode** per series (`items.last_episode`, date), shown on
  the card ("Ep 7 / 24") and the item page.
- With mpv (IPC) or MPC-HC (web interface): remember the **position** too, and mark an
  episode watched past ~90%.

### Continue watching
A row of series you're partway through, ordered by when you last watched, with *Play next*.
Finishing the last episode offers to mark the series completed (and log it in Completed).

---

## 🖼️ Library quality

### Console covers without keys (libretro thumbnails)
Match Redump / No-Intro file names against the
[libretro-thumbnails](https://github.com/libretro-thumbnails) repositories (the box art
RetroArch uses). Free and keyless, and great for PS1/PS2/PSP/Saturn/Dreamcast/Wii, where
HowLongToBeat misses.

### Re-detect titles from stored paths
Re-run the naming rules on the paths already in the database, with no drive needed. It's
instant after a rule fix; only items whose title changes get refetched (your renamed titles
are left alone). Disc reading and new files still need a Rescan.

### Steam play time on the Completed log
When a Steam game is marked completed, store the hours it took then, for the year stats
("312 h, 140 of them in Elden Ring").

---

## Ideas parking lot

- Per-platform "last played" for emulators from their own config/history files
  (PCSX2, RPCS3, Dolphin keep recent lists).
- Launch through Playnite-style **scripts** (pre/post: mount an ISO, start a VPN…).
- Shortcut to **open the save folder** of a PC game (PCGamingWiki save locations).
- Remote/phone view of the library (read-only web page).
