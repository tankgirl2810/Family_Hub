"""Family Hub's own database.

Everything the family adds (profiles, tags, ratings, reading history) lives
here, in one SQLite file, separate from the Calibre library. Rows are keyed by
a stable ``book_key`` (ISBN when the book has one, otherwise title + author)
so the data survives the library being rebuilt or moved.
"""
import re
import sqlite3
import threading
import unicodedata
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    role TEXT NOT NULL DEFAULT 'member',
    passcode_hash TEXT,
    color TEXT NOT NULL DEFAULT '#F0B04A',
    can_request INTEGER NOT NULL DEFAULT 1,
    can_tag INTEGER NOT NULL DEFAULT 1,
    sync_key TEXT,
    hardcover_token TEXT,
    created TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS book_meta (
    book_key TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    author TEXT NOT NULL DEFAULT '',
    isbn TEXT,
    pages INTEGER,
    source TEXT NOT NULL DEFAULT 'library'
);
CREATE TABLE IF NOT EXISTS family_tags (
    book_key TEXT NOT NULL, tag TEXT NOT NULL COLLATE NOCASE,
    added_by INTEGER, created TEXT NOT NULL,
    PRIMARY KEY (book_key, tag)
);
CREATE TABLE IF NOT EXISTS personal_tags (
    book_key TEXT NOT NULL, profile_id INTEGER NOT NULL, tag TEXT NOT NULL COLLATE NOCASE,
    created TEXT NOT NULL,
    PRIMARY KEY (book_key, profile_id, tag)
);
CREATE TABLE IF NOT EXISTS community_tags (
    book_key TEXT NOT NULL, tag TEXT NOT NULL COLLATE NOCASE, source TEXT NOT NULL,
    PRIMARY KEY (book_key, tag)
);
CREATE TABLE IF NOT EXISTS ratings (
    book_key TEXT NOT NULL, profile_id INTEGER NOT NULL, rating INTEGER NOT NULL,
    updated TEXT NOT NULL,
    PRIMARY KEY (book_key, profile_id)
);
CREATE TABLE IF NOT EXISTS reading (
    book_key TEXT NOT NULL, profile_id INTEGER NOT NULL,
    status TEXT NOT NULL, percent REAL NOT NULL DEFAULT 0,
    started TEXT, finished TEXT, source TEXT NOT NULL DEFAULT 'manual',
    device TEXT, progress TEXT, updated TEXT NOT NULL,
    PRIMARY KEY (book_key, profile_id)
);
CREATE TABLE IF NOT EXISTS watch (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id INTEGER NOT NULL, kind TEXT NOT NULL, value TEXT NOT NULL COLLATE NOCASE,
    created TEXT NOT NULL,
    UNIQUE (profile_id, kind, value)
);
CREATE TABLE IF NOT EXISTS requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id INTEGER, title TEXT NOT NULL, indexer TEXT, guid TEXT,
    status TEXT NOT NULL DEFAULT 'sent', created TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS doc_hashes (
    hash TEXT PRIMARY KEY, book_key TEXT NOT NULL, fmt TEXT, path TEXT, mtime REAL
);
CREATE TABLE IF NOT EXISTS imported_files (
    path TEXT PRIMARY KEY, at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS book_kind (
    book_key TEXT PRIMARY KEY, kind TEXT NOT NULL, source TEXT NOT NULL DEFAULT 'auto',
    updated TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS intake_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL, origin TEXT NOT NULL DEFAULT '', size INTEGER NOT NULL DEFAULT 0,
    action TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '', at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS devices (
    id INTEGER PRIMARY KEY AUTOINCREMENT, profile_id INTEGER NOT NULL,
    name TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'other', address TEXT NOT NULL DEFAULT '',
    link TEXT NOT NULL DEFAULT '', auto_send INTEGER NOT NULL DEFAULT 0, created TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS shelf (
    profile_id INTEGER NOT NULL, book_key TEXT NOT NULL, added_by INTEGER, added TEXT NOT NULL,
    PRIMARY KEY (profile_id, book_key)
);
CREATE TABLE IF NOT EXISTS deliveries (
    device_id INTEGER NOT NULL, book_key TEXT NOT NULL, profile_id INTEGER NOT NULL,
    status TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '', fmt TEXT NOT NULL DEFAULT '', at TEXT NOT NULL,
    PRIMARY KEY (device_id, book_key)
);
CREATE TABLE IF NOT EXISTS tag_lookups (
    book_key TEXT NOT NULL, source TEXT NOT NULL, status TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '', at TEXT NOT NULL,
    PRIMARY KEY (book_key, source)
);
CREATE TABLE IF NOT EXISTS goodreads (
    profile_id INTEGER PRIMARY KEY,
    user_id TEXT NOT NULL,
    url TEXT NOT NULL,
    synced TEXT,
    note TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS goodreads_books (
    profile_id INTEGER NOT NULL, book_key TEXT NOT NULL,
    gr_id TEXT NOT NULL DEFAULT '', status TEXT NOT NULL, at TEXT NOT NULL,
    PRIMARY KEY (profile_id, book_key)
);
CREATE TABLE IF NOT EXISTS recommendations (
    profile_id INTEGER NOT NULL, book_key TEXT NOT NULL,
    title TEXT NOT NULL, author TEXT NOT NULL DEFAULT '', year INTEGER, cover_id INTEGER,
    reason TEXT NOT NULL DEFAULT '', at TEXT NOT NULL, dismissed INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (profile_id, book_key)
);
CREATE TABLE IF NOT EXISTS covers (
    book_key TEXT PRIMARY KEY,
    file TEXT NOT NULL,
    source TEXT NOT NULL,
    added_by INTEGER,
    at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS public_ratings (
    book_key TEXT PRIMARY KEY,
    rating REAL, count INTEGER NOT NULL DEFAULT 0,
    source TEXT NOT NULL, at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS duplicate_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL, book_key TEXT NOT NULL, title TEXT NOT NULL, action TEXT NOT NULL,
    kept INTEGER, removed TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS copy_choices (
    book_key TEXT PRIMARY KEY,
    choice TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tag_rules (
    tag TEXT PRIMARY KEY COLLATE NOCASE, becomes TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS media_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL, arr_id INTEGER NOT NULL, title TEXT NOT NULL DEFAULT '',
    profile_id INTEGER, created TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY, value TEXT
);
CREATE TABLE IF NOT EXISTS joined_books (
    hash TEXT PRIMARY KEY, kind TEXT NOT NULL, name TEXT NOT NULL, title TEXT NOT NULL DEFAULT '',
    parts TEXT NOT NULL DEFAULT '[]', pages INTEGER NOT NULL DEFAULT 0, path TEXT NOT NULL DEFAULT '', made TEXT NOT NULL
);
"""

_local = threading.local()


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect(path):
    """One connection per thread; SQLite handles the locking between them."""
    conns = getattr(_local, "conns", None)
    if conns is None:
        conns = _local.conns = {}
    key = str(path)
    if key not in conns:
        con = sqlite3.connect(key, timeout=30)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA foreign_keys=ON")
        conns[key] = con
    return conns[key]


def finish(path, failed=False):
    """End whatever this thread's connection left open. A write that failed part-way (a name already taken,
    say) still holds SQLite's write lock, and every other thread would wait on it until this one wrote again."""
    con = (getattr(_local, "conns", None) or {}).get(str(path))
    if con is not None and con.in_transaction:
        try:
            if failed:
                con.rollback()
            else:
                con.commit()
        except sqlite3.Error:
            con.rollback()


# Columns added after a table was first made: (table, column, definition). Added to older databases on start.
ADDED_COLUMNS = [
    ("watch", "auto", "INTEGER NOT NULL DEFAULT 0"),   # request a coming film or show by itself
    ("watch", "found", "TEXT NOT NULL DEFAULT ''"),    # what the last check found (JSON)
    ("watch", "seen", "TEXT"),                         # keys of found items the person has seen (JSON)
    ("watch", "checked", "TEXT NOT NULL DEFAULT ''"),
    ("watch", "error", "TEXT NOT NULL DEFAULT ''"),
]


def init(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    con = connect(path)
    con.executescript(SCHEMA)
    for table, column, definition in ADDED_COLUMNS:
        if column not in [r[1] for r in con.execute("PRAGMA table_info(%s)" % table)]:
            con.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, column, definition))
    con.commit()
    return con


def kv_get(con, key, default=None):
    row = con.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def kv_set(con, key, value):
    con.execute(
        "INSERT INTO kv(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    con.commit()


def slug(text):
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def clean_isbn(value):
    digits = re.sub(r"[^0-9Xx]", "", value or "")
    return digits.upper() if len(digits) in (10, 13) else ""


def make_key(title, author, isbn=None):
    """Stable identifier for a book, independent of the Calibre book number."""
    isbn = clean_isbn(isbn)
    if isbn:
        return "isbn:" + isbn
    first_author = re.split(r"\s*(?:&|;|,| and )\s*", author or "")[0]
    return "ta:%s|%s" % (slug(title), slug(first_author))


def clean_tag(tag):
    tag = re.sub(r"\s+", " ", (tag or "")).strip()
    return tag[:60]
