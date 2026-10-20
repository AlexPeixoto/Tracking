"""What's coming for an item, for its page: the next episode of a show you watch, when
an anime's next episode airs, a game's release date and its current Steam price / sale.
Everything keyless and cached for a few hours; fetched in the background."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime

from . import db

log = logging.getLogger(__name__)
FRESH = 0.25  # days: prices and air dates change


@dataclass(frozen=True)
class Upcoming:
    text: str
    url: str | None = None
    highlight: bool = False   # worth noticing (a sale, an episode this week)


def _when(d: date, today: date | None = None) -> str:
    """"Fri 24 Oct (in 4 days)" / "(today)" / "(tomorrow)"."""
    today = today or date.today()
    days = (d - today).days
    rel = {0: "today", 1: "tomorrow"}.get(days, f"in {days} days" if days > 0 else f"{-days} days ago")
    return f"{d:%a} {d.day} {d:%b}" + (f" {d.year}" if d.year != today.year else "") + f" ({rel})"


def _soon(d: date, today: date | None) -> bool:
    return 0 <= (d - (today or date.today())).days <= 7


# --------------------------------------------------------------------------- TV

def tv(item: db.Item, today: date | None = None) -> list[Upcoming]:
    from .providers.base import cached_json, session

    ext = item.external_id or ""
    if ext.startswith("tt"):  # matched by IMDb first: ask TVmaze which show that is
        found = cached_json("tvmaze", f"imdb-{ext}", 30, lambda: session().get(
            "https://api.tvmaze.com/lookup/shows", params={"imdb": ext}, timeout=20).json())
        ext = str((found or {}).get("id") or "")
    if not ext.isdigit():
        return []
    show = cached_json("tvmaze", f"next-{ext}", FRESH, lambda: session().get(
        f"https://api.tvmaze.com/shows/{ext}", params={"embed": "nextepisode"}, timeout=20).json())
    nxt = (show.get("_embedded") or {}).get("nextepisode")
    if nxt and nxt.get("airdate"):
        d = date.fromisoformat(nxt["airdate"])
        name = f" “{nxt['name']}”" if nxt.get("name") else ""
        return [Upcoming(f"Next episode: S{nxt.get('season')}E{nxt.get('number')}{name} · {_when(d, today)}",
                         nxt.get("url"), highlight=_soon(d, today))]
    status = show.get("status")
    if status == "Ended":
        return [Upcoming("The show has ended")]
    if status:
        return [Upcoming(f"{status}: no next episode announced yet")]
    return []


# --------------------------------------------------------------------------- anime

def anime(item: db.Item, today: date | None = None) -> list[Upcoming]:
    if item.kind == "movie":
        return []
    from .providers import anilist

    m = re.search(r"anilist\.co/anime/(\d+)", item.external_url or "")
    aid = int(m.group(1)) if m else (anilist.find(item.title, item.year, item.kind) or {}).get("id")
    if not aid:
        return []
    info = anilist.airing(int(aid))
    nxt = info.get("nextAiringEpisode")
    url = f"https://anilist.co/anime/{aid}"
    if nxt:
        d = datetime.fromtimestamp(nxt["airingAt"]).date()
        return [Upcoming(f"Episode {nxt['episode']} airs {_when(d, today)}", url, highlight=_soon(d, today))]
    if info.get("status") == "FINISHED":
        n = info.get("episodes")
        return [Upcoming("Finished airing" + (f" ({n} episodes)" if n else ""), url)]
    if info.get("status") == "NOT_YET_RELEASED":
        return [Upcoming("Not aired yet", url)]
    return []


# --------------------------------------------------------------------------- games

def game(item: db.Item, today: date | None = None) -> list[Upcoming]:
    from .providers.base import cached_json
    from .providers.steam import DETAILS_URL, _get

    appid = item.steam_appid
    if not appid:
        m = re.search(r"store\.steampowered\.com/app/(\d+)", item.external_url or "")
        appid = int(m.group(1)) if m else None
    if not appid:
        return []
    data = cached_json("steam", f"price-{appid}", FRESH,
                       lambda: _get(DETAILS_URL, {"appids": appid, "cc": "us"}))
    d = (data.get(str(appid)) or {}).get("data") or {}
    url = f"https://store.steampowered.com/app/{appid}/"
    out = []
    release = d.get("release_date") or {}
    if release.get("coming_soon"):
        out.append(Upcoming(f"Releases: {release.get('date') or 'date not announced'}", url))
    price = d.get("price_overview") or {}
    if price.get("discount_percent"):
        sale = f"−{price['discount_percent']}% · {price.get('final_formatted')}"
        out.append(Upcoming(f"On sale on Steam: {sale} (was {price.get('initial_formatted')})", url,
                            highlight=True))
    elif price.get("final_formatted") and item.source == db.WANTED:
        out.append(Upcoming(f"Steam price: {price['final_formatted']}", url))
    elif d.get("is_free") and item.source == db.WANTED:
        out.append(Upcoming("Free on Steam", url))
    return out


def for_item(item: db.Item, today: date | None = None) -> list[Upcoming]:
    """Everything worth saying about what's next for ``item`` (may go online)."""
    fn = {"tv": tv, "anime": anime, "game": game}.get(item.category)
    if not fn:
        return []
    try:
        return fn(item, today)
    except Exception as e:  # offline, a service down: just nothing to show
        log.debug("coming up for %r: %s", item.title, e)
        return []
