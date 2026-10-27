"""Demo mode: a small sample library so Family Hub can be tried without any setup.

The sample files are short placeholders named after public-domain classics.
They are real EPUB/PDF files, but they do not contain the books' text.
"""
import sqlite3
import zipfile
from datetime import date
from pathlib import Path

from werkzeug.security import generate_password_hash

from . import db, kosync

BOOKS = [
    # title, author, tags, format, size (paragraph repeats), month added
    ("Dracula", "Bram Stoker", ["Gothic", "Horror", "Classic"], "EPUB", 520, 1),
    ("Pride and Prejudice", "Jane Austen", ["Romance", "Classic"], "EPUB", 430, 1),
    ("Emma", "Jane Austen", ["Romance", "Classic", "Comedy"], "EPUB", 500, 2),
    ("Persuasion", "Jane Austen", ["Romance", "Classic"], "EPUB", 280, 2),
    ("Moby-Dick", "Herman Melville", ["Adventure", "Classic"], "EPUB", 700, 3),
    ("Wuthering Heights", "Emily Bronte", ["Gothic", "Romance", "Classic"], "EPUB", 410, 3),
    ("Little Women", "Louisa May Alcott", ["Classic", "Family"], "EPUB", 560, 4),
    ("Treasure Island", "Robert Louis Stevenson", ["Adventure", "Classic"], "PDF", 240, 5),
    ("The Time Machine", "H. G. Wells", ["Sci-fi", "Classic"], "EPUB", 130, 6),
    ("Northanger Abbey", "Jane Austen", ["Romance", "Gothic", "Classic"], "EPUB", 270, 7),
    ("The Secret Garden", "Frances Hodgson Burnett", ["Classic", "Family"], "EPUB", 300, 8),
    ("A Christmas Carol", "Charles Dickens", ["Classic", "Ghost story"], "EPUB", 110, 9),
]

SYNOPSIS = "Sample entry created by Family Hub's demo mode. Replace the demo library with your own to see real synopses."

SCHEMA = """
CREATE TABLE books (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL DEFAULT 'Unknown', sort TEXT,
    timestamp TIMESTAMP, pubdate TIMESTAMP, series_index REAL NOT NULL DEFAULT 1.0, author_sort TEXT,
    path TEXT NOT NULL DEFAULT '', has_cover BOOL DEFAULT 0, last_modified TIMESTAMP);
CREATE TABLE authors (id INTEGER PRIMARY KEY, name TEXT NOT NULL, sort TEXT, link TEXT NOT NULL DEFAULT '');
CREATE TABLE books_authors_link (id INTEGER PRIMARY KEY, book INTEGER NOT NULL, author INTEGER NOT NULL);
CREATE TABLE tags (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE books_tags_link (id INTEGER PRIMARY KEY, book INTEGER NOT NULL, tag INTEGER NOT NULL);
CREATE TABLE series (id INTEGER PRIMARY KEY, name TEXT NOT NULL, sort TEXT);
CREATE TABLE books_series_link (id INTEGER PRIMARY KEY, book INTEGER NOT NULL, series INTEGER NOT NULL);
CREATE TABLE comments (id INTEGER PRIMARY KEY, book INTEGER NOT NULL, text TEXT NOT NULL);
CREATE TABLE data (id INTEGER PRIMARY KEY, book INTEGER NOT NULL, format TEXT NOT NULL,
    uncompressed_size INTEGER NOT NULL, name TEXT NOT NULL);
CREATE TABLE identifiers (id INTEGER PRIMARY KEY, book INTEGER NOT NULL, type TEXT NOT NULL DEFAULT 'isbn', val TEXT NOT NULL);
"""


def _epub(path, title, author, repeats):
    para = ("<p>This is a placeholder page from the Family Hub demo library for <i>%s</i> by %s. "
            "It stands in for the real text so that downloading, syncing and page counts can be tried out.</p>\n"
            % (title, author))
    chapter = ("<?xml version='1.0' encoding='utf-8'?>\n<html xmlns='http://www.w3.org/1999/xhtml'><head><title>%s</title></head>"
               "<body><h1>%s</h1>\n%s</body></html>" % (title, title, para * repeats))
    opf = ("<?xml version='1.0' encoding='utf-8'?>\n<package xmlns='http://www.idpf.org/2007/opf' version='3.0' unique-identifier='id'>"
           "<metadata xmlns:dc='http://purl.org/dc/elements/1.1/'><dc:identifier id='id'>bookhub-demo-%s</dc:identifier>"
           "<dc:title>%s</dc:title><dc:creator>%s</dc:creator><dc:language>en</dc:language>"
           "<meta property='dcterms:modified'>2026-01-01T00:00:00Z</meta></metadata>"
           "<manifest><item id='nav' href='nav.xhtml' media-type='application/xhtml+xml' properties='nav'/>"
           "<item id='c1' href='chapter1.xhtml' media-type='application/xhtml+xml'/></manifest>"
           "<spine><itemref idref='c1'/></spine></package>" % (db.slug(title), title, author))
    nav = ("<?xml version='1.0' encoding='utf-8'?>\n<html xmlns='http://www.w3.org/1999/xhtml' xmlns:epub='http://www.idpf.org/2007/ops'>"
           "<head><title>Contents</title></head><body><nav epub:type='toc'><ol><li><a href='chapter1.xhtml'>%s</a></li></ol></nav></body></html>" % title)
    container = ("<?xml version='1.0'?>\n<container version='1.0' xmlns='urn:oasis:names:tc:opendocument:xmlns:container'>"
                 "<rootfiles><rootfile full-path='content.opf' media-type='application/oebps-package+xml'/></rootfiles></container>")
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml", container, compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("content.opf", opf, compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("nav.xhtml", nav, compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("chapter1.xhtml", chapter, compress_type=zipfile.ZIP_DEFLATED)


def _pdf(path, title):
    text = "Family Hub demo placeholder for %s" % title
    stream = "BT /F1 18 Tf 72 720 Td (%s) Tj ET" % text
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        "<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream),
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = "%PDF-1.4\n"
    offsets = []
    for i, obj in enumerate(objects, 1):
        offsets.append(len(out))
        out += "%d 0 obj\n%s\nendobj\n" % (i, obj)
    xref = len(out)
    out += "xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += "".join("%010d 00000 n \n" % offset for offset in offsets)
    out += "trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    Path(path).write_bytes(out.encode("latin-1"))


def build(demo_dir):
    """Create the sample library (once) and return the folders to use."""
    root = Path(demo_dir).resolve()
    library, data = root / "library", root / "data"
    data.mkdir(parents=True, exist_ok=True)
    if (library / "metadata.db").exists():
        return {"library": library, "data": data}
    library.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(library / "metadata.db")
    con.executescript(SCHEMA)
    year = date.today().year
    last_month = date.today().month
    authors, tags = {}, {}
    for number, (title, author, book_tags, fmt, size, month) in enumerate(BOOKS, 1):
        month = min(month, last_month)
        folder = "%s/%s (%d)" % (author, title, number)
        (library / folder).mkdir(parents=True, exist_ok=True)
        name = "%s - %s" % (title, author)
        file = library / folder / ("%s.%s" % (name, fmt.lower()))
        if fmt == "EPUB":
            _epub(file, title, author, size)
        else:
            _pdf(file, title)
        stamp = "%04d-%02d-%02d 10:00:00+00:00" % (year, month, min(28, number * 2))
        con.execute("INSERT INTO books(id, title, sort, timestamp, pubdate, author_sort, path, has_cover, last_modified) "
                    "VALUES(?,?,?,?,?,?,?,0,?)", (number, title, title, stamp, "1900-01-01 00:00:00+00:00", author, folder, stamp))
        if author not in authors:
            authors[author] = con.execute("INSERT INTO authors(name, sort) VALUES(?,?)", (author, author)).lastrowid
        con.execute("INSERT INTO books_authors_link(book, author) VALUES(?,?)", (number, authors[author]))
        for tag in book_tags:
            if tag not in tags:
                tags[tag] = con.execute("INSERT INTO tags(name) VALUES(?)", (tag,)).lastrowid
            con.execute("INSERT INTO books_tags_link(book, tag) VALUES(?,?)", (number, tags[tag]))
        con.execute("INSERT INTO comments(book, text) VALUES(?,?)", (number, "<p>%s</p>" % SYNOPSIS))
        con.execute("INSERT INTO data(book, format, uncompressed_size, name) VALUES(?,?,?,?)",
                    (number, fmt, file.stat().st_size, name))
    con.commit()
    con.close()
    return {"library": library, "data": data}


def add_copy(library, title, size=40):
    """Add a second library entry for a book, as the library manager does when
    it is given the same book in two formats. Returns the new entry's id."""
    library = Path(library)
    con = sqlite3.connect(library / "metadata.db")
    try:
        book, stamp, pubdate, author, path = con.execute(
            "SELECT id, timestamp, pubdate, author_sort, path FROM books WHERE title=?", (title,)).fetchone()
        cur = con.execute("INSERT INTO books(title, sort, timestamp, pubdate, author_sort, path, has_cover, last_modified) "
                          "VALUES(?,?,?,?,?,'',0,?)", (title, title, stamp, pubdate, author, stamp))
        new = cur.lastrowid
        folder = "%s (%d)" % (path.rsplit(" (", 1)[0], new)
        con.execute("UPDATE books SET path=? WHERE id=?", (folder, new))
        for (author_id,) in con.execute("SELECT author FROM books_authors_link WHERE book=?", (book,)).fetchall():
            con.execute("INSERT INTO books_authors_link(book, author) VALUES(?,?)", (new, author_id))
        name = "%s - %s" % (title, author)
        (library / folder).mkdir(parents=True, exist_ok=True)
        file = library / folder / (name + ".epub")
        _epub(file, title, author, size)
        con.execute("INSERT INTO data(book, format, uncompressed_size, name) VALUES(?,'EPUB',?,?)", (new, file.stat().st_size, name))
        con.commit()
        return new
    finally:
        con.close()


def seed(hub):
    """Sample profiles and reading history. Only runs on an empty Family Hub."""
    if hub.profiles():
        return
    con = hub.con()
    for name, role, color, can_request in (("You", "admin", "#F0B04A", 1), ("Partner", "admin", "#7FB2E5", 1),
                                           ("Nan", "member", "#9BD1A8", 0)):
        con.execute(
            "INSERT INTO profiles(name, role, passcode_hash, color, can_request, can_tag, sync_key, created) VALUES(?,?,?,?,?,1,?,?)",
            (name, role, generate_password_hash("1234"), color, can_request, kosync.sync_key_for("demo"), db.now()),
        )
    con.commit()
    ids = {profile["name"]: profile["id"] for profile in hub.profiles()}
    hub.load_tagpacks()
    hub.fill_page_counts()
    kosync.index_library(hub)
    year, month = date.today().year, date.today().month

    def day(m, d):
        m = max(1, min(m, month))
        return "%04d-%02d-%02d" % (year, m, min(d, date.today().day if m == month else 28))

    def key(title):
        return next(book["key"] for book in hub.library.books() if book["title"] == title)

    you = ids["You"]
    for title, m, rating in (("Wuthering Heights", 2, 3), ("Little Women", 4, 4), ("Persuasion", 6, 5),
                             ("The Time Machine", 8, 4), ("A Christmas Carol", month, 4)):
        hub.save_reading(you, key(title), status="finished", finished=day(m, 14), started=day(m, 2))
        hub.set_rating(key(title), you, rating)
    hub.save_reading(you, key("Pride and Prejudice"), percent=62, source="device", device="iPad")
    hub.save_reading(you, key("Moby-Dick"), percent=18, source="device", device="Kobo")
    # Books read outside the library, the way Kindle Unlimited reads would arrive.
    for title, author, pages, m in (("Jane Eyre", "Charlotte Bronte", 532, 3), ("The Picture of Dorian Gray", "Oscar Wilde", 254, 5),
                                    ("Villette", "Charlotte Bronte", 573, 7), ("The Woman in White", "Wilkie Collins", 672, 9)):
        hub.save_reading(you, db.make_key(title, author), status="finished", finished=day(m, 20), started=day(m, 5),
                         source="hardcover", title=title, author=author, pages=pages)
    hub.save_reading(you, db.make_key("Frankenstein", "Mary Shelley"), status="reading", percent=0, source="hardcover",
                     title="Frankenstein", author="Mary Shelley", pages=280)

    hub.add_tag("family", key("Pride and Prejudice"), "Class difference", you)
    hub.add_tag("family", key("Little Women"), "Read together", you)
    hub.add_tag("family", key("Treasure Island"), "Read together", you)
    hub.add_tag("family", key("A Christmas Carol"), "Holiday read", you)
    hub.add_tag("mine", key("Pride and Prejudice"), "Comfort re-read", you)
    hub.add_tag("mine", key("Persuasion"), "Comfort re-read", you)
    hub.add_tag("mine", key("Emma"), "Book club", you)

    partner, nan = ids["Partner"], ids["Nan"]
    hub.save_reading(partner, key("Dracula"), status="finished", finished=day(5, 9))
    hub.set_rating(key("Dracula"), partner, 5)
    hub.set_rating(key("Pride and Prejudice"), partner, 5)
    hub.save_reading(partner, key("Treasure Island"), percent=40)
    for title, m in (("Emma", 3), ("The Secret Garden", 6), ("Northanger Abbey", month)):
        hub.save_reading(nan, key(title), status="finished", finished=day(m, 11))
    hub.save_reading(nan, key("Little Women"), percent=35, source="device", device="Fire tablet")
    con.execute("INSERT OR IGNORE INTO watch(profile_id, kind, value, created) VALUES(?,?,?,?)", (you, "tag", "Slow burn", db.now()))
    con.execute("INSERT OR IGNORE INTO watch(profile_id, kind, value, created) VALUES(?,?,?,?)", (you, "author", "Jane Austen", db.now()))
    con.commit()
    hub.backup_now()
