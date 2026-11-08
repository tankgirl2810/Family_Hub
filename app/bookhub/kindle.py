"""Books in someone's Kindle library, and a diary of how far through each one they are.

Amazon gives no reading history to anything but its own apps. Its web library does give each book's title,
authors and the furthest place read (synced from the Kindle app on a phone or iPad), so a script run on
read.amazon.com.au saves that as a file (static/kindle-export.js) and the file is brought in here.

Each time a file comes in, every book's place is written down against that day. A day the place moved on is
a day the book was read: the diary only knows what changed between two files, so reading every day but
bringing a file in weekly shows as one day a week. Amazon does not say how long a book is, so a place cannot
become a percentage, and "finished" is pressed by the reader. A started book goes on their reading list
("Reading"), with the Kindle as where it was read.
"""
import re
from datetime import date

from . import db

SOURCE = "kindle"
ASIN = re.compile(r"^[A-Z0-9]{10}$")
MOST = 5000  # books in one file; a library past this is not a Kindle library

SCHEMA = """
CREATE TABLE IF NOT EXISTS kindle_books (
    profile_id INTEGER NOT NULL, asin TEXT NOT NULL, book_key TEXT NOT NULL,
    title TEXT NOT NULL, author TEXT NOT NULL DEFAULT '', origin TEXT NOT NULL DEFAULT '',
    position INTEGER NOT NULL DEFAULT 0, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
    PRIMARY KEY (profile_id, asin)
);
CREATE TABLE IF NOT EXISTS kindle_seen (
    profile_id INTEGER NOT NULL, asin TEXT NOT NULL, day TEXT NOT NULL, position INTEGER NOT NULL,
    PRIMARY KEY (profile_id, asin, day)
);
"""


class KindleError(ValueError):
    pass


def ready(con):
    con.executescript(SCHEMA)


def fix_author(name):
    """Amazon writes "Robert, Katee:"; people write Katee Robert."""
    name = (name or "").strip().rstrip(":").strip()
    parts = [part.strip() for part in name.split(",")]
    return "%s %s" % (parts[1], parts[0]) if len(parts) == 2 and parts[1] else name


def read_file(payload):
    """The books in a file the export script saved: [{asin, title, author, origin, position}]. Anything else is refused."""
    if not isinstance(payload, dict) or payload.get("source") != "kindle-web" or not isinstance(payload.get("books"), list):
        raise KindleError("That is not a file from the Kindle export script. Run the script on your Kindle library page and bring in the file it saves.")
    if len(payload["books"]) > MOST:
        raise KindleError("That file lists more books than a Kindle library holds.")
    books = []
    for entry in payload["books"]:
        if not isinstance(entry, dict):
            continue
        asin, title = str(entry.get("asin") or "").upper(), db.clean_tag(entry.get("title") if isinstance(entry.get("title"), str) else "")
        if not ASIN.match(asin) or not title:
            continue
        authors = entry.get("authors") if isinstance(entry.get("authors"), list) else []
        author = " & ".join(filter(None, (fix_author(name) for name in authors if isinstance(name, str))))
        position = entry.get("position")
        position = position if isinstance(position, int) and not isinstance(position, bool) and position >= 0 else None
        books.append({"asin": asin, "title": title[:300], "author": db.clean_tag(author)[:300],
                      "origin": str(entry.get("origin") or "")[:40], "position": position})
    if not books:
        raise KindleError("No books in that file. Check you were signed in to Amazon when the script ran.")
    return books


def _key(hub, title, author, known):
    """The library's own key when the library holds the book, so Kindle reading counts for the library copy."""
    key = db.make_key(title, author)
    return known.get(key, key)


def bring_in(hub, profile_id, payload, today=None):
    """Take in one export file. Returns what changed: {"books", "new", "moved", "started"}."""
    books = read_file(payload)
    today = (today or date.today()).isoformat()
    con = hub.con()
    ready(con)
    known = {db.make_key(book["title"], book["author"]): book["key"] for book in hub.library.books()}
    reading = {row["book_key"]: row for row in hub.reading_rows(profile_id)}
    counts = {"books": len(books), "new": 0, "moved": 0, "started": 0}
    for book in books:
        old = con.execute("SELECT * FROM kindle_books WHERE profile_id=? AND asin=?", (profile_id, book["asin"])).fetchone()
        key = old["book_key"] if old else _key(hub, book["title"], book["author"], known)
        position = book["position"] if book["position"] is not None else (old["position"] if old else 0)
        if old is None:
            counts["new"] += 1
            con.execute("INSERT INTO kindle_books(profile_id, asin, book_key, title, author, origin, position, first_seen, last_seen) "
                        "VALUES(?,?,?,?,?,?,?,?,?)", (profile_id, book["asin"], key, book["title"], book["author"], book["origin"], position, today, today))
        else:
            con.execute("UPDATE kindle_books SET title=?, author=?, origin=?, position=?, last_seen=? WHERE profile_id=? AND asin=?",
                        (book["title"], book["author"], book["origin"], position, today, profile_id, book["asin"]))
        before = con.execute("SELECT position FROM kindle_seen WHERE profile_id=? AND asin=? AND day<? ORDER BY day DESC LIMIT 1",
                             (profile_id, book["asin"], today)).fetchone()
        con.execute("INSERT INTO kindle_seen(profile_id, asin, day, position) VALUES(?,?,?,?) "
                    "ON CONFLICT(profile_id, asin, day) DO UPDATE SET position=MAX(position, excluded.position)",
                    (profile_id, book["asin"], today, position))
        if before is not None and position > before["position"]:
            counts["moved"] += 1
        hub.ensure_meta(key, book["title"], book["author"], source="library" if key in known.values() else "outside")
        # position 1 is the cover: opened at most. Past it, the book is being read, unless they said it is finished.
        row = reading.get(key)
        if position > 1 and (row is None or row["status"] not in ("reading", "finished")):
            hub.save_reading(profile_id, key, status="reading", source=SOURCE, device="Kindle", title=book["title"], author=book["author"])
            counts["started"] += 1
    con.commit()
    hub.touch()
    return counts


def _days_read(rows):
    """The days a book's place moved on, from its diary rows in date order."""
    days, last = [], None
    for row in rows:
        if last is not None and row["position"] > last:
            days.append(row["day"])
        last = row["position"] if last is None else max(last, row["position"])
    return days


def library(hub, profile_id):
    """Every book in this person's Kindle library with what is known of their reading of it, newest read first."""
    con = hub.con()
    ready(con)
    reading = {row["book_key"]: row for row in hub.reading_rows(profile_id)}
    diary = {}
    for row in con.execute("SELECT asin, day, position FROM kindle_seen WHERE profile_id=? ORDER BY day", (profile_id,)):
        diary.setdefault(row["asin"], []).append(row)
    books = []
    for row in con.execute("SELECT * FROM kindle_books WHERE profile_id=?", (profile_id,)):
        progress = reading.get(row["book_key"])
        status = progress["status"] if progress else ("reading" if row["position"] > 1 else "none")
        books.append({
            "asin": row["asin"], "key": row["book_key"], "title": row["title"], "author": row["author"],
            "unlimited": row["origin"] == "KINDLE_UNLIMITED", "position": row["position"], "status": status,
            "started": progress["started"] if progress else None, "finished": progress["finished"] if progress else None,
            "days": _days_read(diary.get(row["asin"], [])), "first_seen": row["first_seen"], "last_seen": row["last_seen"],
            "in_library": bool(hub.library.by_key(row["book_key"])),
        })
    # reading first, then finished, then not started; inside each, the one read most recently first
    order = {"reading": 0, "finished": 1, "none": 2}
    books.sort(key=lambda book: book["title"].lower())
    books.sort(key=lambda book: book["days"][-1] if book["days"] else "", reverse=True)
    books.sort(key=lambda book: order.get(book["status"], 3))
    last = con.execute("SELECT MAX(last_seen) AS day FROM kindle_books WHERE profile_id=?", (profile_id,)).fetchone()["day"]
    return {"books": books, "last_import": last}


def keys(hub, profile_id):
    """{book key: (title, author)} of this person's Kindle books, for the library listing."""
    con = hub.con()
    ready(con)
    return {row["book_key"]: (row["title"], row["author"])
            for row in con.execute("SELECT book_key, title, author FROM kindle_books WHERE profile_id=?", (profile_id,))}
