"""Finding out what a book really is, and putting the clear cases right without anybody having to.

bookfix.py gathers the clues (an ISBN, a DOI, the details a file gives itself, the biggest words on a PDF's first
page, the tidied title). This module asks the catalogues about them, weighs what comes back and decides:

  * clear   - the catalogues agree with each other or with the file, and only one book fits. When the admin's
              "fix clear cases by itself" switch is on (it is unless they turn it off), the library manager makes
              the change after a copy of the library's database, and the change goes on a list with an Undo.
  * doubt   - there is a likely answer, but not a sure one. The book waits in the review queue with that answer
              already filled in, so it is one press for a person to say yes.
  * unknown - nobody knew it. Asked again in a month, in case a catalogue has learned it since.

Tabletop books and graphic novels are left to the sorter (kinds.py) and Comic Vine: the book catalogues do not
know game books, and a comic's "title" is its series. What is sent out is a title, an author, an ISBN or a DOI;
never anything about the family.
"""
import json
import re
from datetime import datetime, timedelta, timezone

from . import catalogues, db, kinds

CLEAR, DOUBT, UNKNOWN, FINE = "clear", "doubt", "unknown", "fine"
OPEN_LIBRARY = "https://openlibrary.org/search.json"
OL_FIELDS = "key,title,author_name,first_publish_year,publisher,isbn,cover_i"
CROSSREF = "https://api.crossref.org/works"
RETRY_UNKNOWN_DAYS = 30
SKIP_KINDS = {kinds.TABLETOP, kinds.GRAPHIC}
MAX_CANDIDATES = 8

# What PDF-making programs put in a PDF's own title and author when the person never filled them in.
JUNK_META = re.compile(
    r"^(microsoft (word|powerpoint)|untitled|document\d*$|doc\d+$|book\d*$|layout \d|print$|title$|author$|"
    r"unknown$|user$|owner$|admin|administrator$|default$|scan|img|pdf|adobe|acrobat|indesign|quark|calibre|"
    r"converted|created by|www\.|https?:)|\.(indd|qxd|docx?|pdf|pmd|rtf|txt|tex)\b", re.I)
DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s\"<>,;]+)", re.I)


def junk_meta(text):
    text = (text or "").strip()
    return not text or len(text) < 3 or bool(JUNK_META.search(text)) or not re.search(r"[^\W\d_]{2}", text)


def dois_in(text):
    found = []
    for doi in DOI_RE.findall(text or ""):
        doi = doi.rstrip(".)]}").lower()
        if doi not in found:
            found.append(doi)
    return found


# ----------------------------------------------------------------- PDF clues

def pdf_meta(reader):
    """(title, author) a PDF gives itself, in its info and its XMP, with the programs' leftovers dropped."""
    meta = reader.metadata or {}
    title, author = str(meta.get("/Title") or "").strip(), str(meta.get("/Author") or "").strip()
    try:
        xmp = reader.xmp_metadata
    except Exception:  # broken XMP is common and tells us nothing
        xmp = None
    if xmp is not None:
        try:
            if junk_meta(title):
                title = next(iter((xmp.dc_title or {}).values()), "") or title
            if junk_meta(author):
                author = " & ".join(xmp.dc_creator or []) or author
        except Exception:
            pass
    return ("" if junk_meta(title) else re.sub(r"\s+", " ", title)), ("" if junk_meta(author) else re.sub(r"\s+", " ", author))


def cover_title(path, pages=2):
    """The biggest words on a PDF's first page with words on it: on nearly every book, that is the title.
    "" when the first pages are pictures with no text (a scan) or the PDF can't be read."""
    import pypdfium2 as pdfium
    import pypdfium2.raw as raw
    try:
        doc = pdfium.PdfDocument(str(path))
    except Exception:
        return ""
    try:
        for number in range(min(len(doc), pages)):
            textpage = doc[number].get_textpage()
            count = textpage.count_chars()
            if count < 3:
                continue
            text = textpage.get_text_range(0, count)
            if len(text) != count:  # pdfium sometimes adds line breaks of its own; sizes would not line up
                text = "".join(textpage.get_text_range(i, 1) for i in range(count))
            sizes = [raw.FPDFText_GetFontSize(textpage.raw, i) for i in range(count)]
            biggest = max((size for char, size in zip(text, sizes) if char.isalnum()), default=0)
            if biggest <= 0:
                continue
            letters = [size for char, size in zip(text, sizes) if char.isalnum()]
            if len(letters) > 60 and sum(size >= biggest * 0.85 for size in letters) > len(letters) * 0.4:
                return ""  # most of the page is one size: a page of text, no title standing out
            # every character within 15% of the biggest size, in reading order; gaps between runs become spaces
            words, gap = [], False
            for char, size in zip(text, sizes):
                if size >= biggest * 0.85 and char not in "\r\n":
                    if gap and words:
                        words.append(" ")
                    words.append(char)
                    gap = False
                else:
                    gap = True
            found = re.sub(r"[\ufffd\x00-\x1f\u2000-\u200f\uf000-\uf8ff]", " ", "".join(words))  # unknown glyphs, private-use icons
            found = re.sub(r"\s+", " ", found).strip(" -–:|")
            if len(re.findall(r"[^\W\d_]", found)) >= 3 and len(found) <= 150:
                return found
            if found:
                return ""  # the biggest thing on the page is a page number or a logo, not a title
        return ""
    except Exception:  # one odd PDF must not stop the rest
        return ""
    finally:
        doc.close()


NAME = r"[A-Z][\w'’-]+(?:\s+[A-Z]\.)*(?:\s+(?:de|van|von|le|la|di|da|Mc|O')?\s*[A-Z][\w'’-]+)"
BY_AFTER = re.compile(r"^(?P<title>.{3,}?)\s+by\s+(?P<name>%s(?:\s+(?:and|&)\s+%s)?)$" % (NAME, NAME), re.I)
BY_BEFORE = re.compile(r"^by\s+(?P<name>%s)\s+(?P<title>.{3,})$" % NAME, re.I)


def split_byline(text):
    """("The Asylum Stone", "James L. Sutter") from "The Asylum Stone By James L. Sutter" or "By James L. Sutter
    The Asylum Stone"; (text, "") when there is no byline."""
    text = (text or "").strip()
    for pattern in (BY_AFTER, BY_BEFORE):
        match = pattern.match(text)
        if match:
            return match.group("title").strip(" -–:,"), re.sub(r"\s+", " ", match.group("name")).strip()
    return text, ""


# ----------------------------------------------------------------- asking

def _authors_of_crossref(item):
    return [" ".join(part for part in (person.get("given"), person.get("family")) if part) for person in item.get("author") or []
            if person.get("family")]


def _crossref_doc(item):
    year = ((item.get("issued") or {}).get("date-parts") or [[None]])[0][0]
    return {"title": (item.get("title") or [""])[0], "author_name": _authors_of_crossref(item), "first_publish_year": year,
            "publisher": [item.get("publisher") or ""], "isbn": item.get("ISBN") or [], "description": ""}


def crossref_doi(doi):
    reply = catalogues._get("%s/%s" % (CROSSREF, doi), {})
    item = reply.get("message") or {}
    return [_crossref_doc(item)] if item.get("title") else []


def crossref_search(title, author=""):
    params = {"query.bibliographic": title, "rows": 3, "select": "title,author,issued,publisher,ISBN,type"}
    if author:
        params["query.author"] = author
    reply = catalogues._get(CROSSREF, params)
    return [_crossref_doc(item) for item in (reply.get("message") or {}).get("items") or [] if item.get("title")]


class Candidate(dict):
    """One book the entry may be, with every source and clue that pointed at it."""


def _mark(title, authors):
    surname = (authors[0].split()[-1] if authors and authors[0].split() else "")
    return (db.slug(re.split(r"[:(]", title)[0]), db.slug(surname))


def gather(config, clues, nonfiction=False):
    """([candidates, best first], [errors]) for one book's clues. Every source is asked politely, once."""
    found, errors = {}, []

    def add(doc, source, why, via=None):
        title, authors = (doc.get("title") or "").strip(), [name for name in doc.get("author_name") or [] if name]
        if not title:
            return
        mark = _mark(title, authors)
        if mark not in found:
            found[mark] = Candidate(title=title, authors=authors[:4], year=doc.get("first_publish_year"),
                                    publisher=(doc.get("publisher") or [""])[0], isbn=next(iter(doc.get("isbn") or []), ""),
                                    cover="https://covers.openlibrary.org/b/id/%s-M.jpg" % doc["cover_i"] if doc.get("cover_i") else "",
                                    synopsis=doc.get("description") or "", why=why, sources=[], via=[])
        candidate = found[mark]
        if source not in candidate["sources"]:
            candidate["sources"].append(source)
        if via and via not in candidate["via"]:
            candidate["via"].append(via)
        for field in ("publisher", "synopsis", "cover", "year"):
            if not candidate.get(field):
                candidate[field] = {"publisher": (doc.get("publisher") or [""])[0], "synopsis": doc.get("description") or "",
                                    "cover": "https://covers.openlibrary.org/b/id/%s-M.jpg" % doc["cover_i"] if doc.get("cover_i") else "",
                                    "year": doc.get("first_publish_year")}[field]
        if not candidate["isbn"]:
            candidate["isbn"] = next(iter(doc.get("isbn") or []), "")

    def ask(source, job, *args):
        try:
            return job(*args)
        except catalogues.LookupError_ as exc:
            errors.append("%s: %s" % (source, exc))
            return []

    for isbn, where in clues["isbns"]:
        for doc in ask("Open Library", lambda: catalogues._get(OPEN_LIBRARY, {"q": "isbn:" + isbn, "fields": OL_FIELDS, "limit": 2}).get("docs") or []):
            add(doc, "Open Library", "ISBN %s, found %s" % (isbn, where), "isbn")
    for doi, where in clues.get("dois", []):
        for doc in ask("Crossref", crossref_doi, doi):
            add(doc, "Crossref", "DOI %s, found %s" % (doi, where), "doi")
    for title, author, where in clues["titles"]:
        params = {"title": title, "author": author, "fields": OL_FIELDS, "limit": 4} if author else {"q": title, "fields": OL_FIELDS, "limit": 4}
        for doc in ask("Open Library", lambda: catalogues._get(OPEN_LIBRARY, params).get("docs") or []):
            add(doc, "Open Library", 'Searched for "%s"%s, from %s' % (title, " by " + author if author else "", where))
    if nonfiction and clues["titles"]:
        title, author, where = clues["titles"][0]
        for doc in ask("Crossref", crossref_search, title, author):
            add(doc, "Crossref", 'Searched for "%s", from %s' % (title, where))
    if config.google_books_key:
        queries = ["isbn:" + isbn for isbn, _ in clues["isbns"][:1]] + ['intitle:"%s"' % title for title, _, _ in clues["titles"][:2]]
        for query in queries:
            items = ask("Google Books", lambda: catalogues._get(catalogues.GOOGLE, {"q": query, "maxResults": 4, "key": config.google_books_key}).get("items") or [])
            for item in items:
                info = item.get("volumeInfo") or {}
                ids = [ident.get("identifier") for ident in info.get("industryIdentifiers") or []]
                add({"title": info.get("title"), "author_name": info.get("authors"), "first_publish_year": (info.get("publishedDate") or "")[:4] or None,
                     "publisher": [info.get("publisher") or ""], "isbn": ids, "description": info.get("description") or ""},
                    "Google Books", "Google Books: " + query.replace("intitle:", "title "), "isbn" if query.startswith("isbn:") else None)
    candidates = list(found.values())
    for candidate in candidates:
        score(candidate, clues)
    candidates.sort(key=lambda candidate: -candidate["score"])
    return candidates[:MAX_CANDIDATES], errors


def score(candidate, clues):
    """How sure we are, and the reasons in words. An ISBN or DOI is the strongest clue; a title read from the file
    agreeing with the catalogue comes next; several catalogues agreeing counts too."""
    reasons, points = [], 0
    if "isbn" in candidate["via"]:
        points += 4
        reasons.append("its ISBN matched")
    if "doi" in candidate["via"]:
        points += 4
        reasons.append("its DOI matched")
    # same_book with no author also takes "Designing Virtual Worlds, Richard Bartle": the author tacked on the end
    titled = [where for title, _, where in clues["titles"] if catalogues.same_title(title, candidate["title"])
              or catalogues.same_book(title, "", candidate["title"], candidate["authors"])]
    if titled:
        points += 2
        reasons.append("the title agrees with %s" % titled[0])
    authored = [where for _, author, where in clues["titles"] if catalogues.author_known(author) and catalogues.same_book(candidate["title"], author, candidate["title"], candidate["authors"])]
    if authored:
        points += 2
        reasons.append("the author agrees with %s" % authored[0])
    if len(candidate["sources"]) > 1:
        points += 2
        reasons.append("%s agree" % " and ".join(candidate["sources"]))
    if not candidate["authors"]:
        points -= 3  # a catalogue record with no author can't be written as a book's details
    candidate["score"], candidate["reasons"] = points, reasons
    return points


def verdict(book, candidates):
    """(status, best candidate or None, why in words). A catalogue's answer only counts when its title agrees with
    something the file says, or it came from the book's own ISBN or DOI, and it names an author: a loose search
    for "Heroes Handbook" brings back Greek Mythology, and nobody should be offered that."""
    candidates = [candidate for candidate in candidates if candidate["authors"] and
                  ({"isbn", "doi"} & set(candidate["via"]) or any(reason.startswith("the title agrees") for reason in candidate["reasons"]))]
    if not candidates:
        return UNKNOWN, None, "No catalogue knew it."
    best = candidates[0]
    runner = candidates[1] if len(candidates) > 1 else None
    if catalogues.same_title(book["title"], best["title"]) and best["authors"] and \
            catalogues.same_book(book["title"], book["author"], best["title"], best["authors"]):
        return FINE, best, "The library already has it right."
    strong = bool({"isbn", "doi"} & set(best["via"]))
    agreed = [reason for reason in best["reasons"] if reason.startswith(("the title agrees", "the author agrees"))]
    many = len(best["sources"]) > 1
    sure = best["authors"] and ((strong and (agreed or many)) or (many and len(agreed) == 2))
    # a second book nearly as likely means a person has to choose
    close = runner is not None and runner["score"] >= best["score"] - 1
    if sure and not close:
        return CLEAR, best, "Sure because " + ", ".join(best["reasons"]) + "."
    why = ("Probably %s by %s, because %s." % (best["title"], " & ".join(best["authors"]) or "nobody named", ", ".join(best["reasons"]) or "a catalogue suggested it")
           if best["reasons"] else "A catalogue suggested %s by %s, but nothing in the file confirms it." % (best["title"], " & ".join(best["authors"]) or "nobody named"))
    if close:
        why += " %s by %s is nearly as likely." % (runner["title"], " & ".join(runner["authors"]) or "nobody named")
    return DOUBT, best, why


# ----------------------------------------------------------------- the background job

class Identify:
    def __init__(self, hub):
        self.hub = hub

    @property
    def auto(self):
        return (db.kv_get(self.hub.con(), "identify:auto") or "1") == "1"

    def set_auto(self, on):
        db.kv_set(self.hub.con(), "identify:auto", "1" if on else "0")

    def _rows(self):
        return {row["book_id"]: row for row in self.hub.con().execute("SELECT * FROM identities")}

    def _due(self, row):
        if row is None:
            return True
        if row["status"] != UNKNOWN:
            return False
        at = datetime.strptime(row["at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - at > timedelta(days=RETRY_UNKNOWN_DAYS)

    def pending(self):
        """Books with doubtful details that have not been looked up yet, newest first."""
        rows, kind_of = self._rows(), self.hub.kind_index()
        return [entry for entry in self.hub.bookfix.needing()
                if kind_of.get(entry["key"]) not in SKIP_KINDS and self._due(rows.get(entry["id"]))]

    def look(self, book):
        """(status, best, why, candidates, clues, errors) for one library book; nothing is changed."""
        clues = self.hub.bookfix.clues(book)
        nonfiction = self.hub.kind_index().get(book["key"]) == kinds.NONFICTION or bool(clues.get("dois"))
        candidates, errors = gather(self.hub.config, clues, nonfiction)
        status, best, why = verdict(book, candidates)
        return status, best, why, candidates, clues, errors

    def _save(self, book, status, best, why):
        con = self.hub.con()
        con.execute("INSERT OR REPLACE INTO identities(book_id, book_key, status, best, why, at) VALUES(?,?,?,?,?,?)",
                    (book["id"], book["key"], status, json.dumps(best or {}), why, db.now()))
        con.commit()

    def run(self, limit=2):
        """Look up a few books; put the clear ones right when the switch is on. Returns what happened, for the log."""
        done = []
        for entry in self.pending()[:limit]:
            book = self.hub.library.get(entry["id"])
            if not book:
                continue
            status, best, why, _, _, errors = self.look(book)
            if errors and status == UNKNOWN:
                break  # a catalogue is away; try again next minute rather than calling the book unknown
            if status == CLEAR and self.auto and not self.hub.bookfix.ready():
                try:
                    self.apply(book, best, why)
                    status = "fixed"
                except Exception as exc:  # FixError or the library manager away: keep it as a suggestion
                    why += " It could not be written by itself: %s" % exc
                    status = DOUBT
            elif status == CLEAR:
                status = DOUBT  # the switch is off, or there is no library manager sign-in: a person says yes
            self._save(book, status, best, why)
            done.append((book["id"], status))
        return done

    def apply(self, book, best, why):
        before = {"title": book["title"], "authors": list(book["authors"])}
        result = self.hub.bookfix.apply(book["id"], best["title"], best["authors"], best.get("synopsis", ""), best.get("publisher", ""),
                                        best.get("cover", ""))
        con = self.hub.con()
        con.execute("INSERT INTO identify_log(at, book_id, book_key, before, after, why) VALUES(?,?,?,?,?,?)",
                    (db.now(), book["id"], result["key"], json.dumps(before),
                     json.dumps({"title": best["title"], "authors": best["authors"]}), why))
        con.commit()
        return result

    # ----------------------------------------------------------- what the pages show

    def doubts(self):
        """{book id: {"title", "authors", "why"}} for the review queue to offer."""
        found = {}
        for row in self.hub.con().execute("SELECT book_id, best, why FROM identities WHERE status=?", (DOUBT,)):
            best = json.loads(row["best"] or "{}")
            if best.get("title") and best.get("authors"):
                found[row["book_id"]] = {"title": best["title"], "authors": best["authors"], "publisher": best.get("publisher") or "",
                                         "why": row["why"]}
        return found

    def changes(self, limit=50):
        rows = self.hub.con().execute("SELECT * FROM identify_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [{"id": row["id"], "at": row["at"], "book_id": row["book_id"], "before": json.loads(row["before"]),
                 "after": json.loads(row["after"]), "why": row["why"], "undone": bool(row["undone"])} for row in rows]

    def summary(self):
        counts = {status: 0 for status in (CLEAR, DOUBT, UNKNOWN, FINE, "fixed", "undone")}
        for row in self.hub.con().execute("SELECT status, COUNT(*) AS n FROM identities GROUP BY status"):
            counts[row["status"]] = row["n"]
        return {"auto": self.auto, "counts": counts, "waiting": len(self.pending()), "blocked": self.hub.bookfix.ready(),
                "changes": self.changes()}

    def undo(self, change_id):
        """Put a book back the way it was before it was fixed by itself, and never fix it by itself again."""
        con = self.hub.con()
        row = con.execute("SELECT * FROM identify_log WHERE id=?", (change_id,)).fetchone()
        if not row:
            raise LookupError("There is no such change.")
        if row["undone"]:
            return {"title": json.loads(row["before"])["title"]}
        before = json.loads(row["before"])
        result = self.hub.bookfix.apply(row["book_id"], before["title"], before["authors"])
        con.execute("UPDATE identify_log SET undone=1 WHERE id=?", (change_id,))
        con.execute("UPDATE identities SET status='undone', book_key=? WHERE book_id=?", (result["key"], row["book_id"]))
        con.commit()
        self.hub.bookfix.leave(row["book_id"])  # a person said the old details stand
        return result
