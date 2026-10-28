"""The core of Family Hub: tags, ratings, reading history, stats, recommendations."""
import json
import threading
import time
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

from . import (bookfix, calibre, catalogues, covers, db, devices, duplicates, goodreads, intake, kinds, librarymanager, media, mediasort,
               readers, secondcopy, series, settings, tags)

TAG_WEIGHTS = {"family": 3, "mine": 3, "community": 2, "epub": 1}
SAME_AUTHOR_WEIGHT = 2


def _grouped(finished):
    """Recently finished, with the volumes of one series read one after another as a single line."""
    lines = []
    for item in finished:
        last = lines[-1] if lines else None
        # binge-read volumes 1-7 of something? that's one line, not seven
        if item["series"] and last and last["series"].lower() == item["series"].lower() and last.get("parts") is not None:
            last["parts"].append(item["part"])
            continue
        lines.append(dict(item, parts=[item["part"]]) if item["series"] and item["part"] else dict(item, parts=None))
    for line in lines:
        if line["parts"] and len(line["parts"]) > 1:
            line["title"] = line["series"]
            line["id"] = None
            line["volumes"] = len(line["parts"])
    return lines


def _month_of(day):
    """"2026-10-27" -> 10; 0 for a date with no month in it ("2026", from an imported file, say)."""
    try:
        month = int((day or "")[5:7])
    except ValueError:
        return 0
    return month if 1 <= month <= 12 else 0


class Hub:
    def __init__(self, config):
        self.config = config
        self.library = calibre.Library(config.library_dir)
        db.init(config.db_path)
        self.library.set_choices(self.copy_choices())
        settings.apply(self)
        self.intake = intake.Intake(self)
        self.devices = devices.Devices(self)
        self.catalogues = catalogues.Catalogues(self)
        self.covers = covers.Covers(self)
        self.series = series.Series(self)
        self.media = media.Media(self)
        self.manager = librarymanager.LibraryManager(self)
        self.duplicates = duplicates.Duplicates(self)
        self.sorter = mediasort.Sorter(self)
        self.bookfix = bookfix.BookFix(self)
        self.ingest_stuck = 0  # files the library manager had to be reminded about, last time we looked
        self._backup_lock = threading.Lock()
        self._dirty = False
        self.attempts = {}  # wrong-passcode counters, per profile
        self.goodreads_busy = set()  # profiles whose shelves are being read right now

    def con(self):
        return db.connect(self.config.db_path)

    # ------------------------------------------------------------ profiles

    def profiles(self):
        return self.con().execute("SELECT * FROM profiles ORDER BY role='admin' DESC, id").fetchall()

    def profile(self, profile_id):
        return self.con().execute("SELECT * FROM profiles WHERE id=?", (profile_id,)).fetchone()

    def profile_names(self):
        return {row["id"]: row["name"] for row in self.profiles()}

    # ---------------------------------------------------------------- tags

    def ensure_meta(self, book_key, title=None, author=None, isbn=None, pages=None, source="library"):
        con = self.con()
        row = con.execute("SELECT * FROM book_meta WHERE book_key=?", (book_key,)).fetchone()
        if row is None:
            if title is None:
                book = self.library.by_key(book_key)
                if book:
                    title, author, isbn = book["title"], book["author"], book["isbn"]
                else:
                    title = book_key
            con.execute(
                "INSERT OR IGNORE INTO book_meta(book_key, title, author, isbn, pages, source) VALUES(?,?,?,?,?,?)",
                (book_key, title, author or "", isbn or None, pages, source),
            )
        elif pages and not row["pages"]:
            con.execute("UPDATE book_meta SET pages=? WHERE book_key=?", (pages, book_key))

    def tag_index(self, profile_id=None):
        """{book_key: {'epub': [...], 'community': [...], 'family': [...], 'mine': [...]}}"""
        index = defaultdict(lambda: {"epub": [], "community": [], "family": [], "mine": []})
        con = self.con()
        rules = self.tag_rules()
        for book in self.library.books():
            index[book["key"]]["epub"] = tags.apply_rules(book["tags"], rules)
        for row in con.execute("SELECT book_key, tag FROM community_tags ORDER BY tag"):
            index[row["book_key"]]["community"].append(row["tag"])
        if rules:
            for groups in index.values():
                groups["community"] = tags.apply_rules(groups["community"], rules)
        # family and personal tags are typed by people, so the admin's tidying rules never touch them
        for row in con.execute("SELECT book_key, tag FROM family_tags ORDER BY tag"):
            index[row["book_key"]]["family"].append(row["tag"])
        if profile_id:
            for row in con.execute("SELECT book_key, tag FROM personal_tags WHERE profile_id=? ORDER BY tag", (profile_id,)):
                index[row["book_key"]]["mine"].append(row["tag"])
        return index

    def tag_rules(self):
        """An admin's tidying of machine tags: {tag in lower case: new name, or "" to hide it}."""
        return {row["tag"].lower(): row["becomes"] for row in self.con().execute("SELECT tag, becomes FROM tag_rules")}

    def set_tag_rule(self, tag, becomes):
        """Hide a book-file or community tag everywhere (""), rename it, or put it back (None)."""
        tag = db.clean_tag(tag)
        if not tag:
            return False
        con = self.con()
        if becomes is None or db.clean_tag(becomes).lower() == tag.lower():
            con.execute("DELETE FROM tag_rules WHERE tag=?", (tag,))
        else:
            con.execute("INSERT INTO tag_rules(tag, becomes) VALUES(?,?) ON CONFLICT(tag) DO UPDATE SET becomes=excluded.becomes",
                        (tag, db.clean_tag(becomes)))
        con.commit()
        self.touch()
        return True

    def machine_tags(self):
        """Every book-file and community tag with how many books carry it, before an admin's rules."""
        keys = self.library.keys()
        counts = {}

        def count(tag, source):
            entry = counts.setdefault(tag.lower(), {"tag": tag, "count": 0, "sources": set()})
            entry["count"] += 1
            entry["sources"].add(source)

        removed = Counter()
        for book in self.library.books():
            for tag in book["tags"]:
                count(tag, "Book file")
            removed.update(tags.dropped(book["raw_tags"], book["tags"]))
        for row in self.con().execute("SELECT book_key, tag, source FROM community_tags"):
            if row["book_key"] in keys:
                count(row["tag"], catalogues.label(row["source"]))
        rules = self.tag_rules()
        rows = [{"tag": entry["tag"], "count": entry["count"], "sources": sorted(entry["sources"]),
                 "becomes": rules.get(entry["tag"].lower())} for entry in counts.values()]
        rows.sort(key=lambda entry: (-entry["count"], entry["tag"].lower()))
        return {"tags": rows, "removed": [{"tag": tag, "count": times} for tag, times in removed.most_common()]}

    # -------------------------------------------------------------- copies

    def copy_choices(self):
        return {row["book_key"]: row["choice"] for row in self.con().execute("SELECT book_key, choice FROM copy_choices")}

    def set_copy_choice(self, book_key, choice):
        """Show entries that share a name as one book ("together"), separately
        ("apart"), or let Family Hub decide (None)."""
        con = self.con()
        if choice in (calibre.TOGETHER, calibre.APART):
            con.execute("INSERT INTO copy_choices(book_key, choice) VALUES(?,?) "
                        "ON CONFLICT(book_key) DO UPDATE SET choice=excluded.choice", (book_key, choice))
        else:
            con.execute("DELETE FROM copy_choices WHERE book_key=?", (book_key,))
        con.commit()
        self.library.set_choices(self.copy_choices())
        self.touch()

    def copy_sets(self):
        """What an admin needs to judge each set of same-named entries."""
        def entry(book):
            return {"id": book["id"], "title": book["title"], "author": book["author"], "added": book["added"][:10],
                    "shown": book["copy_of"] is None, "has_cover": book["has_cover"],
                    "files": [{"format": fmt, "size": book["sizes"].get(fmt, 0)} for fmt in sorted(book["own_formats"])]}

        sets = []
        for group in self.library.sets():
            first = group["shown"][0]
            together = len(group["shown"]) == 1
            # wasted space = a copy carrying a format the shown book already has (same thing, twice)
            spare = sum(copy["sizes"].get(fmt, 0) for shown in group["shown"] for copy in shown["copies"]
                        for fmt in copy["own_formats"] if fmt in shown["own_formats"])
            sets.append({"key": group["key"], "title": first["title"], "author": first["author"], "choice": group["choice"],
                         "together": together, "partly": not together and len(group["shown"]) < len(group["books"]),
                         "spare": spare, "books": [entry(book) for book in group["books"]]})
        sets.sort(key=lambda entry: entry["title"].lower())
        return sets

    def add_tag(self, kind, book_key, tag, profile_id):
        tag = db.clean_tag(tag)
        if not tag:
            return False
        con = self.con()
        self.ensure_meta(book_key)
        if kind == "family":
            con.execute("INSERT OR IGNORE INTO family_tags(book_key, tag, added_by, created) VALUES(?,?,?,?)",
                        (book_key, tag, profile_id, db.now()))
        else:
            con.execute("INSERT OR IGNORE INTO personal_tags(book_key, profile_id, tag, created) VALUES(?,?,?,?)",
                        (book_key, profile_id, tag, db.now()))
        con.commit()
        self.touch()
        return True

    def remove_tag(self, kind, book_key, tag, profile_id):
        con = self.con()
        if kind == "family":
            con.execute("DELETE FROM family_tags WHERE book_key=? AND tag=?", (book_key, tag))
        else:
            con.execute("DELETE FROM personal_tags WHERE book_key=? AND profile_id=? AND tag=?",
                        (book_key, profile_id, tag))
        con.commit()
        self.touch()

    def set_rating(self, book_key, profile_id, rating):
        con = self.con()
        self.ensure_meta(book_key)
        if not rating:
            con.execute("DELETE FROM ratings WHERE book_key=? AND profile_id=?", (book_key, profile_id))
        else:
            con.execute(
                "INSERT INTO ratings(book_key, profile_id, rating, updated) VALUES(?,?,?,?) "
                "ON CONFLICT(book_key, profile_id) DO UPDATE SET rating=excluded.rating, updated=excluded.updated",
                (book_key, profile_id, max(1, min(5, int(rating))), db.now()),
            )
        con.commit()
        self.touch()

    # ------------------------------------------------------------- reading

    def save_reading(self, profile_id, book_key, percent=None, status=None, source="manual",
                     device=None, progress=None, started=None, finished=None,
                     title=None, author=None, pages=None):
        con = self.con()
        self.ensure_meta(book_key, title, author, pages=pages,
                         source="library" if self.library.by_key(book_key) else "outside")
        old = con.execute("SELECT * FROM reading WHERE book_key=? AND profile_id=?", (book_key, profile_id)).fetchone()
        today = date.today().isoformat()

        if status == "none":
            con.execute("DELETE FROM reading WHERE book_key=? AND profile_id=?", (book_key, profile_id))
            con.commit()
            self.touch()
            return
        if percent is None:
            percent = 100.0 if status == "finished" else (old["percent"] if old else 0.0)
        percent = min(100.0, max(0.0, float(percent)))
        if status is None:
            if percent >= 99:
                status = "finished"
            elif old and old["status"] == "finished" and source != "manual":
                # opening a finished book on the e-reader to look something up doesn't un-finish it
                status, percent = "finished", 100.0
            else:
                status = "reading"
        if status == "finished":
            percent = 100.0
        started = started or (old["started"] if old else None) or today
        if status == "finished":
            finished = finished or (old["finished"] if old else None) or today
        else:
            finished = None
        con.execute(
            "INSERT INTO reading(book_key, profile_id, status, percent, started, finished, source, device, progress, updated) "
            "VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(book_key, profile_id) DO UPDATE SET "
            "status=excluded.status, percent=excluded.percent, started=excluded.started, finished=excluded.finished, "
            "source=excluded.source, device=COALESCE(excluded.device, reading.device), "
            "progress=COALESCE(excluded.progress, reading.progress), updated=excluded.updated",
            (book_key, profile_id, status, percent, started, finished, source, device, progress, db.now()),
        )
        con.commit()
        self.touch()

    def reading_rows(self, profile_id=None):
        sql = ("SELECT r.*, m.title, m.author, m.pages FROM reading r "
               "LEFT JOIN book_meta m ON m.book_key=r.book_key")
        args = ()
        if profile_id:
            sql += " WHERE r.profile_id=?"
            args = (profile_id,)
        return self.con().execute(sql, args).fetchall()

    # --------------------------------------------------------------- pages

    def fill_page_counts(self, limit=None):
        """Estimate page counts for library EPUBs, and count them for comics, where there is none yet."""
        con = self.con()
        done = 0
        known = {row["book_key"]: row["pages"] for row in con.execute("SELECT book_key, pages FROM book_meta")}
        for book in self.library.books():
            if book["key"] not in known:
                self.ensure_meta(book["key"])
            elif known[book["key"]]:
                continue
            if "EPUB" in book["formats"]:
                path = self.library.file_path(book, "EPUB")
                # no text in it? probably a comic EPUB, so count pictures instead
                pages = (calibre.estimate_pages(path) or calibre.count_pictures(path)) if path else None
            elif "CBZ" in book["formats"]:
                path = self.library.file_path(book, "CBZ")
                pages = calibre.count_pictures(path) if path else None
            else:
                continue
            if pages:
                con.execute("UPDATE book_meta SET pages=? WHERE book_key=?", (pages, book["key"]))
                done += 1
                if limit and done >= limit:
                    break
        con.commit()

    # ------------------------------------------------------------ tag packs

    def load_tagpacks(self):
        """Community tags come from JSON "tag packs" shared through the project.

        A pack lists books with tags. Books in the library receive the tags;
        books that are not in the library become recommendations to look for.
        """
        folder = self.config.tagpacks_dir
        if not folder or not Path(folder).is_dir():
            return 0
        con = self.con()
        count = 0
        by_plain_key = None  # library books by title and author key, made the first time a pack entry needs it
        for file in sorted(Path(folder).glob("*.json")):
            try:
                pack = json.loads(file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            source = "pack:" + str(pack.get("name") or file.stem)[:60]
            for entry in pack.get("books", []):
                title, author = entry.get("title"), entry.get("author", "")
                if not title:
                    continue
                key = db.make_key(title, author, entry.get("isbn"))
                # Prefer the key of a matching library book (it may be ISBN based).
                plain = db.make_key(title, author)
                # for/else: the else only runs if the loop never hit "break". python's weirdest feature, used on purpose
                for candidate in (key, plain):
                    if self.library.by_key(candidate):
                        key = candidate
                        break
                else:
                    if by_plain_key is None:
                        by_plain_key = {}
                        for book in self.library.books():
                            by_plain_key.setdefault(db.make_key(book["title"], book["author"]), book["key"])
                    key = by_plain_key.get(plain, key)
                self.ensure_meta(key, title, author, entry.get("isbn"), entry.get("pages"), source="pack")
                for tag in entry.get("tags", []):
                    tag = db.clean_tag(tag)
                    if tag:
                        con.execute("INSERT OR IGNORE INTO community_tags(book_key, tag, source) VALUES(?,?,?)",
                                    (key, tag, source))
                        count += 1
        con.commit()
        return count

    # --------------------------------------------------------------- stats

    def _matches(self, book_tags, author, kind, value):
        if not kind or not value:
            return True
        value = value.lower()
        if kind == "author":
            return value in (author or "").lower()
        group = {"genre": "epub"}.get(kind, kind)
        return any(tag.lower() == value for tag in book_tags.get(group, []))

    def personal_stats(self, profile_id, kind=None, value=None, year=None):
        year = year or date.today().year
        index = self.tag_index(profile_id)
        keys = self.library.keys()
        kind_of = self.kind_index()
        this_month = date.today().month if year == date.today().year else 12
        months = [{"month": month, "library": 0, "outside": 0, "pages": 0} for month in range(1, this_month + 1)]
        finished, reading_now = [], []
        tag_counts = {"epub": Counter(), "community": Counter(), "family": Counter(), "mine": Counter()}
        author_counts = Counter()
        pages_total = 0
        for row in self.reading_rows(profile_id):
            in_library = row["book_key"] in keys
            book = keys.get(row["book_key"]) or {}
            book_tags = index.get(row["book_key"], {})
            item = {
                "book_key": row["book_key"], "title": book.get("title") or row["title"] or row["book_key"], "author": row["author"] or "",
                "graphic": kind_of.get(row["book_key"]) == kinds.GRAPHIC,
                "series": book.get("series") or "", "part": book.get("part") or "",
                "status": row["status"], "percent": row["percent"], "started": row["started"], "finished": row["finished"],
                "source": row["source"], "device": row["device"], "updated": row["updated"], "pages": row["pages"],
                "in_library": in_library, "id": keys[row["book_key"]]["id"] if in_library else None,
            }
            if row["status"] == "reading":
                reading_now.append(item)
                continue
            if row["status"] != "finished" or not (row["finished"] or "").startswith(str(year)):
                continue
            finished.append(item)
            for group in tag_counts:
                tag_counts[group].update(book_tags.get(group, []))
            # "Unknown" and other placeholders are not authors to count or filter by.
            for name in [author.strip() for author in (row["author"] or "").split("&") if catalogues.author_known(author.strip())]:
                author_counts[name] += 1
            pages_total += row["pages"] or 0
            if not self._matches(book_tags, row["author"], kind, value):
                continue
            month = _month_of(row["finished"])
            if month and month <= len(months):
                months[month - 1]["library" if in_library else "outside"] += 1
                months[month - 1]["pages"] += row["pages"] or 0
        finished.sort(key=lambda item: item["finished"] or "", reverse=True)
        reading_now.sort(key=lambda item: item["updated"] or "", reverse=True)
        current = "%04d-%02d" % (year, this_month)
        month = [item for item in finished if (item["finished"] or "").startswith(current)]
        graphic = [item for item in finished if item["graphic"]]
        return {
            "year": year,
            "read_year": len(finished),
            "read_books": len(finished) - len(graphic),
            "books_library": sum(1 for item in finished if item["in_library"] and not item["graphic"]),
            "read_graphic": len(graphic),  # volumes, each counted
            "graphic_series": len({item["series"].lower() for item in graphic if item["series"]}),
            "read_month": len(month),
            "month_books": sum(1 for item in month if not item["graphic"]),
            "month_graphic": sum(1 for item in month if item["graphic"]),
            "read_library": sum(1 for item in finished if item["in_library"]),
            "pages_year": pages_total,
            "pages_unknown": sum(1 for item in finished if not item["pages"]),
            "authors_year": len(author_counts),
            "months": months,
            "filtered_total": sum(entry["library"] + entry["outside"] for entry in months),
            "top": {group: tag_counts[group].most_common(8) for group in tag_counts},
            "authors": author_counts.most_common(8),
            "reading_now": reading_now,
            "recent": _grouped(finished)[:8],
        }

    def admin_stats(self):
        today = date.today()
        books = self.library.books()
        added = Counter()
        formats = Counter()
        for book in books:
            if book["added"].startswith(str(today.year)):
                added[int(book["added"][5:7])] += 1
            for fmt in book["formats"]:
                formats[fmt] += 1
        keys = self.library.keys()
        started_keys = set()
        per_profile = defaultdict(lambda: {"read_year": 0, "read_month": 0, "reading": 0, "last": None})
        current = today.strftime("%Y-%m")
        for row in self.reading_rows():
            person = per_profile[row["profile_id"]]
            if row["book_key"] in keys:
                started_keys.add(row["book_key"])
            if row["status"] == "reading":
                person["reading"] += 1
            elif row["status"] == "finished" and (row["finished"] or "").startswith(str(today.year)):
                person["read_year"] += 1
                if row["finished"].startswith(current):
                    person["read_month"] += 1
            if not person["last"] or row["updated"] > person["last"]:
                person["last"] = row["updated"]
        family = []
        for prof in self.profiles():
            row = per_profile[prof["id"]]
            family.append({"id": prof["id"], "name": prof["name"], "role": prof["role"], **row})
        recent = sorted(books, key=lambda book: book["added"], reverse=True)[:8]
        return {
            "year": today.year,
            "total": len(books),
            "formats": formats.most_common(),
            "added_month": added.get(today.month, 0),
            "added_year": sum(added.values()),
            "added_by_month": [{"month": month, "count": added.get(month, 0)} for month in range(1, today.month + 1)],
            "read_year": sum(person["read_year"] for person in family),
            "read_month": sum(person["read_month"] for person in family),
            "never_opened": len(books) - len(started_keys),
            "family": family,
            "recent": [{"id": book["id"], "title": book["title"], "author": book["author"], "added": book["added"][:10]} for book in recent],
            "last_backup": db.kv_get(self.con(), "last_backup"),
        }

    # ------------------------------------------------------ recommendations

    def similar(self, book_key, profile_id=None, limit=12):
        """Books sharing tags or an author with the given one, best match first."""
        index = self.tag_index(profile_id)
        con = self.con()
        meta = {row["book_key"]: row for row in con.execute("SELECT * FROM book_meta")}
        base_tags = index.get(book_key, {})
        base_meta = meta.get(book_key)
        base_author = (base_meta["author"] if base_meta else "").lower()
        base = {group: {tag.lower() for tag in base_tags.get(group, [])} for group in TAG_WEIGHTS}
        every = set().union(*base.values()) if base else set()
        finished = set()
        if profile_id:
            finished = {row["book_key"] for row in self.reading_rows(profile_id) if row["status"] == "finished"}
        keys = self.library.keys()
        results = []
        for key in set(index) | set(meta):
            if key == book_key or key.startswith("doc:"):
                continue
            their_tags = index.get(key, {})
            shared, score = [], 0
            for group, weight in TAG_WEIGHTS.items():
                for tag in their_tags.get(group, []):
                    low = tag.lower()
                    # a shared tag scores whichever is heavier: its weight here, or its best weight on the base book
                    if low in every and tag not in shared:
                        shared.append(tag)
                        score += max(weight, max(base_weight for base_group, base_weight in TAG_WEIGHTS.items() if low in base[base_group]))
            their_meta = meta.get(key)
            in_library = key in keys
            title = keys[key]["title"] if in_library else (their_meta["title"] if their_meta else key)
            author = keys[key]["author"] if in_library else (their_meta["author"] if their_meta else "")
            if base_author and author and author.lower() == base_author:
                score += SAME_AUTHOR_WEIGHT
                shared.append("same author")
            if score <= 0:
                continue
            results.append({
                "book_key": key, "title": title, "author": author, "score": score, "shared": shared[:5],
                "in_library": in_library, "id": keys[key]["id"] if in_library else None,
                "already_read": key in finished,
            })
        results.sort(key=lambda result: (result["already_read"], -result["score"], result["title"]))
        return results[:limit]

    # -------------------------------------------------------------- backup

    def export_data(self):
        """Everything the family has added, keyed by book, as plain JSON."""
        con = self.con()
        names = self.profile_names()
        index = self.tag_index()
        # Only kinds a person chose; guesses are made again from the book itself.
        kind_of = {row["book_key"]: row["kind"] for row in con.execute("SELECT book_key, kind FROM book_kind WHERE source='manual'")}
        books = {}

        def entry(key):
            if key not in books:
                meta = con.execute("SELECT * FROM book_meta WHERE book_key=?", (key,)).fetchone()
                owned = self.library.by_key(key)
                books[key] = {
                    "title": owned["title"] if owned else (meta["title"] if meta else key),
                    "author": owned["author"] if owned else (meta["author"] if meta else ""),
                    "isbn": (owned["isbn"] if owned else (meta["isbn"] if meta else "")) or "",
                    "pages": meta["pages"] if meta else None,
                    "kind": kind_of.get(key),
                    "epub_tags": index.get(key, {}).get("epub", []),
                    "community_tags": [], "family_tags": [], "personal_tags": {}, "ratings": {}, "reading": {},
                    "shelves": [],
                }
            return books[key]

        for key in kind_of:
            entry(key)
        # people are written by NAME, not id, so the file still makes sense on a fresh install
        for row in con.execute("SELECT * FROM community_tags ORDER BY tag"):
            entry(row["book_key"])["community_tags"].append({"tag": row["tag"], "source": row["source"]})
        for row in con.execute("SELECT * FROM family_tags ORDER BY tag"):
            entry(row["book_key"])["family_tags"].append(row["tag"])
        for row in con.execute("SELECT * FROM personal_tags ORDER BY tag"):
            entry(row["book_key"])["personal_tags"].setdefault(names.get(row["profile_id"], "?"), []).append(row["tag"])
        for row in con.execute("SELECT * FROM ratings"):
            entry(row["book_key"])["ratings"][names.get(row["profile_id"], "?")] = row["rating"]
        for row in con.execute("SELECT * FROM shelf ORDER BY profile_id"):
            entry(row["book_key"])["shelves"].append(names.get(row["profile_id"], "?"))
        for row in con.execute("SELECT * FROM reading"):
            entry(row["book_key"])["reading"][names.get(row["profile_id"], "?")] = {
                "status": row["status"], "percent": row["percent"], "started": row["started"],
                "finished": row["finished"], "source": row["source"],
            }
        return {
            "format": "bookhub-bookdata", "version": 1, "exported_at": db.now(),
            "profiles": [{"name": profile["name"], "role": profile["role"]} for profile in self.profiles()],
            "tag_rules": [{"tag": row["tag"], "becomes": row["becomes"]}
                          for row in con.execute("SELECT tag, becomes FROM tag_rules ORDER BY tag")],
            "copy_choices": self.copy_choices(),
            "books": books,
        }

    def import_data(self, data):
        """Merge an export back in. Existing entries are kept; nothing is deleted."""
        if not isinstance(data, dict) or data.get("format") != "bookhub-bookdata":
            raise ValueError("This is not a Family Hub book-data file.")
        con = self.con()
        ids = {profile["name"].lower(): profile["id"] for profile in self.profiles()}

        def pid(name):
            """The profile id for a name in the file, making a member profile for a name we've never met. None for
            no name at all, or "?" (whoever's profile was deleted): those would become passcode-less profiles."""
            name = " ".join(str(name).split())
            if not name or name == "?":
                return None
            low = name.lower()
            if low not in ids:
                cur = con.execute("INSERT INTO profiles(name, role, created) VALUES(?, 'member', ?)", (name, db.now()))
                ids[low] = cur.lastrowid
            return ids[low]

        for rule in data.get("tag_rules") or []:
            if db.clean_tag(rule.get("tag")):
                con.execute("INSERT OR IGNORE INTO tag_rules(tag, becomes) VALUES(?,?)",
                            (db.clean_tag(rule["tag"]), db.clean_tag(rule.get("becomes"))))
        for key, choice in (data.get("copy_choices") or {}).items():
            if choice in (calibre.TOGETHER, calibre.APART):
                con.execute("INSERT OR IGNORE INTO copy_choices(book_key, choice) VALUES(?,?)", (key, choice))
        self.library.set_choices(self.copy_choices())
        count = 0
        # INSERT OR IGNORE everywhere: merging never overwrites what's already here, it only fills gaps
        for key, saved in (data.get("books") or {}).items():
            self.ensure_meta(key, saved.get("title") or key, saved.get("author"), saved.get("isbn"), saved.get("pages"), source="import")
            if saved.get("kind") in kinds.KINDS and saved["kind"] != kinds.UNSORTED:
                con.execute("INSERT OR IGNORE INTO book_kind(book_key, kind, source, updated) VALUES(?,?,'manual',?)",
                            (key, saved["kind"], db.now()))
            for tag in saved.get("community_tags", []):
                con.execute("INSERT OR IGNORE INTO community_tags(book_key, tag, source) VALUES(?,?,?)",
                            (key, db.clean_tag(tag.get("tag")), tag.get("source") or "import"))
            for tag in saved.get("family_tags", []):
                con.execute("INSERT OR IGNORE INTO family_tags(book_key, tag, added_by, created) VALUES(?,?,NULL,?)",
                            (key, db.clean_tag(tag), db.now()))
            for name, their_tags in (saved.get("personal_tags") or {}).items():
                for tag in their_tags if pid(name) else []:
                    con.execute("INSERT OR IGNORE INTO personal_tags(book_key, profile_id, tag, created) VALUES(?,?,?,?)",
                                (key, pid(name), db.clean_tag(tag), db.now()))
            for name in saved.get("shelves") or []:
                if pid(name):
                    con.execute("INSERT OR IGNORE INTO shelf(profile_id, book_key, added_by, added) VALUES(?,?,NULL,?)",
                                (pid(name), key, db.now()))
            for name, rating in (saved.get("ratings") or {}).items():
                if pid(name):
                    con.execute("INSERT OR IGNORE INTO ratings(book_key, profile_id, rating, updated) VALUES(?,?,?,?)",
                                (key, pid(name), max(1, min(5, int(rating))), db.now()))
            for name, reading in (saved.get("reading") or {}).items():
                if not pid(name):
                    continue
                con.execute(
                    "INSERT OR IGNORE INTO reading(book_key, profile_id, status, percent, started, finished, source, updated) "
                    "VALUES(?,?,?,?,?,?,?,?)",
                    (key, pid(name), reading.get("status") or "finished", reading.get("percent") or 0, reading.get("started"),
                     reading.get("finished"), reading.get("source") or "import", db.now()),
                )
            count += 1
        con.commit()
        self.touch()
        return count

    def touch(self):
        self._dirty = True

    def backup_now(self):
        """Write today's book-data file and prune old ones."""
        with self._backup_lock:
            folder = self.config.backups_dir
            folder.mkdir(parents=True, exist_ok=True)
            target = folder / ("bookdata-%s.json" % date.today().isoformat())
            # write to .tmp then swap: a crash mid-write leaves yesterday's file whole, not half of today's
            partial = target.with_suffix(".tmp")
            partial.write_text(json.dumps(self.export_data(), indent=1, ensure_ascii=False), encoding="utf-8")
            partial.replace(target)
            files = sorted(folder.glob("bookdata-*.json"))
            # files[:-0] would be everything, so keep at least today's
            for old in files[:-max(1, self.config.backup_keep)]:
                old.unlink(missing_ok=True)
            db.kv_set(self.con(), "last_backup", db.now())
            self._dirty = False
            return target

    def backup_if_needed(self):
        last = db.kv_get(self.con(), "last_backup")
        stale = not last or last[:10] != date.today().isoformat()
        if self._dirty or stale:
            return self.backup_now()
        return None

    # ------------------------------------------------------------ importer

    def import_downloads(self, min_age=120):
        return self.intake.import_downloads(min_age)

    # --------------------------------------------------------------- kinds

    def kind_index(self):
        """{book_key: kind} for every book that has been sorted."""
        return {row["book_key"]: row["kind"] for row in self.con().execute("SELECT book_key, kind FROM book_kind")}

    def set_kind(self, book_key, kind, source="manual"):
        if kind not in kinds.KINDS:
            return False
        self.ensure_meta(book_key)
        self.con().execute(
            "INSERT INTO book_kind(book_key, kind, source, updated) VALUES(?,?,?,?) "
            "ON CONFLICT(book_key) DO UPDATE SET kind=excluded.kind, source=excluded.source, updated=excluded.updated",
            (book_key, kind, source, db.now()))
        return True

    def _origins(self):
        """Where delivered files came from, to recover the folder a book arrived in.

        The library manager renames everything, so a book is matched back to
        its delivery by file size, or failing that by its name.
        """
        by_size, by_name = {}, {}
        for row in self.con().execute("SELECT name, origin, size FROM intake_log WHERE action='delivered' AND origin!=''"):
            ext = Path(row["name"]).suffix.lower().lstrip(".")
            by_size[(ext, row["size"])] = row["origin"]
            by_name[db.slug(Path(row["name"]).stem)] = row["origin"]
        return by_size, by_name

    def sort_new_books(self, limit=None):
        """Guess a kind and franchise tags for library books not looked at yet."""
        con = self.con()
        sorted_by = {row["book_key"]: (row["kind"], row["source"]) for row in con.execute("SELECT book_key, kind, source FROM book_kind")}
        # A book whose file the library manager has not filed yet waits: without its format a comic looks unsorted.
        # One guessed Unsorted that turns out to be all comic files is guessed again.
        todo = [book for book in self.library.books() if book["formats"] and (
            book["key"] not in sorted_by
            or (sorted_by[book["key"]] == (kinds.UNSORTED, "auto") and set(book["formats"]) <= kinds.COMIC_FORMATS))]
        if not todo:
            return 0
        by_size, by_name = self._origins()
        done = 0
        for book in todo[:limit]:
            origin = by_name.get(db.slug(book["title"]), "")
            if by_size:
                for fmt in book["formats"]:
                    path = self.library.file_path(book, fmt)
                    try:
                        size = path.stat().st_size if path else None
                    except OSError:  # the library manager moved it since we looked
                        size = None
                    if size is not None and (fmt.lower(), size) in by_size:
                        origin = by_size[(fmt.lower(), size)]
                        break
            kind, found_tags = kinds.guess(book["title"], book["author"], book["series"], book["tags"],
                                           book["formats"], origin)
            self.set_kind(book["key"], kind, source="auto")
            for tag in found_tags:
                con.execute("INSERT OR IGNORE INTO family_tags(book_key, tag, added_by, created) VALUES(?,?,NULL,?)",
                            (book["key"], tag, db.now()))
            done += 1
        con.commit()
        self.touch()
        return done


def _guarded(app, what, job):
    """Run one job; if it blows up, log it and let the rest of the minute carry on. True when it worked."""
    try:
        job()
        return True
    except Exception as exc:
        app.logger.warning("%s: %s", what, exc)
        return False


def chores_due(hub, app, tick):
    """[(what failing would mean, job)] for this minute, in the order they run. tick counts minutes since start-up,
    so "tick % 10 == 5" means every ten minutes, five minutes in. the odd offsets are on purpose: they stop the
    slow jobs all piling onto the same minute."""
    from . import bookswap, chapterfile, integrations, joined, komga, kosync, recommend, visuals, watchlist
    chores = []

    def every(minutes, offset, what, job):
        if tick % minutes == offset:
            chores.append((what, job))

    def always(what, job):
        chores.append((what, job))

    def adopt_progress():  # joined series books a reading app reported progress in before they were recorded
        if tick == 0:
            joined.record_existing(hub)
        joined.adopt(hub)

    def nudge_stuck():
        hub.ingest_stuck = hub.intake.nudge_stuck()

    def chapter_series():  # followed series by chapter, joined into comic files as chapters arrive
        try:
            if hub.series.configured:  # finished and cancelled series are no longer checked for new chapters
                hub.series.rest_finished()
        except series.SeriesError as exc:
            app.logger.warning("could not put finished series aside: %s", exc)
        chapterfile.start(hub, app)

    def second_copy():  # once a day, after today's book-data file is written
        if secondcopy.due(hub):
            secondcopy.start(hub, app)

    def trackers():
        for profile in hub.profiles():
            if not profile["hardcover_token"]:
                continue
            try:
                integrations.hardcover_sync(hub, profile)
            except integrations.IntegrationError as exc:
                app.logger.warning("tracker sync failed for %s: %s", profile["name"], exc)

    every(10, 0, "could not read the tag packs", hub.load_tagpacks)
    every(10, 0, "could not index the library for reading apps", lambda: kosync.index_library(hub))
    every(10, 0, "could not count pages", lambda: hub.fill_page_counts(limit=200))
    every(10, 0, "could not match reading-app progress in joined books", adopt_progress)
    always("could not tidy the inbox", hub.intake.tidy_inbox)
    always("could not import downloads", hub.import_downloads)
    always("could not nudge the library manager", nudge_stuck)
    always("could not sort new books", lambda: hub.sort_new_books(limit=500))
    every(10, 5, "could not keep the chapter series as comic files", chapter_series)
    # a book download whose sharers have all gone is swapped for a copy someone is sharing
    always("could not look at stuck book downloads", lambda: bookswap.run(hub))
    every(10, 3, "could not match Goodreads books that arrived", lambda: goodreads.reconcile(hub))
    every(10, 7, "could not start the Komga sync", lambda: komga.start(hub, app))  # graphic novels and chapters, for Paperback
    always("could not send books to devices", hub.devices.send_queued)
    always("could not look books up in catalogues", lambda: hub.catalogues.run(limit=4))  # a few a minute, not hammered
    always("could not ask the readers' sites", lambda: readers.run(hub, limit=4))
    always("could not back up the book data", hub.backup_if_needed)
    always("could not start the copy to the second disk", second_copy)
    every(60, 11, "could not refresh suggestions", lambda: recommend.refresh_due(hub, app.logger.warning))  # hourly
    every(5, 2, "could not pass the VPN's port to qBittorrent", hub.media.sync_vpn_port)  # a reconnect can change it
    # a few watchlist entries that are due another look (each every six hours)
    always("could not check the watchlists", lambda: watchlist.run(hub))
    # new visuals from the household's GitHub repository, checked hourly
    always("could not check for new visuals", lambda: visuals.sync(hub))
    # downloads nobody is sharing wait at the back of the line
    always("could not check for downloads waiting for seed", hub.media.park_stalled)
    every(360, 7, "could not sync Goodreads shelves", lambda: goodreads.sync_all(hub, app.logger.warning))
    every(360, 5, "could not sync reading trackers", trackers)
    return chores


def _housekeeping(hub, app, tick):
    """One minute of chores, each on its own: one blowing up (a service away, a file gone) no longer skips the
    rest of the minute, which used to mean no backups or device sends while, say, the downloads folder was odd."""
    for what, job in chores_due(hub, app, tick):
        db.finish(hub.config.db_path)  # keep what the last chore wrote, even if it forgot to commit
        if not _guarded(app, what, job):
            # a write that died half way would otherwise sit on SQLite's write lock for the rest of the minute
            db.finish(hub.config.db_path, failed=True)


def start_background(hub, app):
    """Housekeeping that runs while the server is up."""
    def loop():
        tick = 0
        while True:
            try:
                with app.app_context():
                    _housekeeping(hub, app, tick)
            except Exception as exc:  # whatever happens, the loop lives to see another minute
                app.logger.warning("background job failed: %s", exc)
            tick += 1
            time.sleep(60)

    thread = threading.Thread(target=loop, name="bookhub-jobs", daemon=True)
    thread.start()
    return thread
