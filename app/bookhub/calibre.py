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
from pathlib import Path

from . import catalogues, db, tags

EBOOK_EXTENSIONS = {".epub", ".pdf", ".mobi", ".azw3", ".azw", ".kepub", ".fb2", ".cbz", ".cbr"}
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
    return (not files, not book["has_cover"], "EPUB" not in files, -len(files), -max(book["sizes"].values(), default=0), book["id"])


def unmangle(text):
    """Undo UTF-8 read as Windows-1252, for showing: "The Birdâ€™s Nest" is "The Bird’s Nest"."""
    if not text or not re.search("[âÃ][\u0080-™]", text):
        return text
    try:
        return text.encode("cp1252").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text  # a real "â" or "Ã", or only partly mangled


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
        m = pattern.match(title or "")
        if not m:
            continue
        if word == "Volume" and m["v"] == "V" and not comic and not m["n"].startswith("0"):
            continue  # "Vampire The Masquerade V5" is the fifth edition; volumes are "v01" or "Vol. 5"
        # A leading "[Group]" is a release detail; brackets before the number belong to the name: "(Marvel)", "(2016)".
        name = LEADING_BRACKETS.sub("", m["name"]).strip(" -_.,:")
        if len(name) < 2 or name.count("(") != name.count(")") or name.count("[") != name.count("]"):
            continue  # "Star Trek (Joe 90 - Top Secret)": the 90 is inside the brackets, not an issue number
        n = float(m["n"]) if m["n"] else 0.0
        if word:
            label, index = "%s %g" % (word, n), n
        else:
            extra = m["label"].lower()
            label = m["label"].title() + (" %g" % n if m["n"] else "")
            index = 10000 * (EXTRAS.index(extra) + 1) + n
        subtitle = EDGE_BRACKETS.sub("", m["rest"] or "").strip(" -_.,:")
        return {"series": name, "index": index, "label": label, "subtitle": subtitle}
    return None



COMIC_FORMATS = {"CBZ", "CBR", "CB7", "CBC"}


def collection_of(name):
    """The franchise a comic run belongs to: the name up to its first bracket or " - ".
    "Star Trek - Deep Space Nine (Malibu)" and "Star Trek (Gold Key)" are both "Star Trek"."""
    cut = re.split(r"\s+\(|\s+-\s+|\s+\[", name or "", maxsplit=1)[0].strip(" -_.,:")
    return cut if len(cut) >= 3 else (name or "")


def _same_title_runs(books):
    """Comics that share a title but no number ("Star Trek (Book & Record Set)" three times) are parts of one run."""
    same = {}
    for b in books:
        if not b["series"] and set(b["own_formats"]) & COMIC_FORMATS:
            same.setdefault(b["title"].lower(), []).append(b)
    for parts in same.values():
        if len(parts) < 2:
            continue
        for n, b in enumerate(sorted(parts, key=lambda x: x["id"]), 1):
            b["series"], b["series_index"], b["part"] = b["title"], n, "Part %d" % n
            b["shown_title"] = "%s · Part %d" % (b["title"], n)


# File names cannot hold every apostrophe, so "Child's Play" often arrives as "Child s Play".
SPLIT_APOSTROPHE = re.compile(r"(?<=[A-Za-z]) (s|ve|ll|re|t)(?=$|[\s,.;:!?)])")


def _issue_titles_as_authors(books):
    """Comic files named "Series 05 - Story Title" come out of the library manager with the story as the author.
    When nearly every issue of a series has a different "author", those are the issues' titles: show them so."""
    series = {}
    for b in books:
        if b["part"] and set(b["own_formats"]) & COMIC_FORMATS:
            series.setdefault(b["series"].lower(), []).append(b)
    for parts in series.values():
        authors = [b["author"] for b in parts if b["author"]]
        if len(parts) < 2 or len(set(authors)) < 0.6 * len(parts):
            continue
        for b in parts:
            if b["author"] and b["author"].lower() != "unknown":
                story = SPLIT_APOSTROPHE.sub(r"'\1", b["author"])
                b["subtitle"] = "%s · %s" % (b["subtitle"], story) if b["subtitle"] else story
            b["shown_author"] = ""

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
            for r in con.execute("SELECT id, title, path, has_cover, timestamp, pubdate, series_index FROM books"):
                books[r["id"]] = {
                    "id": r["id"], "title": r["title"], "path": r["path"], "has_cover": bool(r["has_cover"]),
                    "added": (r["timestamp"] or "")[:19].replace(" ", "T"), "pubdate": (r["pubdate"] or "")[:10],
                    "series_index": r["series_index"], "authors": [], "tags": [], "formats": {},
                    "synopsis": "", "series": "", "isbn": "", "publisher": "", "raw_tags": [],
                    "own_formats": {}, "sizes": {}, "copies": [], "copy_of": None,
                }

            def link(sql):
                for r in con.execute(sql):
                    if r[0] in books:
                        yield books[r[0]], r

            for b, r in link("SELECT l.book, a.name FROM books_authors_link l JOIN authors a ON a.id=l.author ORDER BY l.id"):
                b["authors"].append(r[1].replace("|", ","))
            for b, r in link("SELECT l.book, t.name FROM books_tags_link l JOIN tags t ON t.id=l.tag ORDER BY t.name"):
                b["raw_tags"].append(r[1])
            try:
                for b, r in link("SELECT l.book, p.name FROM books_publishers_link l JOIN publishers p ON p.id=l.publisher"):
                    b["publisher"] = r[1]
            except sqlite3.OperationalError:
                pass  # a cut-down library without publishers
            for b, r in link("SELECT l.book, s.name FROM books_series_link l JOIN series s ON s.id=l.series"):
                b["series"] = r[1]
            for b, r in link("SELECT book, text FROM comments"):
                b["synopsis"] = strip_html(r[1])
            for b, r in link("SELECT book, format, name, uncompressed_size FROM data"):
                b["own_formats"][r[1].upper()] = "%s/%s.%s" % (b["path"], r[2], r[1].lower())
                b["sizes"][r[1].upper()] = r[3] or 0
            for b, r in link("SELECT book, type, val FROM identifiers"):
                if r[1].lower() == "isbn":
                    b["isbn"] = db.clean_isbn(r[2])
        finally:
            con.close()

        by_key = {}
        for b in books.values():
            b["author"] = " & ".join(b["authors"])
            # A volume whose file name says so ("Cherry Magic! v01 (2020) (Digital)") but has no series set
            # is shown as part of that series. Only how Family Hub shows it: the library is not changed.
            b["shown_title"], b["shown_author"], b["part"], b["subtitle"] = unmangle(b["title"]), unmangle(b["author"]), "", ""
            if not b["series"]:
                part = volume_from_title(b["shown_title"], comic=bool(set(b["own_formats"]) & COMIC_FORMATS))
                if part:
                    b["series"], b["series_index"] = part["series"], part["index"]
                    b["part"], b["subtitle"] = part["label"], part["subtitle"]
                    b["shown_title"] = "%s · %s" % (part["series"], part["label"])
            # What the file says about itself, minus ISBNs, publishers and other clutter.
            b["tags"] = tags.tidy(b["raw_tags"], skip=[b["title"], b["author"], b["publisher"]] + b["authors"])
            b["key"] = db.make_key(b["title"], b["author"], b["isbn"])
        _same_title_runs(books.values())
        _issue_titles_as_authors(books.values())
        for b in books.values():
            # Comics are gathered by franchise: every Star Trek run on one card, each run its own part of the page.
            comic = bool(set(b["own_formats"]) & COMIC_FORMATS)
            b["collection"] = collection_of(b["series"] or b["title"]) if comic else b["series"]
        self._by_id = books
        self._group()

    def _group(self):
        """Work out which entries are shown, and which are copies of a shown one."""
        groups = {}
        for b in self._by_id.values():
            b["formats"], b["copies"], b["copy_of"] = dict(b["own_formats"]), [], None
            groups.setdefault(b["key"], []).append(b)
        shown, by_key, sets = [], {}, []
        for key, members in groups.items():
            members.sort(key=_best_first)
            choice = self._choices.get(key)
            if len(members) == 1 or choice == TOGETHER or (choice != APART and all(real_name(m) for m in members)):
                clusters = [members]
            elif choice == APART:
                clusters = [[m] for m in members]
            else:
                # The name proves nothing, so only entries holding the very same files go together.
                same = {}
                for m in members:
                    same.setdefault(tuple(sorted(m["sizes"].items())) or m["id"], []).append(m)
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
                sets.append({"key": key, "choice": choice, "shown": [c[0] for c in clusters], "books": members})
        shown.sort(key=lambda b: b["title"].lower())
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
                    # The library manager may be mid-write; keep the last good copy.
                    pass

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
        with zipfile.ZipFile(path) as z:
            return sum(1 for n in z.namelist() if n.lower().endswith((".jpg", ".jpeg", ".png", ".webp", ".gif"))) or None
    except (zipfile.BadZipFile, OSError):
        return None


def estimate_pages(path):
    """Rough page count for an EPUB: about 1,800 characters of text per page."""
    try:
        total = 0
        with zipfile.ZipFile(path) as z:
            for info in z.infolist():
                if info.filename.lower().endswith((".xhtml", ".html", ".htm")):
                    total += len(strip_html(z.read(info).decode("utf-8", "ignore")))
        return max(1, round(total / 1800)) if total else None
    except (zipfile.BadZipFile, OSError, KeyError):
        return None
