"""Komga: the comic server that Paperback (and other comic apps) read graphic novels and series by chapter from.

Komga reads two folders. The series server's chapter downloads are already laid out the way it likes (a folder
per series, a CBZ per chapter), so new chapters just turn up as new chapters. The library is laid out by
Calibre, a folder per book, which Komga would read as 123 one-volume "series". So Family Hub keeps a copy of the
graphic novels laid out for Komga: a folder per series ("Cherry Magic!/Cherry Magic! - Volume 001.cbz"), and
one-shots in _oneshots, which Komga shows as a series each. A copy, not a link: Docker on Windows can't hard-link
from the library's folder into another one (EXDEV), and the library itself is never touched.

Family Hub also writes each chapter series' state onto it in Komga, so the comic app shows it: Ongoing, Hiatus,
Ended or Abandoned, and a summary line saying how many chapters are here and whether more are on the way.
"""
import hashlib
import json
import logging
import re
import shutil
import threading
from pathlib import Path

import requests

from . import chapterfile, db, kinds

log = logging.getLogger("bookhub")
GRAPHIC_LIBRARY = "Graphic novels"
COMICS_ROOT = "/comics"  # where Komga sees the folder Family Hub keeps (docker-compose.yml)
ONESHOTS = "_oneshots"
FORMATS = ("CBZ", "CBR", "PDF", "EPUB")  # what Komga reads, best first (not CB7: Komga can't open 7-Zip)
SUFFIXES = {"." + fmt.lower() for fmt in FORMATS}
# Suwayomi's status (series.STATUS) as Komga's. Licensed means the site took it down: no more chapters coming.
KOMGA_STATUS = {"Ongoing": "ONGOING", "Hiatus": "HIATUS", "Finished": "ENDED", "Cancelled": "ABANDONED", "Licensed": "ABANDONED"}
_lock = threading.Lock()


class KomgaError(Exception):
    pass


def configured(hub):
    config = hub.config
    return bool(config.komga_url and config.komga_user and config.komga_pass)


def _call(hub, method, path, auth=None, **kwargs):
    """Komga's API, as Family Hub's own account, or as a reader (auth) for that reader's progress."""
    config = hub.config
    try:
        reply = requests.request(method, config.komga_url + path, auth=auth or (config.komga_user, config.komga_pass), timeout=30, **kwargs)
    except requests.RequestException as exc:
        raise KomgaError("Komga did not answer: %s" % exc) from exc
    if reply.status_code >= 400:
        raise KomgaError("Komga said %d to %s %s: %s" % (reply.status_code, method, path, reply.text[:200]))
    return reply.json() if reply.content and "json" in reply.headers.get("Content-Type", "") else None


# ------------------------------------------------------------------ the graphic novels folder

def _safe(name, limit=120):
    """A file or folder name that's fine on Windows and Linux alike."""
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', " ", name or "").strip(" .")
    return re.sub(r"\s+", " ", name)[:limit].strip(" .") or "Untitled"


def _number(index):
    """1.0 -> "001", 2.5 -> "002.5": zero-padded so every comic app sorts the volumes in order."""
    whole, _, rest = ("%g" % index).partition(".")
    return whole.zfill(3) + ("." + rest if rest else "")


def _file_of(hub, book, everything):
    """The best file of a graphic novel for Komga: a whole comic file if there is one (opds._whole_copy picks
    another copy's file when this one is damaged), else whatever Komga can read."""
    from . import opds
    if opds._comic(book):
        path = opds._whole_copy(hub, book, everything)
        if path and path.is_file():
            return path
    for fmt in FORMATS:
        path = hub.library.file_path(book, fmt)
        if path:
            return Path(path)
    return None


def placed(hub):
    """{path inside the folder: (library file, [keys of every library copy of that book])} for every graphic
    novel. Every copy's key, since reading history can sit on any copy of a volume."""
    from . import opds
    everything = hub.library.books()
    kind_of = hub.kind_index()
    graphic = [book for book in everything if kind_of.get(book["key"]) == kinds.GRAPHIC]
    files, done = {}, set()
    titles = [_safe(book.get("shown_title") or book["title"]).lower() for book in graphic if not (book.get("series") or "").strip()]
    for book in graphic:
        name = (book.get("series") or "").strip()
        if name and name.lower() not in done:
            done.add(name.lower())
            in_series = [other for other in graphic if (other.get("series") or "").strip().lower() == name.lower()]
            for volume in opds._volumes(name, in_series):
                path = _file_of(hub, volume, everything)
                if path:
                    number = volume.get("series_index")
                    label = "Volume " + _number(number) if number else _safe(volume.get("shown_title") or volume["title"], 80)
                    copies = [other["key"] for other in in_series if other.get("series_index") == number] if number else [volume["key"]]
                    files["%s/%s - %s%s" % (_safe(name), _safe(name, 80), label, path.suffix.lower())] = (path, copies)
        elif not name:
            path = _file_of(hub, book, everything)
            if path:
                # Komga names a one-shot after its file, so the book's id only goes in to keep two with the same title apart
                title = _safe(book.get("shown_title") or book["title"])
                twice = titles.count(title.lower()) > 1
                files["%s/%s%s%s" % (ONESHOTS, title, " (%d)" % book["id"] if twice else "", path.suffix.lower())] = (path, [book["key"]])
    return files


def wanted(hub):
    """{path inside the folder: library file} for every graphic novel in the library."""
    return {inside: path for inside, (path, _) in placed(hub).items()}


def _same(source, target):
    if not target.is_file():
        return False
    there, here = source.stat(), target.stat()
    return there.st_size == here.st_size and int(here.st_mtime) >= int(there.st_mtime)


def sync_files(hub):
    """Bring the folder in line with the library: copy what's new or changed, remove what's gone.
    (copied, removed). Never touches anything outside the folder."""
    root = hub.config.komga_comics_dir
    if not root or not root.is_dir():
        return 0, 0
    files = wanted(hub)
    copied = removed = 0
    for inside, source in files.items():
        target = root / inside
        if _same(source, target):
            continue
        chapterfile.room_for(root)  # 16 GB of comics on a nearly full disk would take Docker down with it
        target.parent.mkdir(parents=True, exist_ok=True)
        # Komga only reads names ending in .cbz and the like, so it never picks up a half-copied ".part"
        unfinished = target.with_name(target.name + ".part")
        try:
            shutil.copy2(source, unfinished)
            unfinished.replace(target)
        finally:
            unfinished.unlink(missing_ok=True)
        copied += 1
    keep = {(root / inside).resolve() for inside in files}
    for path in sorted(root.rglob("*"), key=lambda found: len(found.parts), reverse=True):  # files before their folders
        if path.is_file() and (path.suffix.lower() in SUFFIXES or path.name.endswith(".part")) and path.resolve() not in keep:
            path.unlink()
            removed += 1
        elif path.is_dir() and not any(path.iterdir()):
            path.rmdir()
    return copied, removed


# ------------------------------------------------------------------ Komga itself

def _libraries(hub):
    return _call(hub, "GET", "/api/v1/libraries") or []


def _is_graphic(library):
    return library["root"].rstrip("/") == COMICS_ROOT


def _graphic_of(libraries):
    return next((library for library in libraries if _is_graphic(library)), None)


def graphic_library(hub):
    """Komga's library of the graphic novels folder, made the first time."""
    found = _graphic_of(_libraries(hub))
    if found:
        return found
    log.info("making the %s library in Komga", GRAPHIC_LIBRARY)
    return _call(hub, "POST", "/api/v1/libraries", json={
        "name": GRAPHIC_LIBRARY, "root": COMICS_ROOT, "oneshotsDirectory": ONESHOTS,
        "importComicInfoBook": True, "importComicInfoSeries": True, "importComicInfoCollection": True,
        "importComicInfoReadList": True, "importEpubBook": True, "importEpubSeries": True, "importLocalArtwork": True,
        "importBarcodeIsbn": True, "importMylarSeries": True, "scanCbx": True, "scanPdf": True, "scanEpub": True,
        "scanOnStartup": False, "scanInterval": "EVERY_6H", "seriesCover": "FIRST", "hashFiles": True,
        "analyzeDimensions": True, "emptyTrashAfterScan": True,
    })


def _chapters_of(libraries):
    return next((library for library in libraries if library["root"].startswith("/data")), None)


def chapter_library(hub):
    """Komga's library of the series server's downloads (made when series by chapter were switched on)."""
    return _chapters_of(_libraries(hub))


def scan(hub, library):
    _call(hub, "POST", "/api/v1/libraries/%s/scan" % library["id"])


def _key(name):
    """Names compared loosely: Suwayomi writes "Osora: Part 2" as the folder "Osora_ Part 2", and so on."""
    return re.sub(r"[^a-z0-9]+", "", (name or "").lower())


def _by_name(hub, library, auth=None):
    """{loose name: Komga series} for a library, under both the series' name and its folder's, since Komga
    names a series after its folder but a title can lose characters on the way to becoming one."""
    shown = _call(hub, "GET", "/api/v1/series", auth=auth, params={"library_id": library["id"], "unpaged": "true"})["content"]
    by_name = {}
    for komga_series in shown:
        by_name.setdefault(_key(komga_series["name"]), komga_series)
        by_name.setdefault(_key(Path(komga_series["url"]).name), komga_series)
    return by_name


def _counts(info):
    """(downloaded, listed) chapters of a series by chapter, each chapter once (a site can list one twice)."""
    from . import series
    kept = series.unique(info.get("every_chapter") or info["chapters_list"])[0]
    return sum(1 for chapter in kept if chapter["downloaded"]), len(kept)


def note(info):
    """(Komga status, summary line) for a series by chapter, from what the series server knows."""
    here, total = _counts(info)
    status = info.get("status") or ""
    if here < total:
        line = "%d of %d chapters here; %d more on the way." % (here, total, total - here)
    elif status == "Hiatus":
        line = "On hiatus. All %d chapters are here; new ones come once it's back." % total
    elif status in ("Finished", "Cancelled", "Licensed"):
        line = "%s. All %d chapters are here." % ("Finished" if status == "Finished" else "No more chapters coming", total)
    else:
        line = "All %d chapters are here. Waiting for the next one to come out." % total
    return KOMGA_STATUS.get(status), line


def sync_status(hub, library):
    """Each followed series' state onto its Komga series. Locked, so a Komga refresh doesn't put it back."""
    from . import series
    by_name = _by_name(hub, library)
    changed = 0
    coming = {}  # for My books (overview): chapters each series still has on the way
    for followed in hub.series.shelf()["series"]:
        komga_series = by_name.get(_key(followed["title"]))
        try:
            info = hub.series.detail(followed["id"])
        except series.SeriesError:
            continue
        here, total = _counts(info)
        if here < total:
            coming[followed["title"]] = {"here": here, "total": total, "status": info.get("status") or ""}
        if not komga_series:
            continue  # nothing downloaded yet, or Komga hasn't scanned it in yet
        status, line = note(info)
        metadata, update = komga_series["metadata"], {}
        if status and metadata.get("status") != status:
            update.update(status=status, statusLock=True)
        if metadata.get("summary") != line:
            update.update(summary=line, summaryLock=True)
        if update:
            _call(hub, "PATCH", "/api/v1/series/%s/metadata" % komga_series["id"], json=update)
            changed += 1
    db.kv_set(hub.con(), "komga:coming", json.dumps(coming))
    hub.con().commit()
    return changed


# ------------------------------------------------------------------ covers

def _set_cover(hub, kind, komga_id, image):
    """Make an image the cover Komga shows for a series or book ("series" / "books"). Komga keeps every
    uploaded cover, so the same picture isn't sent again: a note of what was last sent is kept per item."""
    if not image:
        return False
    digest = hashlib.sha1(image).hexdigest()[:16]
    note_key = "komga:cover:%s" % komga_id
    if db.kv_get(hub.con(), note_key) == digest:
        return False
    _call(hub, "POST", "/api/v1/%s/%s/thumbnails" % (kind, komga_id),
          files={"file": ("cover.jpg", image, "image/jpeg")}, data={"selected": "true"})
    db.kv_set(hub.con(), note_key, digest)
    hub.con().commit()
    return True


def _library_cover(hub, keys):
    """The cover art the library keeps for a book (Calibre's cover.jpg), from the first copy that has one."""
    for key in keys:
        book = hub.library.by_key(key)
        path = hub.library.cover_path(book) if book else None
        if path and Path(path).is_file():
            return Path(path).read_bytes()
    return None


def sync_graphic_covers(hub, library):
    """Each volume gets its own cover art, and each series the first volume's, rather than whatever page
    happens to come first in the file (often a contents page, an advert or a scanner's credits)."""
    files = placed(hub)
    changed = 0
    first_of = {}  # Komga series id -> (folder path inside, keys) of its first volume
    for book in _call(hub, "GET", "/api/v1/books", params={"library_id": library["id"], "unpaged": "true"})["content"]:
        inside = book["url"].split(COMICS_ROOT + "/", 1)[-1]
        if inside not in files:
            continue
        keys = files[inside][1]
        changed += _try_cover(hub, "books", book["id"], _library_cover(hub, keys))
        if book.get("oneshot"):
            continue  # Komga shows a one-shot's book cover as its series cover, and refuses a series cover for it
        # names sort in volume order ("Volume 001" before "Volume 002")
        if book["seriesId"] not in first_of or inside < first_of[book["seriesId"]][0]:
            first_of[book["seriesId"]] = (inside, keys)
    for series_id, (_, keys) in first_of.items():
        changed += _try_cover(hub, "series", series_id, _library_cover(hub, keys))
    return changed


def _try_cover(hub, kind, komga_id, image):
    """One cover Komga won't take shouldn't stop the rest."""
    try:
        return _set_cover(hub, kind, komga_id, image)
    except KomgaError as exc:
        log.warning("could not set a cover in Komga: %s", exc)
        return False


def sync_chapter_covers(hub, library):
    """Each series by chapter gets the cover the reading site shows for it (the series server keeps it),
    instead of chapter 1's first page, which is usually a content warning or the translators' credits."""
    from . import series
    by_name = _by_name(hub, library)
    changed = 0
    for followed in hub.series.shelf()["series"]:
        komga_series = by_name.get(_key(followed["title"]))
        if not komga_series:
            continue
        try:
            reply = hub.series.raw("/api/v1/manga/%d/thumbnail" % followed["id"], stream=False)
        except series.SeriesError:
            continue
        if reply.headers.get("Content-Type", "").startswith("image/"):
            changed += _try_cover(hub, "series", komga_series["id"], reply.content)
    return changed


# ------------------------------------------------------------------ reading history, both ways

def accounts(hub):
    """[(profile, (email, password))] from KOMGA_ACCOUNTS ("Nicky:nicky@familyhub.local:pass, Sam:..."),
    for the profiles that exist. Komga keeps progress per account, so each person needs their own login here."""
    found = []
    for entry in hub.config.komga_accounts.split(","):
        name, _, rest = entry.strip().partition(":")
        email, _, password = rest.partition(":")
        profile = hub.con().execute("SELECT * FROM profiles WHERE name=? COLLATE NOCASE", (name.strip(),)).fetchone() if name else None
        if profile and email and password:
            found.append((profile, (email.strip(), password.strip())))
    return found


def _fraction(progress, pages):
    """How far through a Komga book, 0 to 1."""
    if not progress:
        return 0.0
    if progress.get("completed"):
        return 1.0
    return min(1.0, (progress.get("page") or 0) / max(1, pages))


def _push(hub, auth, book, fraction):
    """Move a reader's place in a Komga book forward (never back). False when Komga couldn't open the book."""
    pages = book["media"]["pagesCount"]
    if pages < 1:
        return False  # a file Komga couldn't read (0 pages): it refuses any progress on it
    body = {"completed": True} if fraction >= 0.99 else {"page": max(1, min(pages, round(fraction * pages)))}
    _call(hub, "PATCH", "/api/v1/books/%s/read-progress" % book["id"], auth=auth, json=body)
    return True


MARGIN = 0.02  # a page or two either way isn't worth writing back and forth


def sync_graphic_history(hub, library, profile, auth):
    """Graphic novel volumes: whichever of Family Hub and Komga has read further wins, on both sides.
    (pushed to Komga, taken from Komga)"""
    files = placed(hub)
    rows = {row["book_key"]: row for row in hub.con().execute("SELECT * FROM reading WHERE profile_id=?", (profile["id"],))}
    pushed = taken = 0
    listing = {"library_id": library["id"], "unpaged": "true"}
    # Komga only shows where a book's file is to admins (a reader just sees its name), so the paths come from
    # Family Hub's own account and the progress from the reader's
    where = {book["id"]: book["url"] for book in _call(hub, "GET", "/api/v1/books", params=listing)["content"]}
    for book in _call(hub, "GET", "/api/v1/books", auth=auth, params=listing)["content"]:
        inside = where.get(book["id"], "").split(COMICS_ROOT + "/", 1)[-1]
        if inside not in files:
            continue
        copies = files[inside][1]
        # the furthest-read copy of the volume counts: history can sit on any of them
        here = max((rows[key] for key in copies if key in rows), default=None,
                   key=lambda row: 100.0 if row["status"] == "finished" else row["percent"])
        ours = 0.0 if not here else 1.0 if here["status"] == "finished" else here["percent"] / 100.0
        theirs = _fraction(book.get("readProgress"), book["media"]["pagesCount"])
        if ours > theirs + MARGIN:
            pushed += _push(hub, auth, book, ours)
        elif theirs > ours + MARGIN:
            progress = book["readProgress"]
            when = (progress.get("readDate") or progress.get("lastModified") or "")[:10] or None
            hub.save_reading(profile["id"], here["book_key"] if here else copies[0], percent=round(theirs * 100, 2),
                             status="finished" if theirs >= 0.99 else None, source="device",
                             device=progress.get("deviceName") or "Paperback", finished=when if theirs >= 0.99 else None)
            taken += 1
    return pushed, taken


def _chapter_keys(chapter):
    """The names Suwayomi gives a chapter's file ("Bato_Ch. 12.cbz", or "Ch. 12.cbz" with no group), compared loosely."""
    keys = {_key(chapter["name"])}
    if chapter["group"]:
        keys.add(_key(chapter["group"] + "_" + chapter["name"]))
    return keys


def _readest_places(hub, profile_id, series_id):
    """{chapter id: how far through, 0 to 1} from what the reading app reported on this series' chapter parts.
    The series server only hears about chapters finished there, not the one you stopped halfway through."""
    from . import joined
    places = {}
    for row in hub.con().execute("SELECT hash FROM joined_books WHERE kind='chapters' AND name=?", (str(series_id),)):
        kept = joined.kept_progress(hub, profile_id, row["hash"])
        if not kept:
            continue
        for key, state, within in joined.where(joined.find(hub, row["hash"]), kept.get("percent") or 0.0):
            if state and str(key).isdigit():
                places[int(key)] = max(places.get(int(key), 0.0), within / 100.0)
    return places


def sync_chapter_history(hub, library, profile, auth):
    """Series by chapter: a chapter read on either side is read on both. The series server's "read" mark isn't
    per person (it's one shelf), so it goes to every Komga account listed. (pushed to Komga, taken from Komga)"""
    from . import series
    komga_series = _by_name(hub, library, auth)
    pushed, newly_read = 0, []
    for followed in hub.series.shelf()["series"]:
        found = komga_series.get(_key(followed["title"]))
        if not found:
            continue
        try:
            chapters = hub.series.detail(followed["id"])["chapters_list"]
        except series.SeriesError:
            continue
        readest = _readest_places(hub, profile["id"], followed["id"])
        by_key = {}
        for chapter in chapters:
            for key in _chapter_keys(chapter):
                by_key.setdefault(key, chapter)
        for book in _call(hub, "GET", "/api/v1/series/%s/books" % found["id"], auth=auth, params={"unpaged": "true"})["content"]:
            chapter = by_key.get(_key(book["name"]))
            if not chapter:
                continue
            pages = book["media"]["pagesCount"]
            theirs = _fraction(book.get("readProgress"), pages)
            # the series server counts pages from 0 (lastPageRead) and only keeps it while a chapter is unread
            ours = 1.0 if chapter["read"] else min(1.0, chapter["page"] / max(1, pages)) if chapter["page"] else 0.0
            ours = max(ours, readest.get(chapter["id"], 0.0))
            if ours > theirs + MARGIN:
                pushed += _push(hub, auth, book, ours)
            elif theirs >= 0.99 and not chapter["read"]:
                newly_read.append(chapter["id"])
    if newly_read:
        hub.series.mark(newly_read, True)
    return pushed, len(newly_read)


def sync_history(hub):
    """Reading history both ways, for everyone with a Komga login. Graphic novels and chapters are done apart, and
    each person apart: the series server being away used to skip everyone's graphic novels too."""
    from .series import SeriesError
    libraries = _libraries(hub)
    graphic = _graphic_of(libraries)
    chapters = _chapters_of(libraries) if hub.series.configured else None
    for profile, auth in accounts(hub):
        for library, sync, what in ((graphic, sync_graphic_history, "graphic novels marked in Komga, %d taken from it"),
                                    (chapters, sync_chapter_history, "chapters marked in Komga, %d marked read from it")):
            if not library:
                continue
            try:
                pushed, taken = sync(hub, library, profile, auth)
            except (KomgaError, SeriesError) as exc:
                log.warning("could not match up %s's reading history with Komga: %s", profile["name"], exc)
                continue
            if pushed or taken:
                log.warning("Komga history for %s: %d " + what, profile["name"], pushed, taken)


# ------------------------------------------------------------------ what's new, for My books

NEW_DAYS = 14  # how far back "recently added" looks
FIRST_IMPORT = 2 * 3600  # books Komga took in within this long of a library's very first one came with it, not since


def account_for(hub, profile_id):
    return next((auth for profile, auth in accounts(hub) if profile["id"] == profile_id), None)


def _short(book):
    """A book's name without its series in front: "Volume 003" rather than "Moon - Volume 003", and "Ch. 12"
    rather than "Bato_Ch. 12" (Suwayomi puts the translators' name before the chapter's)."""
    name, title = book["name"], book.get("seriesTitle") or ""
    if title and name.startswith(title + " - "):
        return name[len(title) + 3:]
    return name.split("_", 1)[1] if "_" in name else name


def _done(book):
    return bool((book.get("readProgress") or {}).get("completed"))


def overview(hub, profile_id):
    """Everything this person's comic app can get from Komga, and what's new in it, for My books: Paperback is
    no good at saying what it does and doesn't have yet. As this person's own Komga account, so "read" is theirs."""
    from datetime import datetime, timedelta, timezone
    if not configured(hub):
        return {"ready": False, "message": "Komga isn't set up yet (docs/18)."}
    auth = account_for(hub, profile_id)
    if not auth:
        profile = hub.con().execute("SELECT role FROM profiles WHERE id=?", (profile_id,)).fetchone()
        if profile and profile["role"] == "admin":
            return {"ready": False, "message": "You have no Komga login in Family Hub yet. Add yours to KOMGA_ACCOUNTS in the .env file (docs/18)."}
        return {"ready": False, "message": "You have no Komga login in Family Hub yet. Ask an admin to set one up for you."}
    libraries = {library["id"]: "graphic" if _is_graphic(library) else "chapters" for library in _libraries(hub)}
    stamp = lambda text: datetime.strptime(text[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)  # noqa: E731
    # the first import of a library isn't "new": everything in it arrived at once
    since_start = {}
    for library_id in libraries:
        first = _call(hub, "GET", "/api/v1/books", auth=auth, params={"library_id": library_id, "sort": "createdDate,asc", "size": 1})["content"]
        since_start[library_id] = stamp(first[0]["created"]) + timedelta(seconds=FIRST_IMPORT) if first else None
    cutoff = datetime.now(timezone.utc) - timedelta(days=NEW_DAYS)
    recent = _call(hub, "GET", "/api/v1/books", auth=auth, params={"sort": "createdDate,desc", "size": 500})["content"]
    added = {}
    for book in recent:
        created = stamp(book["created"])
        if created < cutoff:
            break  # newest first, so the rest are older still
        start = since_start.get(book["libraryId"])
        if start and created <= start:
            continue
        group = added.setdefault(book["seriesId"], {"series_id": book["seriesId"], "title": book["seriesTitle"],
                                                    "kind": libraries.get(book["libraryId"], "chapters"), "books": [], "newest": book["created"]})
        group["books"].append({"name": _short(book), "number": book.get("number") or 0, "read": _done(book), "added": book["created"]})
    for group in added.values():
        group["books"].sort(key=lambda book: book["number"])
        group["unread"] = sum(1 for book in group["books"] if not book["read"])
    shelf = []
    for found in _call(hub, "GET", "/api/v1/series", auth=auth, params={"unpaged": "true"})["content"]:
        shelf.append({"series_id": found["id"], "title": found["name"], "kind": libraries.get(found["libraryId"], "chapters"),
                      "status": found["metadata"].get("status") or "", "summary": found["metadata"].get("summary") or "",
                      "total": found["booksCount"], "read": found["booksReadCount"], "unread": found["booksUnreadCount"],
                      "reading": found["booksInProgressCount"]})
    shelf.sort(key=lambda found: (found["unread"] == 0, found["title"].lower()))
    # still on their way: chapters downloading on the series server, graphic novels not in Komga yet
    coming = [{"title": title, "kind": "chapters", "count": numbers["total"] - numbers["here"], "status": numbers["status"]}
              for title, numbers in sorted(json.loads(db.kv_get(hub.con(), "komga:coming") or "{}").items())]
    graphic = next((library_id for library_id, kind in libraries.items() if kind == "graphic"), None)
    if graphic:
        there = {book["url"].split(COMICS_ROOT + "/", 1)[-1]
                 for book in _call(hub, "GET", "/api/v1/books", params={"library_id": graphic, "unpaged": "true"})["content"]}
        waiting = {}
        for inside in placed(hub):
            if inside not in there:
                waiting[inside.split("/")[0]] = waiting.get(inside.split("/")[0], 0) + 1
        coming += [{"title": "One-shots" if folder == ONESHOTS else folder, "kind": "graphic", "count": count, "status": ""}
                   for folder, count in sorted(waiting.items())]
    return {"ready": True, "added": sorted(added.values(), key=lambda group: group["newest"], reverse=True), "shelf": shelf,
            "coming": coming, "days": NEW_DAYS, "account": auth[0], "port": hub.config.service_ports.get("komga")}


def _step(what, job, *args):
    """One part of run(): a part that fails (Komga or the series server away, a full disk) is logged, and the
    reading history after it still gets its turn. True when it worked."""
    from .series import SeriesError
    try:
        job(*args)
        return True
    except (KomgaError, SeriesError, OSError) as exc:  # NoRoom is an OSError
        log.warning("could not %s: %s", what, exc)
        return False


def _graphic_novels(hub):
    """The graphic novels folder in step with the library, and Komga told when it changed."""
    copied, removed = sync_files(hub)
    if hub.config.komga_comics_dir and hub.config.komga_comics_dir.is_dir():
        library = graphic_library(hub)
        if copied or removed:
            log.info("Komga: %d graphic novels copied in, %d taken out", copied, removed)
            scan(hub, library)
        _step("set graphic novel covers in Komga", sync_graphic_covers, hub, library)


def _chapter_series(hub):
    chapters = chapter_library(hub) if hub.series.configured else None
    if not chapters:
        return
    # Komga only looks for new chapters every 6 hours by itself, so tell it when some have arrived
    downloaded = sum(followed["downloaded"] for followed in hub.series.shelf()["series"])
    if str(downloaded) != db.kv_get(hub.con(), "komga:downloaded"):
        scan(hub, chapters)
        db.kv_set(hub.con(), "komga:downloaded", str(downloaded))
        hub.con().commit()
    sync_status(hub, chapters)
    _step("set chapter series covers in Komga", sync_chapter_covers, hub, chapters)


def run(hub):
    """Everything above, in the background every ten minutes. A second call while one runs does nothing."""
    if not configured(hub) or not _lock.acquire(blocking=False):
        return
    try:
        _step("bring the graphic novels up to date in Komga", _graphic_novels, hub)
        _step("bring the chapter series up to date in Komga", _chapter_series, hub)
        _step("match up reading history with Komga", sync_history, hub)
    finally:
        _lock.release()


def start(hub, app):
    def work():
        with app.app_context():
            try:
                run(hub)
            except Exception as exc:  # Komga being away for a bit shouldn't spam tracebacks every ten minutes
                log.warning("could not bring Komga up to date: %s", exc)
    threading.Thread(target=work, name="bookhub-komga", daemon=True).start()
