"""Read-only access to a Calibre library (the folder holding metadata.db).

Family Hub never writes to the library. Calibre-Web Automated (or Calibre
itself) stays in charge of adding, converting and editing the books.

The library often holds the same book more than once, for example an EPUB and
the EPUB the library manager made from its MOBI twin. Those entries are shown
as one book with the others listed as its copies. The library itself is left
as it is.
"""
import html
import re
import sqlite3
import threading
import zipfile
import zlib
from pathlib import Path

from . import catalogues, db, kinds, tags

FILE_NAME_RE = re.compile(r"_|\.(pdf|epub|mobi|azw3?|docx?|cb[zr]|txt)$", re.I)
TOGETHER, APART = "together", "apart"


def real_name(book):
    """Do the title and author look like a book's, not a file name and a user name?

    Entries that share a real name are the same book. Entries that share
    "chapter_01" by "admin" may be anything.
    """
    title = book["title"].strip()
    if FILE_NAME_RE.search(title) or re.fullmatch(r"[\d\- ]{9,}", title):  # a file name or a bare ISBN
        return False
    return book["key"].startswith("isbn:") or catalogues.author_known(book["author"])


def _best_first(book):
    files = book["own_formats"]
    # sort key trick: False sorts before True, so "has files", "has a cover", "has an EPUB" all float to the top,
    # then more formats, then the bigger file, then the oldest id as the tie-breaker
    return (not files, not book["has_cover"], "EPUB" not in files, -len(files), -max(book["sizes"].values(), default=0), book["id"])


def unmangle(text):
    """Undo UTF-8 read as Windows-1252, for showing: "The Birdâ€™s Nest" is "The Bird’s Nest"."""
    # the classic mojibake: UTF-8 bytes read as Windows-1252. encode them back to bytes the wrong way, decode the right way
    if not text or not re.search("[âÃ][\u0080-™]", text):
        return text
    try:
        return text.encode("cp1252").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text  # a real "â" or "Ã" (or only half-mangled). leave it alone


def strip_html(text):
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", "", text or "")
    text = re.sub(r"(?i)<\s*(br|/p|/div|/li|/h[1-6])\s*/?>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


# A volume number in a file's own name: "Title v01 (2020) (Digital) (group)", "Title Vol. 3", "Title Volume 12".
NUMBER = r"(?P<n>\d{1,4}(?:\.\d+)?)"
REST = r"(?P<rest>.*)$"
# The ways a part of a series names itself, tried in order. "Issue" (a bare number) only for comics.
PART_NAMES = [
    ("Volume", re.compile(r"^(?P<name>.*?\S)[\s._,:-]*\b(?:(?P<v>v)(?=\d)|vol\.?[\s._]*|volume[\s._]*)" + NUMBER + r"(?![\w.])" + REST, re.I)),
    ("Chapter", re.compile(r"^(?P<name>.*?\S)[\s._,:-]*\b(?:ch\.?[\s._]*|chapter[\s._]*)" + NUMBER + r"(?![\w.])" + REST, re.I)),
    (None, re.compile(r"^(?P<name>.+?)\s+-\s+(?P<label>ultimate annual|annual|special|celebrity series|one[- ]shot|giant[- ]size)"
                      r"(?:\s*#?\s*(?P<n>\d{1,3}))?" + REST, re.I)),
    ("Issue", re.compile(r"^(?P<name>.+?)\s+#?(?P<n>\d{1,3})(?=\s*(?:$|-\s|\())" + REST)),
]
# Extras such as annuals come after the numbered parts, in this order.
EXTRAS = ["annual", "ultimate annual", "special", "celebrity series", "one-shot", "one shot", "giant-size", "giant size"]
LEADING_BRACKETS = re.compile(r"^(?:\s*[\(\[][^\)\]]*[\)\]])+")
EDGE_BRACKETS = re.compile(r"^(?:\s*[\(\[][^\)\]]*[\)\]])+|(?:\s*[\(\[][^\)\]]*[\)\]])+$")


def volume_from_title(title, comic=False):
    """What part of a series a title names, or None:
    {"series", "index" (for ordering), "label" ("Volume 3", "Issue 12", "Annual 1"), "subtitle"}."""
    for word, pattern in PART_NAMES:
        if word == "Issue" and not comic:
            continue  # "Fahrenheit 451" is not issue 451
        match = pattern.match(title or "")
        if not match:
            continue
        if word == "Volume" and match["v"] == "V" and not comic and not match["n"].startswith("0"):
            continue  # "Vampire The Masquerade V5" is the fifth EDITION, not volume 5. volumes look like "v01" or "Vol. 5"
        if word == "Volume" and match["v"] and not comic and "." in match["n"] and not match["n"].startswith("0"):
            continue  # "Character Sheet v1.1" is a document's version. a manga's "v10.5" is a comic, so that's still a volume
        # A leading "[Group]" is a release detail; brackets before the number belong to the name: "(Marvel)", "(2016)".
        name = LEADING_BRACKETS.sub("", match["name"]).strip(" -_.,:")
        if len(name) < 2 or name.count("(") != name.count(")") or name.count("[") != name.count("]"):
            continue  # "Star Trek (Joe 90 - Top Secret)": the 90 is inside the brackets, not an issue number
        number = float(match["n"]) if match["n"] else 0.0
        if word:
            label, index = "%s %g" % (word, number), number
        else:
            extra = match["label"].lower()
            label = match["label"].title() + (" %g" % number if match["n"] else "")
            # annuals and specials go after every numbered issue: 10000 is just "way past the end"
            index = 10000 * (EXTRAS.index(extra) + 1) + number
        subtitle = EDGE_BRACKETS.sub("", match["rest"] or "").strip(" -_.,:")
        return {"series": name, "index": index, "label": label, "subtitle": subtitle}
    return None



COMIC_FORMATS = kinds.COMIC_FORMATS


def collection_of(name):
    """The franchise a comic run belongs to: the name up to its first bracket or " - ".
    "Star Trek - Deep Space Nine (Malibu)" and "Star Trek (Gold Key)" are both "Star Trek"."""
    cut = re.split(r"\s+\(|\s+-\s+|\s+\[", name or "", maxsplit=1)[0].strip(" -_.,:")
    return cut if len(cut) >= 3 else (name or "")


def _same_title_runs(books):
    """Comics that share a title but no number ("Star Trek (Book & Record Set)" three times) are parts of one run."""
    same = {}
    for book in books:
        if not book["series"] and set(book["own_formats"]) & COMIC_FORMATS:
            same.setdefault(book["title"].lower(), []).append(book)
    for parts in same.values():
        if len(parts) < 2:
            continue
        for part_no, book in enumerate(sorted(parts, key=lambda book: book["id"]), 1):
            book["series"], book["series_index"], book["part"] = book["title"], part_no, "Part %d" % part_no
            book["shown_title"] = "%s · Part %d" % (book["title"], part_no)


# File names cannot hold every apostrophe, so "Child's Play" often arrives as "Child s Play".
SPLIT_APOSTROPHE = re.compile(r"(?<=[A-Za-z]) (s|ve|ll|re|t)(?=$|[\s,.;:!?)])")


def _issue_titles_as_authors(books):
    """Comic files named "Series 05 - Story Title" come out of the library manager with the story as the author.
    When nearly every issue of a series has a different "author", those are the issues' titles: show them so."""
    series = {}
    for book in books:
        if book["part"] and set(book["own_formats"]) & COMIC_FORMATS:
            series.setdefault(book["series"].lower(), []).append(book)
    for parts in series.values():
        authors = [book["author"] for book in parts if book["author"]]
        # 60%+ different "authors" in one series? those aren't authors, they're episode titles in a trench coat
        if len(parts) < 2 or len(set(authors)) < 0.6 * len(parts):
            continue
        for book in parts:
            if book["author"] and book["author"].lower() != "unknown":
                story = SPLIT_APOSTROPHE.sub(r"'\1", book["author"])
                book["subtitle"] = "%s · %s" % (book["subtitle"], story) if book["subtitle"] else story
            book["shown_author"] = ""


class Library:
    def __init__(self, library_dir):
        self.dir = Path(library_dir)
        self.metadata_db = self.dir / "metadata.db"
        self._lock = threading.Lock()
        self._mtime = None
        self._books = []
        self._by_id = {}
        self._by_key = {}
        self._sets = []
        self._choices = {}  # {book key: "together" or "apart"}, an admin's say on entries that share a name

    @property
    def available(self):
        return self.metadata_db.exists()

    def _load(self):
        con = sqlite3.connect("file:%s?mode=ro" % self.metadata_db.as_posix(), uri=True, timeout=30)
        con.row_factory = sqlite3.Row
        try:
            books = {}
            for row in con.execute("SELECT id, title, path, has_cover, timestamp, pubdate, series_index FROM books"):
                books[row["id"]] = {
                    "id": row["id"], "title": row["title"], "path": row["path"], "has_cover": bool(row["has_cover"]),
                    "added": (row["timestamp"] or "")[:19].replace(" ", "T"), "pubdate": (row["pubdate"] or "")[:10],
                    "series_index": row["series_index"], "authors": [], "tags": [], "formats": {},
                    "synopsis": "", "series": "", "own_series": "", "isbn": "", "publisher": "", "language": "", "raw_tags": [],
                    "own_formats": {}, "sizes": {}, "copies": [], "copy_of": None,
                }

            def link(sql):
                """Each row of a link table with the book it belongs to (column 0 is the book id)."""
                for row in con.execute(sql):
                    if row[0] in books:
                        yield books[row[0]], row

            for book, row in link("SELECT l.book, a.name FROM books_authors_link l JOIN authors a ON a.id=l.author ORDER BY l.id"):
                book["authors"].append(row[1].replace("|", ","))  # Calibre stores commas in names as "|", don't ask
            for book, row in link("SELECT l.book, t.name FROM books_tags_link l JOIN tags t ON t.id=l.tag ORDER BY t.name"):
                book["raw_tags"].append(row[1])
            try:
                for book, row in link("SELECT l.book, p.name FROM books_publishers_link l JOIN publishers p ON p.id=l.publisher"):
                    book["publisher"] = row[1]
            except sqlite3.OperationalError:
                pass  # a cut-down library without publishers
            try:  # the first language only ("rus"), for the little badge on cards of books not in English
                for book, row in link("SELECT l.book, g.lang_code FROM books_languages_link l JOIN languages g ON g.id=l.lang_code "
                                      "ORDER BY l.item_order DESC"):
                    book["language"] = row[1]
            except sqlite3.OperationalError:
                pass  # a cut-down library without languages
            for book, row in link("SELECT l.book, s.name FROM books_series_link l JOIN series s ON s.id=l.series"):
                book["series"] = book["own_series"] = row[1]  # own_series: what the library says, before any guess from the name
            for book, row in link("SELECT book, text FROM comments"):
                book["synopsis"] = strip_html(row[1])
            for book, row in link("SELECT book, format, name, uncompressed_size FROM data"):
                fmt, file_name, size = row[1], row[2], row[3]
                book["own_formats"][fmt.upper()] = "%s/%s.%s" % (book["path"], file_name, fmt.lower())
                book["sizes"][fmt.upper()] = size or 0
            for book, row in link("SELECT book, type, val FROM identifiers"):
                if row[1].lower() == "isbn":
                    book["isbn"] = db.clean_isbn(row[2])
        finally:
            con.close()

        for book in books.values():
            book["author"] = " & ".join(book["authors"])
            # A volume whose file name says so ("Cherry Magic! v01 (2020) (Digital)") but has no series set
            # is shown as part of that series. Only how Family Hub shows it: the library is not changed.
            book["shown_title"], book["shown_author"], book["part"], book["subtitle"] = unmangle(book["title"]), unmangle(book["author"]), "", ""
            if not book["series"]:
                part = volume_from_title(book["shown_title"], comic=bool(set(book["own_formats"]) & COMIC_FORMATS))
                if part:
                    book["series"], book["series_index"] = part["series"], part["index"]
                    book["part"], book["subtitle"] = part["label"], part["subtitle"]
                    book["shown_title"] = "%s · %s" % (part["series"], part["label"])
            # What the file says about itself, minus ISBNs, publishers and other clutter.
            book["tags"] = tags.tidy(book["raw_tags"], skip=[book["title"], book["author"], book["publisher"]] + book["authors"])
            book["key"] = db.make_key(book["title"], book["author"], book["isbn"])
        _same_title_runs(books.values())
        _issue_titles_as_authors(books.values())
        for book in books.values():
            # Comics are gathered by franchise: every Star Trek run on one card, each run its own part of the page.
            comic = bool(set(book["own_formats"]) & COMIC_FORMATS)
            book["collection"] = collection_of(book["series"] or book["title"]) if comic else book["series"]
        self._by_id = books
        self._group()

    def _group(self):
        """Work out which entries are shown, and which are copies of a shown one."""
        groups = {}
        for book in self._by_id.values():
            book["formats"], book["copies"], book["copy_of"] = dict(book["own_formats"]), [], None
            groups.setdefault(book["key"], []).append(book)
        shown, by_key, sets = [], {}, []
        for key, members in groups.items():
            members.sort(key=_best_first)
            choice = self._choices.get(key)
            if len(members) == 1 or choice == TOGETHER or (choice != APART and all(real_name(member) for member in members)):
                clusters = [members]
            elif choice == APART:
                clusters = [[member] for member in members]
            else:
                # "chapter_01 by admin" proves nothing, so only entries with byte-for-byte the same sizes go together.
                # (no files at all? then the id is the key, so each stands alone)
                same = {}
                for member in members:
                    same.setdefault(tuple(sorted(member["sizes"].items())) or member["id"], []).append(member)
                clusters = list(same.values())
            for cluster in clusters:
                first = cluster[0]
                for other in cluster[1:]:
                    other["copy_of"] = first["id"]
                    first["copies"].append(other)
                    for fmt, rel in other["own_formats"].items():
                        first["formats"].setdefault(fmt, rel)  # a format only the copy has
                shown.append(first)
            by_key[key] = clusters[0][0]
            if len(members) > 1:
                sets.append({"key": key, "choice": choice, "shown": [cluster[0] for cluster in clusters], "books": members})
        shown.sort(key=lambda book: book["title"].lower())
        self._books, self._by_key, self._sets = shown, by_key, sets

    def set_choices(self, choices):
        """Take an admin's decisions on which same-named entries are one book."""
        with self._lock:
            self._choices = dict(choices)
            if self._by_id:
                self._group()

    def refresh(self, force=False):
        """Reload when metadata.db has changed since the last look, or always with ``force``."""
        if not self.available:
            self._books, self._by_id, self._by_key, self._sets, self._mtime = [], {}, {}, [], None
            return
        mtime = self.metadata_db.stat().st_mtime
        with self._lock:
            if force or mtime != self._mtime:
                try:
                    self._load()
                    self._mtime = mtime
                except sqlite3.Error:
                    pass  # the library manager's probably mid-write. keep showing the last good copy

    def books(self):
        """One entry per book. Copies of a book are under its "copies"."""
        self.refresh()
        return self._books

    def all_books(self):
        """Every library entry, copies included."""
        self.refresh()
        return list(self._by_id.values())

    def sets(self):
        """Entries that share a title and author (or ISBN), shown as one book or not."""
        self.refresh()
        return self._sets

    def get(self, book_id):
        self.refresh()
        return self._by_id.get(book_id)

    def by_key(self, key):
        self.refresh()
        return self._by_key.get(key)

    def keys(self):
        self.refresh()
        return self._by_key

    def file_path(self, book, fmt):
        rel = book["formats"].get(fmt.upper())
        if not rel:
            return None
        path = (self.dir / rel).resolve()
        if self.dir.resolve() not in path.parents or not path.is_file():
            return None
        return path

    def cover_path(self, book):
        path = (self.dir / book["path"] / "cover.jpg").resolve()
        if self.dir.resolve() not in path.parents or not path.is_file():
            return None
        return path


def count_pictures(path):
    """Pages in a comic kept as a zip (CBZ): one picture each."""
    try:
        with zipfile.ZipFile(path) as cbz:
            return sum(1 for name in cbz.namelist() if name.lower().endswith((".jpg", ".jpeg", ".png", ".webp", ".gif"))) or None
    except (zipfile.BadZipFile, OSError):
        return None


def estimate_pages(path):
    """Rough page count for an EPUB: about 1,800 characters of text per page."""
    try:
        total = 0
        with zipfile.ZipFile(path) as epub:
            for info in epub.infolist():
                if info.filename.lower().endswith((".xhtml", ".html", ".htm")):
                    total += len(strip_html(epub.read(info).decode("utf-8", "ignore")))
        return max(1, round(total / 1800)) if total else None
    except (zipfile.BadZipFile, OSError, KeyError, EOFError, zlib.error, RuntimeError, NotImplementedError, ValueError):
        return None  # damaged, locked (RuntimeError) or oddly packed (NotImplementedError): no estimate, but no crash
