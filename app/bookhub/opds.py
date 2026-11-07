"""A private book catalogue for each profile, for reading apps to fetch books from.

Readest, KOReader and most other reading apps can add an "OPDS catalogue".
Point one at  http://<book hub>/opds  and sign in with the profile name and
the profile's sync password. "My books" lists what that person has chosen;
the whole library can be browsed and searched as well.

A comic series (graphic novels and manga volumes, CBZ or CBR) is one entry, not one per volume. Its download
is one EPUB with a contents entry for each volume (comicbook.py), so a reading app shows one book you can
jump around in, from the first volume to the last; the same pages as one CBZ are offered too. Choosing any
volume for My books brings the whole series.

A series followed chapter by chapter (series.py) can be put in My books as well: it comes as the CBZ parts the
server keeps (chapterfile.py), with a contents entry per chapter, and when new chapters arrive the last part comes
again, longer. Only the CBZ is offered: Readest's auto-download picks an EPUB over a CBZ whenever both are there.

A joined series can take minutes to put together, so it is made in the background and kept. Until it is
ready, My books lists the series without a download (Readest looks again every few minutes) and asking for
the file answers "try again shortly". A long series comes in parts of about 1 GB, never splitting a volume,
since a tablet's reading app cannot open a much bigger book; a new volume only changes the last part.
Every joined book is recorded (joined.py) so the reading app's progress in it reaches the volumes.
"""
import functools
import hashlib
import hmac
import json
import logging
import shutil
import threading
import time
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from urllib.parse import quote

from flask import Blueprint, Response, current_app, has_app_context, request, send_file

from . import chapterfile, db, kinds, kosync

bp = Blueprint("opds", __name__, url_prefix="/opds")

NAV = "application/atom+xml;profile=opds-catalog;kind=navigation"
ACQ = "application/atom+xml;profile=opds-catalog;kind=acquisition"
PAGE = 50
MIME = {
    "EPUB": "application/epub+zip", "PDF": "application/pdf", "MOBI": "application/x-mobipocket-ebook",
    "AZW3": "application/vnd.amazon.ebook", "CBZ": "application/vnd.comicbook+zip",
    "CBR": "application/vnd.comicbook-rar", "TXT": "text/plain",
}
MAX_TRIES, LOCK_SECONDS = 10, 60
# control characters are illegal in XML (tab, newline and return excepted). str.translate() with this deletes them
_CONTROL = dict.fromkeys(code for code in range(32) if code not in (9, 10, 13))
_misses = {}  # wrong-password counters, per name


def _profile():
    auth = request.authorization
    if not auth or not auth.username or auth.password is None:
        return None
    name = auth.username.strip()
    misses, last_miss = _misses.get(name.lower(), [0, 0])
    if misses >= MAX_TRIES and time.time() - last_miss < LOCK_SECONDS:
        return None  # ten wrong guesses in a minute? take a breather
    row = current_app.hub.con().execute("SELECT * FROM profiles WHERE name=?", (name,)).fetchone()
    # compare_digest takes the same time right or wrong, so nobody can guess the key one letter at a time
    if row and row["sync_key"] and hmac.compare_digest(row["sync_key"], kosync.sync_key_for(auth.password)):
        _misses.pop(name.lower(), None)
        return row
    _misses[name.lower()] = [misses + 1 if time.time() - last_miss < LOCK_SECONDS else 1, time.time()]
    return None


def signed_in(view):
    @functools.wraps(view)
    def wrapper(*args, **kwargs):
        who = _profile()
        if who is None:
            return Response("Sign in with your Family Hub profile name and sync password.", 401,
                            {"WWW-Authenticate": 'Basic realm="Family Hub", charset="UTF-8"'})
        return view(who, *args, **kwargs)
    return wrapper


def _feed(title, self_href, kind):
    feed = ET.Element("feed", {"xmlns": "http://www.w3.org/2005/Atom", "xmlns:dc": "http://purl.org/dc/terms/",
                               "xmlns:opds": "http://opds-spec.org/2010/catalog"})
    ET.SubElement(feed, "id").text = "urn:bookhub:" + self_href
    ET.SubElement(feed, "title").text = title
    ET.SubElement(feed, "updated").text = db.now()
    ET.SubElement(ET.SubElement(feed, "author"), "name").text = "Family Hub"
    ET.SubElement(feed, "link", {"rel": "self", "href": self_href, "type": kind})
    ET.SubElement(feed, "link", {"rel": "start", "href": "/opds", "type": NAV})
    ET.SubElement(feed, "link", {"rel": "search", "href": "/opds/opensearch.xml",
                                 "type": "application/opensearchdescription+xml"})
    return feed


def _reply(root, kind):
    body = b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="utf-8")
    return Response(body, content_type=kind + ";charset=utf-8")


def _book_entry(feed, book, hub, shared=(), roles=None):
    """shared: keys more than one library book has, whose entries get the book's id on their own id too.
    roles: {book_key: role} of game books, put in as a category ("Adventure") so a reading app can show it."""
    entry = ET.SubElement(feed, "entry")
    # Titles as Family Hub shows them: "Cherry Magic! · Volume 2", not the file name the volume came with.
    title = book.get("shown_title") or book["title"]
    if book.get("subtitle"):
        title += ": " + book["subtitle"]
    ET.SubElement(entry, "title").text = title.translate(_CONTROL)
    # two files can come out as the same book (same title and author, no ISBN), and Atom ids must be unique
    # or the app folds them into one. Only those get the id added, so nobody else's ids change
    ET.SubElement(entry, "id").text = "urn:bookhub:book:" + book["key"] + (":%d" % book["id"] if book["key"] in shared else "")
    ET.SubElement(entry, "updated").text = (book["added"] or db.now()[:19]) + "Z"
    # a comic's "author" is sometimes really its story title (calibre.py hides those), so no author then
    for name in (book["authors"] if book.get("shown_author", book["author"]) else []):
        ET.SubElement(ET.SubElement(entry, "author"), "name").text = name
    if book["synopsis"]:
        ET.SubElement(entry, "summary", {"type": "text"}).text = book["synopsis"][:2000].translate(_CONTROL)
    for tag in book["tags"]:
        ET.SubElement(entry, "category", {"term": tag, "label": tag})
    role = (roles or {}).get(book["key"])
    if role and role not in book["tags"]:
        ET.SubElement(entry, "category", {"term": role, "label": role})
    if book["has_cover"] or hub.covers.path(book["key"]):  # the library's cover, or one chosen in Family Hub
        href = "/opds/cover/%d" % book["id"]
        ET.SubElement(entry, "link", {"rel": "http://opds-spec.org/image", "href": href, "type": "image/jpeg"})
        ET.SubElement(entry, "link", {"rel": "http://opds-spec.org/image/thumbnail", "href": href, "type": "image/jpeg"})
    formats = sorted(book["formats"], key=lambda fmt: (fmt != "EPUB", fmt))  # EPUB first, reading apps grab the first link
    for fmt in formats:
        ET.SubElement(entry, "link", {"rel": "http://opds-spec.org/acquisition", "type": MIME.get(fmt, "application/octet-stream"),
                                      "href": "/opds/book/%d/%s" % (book["id"], fmt.lower())})
    if "EPUB" not in book["formats"] and (hub.config.converted_dir / ("%d.epub" % book["id"])).is_file():
        ET.SubElement(entry, "link", {"rel": "http://opds-spec.org/acquisition", "type": MIME["EPUB"],
                                      "href": "/opds/book/%d/converted" % book["id"], "title": "Converted EPUB"})


COMIC = {"CBZ", "CBR"}
KEEP_SERIES_FILES = 8  # joined series kept ready to send; older ones are made again when asked for
_building = threading.Lock()  # the joined CBZ, made while the app waits
_jobs_lock = threading.Lock()
_jobs, _failed, _progress = {}, {}, {}  # books being made: the jobs, (when, why) one last failed, (pages done, pages)
PART_BYTES = 1_000_000_000  # a joined book is split into parts of about this size
CHAPTERS_PER_PART = chapterfile.CHAPTERS_PER_PART  # chapters are not measured before they are fetched; 50 is usually well under 1 GB
RETRY_FAILED = 600  # seconds before a book that could not be made is tried again
FEED_WAIT, FILE_WAIT = 3, 20  # seconds a list or a download waits for a book still being made
log = logging.getLogger("bookhub")


def fetched(hub, who, item, version):
    """Note that this person's reading app downloaded an item (a book, or one part of a joined series) in this
    version. My books compares it with the version now offered."""
    if who and who != "guest":
        db.kv_set(hub.con(), "fetched:%d:%s" % (who["id"], item), json.dumps({"version": str(version), "at": db.now()}))


def fetched_version(hub, profile_id, item):
    kept = db.kv_get(hub.con(), "fetched:%d:%s" % (profile_id, item))
    try:
        return json.loads(kept)["version"] if kept else None
    except (ValueError, KeyError, TypeError):
        return None


def series_item(series, k):
    return "series:%s:%d" % (db.slug(series), k)


def _comic(book):
    return bool(set(book["formats"]) & COMIC)


def _volumes(name, everything):
    """Every comic volume of a series, in reading order, each volume once: a second copy of volume 2 (a file
    the library took in twice) would otherwise put volume 2 in the joined book twice. The earliest copy wins."""
    low = name.lower()
    found = sorted((book for book in everything if (book.get("series") or "").strip().lower() == low),
                   key=lambda book: (book.get("series_index") or 0, book["id"]))
    volumes, numbers = [], set()
    for book in found:
        number = book.get("series_index")
        if number and number in numbers:
            continue
        numbers.add(number)
        volumes.append(book)
    return volumes


def _gather(books, everything, kind_of=None):
    """The volumes of a comic series become one item, placed where the first of them came in the list.
    Game books are never joined: a deck of spell cards in CBZ files is a set of handouts, not a comic to read
    through, so each stays its own entry."""
    if kind_of is None:
        kind_of = current_app.hub.kind_index() if has_app_context() else {}
    # The library sorted by series once, rather than searched again for every comic in the list.
    by_series = {}
    for book in everything:
        by_series.setdefault((book.get("series") or "").strip().lower(), []).append(book)
    items, done, single = [], set(), set()
    for book in books:
        name = (book.get("series") or "").strip()
        if name and _comic(book) and name.lower() not in single and kind_of.get(book["key"]) != kinds.TABLETOP:
            if name.lower() in done:
                continue
            volumes = _volumes(name, by_series.get(name.lower(), []))
            if len(volumes) > 1 and all(_comic(volume) for volume in volumes):
                done.add(name.lower())
                items.append({"series": volumes[0]["series"], "volumes": volumes})
                continue
            single.add(name.lower())  # not a joined series: its books are listed one by one
        items.append(book)
    return items


def _part(volume):
    return volume.get("part") or ("Volume %g" % volume["series_index"] if volume.get("series_index") else volume.get("shown_title") or volume["title"])


def _file_size(volume):
    return next((volume["sizes"].get(fmt) or 0 for fmt in ("CBZ", "CBR") if fmt in volume["formats"]), 0)


def _shrink_ratio(hub, volume):
    """How much of a volume's size is left once its pages are made tablet-sized, from a few of its pages.
    Kept, so the same series always splits in the same places."""
    key = "shrink:%d:%d" % (volume["id"], _file_size(volume))
    kept = db.kv_get(hub.con(), key)
    if kept:
        return float(kept)
    from . import comicbook, seriesfile
    ratio = 1.0
    try:
        path = hub.library.file_path(volume, next(fmt for fmt in ("CBZ", "CBR") if fmt in volume["formats"]))
        if path and zipfile.is_zipfile(path):
            pages = seriesfile._pages(Path(path), None)
            # three pages from a third of the way in: skips the cover and the credits, which shrink weirdly
            sample = pages[len(pages) // 3:len(pages) // 3 + 3]
            before = after = 0
            for name, read in sample:
                data = read()
                before += len(data)
                after += len(comicbook.fit(data, "." + name.rsplit(".", 1)[-1].lower())[0])
            if before:
                ratio = max(0.02, min(1.0, after / before))
    except (OSError, zipfile.BadZipFile, ValueError, StopIteration):
        pass
    db.kv_set(hub.con(), key, str(ratio))
    hub.con().commit()
    return ratio


def _split(hub, volumes):
    """The volumes in parts of about PART_BYTES once made tablet-sized, each volume whole. Earlier parts stay
    the same when volumes are added, so a reading app only fetches the last part again."""
    ratio = _shrink_ratio(hub, volumes[0])
    parts, size = [[]], 0
    for volume in volumes:
        guess = _file_size(volume) * ratio
        if parts[-1] and size + guess > PART_BYTES:
            parts.append([])
            size = 0
        parts[-1].append(volume)
        size += guess
    return parts


def _part_title(series, part, part_no, count):
    """"Series · Part 2 (Volumes 5–9)", or the series alone when it comes as one book."""
    if count == 1:
        return series
    first, last = _part(part[0]), _part(part[-1])
    word, _, number = first.partition(" ")
    if len(part) == 1:
        span = first
    elif number and last.startswith(word + " "):
        span = "%ss %s–%s" % (word, number, last[len(word) + 1:])  # "Volumes 5–9", "Issues 1–12"
    else:
        span = "%s – %s" % (first, last)
    return "%s · Part %d (%s)" % (series, part_no, span)


def _parts_of(hub, name):
    """(volumes, parts) of a comic series asked for by name, or ([], [])."""
    volumes = _volumes(name, hub.library.books()) if name else []
    if len(volumes) < 2 or not all(_comic(volume) for volume in volumes):
        return [], []
    return volumes, _split(hub, volumes)


def _series_entry(feed, item, hub, prepare=False):
    """One entry per part (most series have one). prepare: start making the books not ready yet (My books)."""
    parts = _split(hub, item["volumes"])
    for part_no, volumes in enumerate(parts, 1):
        _series_part_entry(feed, item["series"], volumes, part_no, len(parts), hub, prepare)


def _series_part_entry(feed, series, volumes, part_no, count, hub, prepare):
    """Until the book is ready the entry has no download and its own id, so an app that downloads new entries
    by itself fetches it once it is ready."""
    ready = _series_book_job(hub, volumes, wait=FEED_WAIT) if prepare else True
    entry = ET.SubElement(feed, "entry")
    ET.SubElement(entry, "title").text = _part_title(series, volumes, part_no, count).translate(_CONTROL)
    # The volume count is part of the id, so when a new volume arrives the app sees a new, longer book.
    ET.SubElement(entry, "id").text = "urn:bookhub:series:%s:%s%d%s" % (
        db.slug(series), "part%d:" % part_no if count > 1 else "", len(volumes), "" if ready else ":making")
    ET.SubElement(entry, "updated").text = (max(volume["added"] for volume in volumes) or db.now()[:19]) + "Z"
    first = volumes[0]
    for name in (first["authors"] if first.get("shown_author", first["author"]) else []):
        ET.SubElement(ET.SubElement(entry, "author"), "name").text = name
    ET.SubElement(entry, "summary", {"type": "text"}).text = (
        "%d volumes in one book, read straight through: %s to %s.%s" % (
            len(volumes), _part(volumes[0]), _part(volumes[-1]),
            "" if ready else " Being put together on the server; it arrives by itself in a few minutes.")).translate(_CONTROL)
    cover = next((volume for volume in volumes if volume["has_cover"] or hub.covers.path(volume["key"])), None)
    if cover:
        href = "/opds/cover/%d" % cover["id"]
        ET.SubElement(entry, "link", {"rel": "http://opds-spec.org/image", "href": href, "type": "image/jpeg"})
        ET.SubElement(entry, "link", {"rel": "http://opds-spec.org/image/thumbnail", "href": href, "type": "image/jpeg"})
    if not ready:
        return
    # the volume count goes in the link too. Readest remembers LINKS it has downloaded, not ids, so a grown
    # series under the same link would just never come. found in readest's services/opds/autoDownload.ts
    which = "?name=" + quote(series) + ("&part=%d" % part_no if count > 1 else "") + "&v=%d" % len(volumes)
    # The EPUB first: reading apps prefer it, and it has the contents list for moving between volumes.
    ET.SubElement(entry, "link", {"rel": "http://opds-spec.org/acquisition", "type": MIME["EPUB"], "title": "EPUB with a contents list",
                                  "href": "/opds/series-book" + which})
    ET.SubElement(entry, "link", {"rel": "http://opds-spec.org/acquisition", "type": MIME["CBZ"], "title": "CBZ",
                                  "href": "/opds/series-file" + which})


def _chapter_parts(info):
    """The downloaded chapters in parts, the same parts as the comic files (chapterfile.py); one empty part when none."""
    return chapterfile.parts(info) or [[]]


def _chapters_entry(feed, info):
    """A series followed chapter by chapter, as one book of the chapters kept so far (in parts when long)."""
    parts = _chapter_parts(info)
    for part_no, kept in enumerate(parts, 1):
        _chapters_part_entry(feed, info, kept, part_no, len(parts))


def _chapters_part_entry(feed, info, kept, part_no, count):
    entry = ET.SubElement(feed, "entry")
    # the comic file as it should be now, or the last one made while the longer one is still on its way
    path = chapterfile.ready_file(current_app.hub, info, part_no, older=True)[0] if kept else None
    title = chapterfile.part_title(info["title"], kept, part_no, count) if kept else info["title"]
    ET.SubElement(entry, "title").text = title.translate(_CONTROL)
    # The id names the exact file on offer, so each longer copy makes the app fetch it again (and "cbz" in it
    # fetches parts first sent as an EPUB once more, as comics).
    ET.SubElement(entry, "id").text = "urn:bookhub:chapters:%d:%scbz:%s" % (
        info["id"], "part%d:" % part_no if count > 1 else "", chapterfile.stamp_of(path) if path else "none")
    ET.SubElement(entry, "updated").text = db.now()
    if info.get("author"):
        ET.SubElement(ET.SubElement(entry, "author"), "name").text = info["author"].translate(_CONTROL)
    if not kept:
        summary = "No chapters kept yet."
    elif not path:
        summary = "%d chapters, from %s to %s. Being put together on the server; it comes by itself once ready." % (
            len(kept), chapterfile.chapter_name(kept[0]), chapterfile.chapter_name(kept[-1]))
    else:
        summary = "%d chapters in one comic, from %s to %s.%s" % (
            len(kept), chapterfile.chapter_name(kept[0]), chapterfile.chapter_name(kept[-1]),
            " New chapters come as a longer copy, opening where you were." if info.get("status") != "Finished" else "")
    ET.SubElement(entry, "summary", {"type": "text"}).text = summary.translate(_CONTROL)
    ET.SubElement(entry, "link", {"rel": "http://opds-spec.org/image", "href": "/opds/chapters-cover/%d" % info["id"], "type": "image/jpeg"})
    if path:
        # same trick as the series links: Readest skips a link it has seen before (even with a new id), so the
        # version rides along in the link or a grown part would never arrive. (the "missing chapters 81-104" bug)
        ET.SubElement(entry, "link", {"rel": "http://opds-spec.org/acquisition", "type": MIME["CBZ"],
                                      "href": "/opds/chapters-file/%d/%d?v=%s" % (info["id"], part_no, chapterfile.stamp_of(path))})


def _book_feed(title, path, books, extra="", prepare=False):
    try:
        page = max(1, int(request.args.get("page") or 1))
    except ValueError:
        page = 1
    href = lambda page_no: "%s?%spage=%d" % (path, extra, page_no)  # noqa: E731
    feed = _feed(title, href(page), ACQ)
    if page * PAGE < len(books):
        ET.SubElement(feed, "link", {"rel": "next", "href": href(page + 1), "type": ACQ})
    if page > 1:
        ET.SubElement(feed, "link", {"rel": "previous", "href": href(page - 1), "type": ACQ})
    seen, shared = set(), set()
    for book in current_app.hub.library.books():
        (shared if book["key"] in seen else seen).add(book["key"])
    roles = current_app.hub.role_index()  # once for the page, not once a book
    for book in books[(page - 1) * PAGE:page * PAGE]:
        if "chapters_list" in book:
            _chapters_entry(feed, book)
        elif "volumes" in book:
            _series_entry(feed, book, current_app.hub, prepare)
        else:
            _book_entry(feed, book, current_app.hub, shared, roles)
    return _reply(feed, ACQ)


@bp.get("")
@bp.get("/")
@signed_in
def root(who):
    hub = current_app.hub
    feed = _feed("Family Hub · " + who["name"], "/opds", NAV)
    # sneaky but important: Readest's auto-download follows whichever feed is marked "sort/new". we point that at
    # My books, so only books someone actually CHOSE turn up by themselves, not the whole 2000-book library.
    # the title dodges words like "newest" too, because some apps sniff for those as well
    ET.SubElement(feed, "link", {"rel": "http://opds-spec.org/sort/new", "href": "/opds/mine", "type": ACQ, "title": "My books"})
    shelved = [book for book in map(hub.library.by_key, hub.devices.shelf(who["id"])) if book]
    mine = len(without_comics(hub, shelved) if comics_elsewhere(hub) else shelved)
    for title, href, text in (
        ("My books", "/opds/mine", "%d chosen for %s" % (mine, who["name"])),
        ("The whole family library", "/opds/all", "%d books, the last added first" % len(_books_here(hub))),
    ):
        entry = ET.SubElement(feed, "entry")
        ET.SubElement(entry, "title").text = title
        ET.SubElement(entry, "id").text = "urn:bookhub:" + href
        ET.SubElement(entry, "updated").text = db.now()
        ET.SubElement(entry, "content", {"type": "text"}).text = text
        ET.SubElement(entry, "link", {"rel": "subsection", "href": href, "type": ACQ})
    return _reply(feed, NAV)


def comics_elsewhere(hub):
    """True when graphic novels and series by chapter are read in a comic app from Komga (COMICS_APP=paperback),
    so the book-reading app's My books leaves them out rather than download them a second time."""
    return hub.config.comics_app == "paperback"


def without_comics(hub, books):
    kind_of = hub.kind_index()
    return [book for book in books if kind_of.get(book["key"]) != kinds.GRAPHIC]


def _books_here(hub):
    """The library as this catalogue shows it: without graphic novels when they're read from Komga instead,
    so only one app ever takes them in."""
    everything = hub.library.books()
    return without_comics(hub, everything) if comics_elsewhere(hub) else everything


@bp.get("/mine")
@signed_in
def mine(who):
    hub = current_app.hub
    shelf = hub.devices.shelf(who["id"])
    books = [book for book in (hub.library.by_key(key) for key in sorted(shelf, key=shelf.get, reverse=True)) if book]
    if comics_elsewhere(hub):
        return _book_feed("My books", "/opds/mine", _gather(without_comics(hub, books), hub.library.books()), prepare=True)
    # Series followed chapter by chapter that this person put in My books come first.
    return _book_feed("My books", "/opds/mine", _my_chapter_series(hub, who) + _gather(books, hub.library.books()), prepare=True)


@bp.get("/all")
@signed_in
def everything(who):
    everything = _books_here(current_app.hub)
    books = sorted(everything, key=lambda book: book["added"], reverse=True)
    return _book_feed("The whole family library", "/opds/all", _gather(books, everything))


@bp.get("/search")
@signed_in
def search(who):
    query = (request.args.get("q") or "").strip().lower()
    everything = _books_here(current_app.hub)
    haystack = lambda book: " ".join([book["title"], book.get("shown_title", ""), book.get("subtitle", ""),  # noqa: E731
                                      book["author"], book["series"]] + book["tags"]).lower()
    books = [book for book in everything if query and query in haystack(book)]
    return _book_feed("Search: " + query, "/opds/search", _gather(books, everything), "q=%s&" % quote(query))


@bp.get("/opensearch.xml")
def opensearch():
    root = ET.Element("OpenSearchDescription", {"xmlns": "http://a9.com/-/spec/opensearch/1.1/"})
    ET.SubElement(root, "ShortName").text = "Family Hub"
    ET.SubElement(root, "Description").text = "Search the family library"
    ET.SubElement(root, "Url", {"type": ACQ, "template": "/opds/search?q={searchTerms}"})
    return Response(b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="utf-8"),
                    mimetype="application/opensearchdescription+xml")


@bp.get("/cover/<int:book_id>")
@signed_in
def cover(who, book_id):
    hub = current_app.hub
    book = hub.library.get(book_id)
    path = (hub.covers.path(book["key"]) or hub.library.cover_path(book)) if book else None
    if not path:
        return "", 404
    return send_file(path, mimetype="image/jpeg", max_age=3600)


@bp.get("/book/<int:book_id>/<fmt>")
@signed_in
def book_file(who, book_id, fmt):
    hub = current_app.hub
    book = hub.library.get(book_id)
    if not book:
        return "", 404
    if fmt == "converted":
        path, ext = hub.config.converted_dir / ("%d.epub" % book_id), "epub"
        if not path.is_file():
            return "", 404
    else:
        path, ext = hub.library.file_path(book, fmt), fmt.lower()
        if not path:
            return "", 404
    from .devices import safe_filename
    fetched(hub, who, "book:%d" % book_id, "file")
    return send_file(path, as_attachment=True, download_name=safe_filename(book["title"], book["author"], ext),
                     mimetype=MIME.get(ext.upper(), "application/octet-stream"))


def _series_file(hub, volumes):
    """The joined series, made once and kept: its name changes whenever a volume is added or replaced."""
    from . import seriesfile
    stamp = hashlib.sha1("|".join("%d:%s" % (volume["id"], sorted(volume["sizes"].items())) for volume in volumes).encode()).hexdigest()[:20]
    folder = Path(hub.config.data_dir) / "series-files"
    target = folder / (stamp + ".cbz")
    with _building:  # two devices asking at once share one build
        if not target.is_file():
            folder.mkdir(parents=True, exist_ok=True)
            made, _ = seriesfile.build(hub.library, volumes, volumes[0]["series"])
            shutil.move(str(made), str(target))
            for old in sorted(folder.glob("*.cbz"), key=lambda file: file.stat().st_mtime)[:-KEEP_SERIES_FILES]:
                if old != target:
                    old.unlink(missing_ok=True)
    return target


@bp.get("/series-file")
@signed_in
def series_file(who):
    """A comic series as one CBZ, every volume in order."""
    from . import seriesfile
    from .devices import safe_filename
    hub = current_app.hub
    volumes = _asked_part(hub)
    if not volumes:
        return "", 404
    try:
        path = _series_file(hub, volumes)
    except seriesfile.SeriesFileError:
        return "", 404
    fetched(hub, who, series_item(volumes[0]["series"], _asked_k()), _series_stamp(hub, volumes))
    return send_file(path, as_attachment=True, download_name=safe_filename(volumes[0]["series"], "", "cbz"), mimetype=MIME["CBZ"])


def _my_chapter_series(hub, who):
    """The series followed chapter by chapter in this person's My books, as the series server describes them."""
    from . import series
    if not hub.series.configured or (who["role"] != "admin" and who["id"] in series.blocked_ids(hub)):
        return []
    found = []
    for series_id in series.my_books(hub, who["id"]):
        try:
            found.append(hub.series.detail(series_id))
        except series.SeriesError:
            continue  # the series server is away, or the series was removed; the rest still show
    return found


def _target(hub, kind, stamp):
    """Where a built book is kept: its name changes whenever what goes into it changes."""
    return Path(hub.config.data_dir) / "series-files" / ("%s-%s" % (stamp, kind))


def _make(target, make):
    unfinished = target.with_name(target.name + ".part")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        chapterfile.room_for(target.parent)  # a full disk takes Docker down with it. check first, always
        _progress[target] = (0, 0)
        make(unfinished)
        unfinished.replace(target)
        # keep the newest few, bin the rest. they're rebuilt on request anyway
        kept = sorted((file for file in target.parent.iterdir() if not file.name.endswith(".part")),
                      key=lambda file: file.stat().st_mtime)
        for old in kept[:-KEEP_SERIES_FILES]:
            if old != target:
                old.unlink(missing_ok=True)
    except Exception as exc:
        log.exception("Could not put together %s", target.name)
        _failed[target] = (time.time(), str(exc) or exc.__class__.__name__)
        unfinished.unlink(missing_ok=True)
    finally:
        _progress.pop(target, None)
        with _jobs_lock:
            _jobs.pop(target, None)


def _job(target, make, wait):
    """Make the book in the background unless it is made, being made, or failed a moment ago (two devices
    asking at once share one job). Waits up to ``wait`` seconds (None: until done); True once it is ready."""
    with _jobs_lock:
        if target.is_file():
            return True
        job = _jobs.get(target)
        if not job and time.time() - _failed.get(target, (0, ""))[0] >= RETRY_FAILED:
            job = _jobs[target] = threading.Thread(target=_make, args=(target, make), daemon=True, name="series-book")
            job.start()
    if job and wait != 0:
        job.join(wait)
    return target.is_file()


def _cached(hub, kind, stamp, make):
    """A built book kept ready to send, made now if need be."""
    target = _target(hub, kind, stamp)
    if not _job(target, make, wait=None):
        raise OSError("The book could not be put together.")
    return target


def _series_stamp(hub, volumes):
    """Every copy of every volume in it counts, so a whole copy arriving for a damaged volume makes it again."""
    name, numbers = volumes[0]["series"].strip().lower(), {volume.get("series_index") for volume in volumes}
    every = sorted((book for book in hub.library.books() if (book.get("series") or "").strip().lower() == name
                    and book.get("series_index") in numbers), key=lambda book: book["id"])
    return hashlib.sha1(("book|" + "|".join("%d:%s" % (copy["id"], sorted(copy["sizes"].items())) for copy in every)).encode()).hexdigest()[:20]


def _series_book_job(hub, volumes, wait, stamp=None):
    """Start (or find) the joined EPUB of these volumes. True when it is ready to send.
    stamp: their _series_stamp, when the caller has worked it out already."""
    target = _target(hub, "series.epub", stamp or _series_stamp(hub, volumes))
    return _job(target, lambda unfinished: _series_book_make(hub, volumes, unfinished, target), wait)


def _state(target):
    if target.is_file():
        return "ready"
    if target in _jobs:
        return "making"
    return "failed" if _failed.get(target) else "waiting"


def series_status(hub, vols):
    """Where each part of a comic series stands, for My books: [{title, state, percent, error}].
    state: "ready", "making", "waiting" (not started) or "failed". Starts the parts not started yet."""
    parts = _split(hub, vols)
    statuses = []
    for part_no, part in enumerate(parts, 1):
        stamp = _series_stamp(hub, part)
        target = _target(hub, "series.epub", stamp)
        _series_book_job(hub, part, wait=0, stamp=stamp)
        done, total = _progress.get(target, (0, 0))
        state = _state(target)
        statuses.append({"title": _part_title(vols[0]["series"], part, part_no, len(parts)), "state": state,
                         "item": series_item(vols[0]["series"], part_no), "version": stamp, "path": str(target),
                         "percent": round(100.0 * done / total) if total else 0,
                         "error": _failed[target][1] if state == "failed" else ""})
    return statuses


def _asked_k():
    try:
        return int(request.args.get("part") or 1)
    except ValueError:
        return 1


def _asked_part(hub):
    """The volumes a series-book or series-file request is for: the series by name, and the part if split."""
    volumes, parts = _parts_of(hub, (request.args.get("name") or "").strip())
    if not volumes:
        return []
    try:
        part_no = int(request.args.get("part") or 1)
    except ValueError:
        return []
    return parts[part_no - 1] if 1 <= part_no <= len(parts) else []


def _comic_file(hub, book):
    return hub.library.file_path(book, next(fmt for fmt in ("CBZ", "CBR") if fmt in book["formats"]))


def _whole_copy(hub, volume, everything):
    """The volume's file, or another copy's when this one is damaged (a download taken in before it had
    finished, with a whole copy taken in later). The volume's own file when no copy looks whole."""
    from .intake import looks_whole
    name, number = (volume.get("series") or "").strip().lower(), volume.get("series_index")
    others = sorted((book for book in everything if book["id"] != volume["id"] and number and book.get("series_index") == number
                     and (book.get("series") or "").strip().lower() == name and _comic(book)), key=lambda book: book["id"])
    files = [_comic_file(hub, copy) for copy in [volume] + others]
    return next((Path(file) for file in files if file and looks_whole(Path(file))), Path(files[0]) if files[0] else None)


def _series_book_make(hub, volumes, target, kept_as=None):
    import tempfile
    from . import comicbook, joined, seriesfile
    scratch = Path(tempfile.mkdtemp())
    everything = hub.library.books()
    try:
        parts = []
        for volume in volumes:
            try:
                path = _whole_copy(hub, volume, everything)
                if not path:
                    raise OSError("its file is missing")
                parts.append((_part(volume), seriesfile._pages(path, scratch)))
            except (zipfile.BadZipFile, seriesfile.SeriesFileError, OSError) as exc:
                # one busted volume gets skipped (and logged); the other 20 still make it into the book
                log.warning("Left %s out of %s: %s", _part(volume), volume["series"], exc)
        comicbook.build(target, _book_title(hub, volumes), volumes[0].get("shown_author") or volumes[0]["author"], parts,
                        progress=lambda done, total: _progress.__setitem__(kept_as or target, (done, total)))
        joined.record(hub, target, "series", volumes[0]["series"], {_part(volume): volume["key"] for volume in volumes}, kept_as=kept_as)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def _book_title(hub, volumes):
    """The joined book's own title: the series, or "Series · Part 2 (Volume 8–14)" when it is one of several."""
    everything = _volumes(volumes[0]["series"], hub.library.books())
    parts = _split(hub, everything) if len(everything) > 1 else [volumes]
    ids = [volume["id"] for volume in volumes]
    part_no = next((number for number, part in enumerate(parts, 1) if [volume["id"] for volume in part] == ids), 1)
    return _part_title(volumes[0]["series"], volumes, part_no, len(parts))


@bp.get("/series-book")
@signed_in
def series_book(who):
    """A comic series as one EPUB with a contents entry for each volume."""
    from .devices import safe_filename
    hub = current_app.hub
    volumes = _asked_part(hub)
    if not volumes:
        return "", 404
    stamp = _series_stamp(hub, volumes)
    if not _series_book_job(hub, volumes, wait=FILE_WAIT, stamp=stamp):
        # 503 + Retry-After is the polite "not yet, come back in 5 minutes" that reading apps understand
        return Response("This series is being put together on the server. Try again in a few minutes.", 503, {"Retry-After": "300"})
    path = _target(hub, "series.epub", stamp)
    fetched(hub, who, series_item(volumes[0]["series"], _asked_k()), stamp)
    return send_file(path, as_attachment=True, download_name=safe_filename(_book_title(hub, volumes), "", "epub"), mimetype=MIME["EPUB"])


@bp.get("/chapters-book/<int:series_id>")
@signed_in
def chapters_book(who, series_id):
    """A series followed chapter by chapter, as one EPUB of every chapter kept so far."""
    import tempfile
    import zipfile
    from . import comicbook, series, seriesfile
    from .devices import safe_filename
    hub = current_app.hub
    if series_id not in series.my_books(hub, who["id"]) or (who["role"] != "admin" and who["id"] in series.blocked_ids(hub)):
        return "", 404
    try:
        info = hub.series.detail(series_id)
    except series.SeriesError:
        return "", 404
    parts = _chapter_parts(info)
    try:
        part_no = int(request.args.get("part") or 1)
    except ValueError:
        return "", 404
    kept = parts[part_no - 1] if 1 <= part_no <= len(parts) else []
    if not kept:
        return "", 404
    title = chapterfile.part_title(info["title"], kept, part_no, len(parts))
    chapter_ids = ",".join(str(chapter["id"]) for chapter in kept)
    stamp = hashlib.sha1(("chapters|%d|%d|" % (series_id, chapterfile.FORMAT) + chapter_ids).encode()).hexdigest()[:20]

    def make(target):
        from . import joined
        scratch = Path(tempfile.mkdtemp())
        opened = []  # the chapters' zips stay open while pages are read lazily, then all get closed in finally
        try:
            pieces, names_of = [], chapterfile.labels(kept)
            for chapter, label in zip(kept, names_of):
                file = scratch / ("%d.cbz" % chapter["id"])
                with hub.series.raw("/api/v1/chapter/%d/download" % chapter["id"], stream=True) as reply, open(file, "wb") as out:
                    for block in reply.iter_content(1 << 20):
                        out.write(block)
                chapter_zip = zipfile.ZipFile(file)
                opened.append(chapter_zip)
                names = sorted((name for name in chapter_zip.namelist() if name.lower().endswith(seriesfile.PICTURES)),
                               key=seriesfile._natural)
                # default args pin each lambda to ITS zip and name (closures in loops are a classic trap)
                pieces.append((label, [(name, (lambda chapter_zip=chapter_zip, name=name: chapter_zip.read(name))) for name in names]))
            comicbook.build(target, title, info.get("author") or "", pieces)
            joined.record(hub, target, "chapters", series_id, dict(zip(names_of, (chapter["id"] for chapter in kept))),
                          kept_as=_target(hub, "chapters.epub", stamp))
        finally:
            for chapter_zip in opened:
                chapter_zip.close()
            shutil.rmtree(scratch, ignore_errors=True)
    try:
        path = _cached(hub, "chapters.epub", stamp, make)
    except (series.SeriesError, zipfile.BadZipFile, ValueError, OSError):
        return "", 404
    fetched(hub, who, "chapters:%d:%d" % (series_id, part_no), chapterfile.version(kept))
    return send_file(path, as_attachment=True, download_name=safe_filename(title, "", "epub"), mimetype=MIME["EPUB"])


@bp.get("/chapters-file/<int:series_id>/<int:part>")
@signed_in
def chapters_file(who, series_id, part):
    """A part of a series by chapter, as the comic file the server keeps of it."""
    from . import series
    from .devices import safe_filename
    hub = current_app.hub
    if series_id not in series.my_books(hub, who["id"]) or (who["role"] != "admin" and who["id"] in series.blocked_ids(hub)):
        return "", 404
    try:
        info = hub.series.detail(series_id)
    except series.SeriesError:
        return "", 404
    path, title = chapterfile.ready_file(hub, info, part, older=True)
    if not path:
        return "", 404
    fetched(hub, who, "chapters:%d:%d" % (series_id, part), chapterfile.stamp_of(path))
    return send_file(path, as_attachment=True, download_name=safe_filename(title, "", "cbz"), mimetype=MIME["CBZ"])


@bp.get("/chapters-cover/<int:series_id>")
@signed_in
def chapters_cover(who, series_id):
    from . import series
    hub = current_app.hub
    try:
        reply = hub.series.raw("/api/v1/manga/%d/thumbnail" % series_id, stream=False)
    except series.SeriesError:
        return "", 404
    # the picture comes from a reading site nobody here controls: anything that isn't a plain picture (a web page,
    # an SVG with a script in it) goes out as a file, never as something the browser runs on Family Hub's address
    mimetype = reply.headers.get("Content-Type", "image/jpeg").split(";")[0].strip().lower()
    if not mimetype.startswith("image/") or "svg" in mimetype:
        mimetype = "application/octet-stream"
    return Response(reply.content, mimetype=mimetype, headers={"Cache-Control": "max-age=3600", "X-Content-Type-Options": "nosniff"})
