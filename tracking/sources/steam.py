"""A Steam account as a library location: its games, with play time.

Added from Locations › "Add Steam account…" with a profile name, profile URL or
SteamID64. Names resolve without a key (the public profile XML); the owned games
and play time need a Steam Web API key (setting "steam_api_key", declared by
providers/steam.py). Each Rescan syncs the list:

- every owned game is an item: source 'steam', volume "steam:<id64>",
  rel_path "app/<appid>", platform PC, with steam_appid / playtime_min / last_played.
  They have their own sidebar section ("Steam"), outside the library counts;
- a PC game that is also on disk (same app id, or same normalised title) gets a
  copy of the play time on its disk item, so the Games section shows it too;
- an owned game that was on "Want to acquire" moves from that list to Steam;
- optionally, the account's wish list goes to "Want to acquire".
"""

from __future__ import annotations

import logging
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from .. import db, library, titles
from ..providers import steam as steam_store
from ..providers.base import ProviderError, RateLimiter, session
from ..scan.model import ScanResult

log = logging.getLogger(__name__)

PREFIX = "steam:"            # volume uid of an account: "steam:<SteamID64>"
SOURCE = "steam"             # items.source of games that come from an account
KEY_SETTING = "steam_api_key"
KEY_URL = "https://steamcommunity.com/dev/apikey"

PROFILE_XML = "https://steamcommunity.com/{kind}/{value}/?xml=1"
OWNED_GAMES = "https://api.steampowered.com/IPlayerService/GetOwnedGames/v1/"
WISHLIST = "https://api.steampowered.com/IWishlistService/GetWishlist/v1/"

NO_KEY = ("Reading a Steam account's games and play time needs a Steam Web API key "
          f"(free, from {KEY_URL}). Add it in Settings or in Locations › Add Steam account.")
BAD_KEY = (f"Steam refused the Web API key (HTTP {{status}}). Check it at {KEY_URL} "
           "and enter it again in Settings.")

_ID64 = re.compile(r"^7656119\d{10}$")
_limit = RateLimiter(1.0)


class SteamError(ProviderError):
    """A problem the user can fix (unknown profile, no key, private games list)."""


def is_account(volume_uid: str | None) -> bool:
    return bool(volume_uid) and volume_uid.startswith(PREFIX)


def wishlist_setting(id64: str) -> str:
    return f"steam_wishlist:{id64}"


# --------------------------------------------------------------------------- finding the account

@dataclass
class Account:
    id64: str
    name: str
    private: bool = False       # profile (and so its games list) isn't public


def parse_account(text: str) -> tuple[str, str]:
    """What the user typed -> ("profiles", SteamID64) or ("id", custom URL name).

    Accepts "gabelogannewell", "https://steamcommunity.com/id/gabelogannewell/",
    "steamcommunity.com/profiles/76561197960287930" or "76561197960287930".
    """
    t = text.strip()
    m = re.search(r"steamcommunity\.com/(id|profiles)/([^/?#\s]+)", t, re.I)
    if m:
        kind, value = m.group(1).lower(), m.group(2)
    elif _ID64.match(t):
        kind, value = "profiles", t
    else:
        kind, value = "id", t
    if kind == "profiles" and not _ID64.match(value):
        raise SteamError(f"“{value}” isn't a SteamID64 (17 digits starting with 7656119).")
    if kind == "id" and not re.fullmatch(r"[\w-]{2,32}", value):
        raise SteamError("Enter your Steam profile name, profile URL or SteamID64.")
    return kind, value


def _cdata(tag: str, xml: str) -> str | None:
    m = re.search(rf"<{tag}>\s*(?:<!\[CDATA\[(.*?)\]\]>|([^<]*))\s*</{tag}>", xml, re.S)
    return (m.group(1) if m.group(1) is not None else m.group(2)).strip() if m else None


def parse_profile_xml(xml: str) -> Account:
    """The account in a steamcommunity.com/...?xml=1 answer."""
    id64 = _cdata("steamID64", xml)
    if not id64:
        error = _cdata("error", xml) or "Steam didn't return a profile."
        raise SteamError(
            f"{error} Use the name from your profile URL (steamcommunity.com/id/<name>) — "
            "your sign-in name isn't public — or paste the profile URL or SteamID64.")
    return Account(id64, _cdata("steamID", xml) or id64,
                   private=(_cdata("privacyState", xml) or "public") != "public")


def resolve(text: str) -> Account:
    """Look up the account; no key needed."""
    kind, value = parse_account(text)
    _limit.wait()
    r = session().get(PROFILE_XML.format(kind=kind, value=value), timeout=20)
    r.raise_for_status()
    return parse_profile_xml(r.text)


def add_account(conn: sqlite3.Connection, account: Account, wishlist: bool = False) -> int:
    """Store the account as a location (Games / PC). Returns its root id."""
    uid = PREFIX + account.id64
    conn.execute(
        "INSERT INTO volumes(uid, serial, label, last_mount) VALUES(?, NULL, ?, NULL) "
        "ON CONFLICT(uid) DO UPDATE SET label=excluded.label",
        (uid, f"Steam · {account.name}"))
    conn.execute("INSERT OR IGNORE INTO roots(volume_uid, rel_path, category, platform) "
                 "VALUES(?, '', 'game', 'PC')", (uid,))
    db.set_setting(conn, wishlist_setting(account.id64), "1" if wishlist else "0")
    return conn.execute("SELECT id FROM roots WHERE volume_uid=? AND rel_path=''",
                        (uid,)).fetchone()["id"]


# --------------------------------------------------------------------------- Web API

@dataclass
class OwnedGame:
    appid: int
    name: str
    playtime_min: float
    last_played: str | None     # "YYYY-MM-DD", None if never


def _clean_name(name: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[™®©]", "", name or "")).strip()


def parse_owned_games(data: dict) -> list[OwnedGame]:
    resp = data.get("response") or {}
    if "games" not in resp and "game_count" not in resp:
        raise SteamError("Steam returned no games for this account: its “Game details” are "
                         "private. Set them to Public in Steam › Edit Profile › Privacy "
                         "Settings, or use the Web API key of this same account.")
    games = []
    for g in resp.get("games") or []:
        if not g.get("appid"):
            continue
        last = g.get("rtime_last_played") or 0
        games.append(OwnedGame(
            int(g["appid"]), _clean_name(g.get("name") or "") or f"Steam app {g['appid']}",
            float(g.get("playtime_forever") or 0),
            datetime.fromtimestamp(last, timezone.utc).date().isoformat() if last else None))
    return games


def owned_games(id64: str, key: str) -> list[OwnedGame]:
    if not key:
        raise SteamError(NO_KEY)
    _limit.wait()
    r = session().get(OWNED_GAMES, timeout=30, params={
        "key": key, "steamid": id64, "include_appinfo": 1, "include_played_free_games": 1,
        "format": "json"})
    if r.status_code in (401, 403):
        raise SteamError(BAD_KEY.format(status=r.status_code))
    r.raise_for_status()
    return parse_owned_games(r.json())


def wishlist(id64: str) -> list[int]:
    """App ids on the account's wish list, its own order first. No key needed."""
    _limit.wait()
    r = session().get(WISHLIST, params={"steamid": id64}, timeout=30)
    r.raise_for_status()
    items = (r.json().get("response") or {}).get("items") or []
    items.sort(key=lambda i: (i.get("priority") or 1 << 30, i.get("date_added") or 0))
    return [int(i["appid"]) for i in items if i.get("appid")]


# --------------------------------------------------------------------------- sync

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _library_games(conn: sqlite3.Connection) -> tuple[dict[int, int], dict[str, int]]:
    """PC games in the library that aren't from an account: by app id and by title."""
    by_appid: dict[int, int] = {}
    by_title: dict[str, int] = {}
    for r in conn.execute(
            "SELECT id, steam_appid, COALESCE(user_title, title) t FROM items "
            "WHERE source IN ('disk', 'manual') AND COALESCE(user_category, category)='game' "
            "AND COALESCE(COALESCE(user_platform, platform), 'PC')='PC' ORDER BY id"):
        if r["steam_appid"]:
            by_appid.setdefault(r["steam_appid"], r["id"])
        by_title.setdefault(titles.normalize(r["t"]), r["id"])
    return by_appid, by_title


def _wanted_games(conn: sqlite3.Connection) -> tuple[dict[int, int], dict[str, int]]:
    by_appid: dict[int, int] = {}
    by_title: dict[str, int] = {}
    for r in conn.execute(
            "SELECT id, steam_appid, COALESCE(user_title, title) t FROM items WHERE source=? "
            "AND COALESCE(user_category, category)='game'", (db.WANTED,)):
        if r["steam_appid"]:
            by_appid.setdefault(r["steam_appid"], r["id"])
        by_title.setdefault(titles.normalize(r["t"]), r["id"])
    return by_appid, by_title


def save_games(conn: sqlite3.Connection, root_id: int, volume_uid: str, games: list[OwnedGame],
               cancelled: Callable[[], bool] = lambda: False) -> ScanResult:
    """Write an account's games to the library (see the module docstring)."""
    result = ScanResult()
    now = _now()
    by_appid, by_title = _library_games(conn)
    wish_appid, wish_title = _wanted_games(conn)
    seen: set[str] = set()
    for g in games:
        if cancelled():
            return result
        result.found += 1
        rel = f"app/{g.appid}"
        key = titles.normalize(g.name)
        own = conn.execute("SELECT id FROM items WHERE volume_uid=? AND rel_path=?",
                           (volume_uid, rel)).fetchone()
        disk = by_appid.get(g.appid) or by_title.get(key)
        if disk:  # also on disk: its library entry shows the play time too
            conn.execute("UPDATE items SET steam_appid=?, playtime_min=?, last_played=? WHERE id=?",
                         (g.appid, g.playtime_min, g.last_played, disk))
        seen.add(rel)
        item_id = own["id"] if own else wish_appid.get(g.appid) or wish_title.get(key)
        if item_id:
            conn.execute(
                "UPDATE items SET root_id=?, volume_uid=?, rel_path=?, source=?, title=?, "
                "platform='PC', steam_appid=?, playtime_min=?, last_played=?, missing=0, "
                "scanned_at=? WHERE id=?",
                (root_id, volume_uid, rel, SOURCE, g.name, g.appid, g.playtime_min,
                 g.last_played, now, item_id))
        else:
            conn.execute(
                "INSERT INTO items(root_id, volume_uid, rel_path, is_dir, category, platform, title, "
                "source, steam_appid, playtime_min, last_played, external_url, detected_how, "
                "scanned_at, added_at) VALUES(?, ?, ?, 0, 'game', 'PC', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (root_id, volume_uid, rel, g.name, SOURCE, g.appid, g.playtime_min, g.last_played,
                 f"https://store.steampowered.com/app/{g.appid}/", "Steam account", now, now))
            result.new += 1

    gone = [r["id"] for r in conn.execute(
        "SELECT id, rel_path FROM items WHERE root_id=? AND source=?", (root_id, SOURCE))
        if r["rel_path"] not in seen]
    conn.executemany("UPDATE items SET missing=1 WHERE id=?", [(i,) for i in gone])
    result.missing = len(gone)
    return result


def import_wishlist(conn: sqlite3.Connection, appids: list[int],
                    progress: Callable[[str], None] = lambda s: None,
                    cancelled: Callable[[], bool] = lambda: False) -> int:
    """Add wish-listed games you don't have yet to "Want to acquire". Returns how many."""
    known = {r["steam_appid"] for r in conn.execute(
        "SELECT steam_appid FROM items WHERE steam_appid IS NOT NULL")}
    _, have_title = _library_games(conn)
    added = 0
    for n, appid in enumerate(appids, 1):
        if cancelled():
            break
        if appid in known:
            continue
        progress(f"Steam wish list {n}/{len(appids)}")
        try:
            data = steam_store.app_details(appid)
        except Exception as e:  # one unreachable app shouldn't stop the rest
            log.warning("steam wish list: no store details for %s: %s", appid, e)
            continue
        name = _clean_name(data.get("name") or "")
        if not name or titles.normalize(name) in have_title:
            continue
        date = (data.get("release_date") or {}).get("date") or ""
        year = next((int(w) for w in re.findall(r"\b\d{4}\b", date)), None)
        library.add_wanted(conn, "game", name, year, steam_appid=appid,
                           url=f"https://store.steampowered.com/app/{appid}/")
        known.add(appid)
        added += 1
    return added


def sync(conn: sqlite3.Connection, root_id: int,
         progress: Callable[[str], None] | None = None,
         cancelled: Callable[[], bool] = lambda: False) -> ScanResult:
    """Rescan of an account location: games and play time, then the wish list.
    Without a key only the wish list is read, then SteamError says why."""
    progress = progress or (lambda s: None)
    uid = conn.execute("SELECT volume_uid FROM roots WHERE id=?", (root_id,)).fetchone()["volume_uid"]
    id64 = uid[len(PREFIX):]
    key = db.get_setting(conn, KEY_SETTING).strip()
    result = ScanResult()
    if key:
        progress("Reading Steam games")
        result = save_games(conn, root_id, uid, owned_games(id64, key), cancelled)
        conn.commit()
        log.info("steam %s: %d games (%d new, %d gone)", id64, result.found, result.new,
                 result.missing)
    if db.get_setting(conn, wishlist_setting(id64)) == "1" and not cancelled():
        try:
            added = import_wishlist(conn, wishlist(id64), progress, cancelled)
            log.info("steam %s: %d games added from the wish list", id64, added)
        except Exception as e:
            log.warning("steam wish list for %s failed: %s", id64, e)
        conn.commit()
    if not key:
        raise SteamError(NO_KEY)
    return result
