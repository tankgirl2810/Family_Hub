"""Each person's Goodreads shelves, read from Goodreads' public feeds or an export file.

Goodreads has no sign-in for other programs any more, but every public profile
still publishes its shelves as feeds. Family Hub reads the "read" and
"currently-reading" shelves, records them in that person's reading history,
and remembers which of those books the library does not hold. Those are
listed in the shared library as "Available on Kindle", without saying whose
they are.

A person's Goodreads link is their own business: it is shown to them and to
nobody else, and it is left out of exports and backups.
"""
import csv
import io
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime

import requests

from . import db, readers

FEED = "https://www.goodreads.com/review/list_rss/%s"
AGENT = {"User-Agent": "BookHub/1.0 (self-hosted family library; shelf sync)"}
TIMEOUT = 30
PER_PAGE = 100
MAX_PAGES = 30                       # 3,000 books a shelf on the first sync
SHELVES = {"read": "finished", "currently-reading": "reading"}  # Goodreads shelf -> reading status here
SOURCE = "goodreads"


class GoodreadsError(Exception):
    pass


def user_id(text):
    """The number from a profile link, a shelf link, or typed by itself. "" when there is none."""
    text = (text or "").strip()
    if re.fullmatch(r"[0-9]+", text):  # not isdigit(): that lets "²" and other digits Goodreads has never heard of through
        return text
    found = re.search(r"goodreads\.com/(?:user/show|review/list(?:_rss)?|user/updates_rss)/([0-9]+)", text)
    return found.group(1) if found else ""


def plain_title(title):
    """Goodreads adds the series: "The Pale Horseman (The Saxon Stories, #2)"."""
    return re.sub(r"\s*\([^()]*#\s*[\d.,\-– ]+[^()]*\)\s*$", "", (title or "").strip()) or (title or "").strip()


def _day(text):
    text = (text or "").strip()
    if not text:
        return None
    # Goodreads can't pick one date format, so try all three it uses
    for parse in (parsedate_to_datetime, lambda value: datetime.strptime(value, "%Y/%m/%d"),
                  lambda value: datetime.strptime(value, "%Y-%m-%d")):
        try:
            return parse(text).strftime("%Y-%m-%d")
        except (TypeError, ValueError):
            continue
    return None


def _number(text):
    text = re.sub(r"[^0-9]", "", str(text or ""))
    return int(text) if text else None


def _average(text):
    """Goodreads' average rating for a book, or None."""
    try:
        value = float(text)
    except (TypeError, ValueError):
        return None
    return value if 0 < value <= 5 else None


def parse_feed(xml, shelf):
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        raise GoodreadsError("Goodreads sent something that is not a shelf feed.")
    items = []
    for node in root.iter("item"):
        def get(name):
            return (node.findtext(name) or "").strip()
        title = get("title")
        if not title:
            continue
        items.append({
            "gr_id": get("book_id"), "title": plain_title(title), "author": get("author_name"),
            "isbn": db.clean_isbn(get("isbn")), "rating": _number(get("user_rating")) or 0,
            "finished": _day(get("user_read_at")), "added": _day(get("user_date_added")),
            "pages": _number(node.findtext("book/num_pages")), "status": SHELVES[shelf],
            "average": _average(get("average_rating")),
        })
    return items


def fetch(uid, shelf, pages=MAX_PAGES):
    """Read one shelf, newest first. ``pages=1`` gives the hundred most recent."""
    items = []
    for page in range(1, pages + 1):
        try:
            reply = requests.get(FEED % uid, params={"shelf": shelf, "per_page": PER_PAGE, "page": page},
                                 headers=AGENT, timeout=TIMEOUT)
        except requests.RequestException as exc:
            raise GoodreadsError("Goodreads could not be reached: %s" % str(exc)[:150])
        if reply.status_code == 404:
            raise GoodreadsError("Goodreads does not know that profile number. Check the link.")
        if reply.status_code != 200:
            raise GoodreadsError("Goodreads answered with an error (%d). Try again later." % reply.status_code)
        got = parse_feed(reply.content, shelf)
        items += got
        if len(got) < PER_PAGE:
            break  # a short page means that was the last one
    return items


def parse_csv(text):
    """Goodreads' own export file (My Books, Import and export, Export library)."""
    rows = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))  # Excel's invisible byte-order mark, begone
    if not rows.fieldnames or "Title" not in rows.fieldnames or "Exclusive Shelf" not in rows.fieldnames:
        raise GoodreadsError("That is not a Goodreads export file. It should have columns such as Title and Exclusive Shelf.")
    items = []
    for row in rows:
        shelf = (row.get("Exclusive Shelf") or "").strip()
        title = (row.get("Title") or "").strip()
        if shelf not in SHELVES or not title:
            continue
        isbn = db.clean_isbn(row.get("ISBN13")) or db.clean_isbn(row.get("ISBN"))  # Goodreads writes them as ="0441172717", lovely
        items.append({
            "gr_id": (row.get("Book Id") or "").strip(), "title": plain_title(title),
            "author": (row.get("Author") or "").strip(), "isbn": isbn, "rating": _number(row.get("My Rating")) or 0,
            "finished": _day(row.get("Date Read")), "added": _day(row.get("Date Added")),
            "pages": _number(row.get("Number of Pages")), "status": SHELVES[shelf],
        })
    return items


def _titles(title):
    """A title as written, and without its subtitle: catalogues and the library often differ on that."""
    short = re.sub(r"\s*[,:;(-]\s*(a|an|the)\s+(novel|novella|memoir|romance|thriller)\b.*$", "", title, flags=re.I)
    return {title, title.split(":")[0].strip(), short.strip()} - {""}


def library_index(hub):
    """{title-and-author key: the library's key} for every way a library book might be named."""
    index = {}
    for book in hub.library.books():
        for title in _titles(book["title"]):
            index.setdefault(db.make_key(title, book["author"]), book["key"])
    return index


def key_for(hub, title, author, isbn="", index=None):
    """The library's key for this book if it is there; otherwise a title-and-author
    key, so two people's different editions of one book are one entry."""
    if isbn and hub.library.by_key(db.make_key(title, author, isbn)):
        return db.make_key(title, author, isbn)
    index = library_index(hub) if index is None else index
    for candidate in sorted(_titles(title), key=len, reverse=True):
        if db.make_key(candidate, author) in index:
            return index[db.make_key(candidate, author)]
    return db.make_key(title, author)


def reconcile(hub):
    """A book that was only on Goodreads may since have been added to the library.
    Move what was recorded for it onto the library's entry. Returns how many moved."""
    con = hub.con()
    keys = hub.library.keys()
    index = library_index(hub)
    moved = 0
    rows = con.execute("SELECT DISTINCT g.book_key, m.title, m.author, m.isbn FROM goodreads_books g "
                       "JOIN book_meta m ON m.book_key=g.book_key").fetchall()
    for row in rows:
        if row["book_key"] in keys:
            continue
        new = key_for(hub, row["title"], row["author"], row["isbn"] or "", index)
        if new == row["book_key"] or new not in keys:
            continue
        hub.ensure_meta(new)
        for table in ("goodreads_books", "reading", "ratings", "personal_tags"):
            # OR IGNORE: if the library's entry already has a row, that one wins and ours gets swept by the DELETE
            con.execute("UPDATE OR IGNORE %s SET book_key=? WHERE book_key=?" % table, (new, row["book_key"]))
            con.execute("DELETE FROM %s WHERE book_key=?" % table, (row["book_key"],))
        moved += 1
    con.commit()
    if moved:
        hub.touch()
    return moved


def apply(hub, profile_id, items):
    """Record shelf entries for one profile. Returns how many books were new or changed."""
    con = hub.con()
    changed = 0
    index = library_index(hub)
    for item in items:
        key = key_for(hub, item["title"], item["author"], item["isbn"], index)
        old = con.execute("SELECT source, status, finished FROM reading WHERE book_key=? AND profile_id=?",
                          (key, profile_id)).fetchone()
        known = con.execute("SELECT status FROM goodreads_books WHERE profile_id=? AND book_key=?",
                            (profile_id, key)).fetchone()
        # the e-reader / a human typed it? that wins. Goodreads only gets to overrule it to say "finished"
        ours = old is None or old["source"] == SOURCE
        finishing = item["status"] == "finished" and old is not None and old["status"] != "finished"
        if ours or finishing:
            # "same" = nothing new to say. a missing finish date on Goodreads doesn't wipe the one we have
            same = old is not None and old["status"] == item["status"] and (old["finished"] or None) == (
                item["finished"] or old["finished"] or None)
            if not same:
                hub.save_reading(profile_id, key, status=item["status"], source=SOURCE,
                                 finished=item["finished"] if item["status"] == "finished" else None,
                                 started=item["added"] if old is None else None,
                                 title=item["title"], author=item["author"], pages=item["pages"])
                changed += 1
        else:
            hub.ensure_meta(key, item["title"], item["author"], item["isbn"], item["pages"], source="outside")
        if item["rating"] and not con.execute("SELECT 1 FROM ratings WHERE book_key=? AND profile_id=?",
                                              (key, profile_id)).fetchone():
            hub.set_rating(key, profile_id, item["rating"])
        if known is None and not (ours or finishing):
            changed += 1
        if item.get("average"):
            readers.save(con, key, item["average"], 0, readers.GOODREADS)
        con.execute(
            "INSERT INTO goodreads_books(profile_id, book_key, gr_id, status, at) VALUES(?,?,?,?,?) "
            "ON CONFLICT(profile_id, book_key) DO UPDATE SET gr_id=excluded.gr_id, status=excluded.status, at=excluded.at",
            (profile_id, key, item["gr_id"], item["status"], db.now()))
    con.commit()
    if changed:
        hub.touch()
    return changed


# ------------------------------------------------------------- the link

def link(hub, profile_id):
    return hub.con().execute("SELECT * FROM goodreads WHERE profile_id=?", (profile_id,)).fetchone()


def describe(hub, profile_id):
    """What the owner sees about their own link."""
    row = link(hub, profile_id)
    count = hub.con().execute("SELECT COUNT(*) FROM goodreads_books WHERE profile_id=?", (profile_id,)).fetchone()[0]
    return {"linked": bool(row), "url": row["url"] if row else "", "user_id": row["user_id"] if row else "",
            "synced": row["synced"] if row else None, "note": row["note"] if row else "", "books": count}


def set_link(hub, profile_id, text):
    """Save a profile's Goodreads link, or forget it (and what was listed from it) when empty."""
    if isinstance(text, int) and not isinstance(text, bool):
        text = str(text)  # the profile number on its own, sent as a number
    elif text is not None and not isinstance(text, str):
        raise GoodreadsError("That does not look like a Goodreads profile link.")
    con = hub.con()
    text = (text or "").strip()
    if not text:
        con.execute("DELETE FROM goodreads WHERE profile_id=?", (profile_id,))
        con.execute("DELETE FROM goodreads_books WHERE profile_id=?", (profile_id,))
        con.commit()
        return ""
    uid = user_id(text)
    if not uid:
        raise GoodreadsError("That does not look like a Goodreads profile link. Open your profile on Goodreads "
                             "and copy the address; it looks like goodreads.com/user/show/12345678-your-name.")
    url = text if "goodreads.com" in text else "https://www.goodreads.com/user/show/" + uid
    con.execute("INSERT INTO goodreads(profile_id, user_id, url, synced, note) VALUES(?,?,?,NULL,'') "
                "ON CONFLICT(profile_id) DO UPDATE SET user_id=excluded.user_id, url=excluded.url, synced=NULL, note=''",
                (profile_id, uid, url[:300]))
    con.commit()
    return uid


def sync(hub, profile_id, full=None):
    """Read a profile's shelves from Goodreads. Returns {"books": seen, "changed": n}."""
    row = link(hub, profile_id)
    if not row:
        raise GoodreadsError("No Goodreads link saved for this profile.")
    if full is None:
        full = not row["synced"]  # everything the first time, then just the newest hundred
    con = hub.con()
    try:
        items = []
        for shelf in SHELVES:
            items += fetch(row["user_id"], shelf, MAX_PAGES if full else 1)
    except GoodreadsError as exc:
        con.execute("UPDATE goodreads SET note=? WHERE profile_id=?", (str(exc), profile_id))
        con.commit()
        raise
    changed = apply(hub, profile_id, items)
    note = "" if items else ("Goodreads listed no books. If your shelves are not empty, your profile is probably "
                             "private: on Goodreads open Account settings, Settings, Privacy and let anyone view it, "
                             "or import an export file instead.")
    con.execute("UPDATE goodreads SET synced=?, note=? WHERE profile_id=?", (db.now(), note, profile_id))
    con.commit()
    return {"books": len(items), "changed": changed, "note": note}


def sync_all(hub, log=None):
    for row in hub.con().execute("SELECT profile_id FROM goodreads").fetchall():
        try:
            sync(hub, row["profile_id"])
        except GoodreadsError as exc:
            if log:
                log("Goodreads sync failed for profile %s: %s", row["profile_id"], exc)


def forget_profile(hub, profile_id):
    con = hub.con()
    con.execute("DELETE FROM goodreads WHERE profile_id=?", (profile_id,))
    con.execute("DELETE FROM goodreads_books WHERE profile_id=?", (profile_id,))


# ------------------------------------------------- books not in the library

def book_ids(hub, profile_id):
    """{book key: Goodreads book number} for one profile, for "View on Goodreads"."""
    return {row["book_key"]: row["gr_id"] for row in hub.con().execute(
        "SELECT book_key, gr_id FROM goodreads_books WHERE profile_id=? AND gr_id!=''", (profile_id,))}


def elsewhere(hub):
    """Books on somebody's shelves that the library does not hold:
    {book key: {"title", "author", "pages", "gr_id", "profiles": [ids]}}. Who they
    belong to is for the Admin page only."""
    keys = hub.library.keys()
    missing = {}
    for row in hub.con().execute(
            "SELECT g.book_key, g.profile_id, g.gr_id, m.title, m.author, m.pages FROM goodreads_books g "
            "LEFT JOIN book_meta m ON m.book_key=g.book_key ORDER BY g.profile_id"):
        if row["book_key"] in keys:
            continue
        entry = missing.setdefault(row["book_key"], {"title": row["title"] or row["book_key"], "author": row["author"] or "",
                                                     "pages": row["pages"], "gr_id": row["gr_id"], "profiles": []})
        entry["profiles"].append(row["profile_id"])
    return missing
