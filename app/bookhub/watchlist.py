"""The watchlist: authors, films and TV shows someone is waiting for, checked every few hours.

Each person's list lives in the watch table (kind "author", "film" or "show", plus the "tag" kind Discover
uses for suggestions). What each check finds is kept with the entry, so the Watchlist page shows it at once
and anything new since the person last looked is marked.

  author   new and coming books by that author (Open Library; Google Books too when its key is saved),
           leaving out what the library already has. They are found by hand in Discover.
  film     films whose title matches, from the same lookup as Movies and TV, Search: announced, in
  show     cinemas, out, or already on the server; for shows, when the next episode airs.

A film or show entry can be set to request a match by itself as soon as one appears that is not out yet
(and not on the server). Radarr and Sonarr then download it once it is released.
"""
import calendar
import json
import re
import time
from datetime import date

from . import catalogues, db
from .media import MediaError

KINDS = ("author", "film", "show")
CHECK_HOURS = 6
MAX_FOUND = 8
OPEN_LIBRARY = "https://openlibrary.org/search.json"


def _words(text):
    return [w for w in db.slug(text or "").split("-") if w]


def matches(query, title, year=None):
    """Does a title fit what someone typed? Every word typed must be in the title ("dune part three" fits
    "Dune: Part Three"); a year typed must be the year."""
    q, t = _words(query), set(_words(title))
    years = [w for w in q if re.fullmatch(r"(19|20)\d\d", w)]
    rest = [w for w in q if w not in years and w not in ("the", "a", "an")]
    if years and str(year or "") not in years:
        return False
    return bool(rest) and all(w in t for w in rest)


def _day(text):
    return (text or "")[:10]


def _nice(day):
    try:
        d = date.fromisoformat(day)
    except ValueError:
        return day
    return "%d %s %d" % (d.day, d.strftime("%b"), d.year)


def _film(item, today):
    """How a film stands: words for it, and whether it is still to come."""
    cinema, digital, disc = _day(item.get("inCinemas")), _day(item.get("digitalRelease")), _day(item.get("physicalRelease"))
    out_at = min([d for d in (digital, disc) if d] or [""])
    status = (item.get("status") or "").lower()
    if out_at and out_at <= today:
        return "Out now", False
    if out_at:
        return "Out to watch at home from %s" % _nice(out_at), True
    if cinema and cinema > today:
        return "In cinemas from %s" % _nice(cinema), True
    if cinema:
        return "In cinemas now; not out at home yet", True
    if status == "released":
        return "Out now", False
    return "Announced", True


def _show(item, today):
    first, nxt, status = _day(item.get("firstAired")), _day(item.get("nextAiring")), (item.get("status") or "").lower()
    if nxt:
        return "Next episode %s" % _nice(nxt), nxt > today
    if status == "upcoming" or (first and first > today) or not first:
        return ("Starts %s" % _nice(first)) if first and first > today else "Announced", True
    return ("Running" if status == "continuing" else "Ended"), False


def check_media(hub, kind, value):
    media = hub.media
    arr = media.arr(kind)
    found = arr.get("movie/lookup" if kind == "film" else "series/lookup", term=value) or []
    library = {(i.get("tmdbId") if kind == "film" else i.get("tvdbId")): i for i in arr.get("movie" if kind == "film" else "series") or []}
    today = date.today().isoformat()
    out = []
    for item in found:
        if not matches(value, item.get("title"), item.get("year")):
            continue
        ext = item.get("tmdbId") if kind == "film" else item.get("tvdbId")
        have = library.get(ext)
        words, coming = (_film if kind == "film" else _show)(have or item, today)
        out.append({"key": "%s:%s" % (kind, ext), "title": item.get("title") or "", "year": item.get("year"),
                    "words": words, "coming": coming, "external_id": ext, "arr_id": have.get("id") if have else None,
                    "in_library": bool(have), "poster": media.poster(item, kind)})
    # What is still to come first (that is what a watchlist is for), then the newest.
    out.sort(key=lambda f: (not f["coming"], -(f["year"] or 0)))
    return out[:MAX_FOUND]


def check_author(hub, value):
    """New and coming books by an author that the library does not have."""
    owned = {db.slug(b["title"]) for b in hub.library.books() if any(w in b["author"].lower() for w in [value.lower()])}
    this_year = date.today().year
    out, seen = [], set()

    def add(key, title, when, cover):
        mark = db.slug(title)
        if not title or mark in seen or mark in owned:
            return
        seen.add(mark)
        today = date.today().isoformat()
        coming = bool(when) and when > today[:len(when)]
        out.append({"key": key, "title": title, "year": int(when[:4]) if when[:4].isdigit() else None,
                    "words": ("Coming %s" % _nice(when) if len(when) == 10 else "Coming %s" % when) if coming else
                             ("Published %s" % (when[:4] or "recently")),
                    "coming": coming, "cover": cover})

    docs = catalogues._get(OPEN_LIBRARY, {"author": value, "sort": "new", "limit": 30,
                                          "fields": "key,title,first_publish_year,author_name,cover_i"}).get("docs") or []
    for d in docs:
        names = d.get("author_name") or []
        if not any(set(_words(value)) <= set(_words(n)) for n in names):
            continue
        year = d.get("first_publish_year") or 0
        if year >= this_year - 1:
            add("ol:" + (d.get("key") or d.get("title", "")), d.get("title"), str(year),
                "https://covers.openlibrary.org/b/id/%s-M.jpg" % d["cover_i"] if d.get("cover_i") else "")
    key = hub.config.google_books_key
    if key:
        items = catalogues._get(catalogues.GOOGLE, {"q": 'inauthor:"%s"' % value, "orderBy": "newest", "maxResults": 20,
                                                    "key": key}).get("items") or []
        for it in items:
            info = it.get("volumeInfo") or {}
            when = info.get("publishedDate") or ""
            if not any(set(_words(value)) <= set(_words(n)) for n in info.get("authors") or []) or when[:4] < str(this_year - 1):
                continue
            add("gb:" + it.get("id", ""), info.get("title"), when, ((info.get("imageLinks") or {}).get("thumbnail") or "").replace("http://", "https://"))
    out.sort(key=lambda x: (not x["coming"], -(x["year"] or 0)))
    return out[:MAX_FOUND]


def check(hub, row):
    """Look again for one entry, keep what was found, and request coming films and shows if asked to.
    Returns the entry as the page shows it."""
    con = hub.con()
    error = ""
    try:
        if row["kind"] == "author":
            found = check_author(hub, row["value"])
        else:
            found = check_media(hub, row["kind"], row["value"])
    except (MediaError, catalogues.LookupError_) as exc:
        found, error = json.loads(row["found"] or "[]"), str(exc)
    seen = json.loads(row["seen"] or "null")
    if seen is None:  # the first look: what is there already is the starting point, not news
        seen = [f["key"] for f in found]
    if row["auto"] and row["kind"] in ("film", "show") and not error:
        found = _request_coming(hub, row, found)
    con.execute("UPDATE watch SET found=?, seen=?, checked=?, error=? WHERE id=?",
                (json.dumps(found), json.dumps(seen), db.now(), error, row["id"]))
    con.commit()
    return describe(con.execute("SELECT * FROM watch WHERE id=?", (row["id"],)).fetchone())


def _request_coming(hub, row, found):
    from .api import can_request_media
    who = hub.con().execute("SELECT * FROM profiles WHERE id=?", (row["profile_id"],)).fetchone()
    if not who or not can_request_media(who, _allowed(hub)):
        return found
    for f in found:
        if f["coming"] and not f["in_library"] and f.get("external_id"):
            try:
                added = hub.media.request(row["kind"], f["external_id"], who)
                f.update(in_library=True, arr_id=added.get("id"), requested=True)
            except MediaError as exc:
                f["request_error"] = "Could not get it automatically: %s" % exc
    return found


def _allowed(hub):
    from .api import media_allowed_ids
    return media_allowed_ids(hub)


def describe(row):
    found = json.loads(row["found"] or "[]")
    seen = set(json.loads(row["seen"] or "[]") or [])
    for f in found:
        f["new"] = f["key"] not in seen
    return {"id": row["id"], "kind": row["kind"], "value": row["value"], "auto": bool(row["auto"]),
            "checked": row["checked"] or "", "error": row["error"] or "", "found": found,
            "new": sum(1 for f in found if f["new"])}


def due(row):
    if not row["checked"]:
        return True
    try:
        stamp = calendar.timegm(time.strptime(row["checked"][:19], "%Y-%m-%dT%H:%M:%S"))  # db.now() is UTC
    except ValueError:
        return True
    return time.time() - stamp >= CHECK_HOURS * 3600


def run(hub, limit=3):
    """Check a few entries that are due; run every minute by the background jobs."""
    rows = hub.con().execute("SELECT * FROM watch WHERE kind IN ('author','film','show') ORDER BY checked").fetchall()
    for row in [r for r in rows if due(r)][:limit]:
        if row["kind"] in ("film", "show") and not hub.media.configured(row["kind"]):
            continue
        check(hub, row)
