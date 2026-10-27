"""Books whose title or author is not really a title or author: finding out what they are, and putting it right.

A book arrives as "PNW0397.pdf" by "elenak" when the file carried no proper details and the library took its
file name and the user name of whoever made the PDF. Clues to what it really is, most trusted first:

  1. An ISBN: in the library entry, in the title or file name, among an EPUB's own details or on its copyright
     page, or printed on the first pages of a PDF.
  2. The title and author a PDF or EPUB gives itself (sometimes better than what the library took).
  3. The title, tidied of file endings, underscores and release tags.

Each clue is looked up in Open Library, and in Google Books when a key is saved. An admin picks the right
match, or types the title and author in. Like removing spare copies, the change is made by the library manager
(librarymanager.py), after the library's database is copied; Family Hub's own records of the book (reading,
tags, ratings, shelves, its own cover) move with it to its new name.
"""
import json
import logging
import re
import zipfile
from pathlib import Path

from . import calibre, catalogues, covers, db, kinds, librarymanager

OPEN_LIBRARY = "https://openlibrary.org/search.json"
OL_FIELDS = "key,title,author_name,first_publish_year,publisher,isbn,cover_i"
PDF_PAGES = 10          # pages of a PDF read for an ISBN: the copyright page is near the front
MAX_CANDIDATES = 8
FILE_LIKE = re.compile(r"\.(pdf|epub|mobi|azw3?|docx?|cb[zr]|txt|rtf|djvu)$|_|~|\d{5,}|^[A-Z]{2,6}\d{3,}", re.I)
ISBN_RE = re.compile(r"(?<![\dX])(97[89][\d\- ]{10,14}|\d[\d\- ]{8,11}[\dXx])(?![\d])")
# Family Hub's records that follow a book to its new name. Community tags and their look-ups are dropped
# instead, so the catalogues are asked again about the real title.
KEYED = ["book_meta", "family_tags", "personal_tags", "ratings", "reading", "shelf", "deliveries", "goodreads_books",
         "recommendations", "covers", "public_ratings", "copy_choices", "book_kind", "doc_hashes"]
ASK_AGAIN = ["community_tags", "tag_lookups"]
# The PDF reader warns about every odd font; only its errors matter here.
logging.getLogger("pypdf").setLevel(logging.ERROR)


class FixError(Exception):
    pass


def valid_isbn(text):
    """The ISBN in text with its check digit right, or ""."""
    digits = re.sub(r"[^0-9Xx]", "", text or "").upper()
    # ISBN check digit maths, straight from the spec: https://en.wikipedia.org/wiki/ISBN#Check_digits
    if len(digits) == 10 and digits[:9].isdigit():
        # ISBN-10: weights 10 down to 1, "X" is 10, total must divide by 11
        total = sum((10 - position) * (10 if digit == "X" else int(digit)) for position, digit in enumerate(digits))
        return digits if total % 11 == 0 else ""
    if len(digits) == 13 and digits.isdigit() and digits[:3] in ("978", "979"):
        # ISBN-13: weights alternate 1, 3, 1, 3... and the total must divide by 10
        total = sum(int(digit) * (1 if position % 2 == 0 else 3) for position, digit in enumerate(digits))
        return digits if total % 10 == 0 else ""
    return ""


def isbns_in(text):
    found = []
    for match in ISBN_RE.finditer(text or ""):
        isbn = valid_isbn(match.group(1))
        if isbn and isbn not in found:
            found.append(isbn)
    return found


def username_like(author):
    """One lower-case word, such as "elenak": the user name a PDF maker wrote, not an author."""
    words = (author or "").split()
    return len(words) == 1 and words[0] == words[0].lower() and words[0].isalpha()


def problems(book):
    """What is wrong with an entry's title and author, in words, or [] when they look real."""
    wrong = []
    title = book["title"].strip()
    if FILE_LIKE.search(title) or valid_isbn(title):
        wrong.append("The title looks like a file name")
    comic = set(book["own_formats"]) <= kinds.COMIC_FORMATS and bool(book["own_formats"])
    # comics basically never have an author in the file and get found by series anyway, so let them off
    if not comic and (not catalogues.author_known(book["author"]) or username_like(book["author"])):
        wrong.append("No real author" if not catalogues.author_known(book["author"]) else "The author looks like a user name")
    return wrong


def tidy_title(title):
    """A title as a person would type it into a search."""
    title = catalogues.query_title(calibre.unmangle(title))
    title = re.sub(r"~.*$", "", title)                       # "Story Circle~tqw~ darksiderg"
    title = re.sub(r"\b(retail|ebook|e-book|epub|pdf|digital|scan)\b", " ", title, flags=re.I)
    return re.sub(r"\s+", " ", title).strip(" -:.")


def _epub_clues(path):
    """(isbns, title, author) from an EPUB's own details and its first pages."""
    isbns, title, author = [], "", ""
    def first(pattern, text):
        match = re.search(pattern, text, re.I)
        return match.group(1).strip() if match else ""

    with zipfile.ZipFile(path) as epub:
        names = epub.namelist()
        opf = next((name for name in names if name.lower().endswith(".opf")), None)
        if opf:
            text = epub.read(opf)[:200000].decode("utf-8", "replace")
            for ident in re.findall(r"<dc:identifier[^>]*>([^<]+)</dc:identifier>", text, re.I):
                isbns += [isbn for isbn in isbns_in(ident) if isbn not in isbns]
            title = first(r"<dc:title[^>]*>([^<]+)</dc:title>", text)
            author = first(r"<dc:creator[^>]*>([^<]+)</dc:creator>", text)
        pages = [name for name in names if name.lower().endswith((".xhtml", ".html", ".htm"))]
        # the copyright page is where ISBNs hide, so pages named like one get read first
        front = [name for name in pages if re.search(r"copy|right|legal|imprint|title", name, re.I)] + pages[:6]
        for name in front[:10]:
            text = re.sub(r"<[^>]+>", " ", epub.read(name)[:100000].decode("utf-8", "replace"))
            if "isbn" in text.lower():
                isbns += [isbn for isbn in isbns_in(text) if isbn not in isbns]
    return isbns, title, author


def _pdf_clues(path):
    """(isbns, title, author) from a PDF's own details and the text of its first pages."""
    from pypdf import PdfReader
    reader = PdfReader(str(path))
    meta = reader.metadata or {}
    title, author = str(meta.get("/Title") or "").strip(), str(meta.get("/Author") or "").strip()
    isbns = []
    for page in reader.pages[:PDF_PAGES]:
        try:
            text = page.extract_text() or ""
        except Exception:  # a damaged page; the others may still say
            continue
        if "isbn" in text.lower():
            isbns += [isbn for isbn in isbns_in(text) if isbn not in isbns]
    return isbns, title, author


class BookFix:
    def __init__(self, hub):
        self.hub = hub

    def _ok(self):
        """Entries an admin said are right as they are."""
        return set(json.loads(db.kv_get(self.hub.con(), "bookfix:ok") or "[]"))

    def needing(self):
        """Every entry whose title or author does not look real, newest first."""
        ok = self._ok()
        needing = []
        for book in self.hub.library.all_books():
            found = problems(book)
            if found and str(book["id"]) not in ok:
                needing.append({"id": book["id"], "key": book["key"], "title": book["shown_title"], "author": book["shown_author"],
                                "problems": found, "formats": sorted(book["own_formats"]), "added": book["added"]})
        needing.sort(key=lambda entry: entry["added"], reverse=True)
        return needing

    def leave(self, book_id):
        """Say an entry is right as it is, so it is not shown again."""
        ok = self._ok()
        ok.add(str(book_id))
        db.kv_set(self.hub.con(), "bookfix:ok", json.dumps(sorted(ok)))

    def clues(self, book):
        """What the entry and its files say about it: {"isbns": [(isbn, where)], "titles": [(title, author, where)]}."""
        isbns, titles = [], []

        def isbn(value, where):
            if value and value not in [known for known, _ in isbns]:
                isbns.append((value, where))

        isbn(valid_isbn(book["isbn"]), "in the library entry")
        for found in isbns_in(book["title"]):
            isbn(found, "in the title")
        readers = {"EPUB": _epub_clues, "PDF": _pdf_clues}
        for fmt, rel in sorted(book["own_formats"].items(), key=lambda pair: pair[0] != "EPUB"):  # EPUB first
            for found in isbns_in(Path(rel).stem):
                isbn(found, "in the file name")
            path = Path(self.hub.config.library_dir) / rel
            if fmt not in readers or not path.is_file():
                continue
            try:
                found_isbns, own_title, own_author = readers[fmt](path)
            except Exception:  # a busted file tells us nothing, but the other clues still count
                continue
            for found in found_isbns:
                isbn(found, "inside the %s" % fmt)
            if own_title and not FILE_LIKE.search(own_title) and tidy_title(own_title).lower() != tidy_title(book["title"]).lower():
                real_author = own_author if catalogues.author_known(own_author) and not username_like(own_author) else ""
                titles.append((own_title, real_author, "the %s's own details" % fmt))
        guess = tidy_title(book["title"])
        if guess and not valid_isbn(guess):
            author = book["author"] if catalogues.author_known(book["author"]) and not username_like(book["author"]) else ""
            titles.append((guess, author, "the title, tidied"))
        return {"isbns": isbns[:4], "titles": titles[:3]}

    def candidates(self, book_id):
        """Books the entry may be, best first, each with the clue that found it."""
        book = self.hub.library.get(book_id)
        if not book:
            raise FixError("That book is not in the library any more.")
        clues = self.clues(book)
        found, seen = [], set()

        def add(doc, why):
            title, authors = (doc.get("title") or "").strip(), doc.get("author_name") or []
            if not title:
                return
            mark = (db.slug(title), db.slug(authors[0]) if authors else "")
            if mark in seen:
                return
            seen.add(mark)
            found.append({"title": title, "authors": authors[:4], "year": doc.get("first_publish_year"),
                        "publisher": (doc.get("publisher") or [""])[0], "isbn": next(iter(doc.get("isbn") or []), ""),
                        "cover": "https://covers.openlibrary.org/b/id/%s-M.jpg" % doc["cover_i"] if doc.get("cover_i") else "",
                        "synopsis": doc.get("description") or "", "why": why})

        errors = []
        for value, where in clues["isbns"]:
            try:
                for doc in catalogues._get(OPEN_LIBRARY, {"q": "isbn:" + value, "fields": OL_FIELDS, "limit": 2}).get("docs") or []:
                    add(doc, "ISBN %s, found %s" % (value, where))
            except catalogues.LookupError_ as exc:
                errors.append(str(exc))
        for title, author, where in clues["titles"]:
            params = {"title": title, "author": author, "fields": OL_FIELDS, "limit": 4} if author else {"q": title, "fields": OL_FIELDS, "limit": 4}
            try:
                for doc in catalogues._get(OPEN_LIBRARY, params).get("docs") or []:
                    add(doc, "Searched for %s, from %s" % ('"%s"' % title + (" by " + author if author else ""), where))
            except catalogues.LookupError_ as exc:
                errors.append(str(exc))
        key = self.hub.config.google_books_key
        query = ""
        if clues["isbns"]:
            query = "isbn:" + clues["isbns"][0][0]
        elif clues["titles"]:
            query = 'intitle:"%s"' % clues["titles"][0][0]
        if key and query and len(found) < MAX_CANDIDATES:
            try:
                for item in catalogues._get(catalogues.GOOGLE, {"q": query, "maxResults": 4, "key": key}).get("items") or []:
                    info = item.get("volumeInfo") or {}
                    ids = [ident.get("identifier") for ident in info.get("industryIdentifiers") or []]
                    # dress Google's answer up like an Open Library one so add() can take it
                    add({"title": info.get("title"), "author_name": info.get("authors"), "first_publish_year": (info.get("publishedDate") or "")[:4] or None,
                         "publisher": [info.get("publisher") or ""], "isbn": ids, "description": info.get("description") or ""},
                        "Google Books: " + query.replace("intitle:", "title "))
            except catalogues.LookupError_ as exc:
                errors.append(str(exc))
        return {"book": {"id": book["id"], "title": book["shown_title"], "author": book["shown_author"], "synopsis": bool(book["synopsis"].strip())},
                "clues": {"isbns": [{"isbn": isbn, "where": where} for isbn, where in clues["isbns"]],
                          "titles": [{"title": title, "author": author, "where": where} for title, author, where in clues["titles"]]},
                "candidates": found[:MAX_CANDIDATES], "errors": errors[:2]}

    def ready(self):
        if not librarymanager.configured(self.hub.config):
            return "The library manager's sign-in is not set up yet. Add it under Admin, Hub, Connections, Library manager."
        return None

    def apply(self, book_id, title, authors, synopsis="", publisher="", cover="", profile_id=None):
        """Give an entry its real title and authors (and a description, publisher and cover when it has none)."""
        blocked = self.ready()
        if blocked:
            raise FixError(blocked)
        book = self.hub.library.get(book_id)
        if not book:
            raise FixError("That book is not in the library any more.")
        title = re.sub(r"\s+", " ", title or "").strip()
        authors = [re.sub(r"\s+", " ", name).strip() for name in authors or [] if name and name.strip()]
        if not title or not authors:
            raise FixError("Give both a title and at least one author.")
        old_key = book["key"]
        self.hub.duplicates.backup()
        manager = self.hub.manager
        try:
            manager.set_field(book_id, "title", title)
            # Calibre splits authors on "&", so an "&" inside a name would make two people out of one
            manager.set_field(book_id, "authors", " & ".join(name.replace("&", "and") for name in authors))
            if synopsis and not book["synopsis"].strip():
                manager.set_field(book_id, "synopsis", synopsis)
            if publisher and not book["publisher"]:
                manager.set_field(book_id, "publisher", publisher)
        except librarymanager.ManagerError as exc:
            raise FixError(str(exc))
        self.hub.library.refresh(force=True)
        fixed = self.hub.library.get(book_id)
        if not fixed or fixed["title"] != title:
            raise FixError("The library manager did not make the change. Does its account have permission to edit books?")
        self.rekey(old_key, fixed["key"], fixed)
        if cover and not fixed["has_cover"] and not self.hub.covers.row(fixed["key"]):
            try:
                self.hub.covers.save(fixed["key"], covers.fetch(cover), "openlibrary", profile_id)
            except covers.CoverError:
                pass  # the details are what matter; a cover can be chosen later on the book's page
        con = self.hub.con()
        con.execute("INSERT INTO duplicate_log(at, book_key, title, action, kept, note) VALUES(?,?,?,?,?,?)",
                    (db.now(), fixed["key"], title, "details", book_id, "was %s by %s" % (book["title"], book["author"])))
        con.commit()
        return {"title": fixed["shown_title"], "author": fixed["shown_author"], "key": fixed["key"]}

    def rekey(self, old, new, book):
        """Move Family Hub's records from a book's old name to its new one."""
        con = self.hub.con()
        if old != new:
            for table in KEYED:
                # UPDATE OR IGNORE skips rows that would clash with one already under the new key; the DELETE
                # then sweeps up those leftovers. two steps, zero unique-constraint explosions
                con.execute("UPDATE OR IGNORE %s SET book_key=? WHERE book_key=?" % table, (new, old))
                con.execute("DELETE FROM %s WHERE book_key=?" % table, (old,))
            for table in ASK_AGAIN:
                con.execute("DELETE FROM %s WHERE book_key=?" % table, (old,))
        con.execute("UPDATE book_meta SET title=?, author=? WHERE book_key=?", (book["title"], book["author"], new))
        con.commit()
