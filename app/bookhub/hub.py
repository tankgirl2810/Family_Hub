"""The core of Family Hub: tags, ratings, reading history, stats, recommendations."""
import json
import threading
import time
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path

from . import (bookfix, calibre, catalogues, covers, db, devices, duplicates, goodreads, intake, kinds, librarymanager, media, mediasort,
               readers, secondcopy, series, settings, tags)

TAG_WEIGHTS = {"family": 3, "mine": 3, "community": 2, "epub": 1}
SAME_AUTHOR_WEIGHT = 2


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
        return {r["id"]: r["name"] for r in self.profiles()}

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
        for r in con.execute("SELECT book_key, tag FROM community_tags ORDER BY tag"):
            index[r["book_key"]]["community"].append(r["tag"])
        if rules:
            for groups in index.values():
                groups["community"] = tags.apply_rules(groups["community"], rules)
        for r in con.execute("SELECT book_key, tag FROM family_tags ORDER BY tag"):
            index[r["book_key"]]["family"].append(r["tag"])
        if profile_id:
            for r in con.execute("SELECT book_key, tag FROM personal_tags WHERE profile_id=? ORDER BY tag", (profile_id,)):
                index[r["book_key"]]["mine"].append(r["tag"])
        return index

    def tag_rules(self):
        """An admin's tidying of machine tags: {tag in lower case: new name, or "" to hide it}."""
        return {r["tag"].lower(): r["becomes"] for r in self.con().execute("SELECT tag, becomes FROM tag_rules")}

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
        for r in self.con().execute("SELECT book_key, tag, source FROM community_tags"):
            if r["book_key"] in keys:
                count(r["tag"], catalogues.label(r["source"]))
        rules = self.tag_rules()
        rows = [{"tag": e["tag"], "count": e["count"], "sources": sorted(e["sources"]),
                 "becomes": rules.get(e["tag"].lower())} for e in counts.values()]
        rows.sort(key=lambda e: (-e["count"], e["tag"].lower()))
        return {"tags": rows, "removed": [{"tag": t, "count": n} for t, n in removed.most_common()]}

    # -------------------------------------------------------------- copies

    def copy_choices(self):
        return {r["book_key"]: r["choice"] for r in self.con().execute("SELECT book_key, choice FROM copy_choices")}

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
        def entry(b):
            return {"id": b["id"], "title": b["title"], "author": b["author"], "added": b["added"][:10],
                    "shown": b["copy_of"] is None, "has_cover": b["has_cover"],
                    "files": [{"format": f, "size": b["sizes"].get(f, 0)} for f in sorted(b["own_formats"])]}

        sets = []
        for s in self.library.sets():
            first = s["shown"][0]
            together = len(s["shown"]) == 1
            # Space taken by files that say nothing new: the same format again.
            spare = sum(c["sizes"].get(f, 0) for b in s["shown"] for c in b["copies"] for f in c["own_formats"]
                        if f in b["own_formats"])
            sets.append({"key": s["key"], "title": first["title"], "author": first["author"], "choice": s["choice"],
                         "together": together, "partly": not together and len(s["shown"]) < len(s["books"]),
                         "spare": spare, "books": [entry(b) for b in s["books"]]})
        sets.sort(key=lambda s: s["title"].lower())
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
        if status is None:
            if percent >= 99:
                status = "finished"
            elif old and old["status"] == "finished" and source != "manual":
                # A device reopening a finished book does not un-finish it.
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
        """Estimate page counts for library EPUBs that do not have one yet."""
        con = self.con()
        done = 0
        for book in self.library.books():
            self.ensure_meta(book["key"])
            row = con.execute("SELECT pages FROM book_meta WHERE book_key=?", (book["key"],)).fetchone()
            if row["pages"] or "EPUB" not in book["formats"]:
                continue
            path = self.library.file_path(book, "EPUB")
            pages = calibre.estimate_pages(path) if path else None
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
                for candidate in (key, plain):
                    if self.library.by_key(candidate):
                        key = candidate
                        break
                else:
                    match = next((b for b in self.library.books() if db.make_key(b["title"], b["author"]) == plain), None)
                    if match:
                        key = match["key"]
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

    def _matches(self, tags, author, kind, value):
        if not kind or not value:
            return True
        value = value.lower()
        if kind == "author":
            return value in (author or "").lower()
        group = {"genre": "epub"}.get(kind, kind)
        return any(t.lower() == value for t in tags.get(group, []))

    def personal_stats(self, profile_id, kind=None, value=None, year=None):
        year = year or date.today().year
        index = self.tag_index(profile_id)
        keys = self.library.keys()
        this_month = date.today().month if year == date.today().year else 12
        months = [{"month": m, "library": 0, "outside": 0, "pages": 0} for m in range(1, this_month + 1)]
        finished, reading_now = [], []
        tag_counts = {"epub": Counter(), "community": Counter(), "family": Counter(), "mine": Counter()}
        author_counts = Counter()
        pages_total = 0
        for r in self.reading_rows(profile_id):
            in_library = r["book_key"] in keys
            tags = index.get(r["book_key"], {})
            item = {
                "book_key": r["book_key"], "title": r["title"] or r["book_key"], "author": r["author"] or "",
                "status": r["status"], "percent": r["percent"], "started": r["started"], "finished": r["finished"],
                "source": r["source"], "device": r["device"], "updated": r["updated"], "pages": r["pages"],
                "in_library": in_library, "id": keys[r["book_key"]]["id"] if in_library else None,
            }
            if r["status"] == "reading":
                reading_now.append(item)
                continue
            if r["status"] != "finished" or not (r["finished"] or "").startswith(str(year)):
                continue
            finished.append(item)
            for group in tag_counts:
                tag_counts[group].update(tags.get(group, []))
            for name in [a.strip() for a in (r["author"] or "").split("&") if a.strip()]:
                author_counts[name] += 1
            pages_total += r["pages"] or 0
            if not self._matches(tags, r["author"], kind, value):
                continue
            month = int(r["finished"][5:7])
            if month <= len(months):
                months[month - 1]["library" if in_library else "outside"] += 1
                months[month - 1]["pages"] += r["pages"] or 0
        finished.sort(key=lambda i: i["finished"] or "", reverse=True)
        reading_now.sort(key=lambda i: i["updated"] or "", reverse=True)
        current = "%04d-%02d" % (year, this_month)
        return {
            "year": year,
            "read_year": len(finished),
            "read_month": sum(1 for i in finished if (i["finished"] or "").startswith(current)),
            "read_library": sum(1 for i in finished if i["in_library"]),
            "pages_year": pages_total,
            "pages_unknown": sum(1 for i in finished if not i["pages"]),
            "authors_year": len(author_counts),
            "months": months,
            "filtered_total": sum(m["library"] + m["outside"] for m in months),
            "top": {g: tag_counts[g].most_common(8) for g in tag_counts},
            "authors": author_counts.most_common(8),
            "reading_now": reading_now,
            "recent": finished[:8],
        }

    def admin_stats(self):
        today = date.today()
        books = self.library.books()
        added = Counter()
        formats = Counter()
        for b in books:
            if b["added"].startswith(str(today.year)):
                added[int(b["added"][5:7])] += 1
            for fmt in b["formats"]:
                formats[fmt] += 1
        keys = self.library.keys()
        started_keys = set()
        per_profile = defaultdict(lambda: {"read_year": 0, "read_month": 0, "reading": 0, "last": None})
        current = today.strftime("%Y-%m")
        for r in self.reading_rows():
            p = per_profile[r["profile_id"]]
            if r["book_key"] in keys:
                started_keys.add(r["book_key"])
            if r["status"] == "reading":
                p["reading"] += 1
            elif r["status"] == "finished" and (r["finished"] or "").startswith(str(today.year)):
                p["read_year"] += 1
                if r["finished"].startswith(current):
                    p["read_month"] += 1
            if not p["last"] or r["updated"] > p["last"]:
                p["last"] = r["updated"]
        family = []
        for prof in self.profiles():
            row = per_profile[prof["id"]]
            family.append({"id": prof["id"], "name": prof["name"], "role": prof["role"], **row})
        recent = sorted(books, key=lambda b: b["added"], reverse=True)[:8]
        return {
            "year": today.year,
            "total": len(books),
            "formats": formats.most_common(),
            "added_month": added.get(today.month, 0),
            "added_year": sum(added.values()),
            "added_by_month": [{"month": m, "count": added.get(m, 0)} for m in range(1, today.month + 1)],
            "read_year": sum(f["read_year"] for f in family),
            "read_month": sum(f["read_month"] for f in family),
            "never_opened": len(books) - len(started_keys),
            "family": family,
            "recent": [{"id": b["id"], "title": b["title"], "author": b["author"], "added": b["added"][:10]} for b in recent],
            "last_backup": db.kv_get(self.con(), "last_backup"),
        }

    # ------------------------------------------------------ recommendations

    def similar(self, book_key, profile_id=None, limit=12):
        """Books sharing tags or an author with the given one, best match first."""
        index = self.tag_index(profile_id)
        con = self.con()
        meta = {r["book_key"]: r for r in con.execute("SELECT * FROM book_meta")}
        base_tags = index.get(book_key, {})
        base_meta = meta.get(book_key)
        base_author = (base_meta["author"] if base_meta else "").lower()
        base = {g: {t.lower() for t in base_tags.get(g, [])} for g in TAG_WEIGHTS}
        every = set().union(*base.values()) if base else set()
        finished = set()
        if profile_id:
            finished = {r["book_key"] for r in self.reading_rows(profile_id) if r["status"] == "finished"}
        keys = self.library.keys()
        results = []
        for key in set(index) | set(meta):
            if key == book_key or key.startswith("doc:"):
                continue
            tags = index.get(key, {})
            shared, score = [], 0
            for group, weight in TAG_WEIGHTS.items():
                for tag in tags.get(group, []):
                    low = tag.lower()
                    # A tag counts wherever it appears on the base book.
                    if low in every and tag not in shared:
                        shared.append(tag)
                        score += max(weight, max(w for g, w in TAG_WEIGHTS.items() if low in base[g]))
            m = meta.get(key)
            in_library = key in keys
            title = keys[key]["title"] if in_library else (m["title"] if m else key)
            author = keys[key]["author"] if in_library else (m["author"] if m else "")
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
        results.sort(key=lambda r: (r["already_read"], -r["score"], r["title"]))
        return results[:limit]

    # -------------------------------------------------------------- backup

    def export_data(self):
        """Everything the family has added, keyed by book, as plain JSON."""
        con = self.con()
        names = self.profile_names()
        index = self.tag_index()
        # Only kinds a person chose; guesses are made again from the book itself.
        kind_of = {r["book_key"]: r["kind"] for r in con.execute("SELECT book_key, kind FROM book_kind WHERE source='manual'")}
        books = {}

        def entry(key):
            if key not in books:
                m = con.execute("SELECT * FROM book_meta WHERE book_key=?", (key,)).fetchone()
                lib = self.library.by_key(key)
                books[key] = {
                    "title": lib["title"] if lib else (m["title"] if m else key),
                    "author": lib["author"] if lib else (m["author"] if m else ""),
                    "isbn": (lib["isbn"] if lib else (m["isbn"] if m else "")) or "",
                    "pages": m["pages"] if m else None,
                    "kind": kind_of.get(key),
                    "epub_tags": index.get(key, {}).get("epub", []),
                    "community_tags": [], "family_tags": [], "personal_tags": {}, "ratings": {}, "reading": {},
                    "shelves": [],
                }
            return books[key]

        for key in kind_of:
            entry(key)
        for r in con.execute("SELECT * FROM community_tags ORDER BY tag"):
            entry(r["book_key"])["community_tags"].append({"tag": r["tag"], "source": r["source"]})
        for r in con.execute("SELECT * FROM family_tags ORDER BY tag"):
            entry(r["book_key"])["family_tags"].append(r["tag"])
        for r in con.execute("SELECT * FROM personal_tags ORDER BY tag"):
            entry(r["book_key"])["personal_tags"].setdefault(names.get(r["profile_id"], "?"), []).append(r["tag"])
        for r in con.execute("SELECT * FROM ratings"):
            entry(r["book_key"])["ratings"][names.get(r["profile_id"], "?")] = r["rating"]
        for r in con.execute("SELECT * FROM shelf ORDER BY profile_id"):
            entry(r["book_key"])["shelves"].append(names.get(r["profile_id"], "?"))
        for r in con.execute("SELECT * FROM reading"):
            entry(r["book_key"])["reading"][names.get(r["profile_id"], "?")] = {
                "status": r["status"], "percent": r["percent"], "started": r["started"],
                "finished": r["finished"], "source": r["source"],
            }
        return {
            "format": "bookhub-bookdata", "version": 1, "exported_at": db.now(),
            "profiles": [{"name": p["name"], "role": p["role"]} for p in self.profiles()],
            "tag_rules": [{"tag": r["tag"], "becomes": r["becomes"]}
                          for r in con.execute("SELECT tag, becomes FROM tag_rules ORDER BY tag")],
            "copy_choices": self.copy_choices(),
            "books": books,
        }

    def import_data(self, data):
        """Merge an export back in. Existing entries are kept; nothing is deleted."""
        if not isinstance(data, dict) or data.get("format") != "bookhub-bookdata":
            raise ValueError("This is not a Family Hub book-data file.")
        con = self.con()
        ids = {p["name"].lower(): p["id"] for p in self.profiles()}

        def pid(name):
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
        for key, b in (data.get("books") or {}).items():
            self.ensure_meta(key, b.get("title") or key, b.get("author"), b.get("isbn"), b.get("pages"), source="import")
            if b.get("kind") in kinds.KINDS and b["kind"] != kinds.UNSORTED:
                con.execute("INSERT OR IGNORE INTO book_kind(book_key, kind, source, updated) VALUES(?,?,'manual',?)",
                            (key, b["kind"], db.now()))
            for t in b.get("community_tags", []):
                con.execute("INSERT OR IGNORE INTO community_tags(book_key, tag, source) VALUES(?,?,?)",
                            (key, db.clean_tag(t.get("tag")), t.get("source") or "import"))
            for t in b.get("family_tags", []):
                con.execute("INSERT OR IGNORE INTO family_tags(book_key, tag, added_by, created) VALUES(?,?,NULL,?)",
                            (key, db.clean_tag(t), db.now()))
            for name, tags in (b.get("personal_tags") or {}).items():
                for t in tags:
                    con.execute("INSERT OR IGNORE INTO personal_tags(book_key, profile_id, tag, created) VALUES(?,?,?,?)",
                                (key, pid(name), db.clean_tag(t), db.now()))
            for name in b.get("shelves") or []:
                con.execute("INSERT OR IGNORE INTO shelf(profile_id, book_key, added_by, added) VALUES(?,?,NULL,?)",
                            (pid(name), key, db.now()))
            for name, rating in (b.get("ratings") or {}).items():
                con.execute("INSERT OR IGNORE INTO ratings(book_key, profile_id, rating, updated) VALUES(?,?,?,?)",
                            (key, pid(name), max(1, min(5, int(rating))), db.now()))
            for name, r in (b.get("reading") or {}).items():
                con.execute(
                    "INSERT OR IGNORE INTO reading(book_key, profile_id, status, percent, started, finished, source, updated) "
                    "VALUES(?,?,?,?,?,?,?,?)",
                    (key, pid(name), r.get("status") or "finished", r.get("percent") or 0, r.get("started"),
                     r.get("finished"), r.get("source") or "import", db.now()),
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
            tmp = target.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.export_data(), indent=1, ensure_ascii=False), encoding="utf-8")
            tmp.replace(target)
            files = sorted(folder.glob("bookdata-*.json"))
            for old in files[:-self.config.backup_keep]:
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
        return {r["book_key"]: r["kind"] for r in self.con().execute("SELECT book_key, kind FROM book_kind")}

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
        for r in self.con().execute("SELECT name, origin, size FROM intake_log WHERE action='delivered' AND origin!=''"):
            ext = Path(r["name"]).suffix.lower().lstrip(".")
            by_size[(ext, r["size"])] = r["origin"]
            by_name[db.slug(Path(r["name"]).stem)] = r["origin"]
        return by_size, by_name

    def sort_new_books(self, limit=None):
        """Guess a kind and franchise tags for library books not looked at yet."""
        con = self.con()
        seen = set(self.kind_index())
        todo = [b for b in self.library.books() if b["key"] not in seen]
        if not todo:
            return 0
        by_size, by_name = self._origins()
        done = 0
        for book in todo[:limit]:
            origin = by_name.get(db.slug(book["title"]), "")
            if by_size:
                for fmt in book["formats"]:
                    path = self.library.file_path(book, fmt)
                    if path and (fmt.lower(), path.stat().st_size) in by_size:
                        origin = by_size[(fmt.lower(), path.stat().st_size)]
                        break
            kind, tags = kinds.guess(book["title"], book["author"], book["series"], book["tags"],
                                     book["formats"], origin)
            self.set_kind(book["key"], kind, source="auto")
            for tag in tags:
                con.execute("INSERT OR IGNORE INTO family_tags(book_key, tag, added_by, created) VALUES(?,?,NULL,?)",
                            (book["key"], tag, db.now()))
            done += 1
        con.commit()
        self.touch()
        return done


def start_background(hub, app):
    """Housekeeping that runs while the server is up."""
    from . import kosync

    def loop():
        tick = 0
        while True:
            try:
                with app.app_context():
                    if tick % 10 == 0:
                        hub.load_tagpacks()
                        kosync.index_library(hub)
                        hub.fill_page_counts(limit=200)
                    hub.intake.tidy_inbox()
                    hub.import_downloads()
                    hub.ingest_stuck = hub.intake.nudge_stuck()
                    hub.sort_new_books(limit=500)
                    if tick % 10 == 3:
                        goodreads.reconcile(hub)  # books that were only on Goodreads and have now arrived
                    hub.devices.send_queued()
                    hub.catalogues.run(limit=4)  # a few books a minute, so catalogues are not hammered
                    readers.run(hub, limit=4)
                    hub.backup_if_needed()
                    if secondcopy.due(hub):  # once a day, after today's book-data file is written
                        secondcopy.start(hub, app)
                    if tick % 60 == 11:  # hourly: is anyone's weekly list of suggestions due?
                        from . import recommend
                        recommend.refresh_due(hub, app.logger.warning)
                    if tick % 5 == 2:  # the VPN can hand out a new port when it reconnects
                        try:
                            hub.media.sync_vpn_port()
                        except Exception as exc:
                            app.logger.warning("could not pass the VPN's port to qBittorrent: %s", exc)
                    try:  # new visuals from the household's GitHub repository, checked hourly
                        from . import visuals
                        visuals.sync(hub)
                    except Exception as exc:
                        app.logger.warning("could not check for new visuals: %s", exc)
                    try:  # downloads nobody is sharing wait at the back of the line
                        hub.media.park_stalled()
                    except Exception as exc:
                        app.logger.warning("could not check for downloads waiting for seed: %s", exc)
                    if tick % 360 == 7:  # every six hours
                        goodreads.sync_all(hub, app.logger.warning)
                    if tick % 360 == 5:  # every six hours
                        from . import integrations
                        for profile in hub.profiles():
                            if profile["hardcover_token"]:
                                try:
                                    integrations.hardcover_sync(hub, profile)
                                except integrations.IntegrationError as exc:
                                    app.logger.warning("tracker sync failed for %s: %s", profile["name"], exc)
            except Exception as exc:  # keep the loop alive whatever happens
                app.logger.warning("background job failed: %s", exc)
            tick += 1
            time.sleep(60)

    thread = threading.Thread(target=loop, name="bookhub-jobs", daemon=True)
    thread.start()
    return thread
