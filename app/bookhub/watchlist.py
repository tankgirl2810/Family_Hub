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
    return [word for word in db.slug(text or "").split("-") if word]


def matches(query, title, year=None):
    """Does a title fit what someone typed? Every word typed must be in the title ("dune part three" fits
    "Dune: Part Three"); a year typed must be the year."""
    typed, in_title = _words(query), set(_words(title))
    years = [word for word in typed if re.fullmatch(r"(19|20)\d\d", word)]
    rest = [word for word in typed if word not in years and word not in ("the", "a", "an")]
    if years and str(year or "") not in years:
        return False
    return bool(rest) and all(word in in_title for word in rest)


def _day(text):
    return (text or "")[:10]


def _nice(day):
    try:
        when = date.fromisoformat(day)
    except ValueError:
        return day
    return "%d %s %d" % (when.day, when.strftime("%b"), when.year)


def _film(item, today):
    """How a film stands: words for it, and whether it is still to come."""
    cinema, digital, disc = _day(item.get("inCinemas")), _day(item.get("digitalRelease")), _day(item.get("physicalRelease"))
    out_at = min([day for day in (digital, disc) if day] or [""])
    # these are all "YYYY-MM-DD" strings, which happily compare just like dates do
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
    first, next_ep, status = _day(item.get("firstAired")), _day(item.get("nextAiring")), (item.get("status") or "").lower()
    if next_ep:
        return "Next episode %s" % _nice(next_ep), next_ep > today
    if status == "upcoming" or (first and first > today) or not first:
        return ("Starts %s" % _nice(first)) if first and first > today else "Announced", True
    return ("Running" if status == "continuing" else "Ended"), False


def check_media(hub, kind, value):
    media = hub.media
    arr = media.arr(kind)
    film = kind == "film"
    external_id = lambda item: item.get("tmdbId") if film else item.get("tvdbId")  # noqa: E731
    found = arr.get("movie/lookup" if film else "series/lookup", term=value) or []
    library = {external_id(item): item for item in arr.get("movie" if film else "series") or []}
    today = date.today().isoformat()
    results = []
    for item in found:
        if not matches(value, item.get("title"), item.get("year")):
            continue
        ext = external_id(item)
        have = library.get(ext)
        words, coming = (_film if film else _show)(have or item, today)
        results.append({"key": "%s:%s" % (kind, ext), "title": item.get("title") or "", "year": item.get("year"),
                        "words": words, "coming": coming, "external_id": ext, "arr_id": have.get("id") if have else None,
                        "in_library": bool(have), "poster": media.poster(item, kind)})
    # the stuff that's still coming goes first, that's the whole point of a watchlist. then newest
    results.sort(key=lambda result: (not result["coming"], -(result["year"] or 0)))
    return results[:MAX_FOUND]


def check_author(hub, value):
    """New and coming books by an author that the library does not have."""
    owned = {db.slug(book["title"]) for book in hub.library.books() if value.lower() in book["author"].lower()}
    this_year = date.today().year
    books, seen = [], set()
    by_them = lambda names: any(set(_words(value)) <= set(_words(name)) for name in names)  # noqa: E731

    def add(key, title, when, cover):
        mark = db.slug(title)
        if not title or mark in seen or mark in owned:
            return
        seen.add(mark)
        today = date.today().isoformat()
        # "when" might be just "2027" or a full date; chop today to the same length so they compare fairly
        coming = bool(when) and when > today[:len(when)]
        books.append({"key": key, "title": title, "year": int(when[:4]) if when[:4].isdigit() else None,
                    "words": ("Coming %s" % _nice(when) if len(when) == 10 else "Coming %s" % when) if coming else
                             ("Published %s" % (when[:4] or "recently")),
                    "coming": coming, "cover": cover})

    docs = catalogues._get(OPEN_LIBRARY, {"author": value, "sort": "new", "limit": 30,
                                          "fields": "key,title,first_publish_year,author_name,cover_i"}).get("docs") or []
    for doc in docs:
        if not by_them(doc.get("author_name") or []):
            continue
        year = doc.get("first_publish_year") or 0
        if year >= this_year - 1:
            add("ol:" + (doc.get("key") or doc.get("title", "")), doc.get("title"), str(year),
                "https://covers.openlibrary.org/b/id/%s-M.jpg" % doc["cover_i"] if doc.get("cover_i") else "")
    key = hub.config.google_books_key
    if key:
        items = catalogues._get(catalogues.GOOGLE, {"q": 'inauthor:"%s"' % value, "orderBy": "newest", "maxResults": 20,
                                                    "key": key}).get("items") or []
        for volume in items:
            info = volume.get("volumeInfo") or {}
            when = info.get("publishedDate") or ""
            if not by_them(info.get("authors") or []) or when[:4] < str(this_year - 1):
                continue
            thumbnail = ((info.get("imageLinks") or {}).get("thumbnail") or "").replace("http://", "https://")
            add("gb:" + volume.get("id", ""), info.get("title"), when, thumbnail)
    books.sort(key=lambda book: (not book["coming"], -(book["year"] or 0)))
    return books[:MAX_FOUND]


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
    if seen is None:  # first ever look: everything there now is the baseline, not "news!!"
        seen = [item["key"] for item in found]
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
    for item in found:
        if not item["coming"] or item["in_library"] or not item.get("external_id"):
            continue
        try:
            added = hub.media.request(row["kind"], item["external_id"], who)
            item.update(in_library=True, arr_id=added.get("id"), requested=True)
        except MediaError as exc:
            item["request_error"] = "Could not get it automatically: %s" % exc
    return found


def _allowed(hub):
    from .api import media_allowed_ids
    return media_allowed_ids(hub)


def describe(row):
    found = json.loads(row["found"] or "[]")
    seen = set(json.loads(row["seen"] or "[]") or [])
    for item in found:
        item["new"] = item["key"] not in seen
    return {"id": row["id"], "kind": row["kind"], "value": row["value"], "auto": bool(row["auto"]),
            "checked": row["checked"] or "", "error": row["error"] or "", "found": found,
            "new": sum(1 for item in found if item["new"])}


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
    for row in [row for row in rows if due(row)][:limit]:
        if row["kind"] in ("film", "show") and not hub.media.configured(row["kind"]):
            continue
        check(hub, row)
