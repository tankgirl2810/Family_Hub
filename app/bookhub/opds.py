"""A private book catalogue for each profile, for reading apps to fetch books from.

Readest, KOReader and most other reading apps can add an "OPDS catalogue".
Point one at  http://<book hub>/opds  and sign in with the profile name and
the profile's sync password. "My books" lists what that person has chosen;
the whole library can be browsed and searched as well.

A comic series (graphic novels and manga volumes, CBZ or CBR) is one entry, not one per volume. Its download
is one EPUB with a contents entry for each volume (comicbook.py), so a reading app shows one book you can
jump around in, from the first volume to the last; the same pages as one CBZ are offered too. Choosing any
volume for My books brings the whole series.

A series followed chapter by chapter (series.py) can be put in My books as well: it comes as one EPUB with a
contents entry per chapter, and when new chapters arrive it comes again, longer.

A joined series can take minutes to put together, so it is made in the background and kept. Until it is
ready, My books lists the series without a download (Readest looks again every few minutes) and asking for
the file answers "try again shortly". A long series comes in parts of about 1 GB, never splitting a volume,
since a tablet's reading app cannot open a much bigger book; a new volume only changes the last part.
Every joined book is recorded (joined.py) so the reading app's progress in it reaches the volumes.
"""
import functools
import hashlib
import logging
import shutil
import threading
import time
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from urllib.parse import quote

from flask import Blueprint, Response, current_app, request, send_file

from . import db, kosync

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
_CONTROL = dict.fromkeys(c for c in range(32) if c not in (9, 10, 13))  # not allowed in XML
_misses = {}  # wrong-password counters, per name


def _profile():
    auth = request.authorization
    if not auth or not auth.username or auth.password is None:
        return None
    name = auth.username.strip()
    entry = _misses.get(name.lower(), [0, 0])
    if entry[0] >= MAX_TRIES and time.time() - entry[1] < LOCK_SECONDS:
        return None
    row = current_app.hub.con().execute("SELECT * FROM profiles WHERE name=?", (name,)).fetchone()
    if row and row["sync_key"] and kosync.hmac.compare_digest(row["sync_key"], kosync.sync_key_for(auth.password)):
        _misses.pop(name.lower(), None)
        return row
    _misses[name.lower()] = [entry[0] + 1 if time.time() - entry[1] < LOCK_SECONDS else 1, time.time()]
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


def _book_entry(feed, book, hub):
    entry = ET.SubElement(feed, "entry")
    # Titles as Family Hub shows them: "Cherry Magic! · Volume 2", not the file name the volume came with.
    title = book.get("shown_title") or book["title"]
    if book.get("subtitle"):
        title += ": " + book["subtitle"]
    ET.SubElement(entry, "title").text = title.translate(_CONTROL)
    ET.SubElement(entry, "id").text = "urn:bookhub:book:" + book["key"]
    ET.SubElement(entry, "updated").text = (book["added"] or db.now()[:19]) + "Z"
    for name in (book["authors"] if book.get("shown_author", book["author"]) else []):  # a comic's "author" may be its story
        ET.SubElement(ET.SubElement(entry, "author"), "name").text = name
    if book["synopsis"]:
        ET.SubElement(entry, "summary", {"type": "text"}).text = book["synopsis"][:2000].translate(_CONTROL)
    for tag in book["tags"]:
        ET.SubElement(entry, "category", {"term": tag, "label": tag})
    if book["has_cover"] or hub.covers.path(book["key"]):  # the library's cover, or one chosen in Family Hub
        href = "/opds/cover/%d" % book["id"]
        ET.SubElement(entry, "link", {"rel": "http://opds-spec.org/image", "href": href, "type": "image/jpeg"})
        ET.SubElement(entry, "link", {"rel": "http://opds-spec.org/image/thumbnail", "href": href, "type": "image/jpeg"})
    formats = sorted(book["formats"], key=lambda f: (f != "EPUB", f))
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
CHAPTERS_PER_PART = 50      # chapters are not measured before they are fetched; 50 is usually well under 1 GB
RETRY_FAILED = 600  # seconds before a book that could not be made is tried again
FEED_WAIT, FILE_WAIT = 3, 20  # seconds a list or a download waits for a book still being made
log = logging.getLogger("bookhub")


def _comic(book):
    return bool(set(book["formats"]) & COMIC)


def _volumes(name, everything):
    """Every comic volume of a series, in reading order, each volume once: a second copy of volume 2 (a file
    the library took in twice) would otherwise put volume 2 in the joined book twice. The earliest copy wins."""
    low = name.lower()
    found = sorted((b for b in everything if (b.get("series") or "").strip().lower() == low),
                   key=lambda b: (b.get("series_index") or 0, b["id"]))
    out, numbers = [], set()
    for b in found:
        n = b.get("series_index")
        if n and n in numbers:
            continue
        numbers.add(n)
        out.append(b)
    return out


def _gather(books, everything):
    """The volumes of a comic series become one item, placed where the first of them came in the list."""
    out, done = [], set()
    for b in books:
        name = (b.get("series") or "").strip()
        if name and _comic(b):
            if name.lower() in done:
                continue
            vols = _volumes(name, everything)
            if len(vols) > 1 and all(_comic(v) for v in vols):
                done.add(name.lower())
                out.append({"series": vols[0]["series"], "volumes": vols})
                continue
        out.append(b)
    return out


def _part(v):
    return v.get("part") or ("Volume %g" % v["series_index"] if v.get("series_index") else v.get("shown_title") or v["title"])


def _file_size(v):
    return next((v["sizes"].get(f) or 0 for f in ("CBZ", "CBR") if f in v["formats"]), 0)


def _shrink_ratio(hub, v):
    """How much of a volume's size is left once its pages are made tablet-sized, from a few of its pages.
    Kept, so the same series always splits in the same places."""
    key = "shrink:%d:%d" % (v["id"], _file_size(v))
    kept = db.kv_get(hub.con(), key)
    if kept:
        return float(kept)
    from . import comicbook, seriesfile
    ratio = 1.0
    try:
        path = hub.library.file_path(v, next(f for f in ("CBZ", "CBR") if f in v["formats"]))
        if path and zipfile.is_zipfile(path):
            pages = seriesfile._pages(Path(path), None)
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


def _split(hub, vols):
    """The volumes in parts of about PART_BYTES once made tablet-sized, each volume whole. Earlier parts stay
    the same when volumes are added, so a reading app only fetches the last part again."""
    ratio = _shrink_ratio(hub, vols[0])
    parts, size = [[]], 0
    for v in vols:
        guess = _file_size(v) * ratio
        if parts[-1] and size + guess > PART_BYTES:
            parts.append([])
            size = 0
        parts[-1].append(v)
        size += guess
    return parts


def _part_title(series, part, k, count):
    """"Series · Part 2 (Volumes 5–9)", or the series alone when it comes as one book."""
    if count == 1:
        return series
    first, last = _part(part[0]), _part(part[-1])
    word, _, n = first.partition(" ")
    if len(part) == 1:
        span = first
    elif n and last.startswith(word + " "):
        span = "%ss %s–%s" % (word, n, last[len(word) + 1:])  # "Volumes 5–9", "Issues 1–12"
    else:
        span = "%s – %s" % (first, last)
    return "%s · Part %d (%s)" % (series, k, span)


def _parts_of(hub, name):
    """(volumes, parts) of a comic series asked for by name, or ([], [])."""
    vols = _volumes(name, hub.library.books()) if name else []
    if len(vols) < 2 or not all(_comic(v) for v in vols):
        return [], []
    return vols, _split(hub, vols)


def _series_entry(feed, item, hub, prepare=False):
    """One entry per part (most series have one). prepare: start making the books not ready yet (My books)."""
    parts = _split(hub, item["volumes"])
    for k, vols in enumerate(parts, 1):
        _series_part_entry(feed, item["series"], vols, k, len(parts), hub, prepare)


def _series_part_entry(feed, series, vols, k, count, hub, prepare):
    """Until the book is ready the entry has no download and its own id, so an app that downloads new entries
    by itself fetches it once it is ready."""
    ready = _series_book_job(hub, vols, wait=FEED_WAIT) if prepare else True
    entry = ET.SubElement(feed, "entry")
    ET.SubElement(entry, "title").text = _part_title(series, vols, k, count).translate(_CONTROL)
    # The volume count is part of the id, so when a new volume arrives the app sees a new, longer book.
    ET.SubElement(entry, "id").text = "urn:bookhub:series:%s:%s%d%s" % (
        db.slug(series), "part%d:" % k if count > 1 else "", len(vols), "" if ready else ":making")
    ET.SubElement(entry, "updated").text = (max(v["added"] for v in vols) or db.now()[:19]) + "Z"
    first = vols[0]
    for name in (first["authors"] if first.get("shown_author", first["author"]) else []):
        ET.SubElement(ET.SubElement(entry, "author"), "name").text = name
    ET.SubElement(entry, "summary", {"type": "text"}).text = (
        "%d volumes in one book, read straight through: %s to %s.%s" % (
            len(vols), _part(vols[0]), _part(vols[-1]),
            "" if ready else " Being put together on the server; it arrives by itself in a few minutes.")).translate(_CONTROL)
    cover = next((v for v in vols if v["has_cover"] or hub.covers.path(v["key"])), None)
    if cover:
        href = "/opds/cover/%d" % cover["id"]
        ET.SubElement(entry, "link", {"rel": "http://opds-spec.org/image", "href": href, "type": "image/jpeg"})
        ET.SubElement(entry, "link", {"rel": "http://opds-spec.org/image/thumbnail", "href": href, "type": "image/jpeg"})
    if not ready:
        return
    which = "?name=" + quote(series) + ("&part=%d" % k if count > 1 else "")
    # The EPUB first: reading apps prefer it, and it has the contents list for moving between volumes.
    ET.SubElement(entry, "link", {"rel": "http://opds-spec.org/acquisition", "type": MIME["EPUB"], "title": "EPUB with a contents list",
                                  "href": "/opds/series-book" + which})
    ET.SubElement(entry, "link", {"rel": "http://opds-spec.org/acquisition", "type": MIME["CBZ"], "title": "CBZ",
                                  "href": "/opds/series-file" + which})


def _chapter_parts(info):
    kept = [c for c in info["chapters_list"] if c["downloaded"]]
    return [kept[i:i + CHAPTERS_PER_PART] for i in range(0, len(kept), CHAPTERS_PER_PART)] or [[]]


def _chapters_entry(feed, info):
    """A series followed chapter by chapter, as one book of the chapters kept so far (in parts when long)."""
    parts = _chapter_parts(info)
    for k, kept in enumerate(parts, 1):
        _chapters_part_entry(feed, info, kept, k, len(parts))


def _chapters_part_entry(feed, info, kept, k, count):
    entry = ET.SubElement(feed, "entry")
    title = info["title"] if count == 1 else "%s · Part %d (%s–%s)" % (info["title"], k, kept[0]["name"], kept[-1]["name"])
    ET.SubElement(entry, "title").text = title.translate(_CONTROL)
    # The chapter count is part of the id, so each new chapter makes the app fetch the longer book.
    ET.SubElement(entry, "id").text = "urn:bookhub:chapters:%d:%s%d" % (info["id"], "part%d:" % k if count > 1 else "", len(kept))
    ET.SubElement(entry, "updated").text = db.now()
    if info.get("author"):
        ET.SubElement(ET.SubElement(entry, "author"), "name").text = info["author"].translate(_CONTROL)
    ET.SubElement(entry, "summary", {"type": "text"}).text = (
        "%d chapters in one book, from %s to %s.%s" % (len(kept), kept[0]["name"], kept[-1]["name"],
                                                       " New chapters come as a longer copy." if info.get("status") != "Finished" else "")
        if kept else "No chapters kept yet.").translate(_CONTROL)
    ET.SubElement(entry, "link", {"rel": "http://opds-spec.org/image", "href": "/opds/chapters-cover/%d" % info["id"], "type": "image/jpeg"})
    if kept:
        ET.SubElement(entry, "link", {"rel": "http://opds-spec.org/acquisition", "type": MIME["EPUB"],
                                      "href": "/opds/chapters-book/%d%s" % (info["id"], "?part=%d" % k if count > 1 else "")})
        from . import chapterfile
        if chapterfile.ready_file(current_app.hub, info, k)[0]:  # the comic file the server keeps, once it is made
            ET.SubElement(entry, "link", {"rel": "http://opds-spec.org/acquisition", "type": MIME["CBZ"], "title": "CBZ",
                                          "href": "/opds/chapters-file/%d/%d" % (info["id"], k)})


def _book_feed(title, path, books, extra="", prepare=False):
    try:
        page = max(1, int(request.args.get("page") or 1))
    except ValueError:
        page = 1
    href = lambda n: "%s?%spage=%d" % (path, extra, n)  # noqa: E731
    feed = _feed(title, href(page), ACQ)
    if page * PAGE < len(books):
        ET.SubElement(feed, "link", {"rel": "next", "href": href(page + 1), "type": ACQ})
    if page > 1:
        ET.SubElement(feed, "link", {"rel": "previous", "href": href(page - 1), "type": ACQ})
    for book in books[(page - 1) * PAGE:page * PAGE]:
        if "chapters_list" in book:
            _chapters_entry(feed, book)
        elif "volumes" in book:
            _series_entry(feed, book, current_app.hub, prepare)
        else:
            _book_entry(feed, book, current_app.hub)
    return _reply(feed, ACQ)


@bp.get("")
@bp.get("/")
@signed_in
def root(who):
    hub = current_app.hub
    feed = _feed("Family Hub · " + who["name"], "/opds", NAV)
    # Apps that download a catalogue's new books by themselves (Readest's automatic download) follow the feed
    # marked "sort/new". That is My books, so only the books a person chose arrive unasked; the whole library is
    # browsed and downloaded by hand. Its title avoids words such as "newest", which those apps also take as the mark.
    ET.SubElement(feed, "link", {"rel": "http://opds-spec.org/sort/new", "href": "/opds/mine", "type": ACQ, "title": "My books"})
    mine = len([k for k in hub.devices.shelf(who["id"]) if hub.library.by_key(k)])
    for title, href, text in (
        ("My books", "/opds/mine", "%d chosen for %s" % (mine, who["name"])),
        ("The whole family library", "/opds/all", "%d books, the last added first" % len(hub.library.books())),
    ):
        entry = ET.SubElement(feed, "entry")
        ET.SubElement(entry, "title").text = title
        ET.SubElement(entry, "id").text = "urn:bookhub:" + href
        ET.SubElement(entry, "updated").text = db.now()
        ET.SubElement(entry, "content", {"type": "text"}).text = text
        ET.SubElement(entry, "link", {"rel": "subsection", "href": href, "type": ACQ})
    return _reply(feed, NAV)


@bp.get("/mine")
@signed_in
def mine(who):
    hub = current_app.hub
    shelf = hub.devices.shelf(who["id"])
    books = [b for b in (hub.library.by_key(k) for k in sorted(shelf, key=shelf.get, reverse=True)) if b]
    # Series followed chapter by chapter that this person put in My books come first.
    return _book_feed("My books", "/opds/mine", _my_chapter_series(hub, who) + _gather(books, hub.library.books()), prepare=True)


@bp.get("/all")
@signed_in
def everything(who):
    everything = current_app.hub.library.books()
    books = sorted(everything, key=lambda b: b["added"], reverse=True)
    return _book_feed("The whole family library", "/opds/all", _gather(books, everything))


@bp.get("/search")
@signed_in
def search(who):
    q = (request.args.get("q") or "").strip().lower()
    everything = current_app.hub.library.books()
    books = [b for b in everything
             if q and q in " ".join([b["title"], b.get("shown_title", ""), b.get("subtitle", ""), b["author"], b["series"]] + b["tags"]).lower()]
    return _book_feed("Search: " + q, "/opds/search", _gather(books, everything), "q=%s&" % quote(q))


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
    return send_file(path, as_attachment=True, download_name=safe_filename(book["title"], book["author"], ext),
                     mimetype=MIME.get(ext.upper(), "application/octet-stream"))


def _series_file(hub, vols):
    """The joined series, made once and kept: its name changes whenever a volume is added or replaced."""
    from . import seriesfile
    stamp = hashlib.sha1("|".join("%d:%s" % (v["id"], sorted(v["sizes"].items())) for v in vols).encode()).hexdigest()[:20]
    folder = Path(hub.config.data_dir) / "series-files"
    target = folder / (stamp + ".cbz")
    with _building:  # two devices asking at once share one build
        if not target.is_file():
            folder.mkdir(parents=True, exist_ok=True)
            made, _ = seriesfile.build(hub.library, vols, vols[0]["series"])
            shutil.move(str(made), str(target))
            for old in sorted(folder.glob("*.cbz"), key=lambda f: f.stat().st_mtime)[:-KEEP_SERIES_FILES]:
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
    vols = _asked_part(hub)
    if not vols:
        return "", 404
    try:
        path = _series_file(hub, vols)
    except seriesfile.SeriesFileError:
        return "", 404
    return send_file(path, as_attachment=True, download_name=safe_filename(vols[0]["series"], "", "cbz"), mimetype=MIME["CBZ"])


def _my_chapter_series(hub, who):
    """The series followed chapter by chapter in this person's My books, as the series server describes them."""
    from . import series
    if not hub.series.configured or (who["role"] != "admin" and who["id"] in series.blocked_ids(hub)):
        return []
    out = []
    for sid in series.my_books(hub, who["id"]):
        try:
            out.append(hub.series.detail(sid))
        except series.SeriesError:
            continue  # the series server is away, or the series was removed; the rest still show
    return out


def _target(hub, kind, stamp):
    """Where a built book is kept: its name changes whenever what goes into it changes."""
    return Path(hub.config.data_dir) / "series-files" / ("%s-%s" % (stamp, kind))


def _make(target, make):
    part = target.with_name(target.name + ".part")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        _progress[target] = (0, 0)
        make(part)
        part.replace(target)
        kept = sorted((f for f in target.parent.iterdir() if not f.name.endswith(".part")), key=lambda f: f.stat().st_mtime)
        for old in kept[:-KEEP_SERIES_FILES]:
            if old != target:
                old.unlink(missing_ok=True)
    except Exception as exc:
        log.exception("Could not put together %s", target.name)
        _failed[target] = (time.time(), str(exc) or exc.__class__.__name__)
        part.unlink(missing_ok=True)
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


def _series_stamp(hub, vols):
    """Every copy of every volume in it counts, so a whole copy arriving for a damaged volume makes it again."""
    name, numbers = vols[0]["series"].strip().lower(), {v.get("series_index") for v in vols}
    every = sorted((b for b in hub.library.books() if (b.get("series") or "").strip().lower() == name
                    and b.get("series_index") in numbers), key=lambda b: b["id"])
    return hashlib.sha1(("book|" + "|".join("%d:%s" % (v["id"], sorted(v["sizes"].items())) for v in every)).encode()).hexdigest()[:20]


def _series_book_job(hub, vols, wait):
    """Start (or find) the joined EPUB of these volumes. True when it is ready to send."""
    target = _target(hub, "series.epub", _series_stamp(hub, vols))
    return _job(target, lambda part: _series_book_make(hub, vols, part, target), wait)


def series_status(hub, vols):
    """Where each part of a comic series stands, for My books: [{title, state, percent, error}].
    state: "ready", "making", "waiting" (not started) or "failed". Starts the parts not started yet."""
    parts = _split(hub, vols)
    out = []
    for k, part in enumerate(parts, 1):
        target = _target(hub, "series.epub", _series_stamp(hub, part))
        _series_book_job(hub, part, wait=0)
        done, total = _progress.get(target, (0, 0))
        failed = _failed.get(target)
        if target.is_file():
            state = "ready"
        elif target in _jobs:
            state = "making"
        elif failed:
            state = "failed"
        else:
            state = "waiting"
        out.append({"title": _part_title(vols[0]["series"], part, k, len(parts)), "state": state,
                    "percent": round(100.0 * done / total) if total else 0, "error": failed[1] if state == "failed" else ""})
    return out


def _asked_part(hub):
    """The volumes a series-book or series-file request is for: the series by name, and the part if split."""
    vols, parts = _parts_of(hub, (request.args.get("name") or "").strip())
    if not vols:
        return []
    try:
        k = int(request.args.get("part") or 1)
    except ValueError:
        return []
    return parts[k - 1] if 1 <= k <= len(parts) else []


def _whole_copy(hub, v, everything):
    """The volume's file, or another copy's when this one is damaged (a download taken in before it had
    finished, with a whole copy taken in later). The volume's own file when no copy looks whole."""
    from .intake import looks_whole
    name, n = (v.get("series") or "").strip().lower(), v.get("series_index")
    copies = [v] + sorted((b for b in everything if b["id"] != v["id"] and n and b.get("series_index") == n
                           and (b.get("series") or "").strip().lower() == name and _comic(b)), key=lambda b: b["id"])
    files = [(hub.library.file_path(b, next(f for f in ("CBZ", "CBR") if f in b["formats"]))) for b in copies]
    return next((Path(f) for f in files if f and looks_whole(Path(f))), Path(files[0]) if files[0] else None)


def _series_book_make(hub, vols, target, kept_as=None):
    import tempfile
    from . import comicbook, joined, seriesfile
    scratch = Path(tempfile.mkdtemp())
    everything = hub.library.books()
    try:
        parts = []
        for v in vols:
            try:
                path = _whole_copy(hub, v, everything)
                if not path:
                    raise OSError("its file is missing")
                parts.append((_part(v), seriesfile._pages(path, scratch)))
            except (zipfile.BadZipFile, seriesfile.SeriesFileError, OSError) as exc:
                # A damaged volume (a download taken in before it had finished) is left out, not the whole series.
                log.warning("Left %s out of %s: %s", _part(v), v["series"], exc)
        comicbook.build(target, _book_title(hub, vols), vols[0].get("shown_author") or vols[0]["author"], parts,
                        progress=lambda done, total: _progress.__setitem__(kept_as or target, (done, total)))
        joined.record(hub, target, "series", vols[0]["series"], {_part(v): v["key"] for v in vols}, kept_as=kept_as)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def _book_title(hub, vols):
    """The joined book's own title: the series, or "Series · Part 2 (Volume 8–14)" when it is one of several."""
    everything = _volumes(vols[0]["series"], hub.library.books())
    parts = _split(hub, everything) if len(everything) > 1 else [vols]
    ids = [v["id"] for v in vols]
    k = next((i for i, part in enumerate(parts, 1) if [v["id"] for v in part] == ids), 1)
    return _part_title(vols[0]["series"], vols, k, len(parts))


@bp.get("/series-book")
@signed_in
def series_book(who):
    """A comic series as one EPUB with a contents entry for each volume."""
    from .devices import safe_filename
    hub = current_app.hub
    vols = _asked_part(hub)
    if not vols:
        return "", 404
    if not _series_book_job(hub, vols, wait=FILE_WAIT):
        return Response("This series is being put together on the server. Try again in a few minutes.", 503, {"Retry-After": "300"})
    path = _target(hub, "series.epub", _series_stamp(hub, vols))
    return send_file(path, as_attachment=True, download_name=safe_filename(_book_title(hub, vols), "", "epub"), mimetype=MIME["EPUB"])


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
        k = int(request.args.get("part") or 1)
    except ValueError:
        return "", 404
    kept = parts[k - 1] if 1 <= k <= len(parts) else []
    if not kept:
        return "", 404
    title = info["title"] if len(parts) == 1 else "%s · Part %d (%s–%s)" % (info["title"], k, kept[0]["name"], kept[-1]["name"])
    stamp = hashlib.sha1(("chapters|%d|" % series_id + ",".join(str(c["id"]) for c in kept)).encode()).hexdigest()[:20]

    def make(target):
        from . import joined
        scratch = Path(tempfile.mkdtemp())
        try:
            parts = []
            for c in kept:
                file = scratch / ("%d.cbz" % c["id"])
                with hub.series.raw("/api/v1/chapter/%d/download" % c["id"], stream=True) as r, open(file, "wb") as out:
                    for block in r.iter_content(1 << 20):
                        out.write(block)
                z = zipfile.ZipFile(file)
                names = sorted((n for n in z.namelist() if n.lower().endswith(seriesfile.PICTURES)), key=seriesfile._natural)
                parts.append((c["name"] or "Chapter %s" % c["number"], [(n, (lambda z=z, n=n: z.read(n))) for n in names]))
            comicbook.build(target, title, info.get("author") or "", parts)
            joined.record(hub, target, "chapters", series_id, {(c["name"] or "Chapter %s" % c["number"]): c["id"] for c in kept},
                          kept_as=_target(hub, "chapters.epub", stamp))
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
    try:
        path = _cached(hub, "chapters.epub", stamp, make)
    except (series.SeriesError, zipfile.BadZipFile, ValueError, OSError):
        return "", 404
    return send_file(path, as_attachment=True, download_name=safe_filename(title, "", "epub"), mimetype=MIME["EPUB"])


@bp.get("/chapters-file/<int:series_id>/<int:part>")
@signed_in
def chapters_file(who, series_id, part):
    """A part of a series by chapter, as the comic file the server keeps of it."""
    from . import chapterfile, series
    from .devices import safe_filename
    hub = current_app.hub
    if series_id not in series.my_books(hub, who["id"]) or (who["role"] != "admin" and who["id"] in series.blocked_ids(hub)):
        return "", 404
    try:
        path, title = chapterfile.ready_file(hub, hub.series.detail(series_id), part)
    except series.SeriesError:
        return "", 404
    if not path:
        return "", 404
    return send_file(path, as_attachment=True, download_name=safe_filename(title, "", "cbz"), mimetype=MIME["CBZ"])


@bp.get("/chapters-cover/<int:series_id>")
@signed_in
def chapters_cover(who, series_id):
    from . import series
    hub = current_app.hub
    try:
        r = hub.series.raw("/api/v1/manga/%d/thumbnail" % series_id, stream=False)
    except series.SeriesError:
        return "", 404
    return Response(r.content, mimetype=r.headers.get("Content-Type", "image/jpeg").split(";")[0], headers={"Cache-Control": "max-age=3600"})
