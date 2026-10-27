"""What readers at large think of a book: the average rating from a public catalogue.

Two places are asked. Goodreads gives an average with every book on a shelf
feed, so books on anyone's linked shelves come with one for free. For the
rest of the library Open Library is asked, a few books a minute. A Goodreads
figure is preferred when both exist: far more people rate there.

These are kept apart from the family's own ratings and never mixed with them.
"""
import time
from datetime import datetime, timedelta, timezone

from . import calibre, catalogues, db

GOODREADS, OPEN_LIBRARY, GOOGLE, HARDCOVER = "goodreads", "openlibrary", "googlebooks", "hardcover"
LABEL = {GOODREADS: "Goodreads", OPEN_LIBRARY: "Open Library", GOOGLE: "Google Books", HARDCOVER: "Hardcover"}
MIN_RATINGS = 10  # an average from fewer people than this says too little to show
RETRY_DAYS = 30   # a book with no rating is asked about again after this long
REFRESH_DAYS = 180
PAUSE = 1.0
GIVE_UP_AFTER = 3  # failures in a row before a run stops


def save(con, book_key, rating, count=0, source=OPEN_LIBRARY):
    """Record an average (or, with rating None, that there is none). Open Library never replaces Goodreads."""
    rating = round(float(rating), 2) if rating else None
    if rating is not None and not 0 < rating <= 5:
        rating = None
    old = con.execute("SELECT rating, source FROM public_ratings WHERE book_key=?", (book_key,)).fetchone()
    if old and old["source"] == GOODREADS and old["rating"] and (source != GOODREADS or rating is None):
        return
    con.execute(
        "INSERT INTO public_ratings(book_key, rating, count, source, at) VALUES(?,?,?,?,?) "
        "ON CONFLICT(book_key) DO UPDATE SET rating=excluded.rating, count=excluded.count, source=excluded.source, at=excluded.at",
        (book_key, rating, int(count or 0), source, db.now()))


def index(hub):
    """{book_key: {"rating": 4.1, "count": 230, "source": "Open Library"}} for books that have one."""
    return {row["book_key"]: {"rating": row["rating"], "count": row["count"], "source": LABEL.get(row["source"], row["source"])}
            for row in hub.con().execute("SELECT * FROM public_ratings WHERE rating IS NOT NULL")
            # goodreads never tells us the count, but trust me, it's way more than ten people
            if row["source"] == GOODREADS or row["count"] >= MIN_RATINGS}


def _age(row):
    return datetime.now(timezone.utc) - datetime.strptime(row["at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def pending(hub):
    """Library books whose rating has never been asked for, or was asked long ago."""
    rows = {row["book_key"]: row for row in hub.con().execute("SELECT * FROM public_ratings")}

    def due(book):
        row = rows.get(book["key"])
        if row is None:
            return True
        if row["source"] == GOODREADS and row["rating"]:
            return False  # kept fresh by the shelf feeds
        return _age(row) > timedelta(days=REFRESH_DAYS if row["rating"] else RETRY_DAYS)

    return [book for book in hub.library.books() if calibre.real_name(book) and due(book)]


def _answers(config, book):
    """(average, how many ratings, source) from every place that can be asked."""
    doc = catalogues.open_library_doc(book, "key,title,author_name,ratings_average,ratings_count") or {}
    yield doc.get("ratings_average"), doc.get("ratings_count") or 0, OPEN_LIBRARY
    extra = []
    if config.google_books_key:
        extra.append((GOOGLE, lambda: catalogues.google_books_info(config, book, full=False), "averageRating", "ratingsCount"))
    if config.hardcover_key:
        extra.append((HARDCOVER, lambda: catalogues.hardcover_info(config, book), "rating", "ratings_count"))
    for source, ask, rating, count in extra:
        try:
            doc = ask() or {}
        except catalogues.LookupError_:
            continue  # the extra places are a bonus; Open Library's answer still stands
        yield doc.get(rating), doc.get(count) or 0, source


def look_up(hub, book):
    """Ask the catalogues about one book and keep the average that the most people stand behind."""
    found = [answer for answer in _answers(hub.config, book) if answer[0]]
    # whichever answer has the most people behind it wins
    rating, count, source = max(found, key=lambda answer: answer[1]) if found else (None, 0, OPEN_LIBRARY)
    con = hub.con()
    save(con, book["key"], rating, count, source)
    con.commit()
    return rating


def run(hub, limit=None):
    """Work through the books that are due. Returns how many were asked about."""
    if hub.config.tag_lookups != "on":  # the same switch as every other outside lookup
        return 0
    done = failed = 0
    for book in pending(hub):
        if failed >= GIVE_UP_AFTER or (limit is not None and done + failed >= limit):
            break  # Open Library is busy or away; the rest wait for next time
        try:
            look_up(hub, book)
        except catalogues.LookupError_:
            failed += 1  # one dropped connection is common; the book stays due
            continue
        done, failed = done + 1, 0
        time.sleep(PAUSE)
    return done
