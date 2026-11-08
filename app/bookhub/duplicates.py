"""Removing spare copies of a book from the library.

This is the one place where Family Hub changes the Calibre library, and it
does so by asking the library manager (see librarymanager.py). The rule for
each set of entries that share a title and author:

  identical   the spare's files are the same, byte for byte     -> removed outright
  duplicate   sizes within 10%, the details are the same        -> spare set aside, then removed
  merge       sizes within 10%, one entry has more details      -> details copied, spare set aside, then removed
  review      sizes further apart, an entry with no file,
              or a doubtful name                                -> a person decides

"Set aside" means the spare's files are copied to needs-attention/_duplicates
before the entry is removed, so nothing but byte-identical files is ever lost.
The library's database is copied before every batch.

The page works through the sets one at a time, as a queue (see "the queue" below): the copies side by side, a
person picks the one to keep and puts its title, author, series and tags right, and the others go by the rules
above. Copies a person says are different books each get their own name and are never suggested again.
"""
import hashlib
import json
import re
import shutil
import sqlite3
import threading
import zipfile
from datetime import datetime
from pathlib import Path

from . import bookfix, calibre, catalogues, db, librarymanager, review

SIMILAR = 0.10
SET_ASIDE = "_duplicates"
KEEP_BACKUPS = 20
IDENTICAL, DUPLICATE, MERGE, REVIEW = "identical", "duplicate", "merge", "review"
AUTOMATIC = (IDENTICAL, DUPLICATE, MERGE)
# Details the library manager can copy from one entry to another, and ones it cannot.
COPYABLE = ("synopsis", "tags", "series", "publisher")
FIXED = ("isbn", "pubdate", "cover")
LABEL = {"synopsis": "description", "tags": "tags", "series": "series", "publisher": "publisher",
         "isbn": "ISBN", "pubdate": "date published", "cover": "cover"}
_hashes = {}  # (path, size, mtime) -> digest


def _digest(path):
    stamp = path.stat()
    key = (str(path), stamp.st_size, int(stamp.st_mtime))
    if key not in _hashes:
        digest = hashlib.sha1()
        with open(path, "rb") as file:
            # iter() with a sentinel: keep calling read() until it hands back b"" (end of file). 1 MB bites
            for block in iter(lambda: file.read(1 << 20), b""):
                digest.update(block)
        _hashes[key] = digest.hexdigest()
    return _hashes[key]


def details(book):
    """Which details an entry carries. Calibre writes the year 0101 for "no date"."""
    return {
        # the library's own series: one only guessed from the file name ("Foo v01") isn't there to copy across
        "synopsis": bool(book["synopsis"].strip()), "tags": bool(book["raw_tags"]), "series": bool(book.get("own_series", book["series"])),
        "publisher": bool(book["publisher"]), "isbn": bool(book["isbn"]),
        "pubdate": bool(book["pubdate"]) and not book["pubdate"].startswith("0101"), "cover": book["has_cover"],
    }


def made_by_calibre(path):
    """Was this EPUB produced by Calibre's converter (as the library manager does with a MOBI)?"""
    try:
        with zipfile.ZipFile(path) as epub:
            names = {name.rsplit("/", 1)[-1] for name in epub.namelist()}
    except (OSError, zipfile.BadZipFile):
        return False
    # Calibre's converter always leaves these two stylesheets behind. its fingerprint, basically
    return "page_styles.css" in names and "stylesheet.css" in names


class Duplicates:
    def __init__(self, hub):
        self.hub = hub
        self._lock = threading.Lock()
        self.job = {"running": False, "done": 0, "total": 0, "problems": [], "finished": None}

    # ------------------------------------------------------------ judging

    def _file(self, book, fmt):
        return Path(self.hub.config.library_dir) / book["own_formats"][fmt]

    def _same_files(self, keep, spare):
        if not spare["own_formats"]:
            return False
        for fmt in spare["own_formats"]:
            if fmt not in keep["own_formats"] or keep["sizes"].get(fmt) != spare["sizes"].get(fmt):
                return False
            try:
                if _digest(self._file(keep, fmt)) != _digest(self._file(spare, fmt)):
                    return False
            except OSError:
                return False
        return True

    def _similar(self, keep, spare):
        shared = [fmt for fmt in spare["own_formats"] if fmt in keep["own_formats"]]
        for fmt in shared:
            kept_size, spare_size = keep["sizes"].get(fmt, 0), spare["sizes"].get(fmt, 0)
            bigger = max(kept_size, spare_size)
            if not bigger or abs(kept_size - spare_size) / bigger > SIMILAR:
                return False
        return bool(spare["own_formats"]) and bool(keep["own_formats"])

    def _original_first(self, book):
        """Prefer the file the family supplied over one the library manager converted from a MOBI."""
        epub = book["own_formats"].get("EPUB")
        converted = bool(epub) and made_by_calibre(Path(self.hub.config.library_dir) / epub)
        return (converted,) + calibre._best_first(book)

    def plan(self, group):
        """What to do with one set, and why."""
        if group["choice"] == calibre.APART:
            return None  # an admin has said these are different books
        books = list(group["books"])
        doubtful = len(group["shown"]) > 1 and group["choice"] != calibre.TOGETHER
        keep = min(books, key=self._original_first)
        spares = [book for book in books if book is not keep]
        cls, why, lost, copy = self._judge(keep, spares, books, doubtful)
        return {"key": group["key"], "title": books[0]["title"], "author": books[0]["author"],
                "cls": cls, "why": why, "lost": lost, "copy": copy,
                "keep": keep["id"], "remove": [spare["id"] for spare in spares],
                "spare_bytes": sum(sum(spare["sizes"].values()) for spare in spares),
                "books": [self._entry(book, book is keep) for book in books]}

    def _judge(self, keep, spares, books, doubtful):
        """(class, why, details that would be lost, details to copy across) for a set with this keeper."""
        mine = details(keep)
        if all(self._same_files(keep, spare) for spare in spares):
            # same bytes, but a spare may still carry a description or series someone typed in. those come across too
            lost = sorted({LABEL[field] for spare in spares for field in FIXED if details(spare)[field] and not mine[field]})
            if lost:
                return REVIEW, "The kept entry lacks the %s, which Calibre-Web cannot copy across." % " and ".join(lost), lost, []
            return IDENTICAL, "", [], self._copies(mine, spares)
        if doubtful:
            return REVIEW, "The title looks like a file name or the author like a user name, so these may be different documents.", [], []
        if not all(self._similar(keep, spare) for spare in spares):
            why = ("One entry has no book file." if any(not book["own_formats"] for book in books)
                   else "The files differ in size by more than %d%%." % round(SIMILAR * 100))
            return REVIEW, why, [], []
        have = [details(book) for book in books]
        if all(entry == have[0] for entry in have):
            return DUPLICATE, "", [], []
        # keep the better file, haul the other's details across. BUT the library manager can't copy an ISBN,
        # a date or a cover, so if we'd lose one of those a human gets to decide
        lost = sorted({LABEL[field] for spare in spares for field in FIXED if details(spare)[field] and not mine[field]})
        if lost:
            why = "The entry with the better file lacks the %s, which Calibre-Web cannot copy across." % " and ".join(lost)
            return REVIEW, why, lost, []
        return MERGE, "", [], self._copies(mine, spares)

    def _copies(self, mine, givers):
        """The details the kept entry lacks and one of the others has: [{"field", "label", "from"}]."""
        copies = []
        for field in COPYABLE:
            giver = next((book for book in givers if details(book)[field] and not mine[field]), None)
            if giver:
                copies.append({"field": field, "label": LABEL[field], "from": giver["id"]})
        return copies

    def _entry(self, book, kept):
        have = details(book)
        epub = book["own_formats"].get("EPUB")
        return {"id": book["id"], "kept": kept, "added": book["added"][:10],
                "files": [{"format": fmt, "size": book["sizes"].get(fmt, 0)} for fmt in sorted(book["own_formats"])],
                "details": [LABEL[field] for field in COPYABLE + FIXED if have[field]], "tags": len(book["raw_tags"]),
                "converted": bool(epub) and made_by_calibre(Path(self.hub.config.library_dir) / epub)}

    def plans(self):
        apart = self.told_apart()
        found = [plan for plan in (self.plan(group) for group in self.hub.library.sets())
                 if plan and not self._was_told_apart(plan["books"], apart)]
        found.sort(key=lambda plan: plan["title"].lower())
        return found

    # ------------------------------------------------------------- doing

    def ready(self):
        """None when spare copies can be removed, otherwise what is missing."""
        config = self.hub.config
        if not librarymanager.configured(config):
            return "Calibre-Web's sign-in is not set up yet. Add it under Admin, Hub, Connections, Calibre-Web."
        if not config.attention_dir or not Path(config.attention_dir).is_dir():
            return "The needs-attention folder is not set up, so there is nowhere to set spare copies aside."
        return None

    def backup(self):
        """Copy the library's database before changing anything."""
        folder = Path(self.hub.config.data_dir) / "library-backups"
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / ("metadata-%s.db" % datetime.now().strftime("%Y%m%d-%H%M%S"))
        source = sqlite3.connect("file:%s?mode=ro" % self.hub.config.metadata_db.as_posix(), uri=True, timeout=30)
        copy = sqlite3.connect(str(target))
        try:
            with copy:
                source.backup(copy)
        finally:
            copy.close()
            source.close()
        for old in sorted(folder.glob("metadata-*.db"))[:-KEEP_BACKUPS]:
            old.unlink(missing_ok=True)
        return target

    def _set_aside(self, book):
        """Copy an entry's files out of the library before it is removed. Returns the folder."""
        name = re.sub(r'[\\/:*?"<>|]+', " ", "%s - %s (%d)" % (book["author"] or "Unknown", book["title"], book["id"]))[:150].strip()
        target = Path(self.hub.config.attention_dir) / SET_ASIDE / name
        target.mkdir(parents=True, exist_ok=True)
        for fmt in book["own_formats"]:
            src = self._file(book, fmt)
            shutil.copy2(src, target / src.name)
            if (target / src.name).stat().st_size != src.stat().st_size:
                raise OSError("the copy of %s is not complete" % src.name)
        for extra in ("cover.jpg", "metadata.opf"):
            src = Path(self.hub.config.library_dir) / book["path"] / extra
            if src.is_file():
                shutil.copy2(src, target / extra)
        return target

    def _raw(self, book_id, field):
        """A detail exactly as the library holds it, for handing to the library manager."""
        con = sqlite3.connect("file:%s?mode=ro" % self.hub.config.metadata_db.as_posix(), uri=True, timeout=30)
        try:
            if field == "synopsis":
                row = con.execute("SELECT text FROM comments WHERE book=?", (book_id,)).fetchone()
                return row[0] if row else ""
            if field == "tags":
                return ", ".join(row[0] for row in con.execute(
                    "SELECT t.name FROM books_tags_link l JOIN tags t ON t.id=l.tag WHERE l.book=? ORDER BY t.name", (book_id,)))
            table, link, col = {"series": ("series", "books_series_link", "series"),
                                "publisher": ("publishers", "books_publishers_link", "publisher")}[field]
            row = con.execute("SELECT x.name FROM %s l JOIN %s x ON x.id=l.%s WHERE l.book=?" % (link, table, col), (book_id,)).fetchone()
            return row[0] if row else ""
        finally:
            con.close()

    def carry_out(self, wanted, keep=None, identical_goes=False):
        """Do one set. ``wanted`` is the plan the admin saw; it is worked out afresh and must still match.
        With ``identical_goes`` (the queue), a spare a person chose against is deleted outright when its files
        are byte for byte the kept one's, as the rules say; otherwise a person's choice always sets spares aside."""
        hub = self.hub
        hub.library.refresh()
        group = next((found for found in hub.library.sets() if found["key"] == wanted["key"]), None)
        now = self.plan(group) if group else None
        if now is None:
            raise ValueError("These entries are no longer in the library as a set.")
        by_id = {book["id"]: book for book in group["books"]}
        if keep is not None:  # a person's decision on a set that needed review
            if keep not in by_id:
                raise ValueError("That entry is no longer in the library.")
            givers = [book for book in group["books"] if book["id"] != keep]
            same = identical_goes and all(self._same_files(by_id[keep], book) for book in givers)
            now.update(cls=IDENTICAL if same else REVIEW, keep=keep, remove=[book_id for book_id in by_id if book_id != keep],
                       copy=self._copies(details(by_id[keep]), givers))
        # the library could have shifted since the admin looked. if the plan isn't the same now, bail
        elif (now["cls"], now["keep"], sorted(now["remove"])) != (wanted["cls"], wanted["keep"], sorted(wanted["remove"])) \
                or now["cls"] not in AUTOMATIC:
            raise ValueError("The library has changed since this list was made. Reload the page and look again.")
        manager = hub.manager
        for step in now["copy"]:
            manager.set_field(now["keep"], step["field"], self._raw(step["from"], step["field"]))
            if step["field"] == "series":
                manager.set_field(now["keep"], "series_index", str(by_id[step["from"]]["series_index"] or 1))
        if now["copy"]:
            hub.library.refresh(force=True)
            kept = details(hub.library.get(now["keep"]) or by_id[now["keep"]])
            missed = [step["label"] for step in now["copy"] if not kept[step["field"]]]
            if missed:
                raise ValueError("Calibre-Web did not take the %s, so nothing was removed." % " and ".join(missed))
        aside = ""
        if now["cls"] != IDENTICAL:
            for book_id in now["remove"]:
                aside = str(self._set_aside(by_id[book_id]).parent)
        manager.merge(now["keep"], now["remove"])
        hub.library.refresh(force=True)
        left = [book_id for book_id in now["remove"] if hub.library.get(book_id)]  # trust, but verify
        if left:
            raise ValueError("Calibre-Web reported success but %d spare entries are still there." % len(left))
        con = hub.con()
        con.execute("INSERT INTO duplicate_log(at, book_key, title, action, kept, removed, note) VALUES(?,?,?,?,?,?,?)",
                    (db.now(), now["key"], now["title"], now["cls"], now["keep"], ",".join(str(book_id) for book_id in now["remove"]),
                     ", ".join(step["label"] for step in now["copy"])))
        con.commit()
        hub.touch()
        return {"cls": now["cls"], "set_aside": aside}

    def start(self, app, wanted, keep=None):
        """Work through some sets in the background. Returns False when a batch is already running."""
        if not self._lock.acquire(blocking=False):
            return False
        self.job = {"running": True, "done": 0, "total": len(wanted), "problems": [], "finished": None, "backup": ""}

        def work():
            try:
                with app.app_context():
                    try:
                        self.job["backup"] = self.backup().name
                    except (OSError, sqlite3.Error) as exc:
                        self.job["problems"].append("The library's database could not be copied first, so nothing was changed: %s" % exc)
                        return
                    refused = 0  # the library manager's failures in a row
                    for plan in wanted:
                        try:
                            self.carry_out(plan, keep)
                            refused = 0
                        except (ValueError, OSError, sqlite3.Error, librarymanager.ManagerError) as exc:
                            self.job["problems"].append("%s: %s" % (plan["title"], exc))
                            refused = refused + 1 if isinstance(exc, librarymanager.ManagerError) else 0
                            if refused >= 3:
                                self.job["problems"].append("Stopped after three failures in a row with Calibre-Web.")
                                break
                        self.job["done"] += 1
            finally:
                self.job["running"] = False
                self.job["finished"] = db.now()
                self._lock.release()

        threading.Thread(target=work, name="bookhub-duplicates", daemon=True).start()
        return True

    def log(self, limit=30):
        return [dict(row) for row in self.hub.con().execute("SELECT * FROM duplicate_log ORDER BY id DESC LIMIT ?", (limit,))]

    # ------------------------------------------------------------ the queue
    # One set at a time, side by side. A person picks the copy to keep and puts its details right; the spare goes
    # by the rules at the top of this file. Or they say the copies are different books and give each its own name.

    def told_apart(self):
        """Sets a person said are different books, as [{entry ids}]. They are never suggested again."""
        return [set(ids) for ids in json.loads(db.kv_get(self.hub.con(), "duplicates:apart") or "[]")]

    @staticmethod
    def _was_told_apart(books, apart):
        ids = {book["id"] for book in books}
        return any(ids <= group for group in apart)

    def page_count(self, book):
        """Pages in an entry's main file (EPUB, then PDF, then CBZ), or None. Remembered per file."""
        fmt = next((fmt for fmt in ("EPUB", "PDF", "CBZ") if fmt in book["own_formats"]), None)
        if not fmt:
            return None
        path = self._file(book, fmt)
        try:
            stamp = path.stat()
        except OSError:
            return None
        key = (str(path), stamp.st_size, int(stamp.st_mtime))
        if key not in _pages:
            _pages[key] = _count_pages(path, fmt)
        return _pages[key]

    def queue(self, key=None):
        """The sets still to look at (the ones only a person can decide first), and the chosen one side by side."""
        plans = self.plans()
        plans.sort(key=lambda plan: plan["cls"] != REVIEW)  # sort() keeps the title order within each
        current = next((plan for plan in plans if plan["key"] == key), plans[0] if plans else None)
        return {"blocked": self.ready(), "busy": self.job["running"], "set_aside": "needs-attention/" + SET_ASIDE,
                "sets": [{"key": plan["key"], "title": plan["title"], "cls": plan["cls"], "copies": len(plan["books"]),
                          "cover": plan["books"][0]["id"] if plan["books"] else None} for plan in plans],
                "counts": {cls: sum(1 for plan in plans if plan["cls"] == cls) for cls in AUTOMATIC + (REVIEW,)},
                "set": self.compare(current) if current else None}

    def compare(self, plan):
        """One set as the queue shows it: every copy's details, what happens to the others for each copy a
        person could keep, and the kept copy's details to start from."""
        group = next((found for found in self.hub.library.sets() if found["key"] == plan["key"]), None)
        by_id = {book["id"]: book for book in group["books"]} if group else {}
        books = [by_id[entry["id"]] for entry in plan["books"] if entry["id"] in by_id]
        copies = []
        for entry in plan["books"]:
            book = by_id.get(entry["id"])
            if not book:
                continue
            others = [other for other in books if other is not book]
            copies.append({"id": book["id"], "title": book["title"], "authors": book["authors"], "author": book["author"],
                           "series": book["own_series"], "series_index": book["series_index"] if book["own_series"] else None,
                           "files": entry["files"], "converted": entry["converted"], "pages": self.page_count(book),
                           "added": entry["added"], "tags": book["raw_tags"], "publisher": book["publisher"],
                           "language": book["language"], "has_cover": book["has_cover"], "isbn": book["isbn"],
                           "synopsis": len(book["synopsis"].split()), "outcome": self._outcome(book, others)})
        return {"key": plan["key"], "title": plan["title"], "author": plan["author"], "cls": plan["cls"], "why": plan["why"],
                "keep": plan["keep"], "copies": copies, "start": self.prefill(books, plan["keep"])}

    def _outcome(self, keeper, others):
        """What happens to the other copies if this one is kept, in the words the button uses."""
        identical = all(self._same_files(keeper, other) for other in others)
        many = len(others) > 1
        spare = ("the other %d copies" % len(others)) if many else "the other copy"
        if identical:
            words = "%s %s identical and will be deleted" % (spare, "are" if many else "is")
        else:
            words = "%s will be set aside in needs-attention" % spare
        # the editor already shows the title, author, series and tags; these come across unseen
        across = [step["label"] for step in self._copies(details(keeper), others) if step["field"] in ("synopsis", "publisher")]
        return {"identical": identical, "words": words, "across": across}

    @staticmethod
    def prefill(books, keep_id):
        """The kept copy's details to start from, field by field from whichever copy says more. Ties go to the
        copy Family Hub would keep. Tags from every copy."""
        order = sorted(books, key=lambda book: book["id"] != keep_id)  # max() keeps the first of equals
        title = max(order, key=lambda book: (not bookfix.FILE_LIKE.search(book["title"]), len(book["title"].strip())))
        author = max(order, key=lambda book: (catalogues.author_known(book["author"]) and not bookfix.username_like(book["author"]),
                                              len(book["authors"]), len(book["author"])))
        series = max(order, key=lambda book: (bool(book["own_series"]), book["series_index"] is not None))
        tags = []
        for book in order:
            tags += [tag for tag in book["raw_tags"] if tag.lower() not in [known.lower() for known in tags]]
        return {"title": title["title"], "authors": author["authors"], "series": series["own_series"],
                "series_index": series["series_index"] if series["own_series"] else None, "tags": tags,
                "from": {"title": title["id"], "authors": author["id"], "series": series["id"]}}

    def _clean(self, fields):
        try:
            return review.Review.clean_fields(fields)
        except review.ReviewError as exc:
            raise ValueError(str(exc))

    def _begin(self):
        """Check the library manager is there, take the lock and copy the library's database. Caller releases."""
        blocked = self.ready()
        if blocked:
            raise ValueError(blocked)
        if not self._lock.acquire(blocking=False):
            raise Busy("A batch is already running. Wait for it to finish.")
        try:
            self.backup()
        except (OSError, sqlite3.Error) as exc:
            self._lock.release()
            raise ValueError("The library's database could not be copied first, so nothing was changed: %s" % exc)

    def keep(self, key, keep_id, fields):
        """Keep one copy of a set with the details a person gave it. The others go by the rules; then the new
        details are written, all after one copy of the library's database. Returns what was done."""
        fields = self._clean(fields)
        plan = next((found for found in self.plans() if found["key"] == key), None)
        if not plan:
            raise ValueError("These entries are no longer in the library as a set.")
        if keep_id not in [entry["id"] for entry in plan["books"]]:
            raise ValueError("That entry is not part of this set.")
        self._begin()
        try:
            done = self.carry_out(plan, keep_id, identical_goes=True)
            problem = ""
            try:
                self._put_details(self.hub.library.get(keep_id), key, fields)
            except (ValueError, librarymanager.ManagerError) as exc:
                # the spare has gone by now, so this is said, not undone. Fix book details can finish it
                problem = "The other copy was dealt with, but the new details did not go in: %s" % exc
        finally:
            self._lock.release()
        self.hub.touch()
        kept = self.hub.library.get(keep_id)
        return {"cls": done["cls"], "set_aside": done["set_aside"], "problem": problem,
                "title": kept["shown_title"] if kept else fields["title"]}

    def _put_details(self, book, old_key, fields):
        """Send the kept copy's new title, authors, series and tags to the library manager, only what changed,
        then move Family Hub's records to its new name."""
        if not book:
            raise ValueError("The kept entry is not in the library any more.")
        self._send(book, fields)
        self.hub.library.refresh(force=True)
        fixed = self.hub.library.get(book["id"])
        if not fixed or fixed["title"] != fields["title"]:
            raise ValueError("Calibre-Web did not make the change. Does its account have permission to edit books?")
        self._follow(old_key, fixed, move=True)

    @staticmethod
    def _changed(book, fields):
        """Which of title, authors, series, number and tags differ from the library entry."""
        found = []
        if fields["title"] != book["title"]:
            found.append("title")
        if " & ".join(fields["authors"]) != book["author"]:
            found.append("authors")
        if fields["series"] != book["own_series"]:
            found.append("series")
        if fields["series"] and fields["series_index"] is not None and (
                fields["series"] != book["own_series"] or float(book["series_index"] or 0) != float(fields["series_index"])):
            found.append("series_index")
        if sorted(tag.lower() for tag in fields["tags"]) != sorted(tag.lower() for tag in book["raw_tags"]):
            found.append("tags")
        return found

    def _send(self, book, fields):
        """Only what changed, to the library manager."""
        manager = self.hub.manager
        for name in self._changed(book, fields):
            if name == "authors":
                # Calibre splits authors on "&", so an "&" inside a name would make two people out of one
                manager.set_field(book["id"], "authors", " & ".join(person.replace("&", "and") for person in fields["authors"]))
            elif name == "series_index":
                manager.set_field(book["id"], "series_index", str(fields["series_index"]))
            elif name == "tags":
                # the whole list, so a tag taken off in the editor comes off the entry too
                manager.set_field(book["id"], "tags", ", ".join(tag.replace(",", " ") for tag in fields["tags"]))
            else:
                manager.set_field(book["id"], name, fields[name])

    def _follow(self, old_key, fixed, move):
        """Family Hub's records go with a renamed entry: moved, or copied when other entries keep the old name."""
        hub, con = self.hub, self.hub.con()
        if move or fixed["key"] == old_key:
            hub.bookfix.rekey(old_key, fixed["key"], fixed)
            return
        hub.ensure_meta(fixed["key"], fixed["title"], fixed["author"])
        con.execute("INSERT OR IGNORE INTO family_tags(book_key, tag, added_by, created) "
                    "SELECT ?, tag, added_by, created FROM family_tags WHERE book_key=?", (fixed["key"], old_key))
        con.execute("INSERT OR IGNORE INTO book_kind(book_key, kind, source, updated, role) "
                    "SELECT ?, kind, source, updated, role FROM book_kind WHERE book_key=?", (fixed["key"], old_key))
        con.commit()

    def tell_apart(self, key, names):
        """Say a set's copies are different books. Each gets the details a person gave it ({id: {"title",
        "authors", and if given "series", "series_index", "tags"}}), written by the library manager, and Family
        Hub remembers never to suggest them again."""
        group = next((found for found in self.hub.library.sets() if found["key"] == key), None)
        if not group:
            raise ValueError("These entries are no longer in the library as a set.")
        names = {str(book_id): value for book_id, value in (names or {}).items()}
        renamed = []
        for book in group["books"]:
            given = names.get(str(book["id"])) or {}
            fields = self._clean({"title": given.get("title") or book["title"], "authors": given.get("authors") or book["authors"],
                                  "series": given.get("series", book["own_series"]),
                                  "series_index": given.get("series_index", book["series_index"] if book["own_series"] else None),
                                  "tags": given.get("tags", book["raw_tags"])})
            if self._changed(book, fields):
                renamed.append((book, fields))
        ids = sorted(book["id"] for book in group["books"])
        if renamed:
            self._begin()
            try:
                for book, fields in renamed:
                    self._send(book, fields)
            finally:
                self._lock.release()
            self.hub.library.refresh(force=True)
            stayed = any(book["key"] == key for book in self.hub.library.all_books() if book["id"] in ids)
            for number, (book, fields) in enumerate(renamed):
                fixed = self.hub.library.get(book["id"])
                if not fixed or fixed["title"] != fields["title"]:
                    raise ValueError("Calibre-Web did not make the change. Does its account have permission to edit books?")
                # the last one renamed takes the records with it, unless a copy kept the old name
                self._follow(key, fixed, move=not stayed and number == len(renamed) - 1)
        con = self.hub.con()
        apart = [sorted(found) for found in self.told_apart() if not found <= set(ids)] + [ids]
        db.kv_set(con, "duplicates:apart", json.dumps(apart))
        # any that still share a name are shown as separate books too
        keys = {}
        for book in self.hub.library.all_books():
            if book["id"] in ids:
                keys[book["key"]] = keys.get(book["key"], 0) + 1
        for shared, count in keys.items():
            if count > 1:
                self.hub.set_copy_choice(shared, calibre.APART)
        con.execute("INSERT INTO duplicate_log(at, book_key, title, action, kept, removed, note) VALUES(?,?,?,?,?,?,?)",
                    (db.now(), key, group["books"][0]["title"], "apart", None, "", ", ".join(fields["title"] for _, fields in renamed)))
        con.commit()
        self.hub.touch()
        return {"renamed": len(renamed)}


class Busy(Exception):
    pass


_pages = {}  # (path, size, mtime) -> pages


def _count_pages(path, fmt):
    if fmt == "EPUB":
        return calibre.estimate_pages(path)
    if fmt == "CBZ":
        return calibre.count_pictures(path)
    try:
        from pypdf import PdfReader
        return len(PdfReader(str(path)).pages) or None
    except Exception:  # encrypted, damaged or not really a PDF: no count, no crash
        return None
