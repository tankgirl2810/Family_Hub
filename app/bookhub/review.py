"""The review queue: books the sorter has a suggestion for, an admin's say on each, and the rules it learns.

The sorter (kinds.py) leaves a verdict on every library book. Most verdicts need nobody: the kind, role and game
tags go on quietly. A book waits here only when there is something a person should look at: a cleaner title or
author, a series and number, game and edition tags the library entry does not carry yet, clues that disagree, or
a game nobody has told Family Hub about. A book that is simply Unsorted, with no clue to go on, is left alone.

Confirming a book writes its details into the library through the library manager (librarymanager.py), after a
copy of the library's database, the same way fixing a book's details does (bookfix.py); Family Hub's own records
follow the book to its new name. Every confirm or reject also saves a learned rule ("anything arriving in
Pathfinder/2nd Edition/Society is a Pathfinder 2e adventure"), so the next books like it are sorted by
themselves. Rules can be read, deleted and, when several say nearly the same thing, combined into one. Nothing is
ever combined without an admin saying so.

Series followed chapter by chapter (series.py) are not in the library, so none of this touches them.
"""
import json
import re
import sqlite3
import threading

from . import bookfix, catalogues, db, kinds, librarymanager, peek

TABLETOP, BOOKS, GRAPHIC, UNSORTED = "tabletop", "books", "graphic", "unsorted"
MEDIA = [(TABLETOP, "Tabletop"), (BOOKS, "Books"), (GRAPHIC, "Graphic novels & manga"), (UNSORTED, "Unsorted")]
UNKNOWN_GAME = "Looks like a game book, but which game is not known."
# Who publishes a game line, for game books whose "author" is a file name. The writers on the credits page are
# better, when they can be found (credits()).
PUBLISHERS = {"Pathfinder": "Paizo", "Starfinder": "Paizo", "D&D": "Wizards of the Coast", "World of Darkness": "White Wolf",
              "Call of Cthulhu": "Chaosium"}
# The library manager takes languages by their English names, not their codes
LANGUAGES = {"eng": "English", "rus": "Russian", "deu": "German", "fra": "French", "spa": "Spanish", "ita": "Italian",
             "por": "Portuguese", "pol": "Polish", "nld": "Dutch", "jpn": "Japanese", "kor": "Korean", "zho": "Chinese",
             "ell": "Greek", "ukr": "Ukrainian", "ces": "Czech", "swe": "Swedish"}
# The end of a folder name that says which edition: "Shadowrun 6e", "Shadowrun 6th Edition", "Mutants V2"
EDITION_END = re.compile(r"[\s_-]*\b(\d+(?:st|nd|rd|th)?[\s_-]?(?:e|ed|edition)|v\d+)\s*$", re.I)
# Where a credits page names the writers. pypdf runs lines together, so a name list stops at the next heading.
CREDITS_RE = re.compile(r"\b(?:authors?|written by|writers?|writing|lead designers?|designers?)\s*[:\-–]?\s*(.{3,240}?)"
                        r"(?=\b(?:development|developers?|developed|editing|editors?|edited|art|artists?|cover|illustrat\w*|"
                        r"layout|graphic|publisher|creative|additional|playtest\w*|special thanks|cartograph\w*)\b|$)", re.I | re.S)
# "Authors" the files carry that are nobody: "Cleaned by VH", "Copyright 2010 Paizo Publishing, LLC", "JPGs"
JUNK_AUTHOR = re.compile(r"copyright|cleaned by|scanned|\bscan\b|ripped|^unknown$|^[\d\s]+$|^(jpe?gs?|pdfs?|cbz|maps?|"
                         r"interactive maps?)$|^\S+\.\S+$", re.I)
# An author that is a company: kept as the author, but never glued onto the title
COMPANY = re.compile(r"\b(publishing|games|studios?|llc|inc|ltd|press|paizo|wizards of the coast|white wolf|chaosium)\b", re.I)
NAME_RE = re.compile(r"^[A-Z][\w'.-]+(?: [A-Z][\w'.-]*){1,3}$")
EDITABLE = ["title", "authors", "series", "series_index", "publisher", "tags", "game", "edition", "kind", "role", "language"]


class ReviewError(Exception):
    pass


def media_of(kind, formats):
    """Which tab of the queue a book is in."""
    if kind == kinds.UNSORTED:
        return UNSORTED
    if kind == kinds.TABLETOP:
        return TABLETOP
    if kind == kinds.GRAPHIC or (formats and set(formats) <= kinds.COMIC_FORMATS):
        return GRAPHIC
    return BOOKS


def _name(origin, title, author):
    """The name a learned rule's pattern is matched against, as kinds.sort_book matches it."""
    _, file_name, _ = kinds.split_origin(origin)
    file_name = re.sub(r"_+", " ", file_name)
    return file_name or " ".join(part for part in (title, author) if part and part.lower() != "unknown")


def name_pattern(name, exact=False):
    """A pattern for file names like this one: numbers may differ ("Map 01", "Map 02"), unless exact."""
    pattern = re.escape(name.strip())
    return "^%s$" % (pattern if exact else re.sub(r"\d+", r"\\d+", pattern))


def batch_of(origin, title, author):
    """(batch id, folder, pattern): books that one learned rule would cover. The arrival folder when there is one,
    otherwise a name pattern with the numbers left open."""
    folder, _, _ = kinds.split_origin(origin)
    if folder:
        return "folder:" + folder.lower(), folder, ""
    pattern = name_pattern(_name(origin, title, author))
    return "name:" + pattern.lower(), "", pattern


def shown_pattern(pattern):
    """A name pattern as a person reads it: "^Map \\d+$" is "Map #"."""
    shown = (pattern or "").strip("^$").replace(r"\d+", "#")
    return re.sub(r"\\(.)", r"\1", shown)


def rule_words(rule):
    """A learned rule in plain words: "Anything arriving in Pathfinder/2nd Edition/Society → Pathfinder ›
    Pathfinder 2e, Adventure, series Pathfinder Society"."""
    where = "Anything"
    if rule.get("folder"):
        where += " arriving in %s" % rule["folder"]
    if rule.get("pattern"):
        where += " named like “%s”" % shown_pattern(rule["pattern"])
    sets, rejects = rule.get("sets") or {}, rule.get("rejects") or {}
    said = []
    if sets.get("game"):
        said.append(" › ".join(part for part in (sets["game"], sets.get("edition")) if part))
    elif sets.get("edition"):
        said.append(sets["edition"])
    if sets.get("role"):
        said.append(sets["role"])
    elif sets.get("kind"):
        said.append(sets["kind"])
    if sets.get("series"):
        said.append("series %s" % sets["series"])
    if sets.get("tags"):
        said.append("tags %s" % ", ".join(sets["tags"]))
    nots = ["%s “%s”" % (field.replace("_", " "), value) for field, value in rejects.items() if value not in ("", None)]
    marks = sets.get("labels") or {}
    if marks:
        said.append("%s read from the places marked on page %s" % (" and ".join("title" if field == "title" else "author" for field in marks),
                                                                  " and ".join(sorted({str(mark["page"] + 1) for mark in marks.values()}))))
    words = where + " → " + (", ".join(said) or "left as the sorter has it")
    return words + ("; never suggest " + ", ".join(nots) if nots else "")


def new_game_from(folder, lines):
    """{"game", "edition"} when the top folder looks like a game Family Hub does not know: "Shadowrun 6e/..." is
    Shadowrun, edition Shadowrun 6e. None when it doesn't."""
    parts = [part.strip() for part in (folder or "").split("/") if part.strip()]
    if not parts:
        return None
    top = parts[0]
    if (kinds.PLAIN_FOLDER.match(top) or any(rule.match(top) for _, rule in kinds.FOLDER_ROLES) or len(top) > 40
            or not re.search(r"[A-Za-z]{3}", top) or kinds._game(top, lines, folder=True)[0]):
        return None
    found = EDITION_END.search(top)
    game = top[:found.start()].strip(" -_") if found else top
    said = found.group(1) if found else ""
    if not said and len(parts) > 1:  # "Shadowrun/6th Edition/..."
        second = re.match(r"^(\d+)(?:st|nd|rd|th)?[\s_-]?(?:e|ed|edition)$", parts[1], re.I)
        said = second.group(0) if second else ""
    if not game or len(game) < 3:
        return None
    number = re.search(r"\d+", said)
    edition = ""
    if said:
        edition = "%s %s" % (game, ("%se" % number.group()) if number and not said.lower().startswith("v") else said.upper())
    return {"game": game, "edition": edition}


def game_line_entry(game, edition="", publisher="", entry=None):
    """A game line in the shape gamelines.json (and kinds.load_game_lines) takes, or an old one with an edition added."""
    entry = dict(entry or {"tag": game, "match": [re.escape(game.lower()).replace(r"\ ", r"[\s_.-]*")], "editions": []})
    if publisher:
        entry["publisher"] = publisher
    editions = list(entry.get("editions") or [])
    if edition and edition.lower() not in [known["tag"].lower() for known in editions]:
        said = edition[len(game):].strip() if edition.lower().startswith(game.lower()) else edition
        number = re.match(r"^(\d+)e$", said, re.I)
        if number:
            match = r"\b%s ?e\b|\b%s(st|nd|rd|th)? ?ed(ition)?\b" % (number.group(1), number.group(1))
        else:
            match = r"\b%s\b" % re.escape(said)
        editions.append({"tag": edition, "match": [match]})
    entry["editions"] = editions
    return entry


def credits(text):
    """The writers a credits page names, from the text of a PDF's first pages. [] when it names nobody clearly."""
    found = []
    for match in CREDITS_RE.finditer(text or ""):
        for name in re.split(r"\s*(?:,|;|&|\band\b|\n)\s*", match.group(1)):
            name = re.sub(r"\s+", " ", name).strip(" .")
            if NAME_RE.match(name) and name not in found:
                found.append(name)
        if found:
            break  # the first list of writers is the one; later ones are often "additional writing"
    return found[:6]


def junk_author(author):
    """True for an "author" that is a release note, a file's bit of name or a user name, not a person or company."""
    return (not catalogues.author_known(author) or bookfix.username_like(author) or len(author.split()) == 1
            or bool(JUNK_AUTHOR.search(author)))


def _real_author(book, verdict):
    """The entry's author when it is a person (or the publisher), or ""."""
    author = book["author"]
    if junk_author(author):
        return ""
    # "PF 2E" by "Core Rulebook": the library split the file name, so the "author" is part of the title
    if verdict.get("title") and author.lower() in verdict["title"].lower():
        return ""
    return author


def game_entries(con):
    """{game: its gamelines.json entry}, with the ones an admin added on top."""
    entries = {}
    try:
        entries = {entry["tag"]: entry for entry in json.loads(kinds.GAMELINES_FILE.read_text(encoding="utf-8"))["games"]}
    except (OSError, ValueError, KeyError):
        pass
    for row in con.execute("SELECT entry FROM game_lines"):
        try:
            entry = json.loads(row["entry"])
            entries[entry["tag"]] = entry
        except (ValueError, KeyError, TypeError):
            continue
    return entries


def publisher_of(game, lines, entries):
    """Who publishes a game, walking up to its parent game ("Vampire: The Masquerade" is World of Darkness)."""
    seen = set()
    while game and game not in seen:
        seen.add(game)
        if PUBLISHERS.get(game) or (entries.get(game) or {}).get("publisher"):
            return PUBLISHERS.get(game) or entries[game]["publisher"]
        line = next((line for line in lines if line.tag == game), None)
        game = line.parent if line else ""
    return ""


class Review:
    def __init__(self, hub):
        self.hub = hub
        self._lock = threading.Lock()  # one batch or re-sort at a time
        self.job = {"running": False, "what": "", "done": 0, "total": 0, "problems": [], "finished": None}

    # ------------------------------------------------------------ the queue

    def _reviewed(self):
        """Library entries an admin has confirmed or rejected (by entry number, which a rename keeps)."""
        return set(json.loads(db.kv_get(self.hub.con(), "review:done") or "[]"))

    def _mark_reviewed(self, book_ids):
        done = self._reviewed() | {int(book_id) for book_id in book_ids}
        db.kv_set(self.hub.con(), "review:done", json.dumps(sorted(done)))

    def _languages(self):
        """{entry id: [language codes]} straight from the library (Family Hub's copy of it skips languages)."""
        found = {}
        try:
            con = sqlite3.connect("file:%s?mode=ro" % self.hub.library.metadata_db.as_posix(), uri=True, timeout=30)
            try:
                for book, code in con.execute("SELECT l.book, g.lang_code FROM books_languages_link l JOIN languages g ON g.id=l.lang_code"):
                    found.setdefault(book, []).append(code)
            finally:
                con.close()
        except sqlite3.Error:
            pass  # a cut-down library without languages
        return found

    def _verdicts(self):
        """{book key: (verdict, origin)} the sorter left."""
        found = {}
        for row in self.hub.con().execute("SELECT book_key, verdict, origin FROM sort_verdicts"):
            try:
                found[row["book_key"]] = (json.loads(row["verdict"]), row["origin"])
            except ValueError:
                continue
        return found

    def suggestion(self, book, verdict, lines, languages=(), entries=None):
        """What the review form starts with for one entry, from its verdict."""
        tabletop = verdict["kind"] == kinds.TABLETOP
        if entries is None:
            entries = game_entries(self.hub.con())
        publisher = book["publisher"] or (publisher_of(verdict["game"], lines, entries) if tabletop and verdict["game"] else "")
        author = _real_author(book, verdict) if tabletop else book["author"]
        authors = [name.strip() for name in author.split("&") if name.strip()] if author else ([publisher] if publisher else book["authors"])
        if verdict.get("marked_authors"):
            authors = list(verdict["marked_authors"])  # read from where a person marked the author on books like it
        tags = [tag for tag in verdict["tags"] if tag not in verdict["parents"].values() or tag in (verdict["game"], verdict["edition"])]
        extra = [tag for tag in tags if tag not in (verdict["game"], verdict["edition"])]
        language = verdict.get("language") or ""
        title = verdict["title"] or book["title"]
        if verdict["title"] and (junk_author(book["author"]) or COMPANY.search(book["author"])):
            # with no file name to go on the sorter joins title and "author": "ClanBook Baali" by "Moose" is not
            # "ClanBook Baali Moose"
            tail = kinds.clean_title(book["author"])
            if tail and title.lower().endswith(tail.lower()) and title.lower() != tail.lower():
                title = title[:-len(tail)].strip(" -–:") or book["title"]
        return {"title": title, "authors": authors,
                "series": verdict["series"] or book["own_series"],
                "series_index": verdict["series_index"] if verdict["series"] else (book["series_index"] if book["own_series"] else None),
                "publisher": publisher, "tags": extra, "game": verdict["game"], "edition": verdict["edition"],
                "kind": verdict["kind"], "role": verdict["role"], "language": "" if language in languages else language}

    def _changes(self, book, suggest, languages):
        """[{field, now, to}]: what confirming would change in the library entry."""
        changes = []

        def change(field, now, to):
            if to not in ("", None, []) and str(now or "").strip().lower() != str(to).strip().lower():
                changes.append({"field": field, "now": now, "to": to})

        change("title", book["title"], suggest["title"])
        change("authors", book["author"], " & ".join(suggest["authors"]))
        change("series", book["own_series"], suggest["series"])
        if suggest["series"] and suggest["series_index"] is not None:
            now = book["series_index"] if book["own_series"] else None
            if now is None or float(now) != float(suggest["series_index"]):
                changes.append({"field": "series_index", "now": now, "to": suggest["series_index"]})
        change("publisher", book["publisher"], suggest["publisher"])
        have = {tag.lower() for tag in book["raw_tags"]}
        missing = [tag for tag in self._library_tags(suggest) if tag.lower() not in have]
        if missing:
            changes.append({"field": "tags", "now": ", ".join(book["raw_tags"]), "to": ", ".join(missing)})
        if suggest["language"] and suggest["language"] not in languages:
            changes.append({"field": "language", "now": ", ".join(languages), "to": suggest["language"]})
        return changes

    @staticmethod
    def _library_tags(fields):
        """The tags confirming puts on the library entry: the game, edition and role, then any others."""
        found = []
        for tag in [fields.get("game"), fields.get("edition"), fields.get("role") if fields.get("kind") == kinds.TABLETOP else ""] + list(fields.get("tags") or []):
            tag = db.clean_tag((tag or "").replace(",", " "))
            if tag and tag.lower() not in [known.lower() for known in found]:
                found.append(tag)
        return found

    def item(self, book, verdict, origin, lines, languages, shares=1, entries=None, rules=None, kind_now=None, force=False,
             doubts=None):
        """One queue item, or None when there is nothing for a person to look at. A book a learned rule sorted
        is "ready": a person already said what books like it are, so it only waits for its library entry to be
        written (one press for the rule's whole batch), and only while the entry still lacks something."""
        ready = None
        if verdict.get("learned"):
            rules = {rule["id"]: rule for rule in (self.hub.learned_rules() if rules is None else rules)}
            setting = [rule_id for rule_id in verdict["learned"] if (rules.get(rule_id) or {}).get("sets")]
            if not setting and not force:
                return None  # only a "not right": the book is to be left as it is
            ready = setting[-1] if setting else None  # the rules are applied widest first, so the last is the closest fit
        suggest = self.suggestion(book, verdict, lines, languages, entries)
        # a catalogue's likely answer for a book whose title is a file name (identify.py), when it is not a game book
        doubt = (doubts if doubts is not None else self.hub.identify.doubts()).get(book["id"])
        if doubt and verdict["kind"] != kinds.TABLETOP:
            suggest.update(title=doubt["title"], authors=list(doubt["authors"]), publisher=suggest["publisher"] or doubt["publisher"])
        folder, _, _ = kinds.split_origin(origin)
        new_game = new_game_from(folder, lines) if UNKNOWN_GAME in verdict["unsure"] else None
        if new_game:
            suggest.update(game=new_game["game"], edition=new_game["edition"])
        changes = self._changes(book, suggest, languages)
        kind_now = kind_now or verdict["kind"]
        # a book nobody could sort always waits here, clue or not: a person can look inside and say what it is
        nobody_knows = kind_now == kinds.UNSORTED
        if not force and not nobody_knows and (not (changes or verdict["conflicts"] or verdict["unsure"] or new_game) or (ready and not changes)):
            return None
        why = list(verdict["conflicts"]) + [line for line in verdict["unsure"] if not (new_game and line == UNKNOWN_GAME)]
        if doubt and verdict["kind"] != kinds.TABLETOP:
            why.append(doubt["why"])
        if new_game:
            why.append("A game Family Hub does not know yet: %s." % " › ".join(part for part in (new_game["game"], new_game["edition"]) if part))
        batch, batch_folder, pattern = batch_of(origin, book["title"], book["author"])
        return {"book_id": book["id"], "key": book["key"], "media": media_of(kind_now, book["own_formats"]),
                "title": book["shown_title"], "author": book["shown_author"], "formats": sorted(book["own_formats"]),
                "origin": origin, "now": {"title": book["title"], "authors": book["authors"], "series": book["own_series"],
                                          "series_index": book["series_index"] if book["own_series"] else None,
                                          "publisher": book["publisher"], "tags": book["raw_tags"], "language": ", ".join(languages)},
                "suggest": suggest, "changes": changes, "why": why, "new_game": new_game, "shares": shares,
                "batch": batch, "batch_folder": batch_folder, "batch_pattern": pattern, "sources": verdict.get("sources") or {},
                "ready": ready}

    def items(self):
        """Everything waiting for a person: books that share one name first (they share one set of tags until
        they are told apart), then the biggest batches."""
        hub = self.hub
        verdicts, reviewed, languages, kind_of = self._verdicts(), self._reviewed(), self._languages(), hub.kind_index()
        lines, learned, origins, games = hub.game_lines(), hub.learned_rules(), None, game_entries(hub.con())
        doubts = hub.identify.doubts()
        by_key = {}
        for book in hub.library.all_books():
            by_key.setdefault(book["key"], []).append(book)
        found = []
        for key, entries in by_key.items():
            if key not in verdicts:
                continue
            verdict, origin = verdicts[key]
            for book in entries:
                if book["id"] in reviewed or not book["own_formats"]:
                    continue
                book_verdict, book_origin = verdict, origin
                if len(entries) > 1:
                    # the entries share one key and so one saved verdict; each gets its own from its own file name
                    origins = origins or hub._origins()
                    book_verdict, book_origin = hub.verdict_for(book, origins, lines, learned, read_pages=False)
                    if book_origin == origin:
                        book_verdict = verdict  # that's the one the saved verdict (pages and all) is about
                entry = self.item(book, book_verdict, book_origin, lines, languages.get(book["id"], []), len(entries), entries=games,
                                  rules=learned, kind_now=kind_of.get(key), doubts=doubts)
                if entry:
                    found.append(entry)
        sizes = {}
        for entry in found:
            if not entry["ready"]:  # books a rule sorted are written as the rule's batch, not with these
                sizes[entry["batch"]] = sizes.get(entry["batch"], 0) + 1
        for entry in found:
            entry["batch_size"] = sizes.get(entry["batch"], 0) if not entry["ready"] else 0
        found.sort(key=lambda entry: (-(entry["shares"] > 1), -entry["batch_size"], entry["batch"], entry["title"].lower()))
        return found

    def item_for(self, book_id):
        """One library entry as a queue item, whatever the sorter thought of it, for "Edit details" anywhere."""
        hub = self.hub
        book = hub.library.get(book_id)
        if not book:
            raise ReviewError("That book is not in the library any more.")
        lines, learned = hub.game_lines(), hub.learned_rules()
        verdict, origin = hub.verdict_for(book, lines=lines, learned=learned, read_pages=False)
        shares = sum(1 for other in hub.library.all_books() if other["key"] == book["key"])
        entry = self.item(book, verdict, origin, lines, self._languages().get(book["id"], []), shares, entries=game_entries(hub.con()),
                          rules=learned, kind_now=hub.kind_index().get(book["key"]), force=True)
        entry["batch_size"] = 0  # one book on its own: confirming all like it is the queue's job
        entry["ready"] = None
        return entry

    def queue(self, media=None, limit=40):
        """The queue as the page shows it, one tab at a time: how many in each tab, the books learned rules sorted
        grouped by rule ("ready to write"), the first ``limit`` of the rest, and the re-sort's progress."""
        found = self.items()
        counts = {tab: 0 for tab, _ in MEDIA}
        for entry in found:
            counts[entry["media"]] += 1
        if media not in counts:
            media = next((tab for tab, _ in MEDIA if counts[tab]), MEDIA[0][0])
        mine = [entry for entry in found if entry["media"] == media]
        rules = {rule["id"]: rule for rule in self.hub.learned_rules()}
        groups = {}
        for entry in mine:
            if entry["ready"]:
                groups.setdefault(entry["ready"], []).append(entry)
        ready = [{"rule": rule_id, "words": rule_words(rules[rule_id]), "count": len(books), "items": books}
                 for rule_id, books in sorted(groups.items(), key=lambda pair: -len(pair[1])) if rule_id in rules]
        waiting = [entry for entry in mine if not entry["ready"]]
        return {"blocked": self.ready(), "tab": media,
                "tabs": [{"id": tab, "label": label, "count": counts[tab]} for tab, label in MEDIA],
                "ready": ready, "items": waiting[:limit], "more": max(0, len(waiting) - limit),
                "job": self.job, "roles": kinds.ROLES, "kinds": kinds.KINDS,
                "languages": [{"code": code, "name": name} for code, name in sorted(LANGUAGES.items(), key=lambda pair: pair[1])],
                "merges": len(self.merge_suggestions())}

    def find_credits(self, book_id):
        """The writers named on a PDF's first pages, for the author box. Slow-ish, so only when asked."""
        book = self.hub.library.get(book_id)
        if not book:
            raise ReviewError("That book is not in the library any more.")
        path = self.hub.library.file_path(book, "PDF")
        return credits(bookfix.pdf_text(path, pages=4)) if path else []

    def ready(self):
        if not librarymanager.configured(self.hub.config):
            return "The library manager's sign-in is not set up yet. Add it under Admin, Hub, Connections, Library manager."
        return None

    # ------------------------------------------------------------ confirming

    @staticmethod
    def clean_fields(fields):
        """The review form's values, tidied and checked. Raises ReviewError when they won't do."""
        fields = dict(fields or {})
        text = lambda name: re.sub(r"\s+", " ", str(fields.get(name) or "")).strip()  # noqa: E731
        authors = fields.get("authors") or []
        if isinstance(authors, str):
            authors = re.split(r"\s*(?:&|;)\s*", authors)
        tags = fields.get("tags") or []
        if isinstance(tags, str):
            tags = tags.split(",")
        index = fields.get("series_index")
        try:
            index = float(index) if index not in (None, "") else None
        except (TypeError, ValueError):
            raise ReviewError("The series number must be a number.")
        index = int(index) if index is not None and index == int(index) else index
        clean = {"title": text("title"), "authors": [re.sub(r"\s+", " ", str(name)).strip() for name in authors if str(name).strip()],
                 "series": text("series"), "series_index": index, "publisher": text("publisher"),
                 "tags": [db.clean_tag(str(tag)) for tag in tags if db.clean_tag(str(tag))],
                 "game": db.clean_tag(text("game")), "edition": db.clean_tag(text("edition")),
                 "kind": text("kind") or kinds.UNSORTED, "role": text("role"), "language": text("language").lower(),
                 # where on the pages the admin marked the title and author, for the learned rule to read next time
                 "labels": peek.clean_labels(fields.get("labels"))}
        if not clean["title"] or not clean["authors"]:
            raise ReviewError("Give both a title and at least one author.")
        if clean["kind"] not in kinds.KINDS or (clean["role"] and clean["role"] not in kinds.ROLES):
            raise ReviewError("That kind or role is not one Family Hub knows.")
        if clean["kind"] != kinds.TABLETOP:
            clean["role"] = ""
        if clean["language"] and clean["language"] not in LANGUAGES:
            raise ReviewError("That language is not one Family Hub can set.")
        return clean

    def _write(self, book, fields, languages):
        """Ask the library manager to put the confirmed details on one entry. Only what changes is sent."""
        manager = self.hub.manager
        if fields["title"] != book["title"]:
            manager.set_field(book["id"], "title", fields["title"])
        if " & ".join(fields["authors"]) != book["author"]:
            # Calibre splits authors on "&", so an "&" inside a name would make two people out of one
            manager.set_field(book["id"], "authors", " & ".join(name.replace("&", "and") for name in fields["authors"]))
        if fields["series"] != book["own_series"]:
            manager.set_field(book["id"], "series", fields["series"])
        if fields["series"] and fields["series_index"] is not None and (
                fields["series"] != book["own_series"] or float(book["series_index"] or 0) != float(fields["series_index"])):
            manager.set_field(book["id"], "series_index", str(fields["series_index"]))
        if fields["publisher"] and fields["publisher"] != book["publisher"]:
            manager.set_field(book["id"], "publisher", fields["publisher"])
        have = {tag.lower() for tag in book["raw_tags"]}
        new = [tag for tag in self._library_tags(fields) if tag.lower() not in have]
        if new:
            # the library manager replaces the whole list, so the entry's own tags go along with the new ones
            manager.set_field(book["id"], "tags", ", ".join([tag.replace(",", " ") for tag in book["raw_tags"]] + new))
        if fields["language"] and fields["language"] not in languages:
            manager.set_field(book["id"], "languages", LANGUAGES[fields["language"]])

    def _settle(self, book, old_key, fields, profile_id, verdict, origin, rule_id):
        """After the library changed: move Family Hub's records to the book's new name, and put the confirmed
        kind, role and family tags on it."""
        hub, con = self.hub, self.hub.con()
        fixed = hub.library.get(book["id"])
        new_key = fixed["key"]
        others = [other for other in hub.library.all_books() if other["key"] == old_key and other["id"] != book["id"]]
        if new_key != old_key and others:
            # the other entries still go by the old name, so its records are copied, not moved
            hub.ensure_meta(new_key, fixed["title"], fixed["author"])
            con.execute("INSERT OR IGNORE INTO family_tags(book_key, tag, added_by, created) "
                        "SELECT ?, tag, added_by, created FROM family_tags WHERE book_key=?", (new_key, old_key))
        else:
            hub.bookfix.rekey(old_key, new_key, fixed)
            if new_key != old_key:
                con.execute("DELETE FROM sort_verdicts WHERE book_key=?", (old_key,))
        hub.set_kind(new_key, fields["kind"], source="manual", role=fields["role"] or None)
        keep = [tag for tag in (fields["game"], fields["edition"]) + tuple(fields["tags"]) if tag]
        # the sorter's own tags it got wrong come off; tags people added stay
        for tag in set(verdict.get("tags") or []) - set(keep):
            con.execute("DELETE FROM family_tags WHERE book_key=? AND tag=? AND added_by IS NULL", (new_key, tag))
        for tag in keep:
            hub.put_family_tag(new_key, tag, profile_id)  # through the one door, so an admin's tag merges apply
        lines = hub.game_lines()
        if fields["edition"] and fields["game"]:
            con.execute("INSERT OR IGNORE INTO tag_parents(tag, parent) VALUES(?,?)", (fields["edition"], fields["game"]))
        line = next((line for line in lines if line.tag == fields["game"]), None)
        while line and line.parent:
            con.execute("INSERT OR IGNORE INTO tag_parents(tag, parent) VALUES(?,?)", (line.tag, line.parent))
            if line.parent not in keep:
                hub.put_family_tag(new_key, line.parent, profile_id)
            line = next((other for other in lines if other.tag == line.parent), None)
        confirmed = dict(verdict, kind=fields["kind"], role=fields["role"], game=fields["game"], edition=fields["edition"],
                         series=fields["series"], series_index=fields["series_index"], title="", tags=keep,
                         conflicts=[], unsure=[], learned=[rule_id] if rule_id else [])
        con.execute("INSERT INTO sort_verdicts(book_key, verdict, origin, updated) VALUES(?,?,?,?) "
                    "ON CONFLICT(book_key) DO UPDATE SET verdict=excluded.verdict, origin=excluded.origin, updated=excluded.updated",
                    (new_key, json.dumps(confirmed), origin, db.now()))
        con.execute("INSERT INTO duplicate_log(at, book_key, title, action, kept, note) VALUES(?,?,?,?,?,?)",
                    (db.now(), new_key, fixed["title"], "review", book["id"], "was %s by %s" % (book["title"], book["author"])))
        con.commit()
        return fixed

    def _plan(self, book_id):
        """(book, verdict, origin, languages) for one entry, or raises ReviewError."""
        hub = self.hub
        book = hub.library.get(book_id)
        if not book:
            raise ReviewError("That book is not in the library any more.")
        row = hub.con().execute("SELECT verdict, origin FROM sort_verdicts WHERE book_key=?", (book["key"],)).fetchone()
        verdict, origin = (json.loads(row["verdict"]), row["origin"]) if row else (None, "")
        shares = sum(1 for other in hub.library.all_books() if other["key"] == book["key"])
        if verdict is None or shares > 1:
            # entries sharing one name share one saved verdict, so each is sorted again from its own file name
            own, own_origin = hub.verdict_for(book, read_pages=False)
            if verdict is None or own_origin != origin:
                verdict, origin = own, own_origin
        return book, verdict, origin, self._languages().get(book["id"], [])

    def _carry_out(self, plans, profile_id, progress=None):
        """Write each (book_id, fields) to the library, then settle Family Hub's records. One library backup for
        the lot. Returns (fixed books, problems)."""
        blocked = self.ready()
        if blocked:
            raise ReviewError(blocked)
        try:
            self.hub.duplicates.backup()
        except (OSError, sqlite3.Error) as exc:
            raise ReviewError("The library's database could not be copied first, so nothing was changed: %s" % exc)
        written, problems, refused = [], [], 0
        for book_id, fields in plans:
            try:
                book, verdict, origin, languages = self._plan(book_id)
                self._write(book, fields, languages)
                written.append((book, fields, verdict, origin))
                refused = 0
            except ReviewError as exc:
                problems.append(str(exc))
            except librarymanager.ManagerError as exc:
                problems.append("%s: %s" % (fields["title"], exc))
                refused += 1
                if refused >= 3:
                    problems.append("Stopped after three failures in a row with the library manager.")
                    break
            if progress:
                progress()
        self.hub.library.refresh(force=True)
        return written, problems

    def confirm(self, book_id, fields, profile_id=None, new_game=None, plans=None, progress=None, rule_id=None):
        """Confirm one book's details (or, with plans, a batch of [(book_id, fields)] that share the first one's
        rule). Writes to the library, moves Family Hub's records, saves the learned rule and sorts the books it
        covers. With rule_id the books are ones that rule already sorted, so no new rule is learned.
        Returns {"done": [...], "problems": [...], "rule": id}."""
        fields = self.clean_fields(fields)
        plans = plans or [(book_id, fields)]
        written, problems = self._carry_out(plans, profile_id, progress)
        done = []
        learned = rule_id is None
        if written and learned:
            first, _, _, origin = written[0]
            if new_game and new_game.get("game"):
                self.add_game_line(fields["game"] or new_game["game"], fields["edition"] or new_game.get("edition", ""), fields["publisher"])
            rule_id = self.learn(origin, first, fields, confirmations=len(written))
        for book, book_fields, verdict, origin in written:
            fixed = self.hub.library.get(book["id"])
            if not fixed or fixed["title"] != book_fields["title"]:
                problems.append("%s: the library manager did not make the change. Does its account have permission to edit books?" % book_fields["title"])
                continue
            fixed = self._settle(book, book["key"], book_fields, profile_id, verdict, origin, rule_id)
            done.append({"book_id": book["id"], "title": fixed["shown_title"], "author": fixed["shown_author"], "key": fixed["key"]})
        self._mark_reviewed([entry["book_id"] for entry in done])
        if rule_id and learned:
            self.sort_covered(rule_id)  # they stay in the queue as "ready to write" until written too
        self.hub.touch()
        return {"done": done, "problems": problems, "rule": rule_id}

    def batch_plans(self, batch, template_id, fields):
        """[(book_id, fields)] for every queue item in a batch. What the batch shares (kind, role, game, edition,
        publisher, language, tags) comes from the edited item; each book keeps its own title and number, and its
        own author and series unless those were changed on the edited item."""
        fields = self.clean_fields(fields)
        found = [entry for entry in self.items() if entry["batch"] == batch and not entry["ready"]]
        template = next((entry for entry in found if entry["book_id"] == template_id), None)
        if not template:
            raise ReviewError("That book is no longer waiting. Reload the page.")
        plans = []
        for entry in found:
            if entry["book_id"] == template_id:
                plans.append((entry["book_id"], fields))
                continue
            own = dict(entry["suggest"])
            shared = {name: fields[name] for name in ("kind", "role", "game", "edition", "publisher", "language", "tags")}
            if fields["authors"] != template["suggest"]["authors"]:
                shared["authors"] = fields["authors"]
            if fields["series"] != template["suggest"]["series"]:
                shared["series"] = fields["series"]
            own.update(shared)
            if fields["labels"]:
                # each book's own title and author, read from the places marked on the edited one
                other = self.hub.library.get(entry["book_id"])
                path = self.hub.library.file_path(other, "PDF") if other else None
                found = peek.read_labels(path, fields["labels"]) if path else {}
                if peek.as_title(found.get("title", "")):
                    own["title"] = peek.as_title(found["title"])
                if peek.as_authors(found.get("authors", "")):
                    own["authors"] = peek.as_authors(found["authors"])
            try:
                plans.append((entry["book_id"], self.clean_fields(own)))
            except ReviewError:
                continue  # one with no title to go on waits for a person
        return plans

    def start_batch(self, app, batch, template_id, fields, profile_id, new_game=None):
        """Confirm a whole batch in the background. False when something else is running."""
        return self._start(app, self.batch_plans(batch, template_id, fields), template_id, fields, profile_id, new_game)

    def ready_plans(self, rule_id):
        """[(book_id, fields)] for the books a learned rule sorted whose library entries still lack something."""
        plans = []
        for entry in self.items():
            if entry["ready"] == rule_id:
                try:
                    plans.append((entry["book_id"], self.clean_fields(entry["suggest"])))
                except ReviewError:
                    continue  # one with no title to go on waits for a person
        return plans

    def start_ready(self, app, rule_id, profile_id):
        """Write every "ready" book of one rule to the library in the background, after one backup. False when
        something else is running."""
        plans = self.ready_plans(rule_id)
        if not plans:
            raise ReviewError("Nothing from that rule is waiting to be written. Reload the page.")
        return self._start(app, plans, plans[0][0], plans[0][1], profile_id, rule_id=rule_id)

    def _start(self, app, plans, template_id, fields, profile_id, new_game=None, rule_id=None):
        if self.ready():
            raise ReviewError(self.ready())
        if not self._lock.acquire(blocking=False):
            return False
        self.job = {"running": True, "what": "batch", "done": 0, "total": len(plans), "problems": [], "finished": None}

        def step():
            self.job["done"] += 1

        def work():
            try:
                with app.app_context():
                    try:
                        result = self.confirm(template_id, fields, profile_id, new_game, plans=plans, progress=step, rule_id=rule_id)
                        self.job["problems"] = result["problems"]
                        self.job["confirmed"] = len(result["done"])
                    except ReviewError as exc:
                        self.job["problems"].append(str(exc))
            except Exception as exc:  # the page must never show a batch running for ever
                self.job["problems"].append("The batch stopped: %s" % exc)
            finally:
                self.job["running"] = False
                self.job["finished"] = db.now()
                self._lock.release()

        threading.Thread(target=work, name="bookhub-review", daemon=True).start()
        return True

    def reject(self, book_id, profile_id=None):
        """Say the suggestion for one book is wrong. The library is not touched; a rule remembers what not to
        suggest for this file again, and the book leaves the queue."""
        book, verdict, origin, languages = self._plan(book_id)
        suggest = self.suggestion(book, verdict, self.hub.game_lines(), languages)
        rejects = {}
        for change in self._changes(book, suggest, languages):
            if change["field"] in ("title", "series"):
                rejects[change["field"]] = suggest[change["field"]]
        for field in ("game", "edition", "role"):
            if suggest[field]:
                rejects[field] = suggest[field]
        folder, _, _ = kinds.split_origin(origin)
        # a rejection is about this one file, so its rule is: this folder, exactly this name
        rule_id = self.save_rule(folder, name_pattern(_name(origin, book["title"], book["author"]), exact=True), {}, rejects,
                                 note="Rejected for %s" % book["title"])
        self._mark_reviewed([book["id"]])
        self.sort_covered(rule_id)
        return {"rule": rule_id}

    # ------------------------------------------------------------ learned rules

    def learn(self, origin, book, fields, confirmations=1):
        """Save (or strengthen) the rule a confirmation teaches: books arriving in the same folder, or named
        like this one when no folder is known, are what this one is."""
        folder, _, _ = kinds.split_origin(origin)
        pattern = "" if folder else name_pattern(_name(origin, book["title"], book["author"]))
        sets = {"kind": fields["kind"]}
        for name in ("role", "game", "edition"):
            if fields[name]:
                sets[name] = fields[name]
        # a series is only shared by a folder's books when the folder names it (no number from the file name)
        if fields["series"] and folder and fields["series_index"] is None:
            sets["series"] = fields["series"]
        if fields["tags"]:
            sets["tags"] = fields["tags"]
        if fields.get("labels"):
            sets["labels"] = fields["labels"]
        return self.save_rule(folder, pattern, sets, {}, confirmations, note="Confirmed on %s" % fields["title"])

    def save_rule(self, folder, pattern, sets, rejects, confirmations=1, note=""):
        """Add a rule, or add to the one already saved for the same folder and name pattern. Returns its id."""
        con = self.hub.con()
        row = con.execute("SELECT * FROM learned_rules WHERE lower(folder)=lower(?) AND pattern=?", (folder, pattern)).fetchone()
        if row:
            old_sets, old_rejects = json.loads(row["sets"] or "{}"), json.loads(row["rejects"] or "{}")
            old_sets.update(sets)
            old_rejects.update(rejects)
            for field in sets:  # a value confirmed now is no longer one never to suggest
                if old_rejects.get(field) == sets[field]:
                    old_rejects.pop(field)
            con.execute("UPDATE learned_rules SET sets=?, rejects=?, confirmations=confirmations+?, updated=? WHERE id=?",
                        (json.dumps(old_sets), json.dumps(old_rejects), confirmations, db.now(), row["id"]))
            con.commit()
            return row["id"]
        cur = con.execute("INSERT INTO learned_rules(folder, pattern, sets, rejects, confirmations, note, created, updated) "
                          "VALUES(?,?,?,?,?,?,?,?)", (folder, pattern, json.dumps(sets), json.dumps(rejects), confirmations, note,
                                                      db.now(), db.now()))
        con.commit()
        return cur.lastrowid

    def delete_rule(self, rule_id):
        con = self.hub.con()
        gone = con.execute("DELETE FROM learned_rules WHERE id=?", (rule_id,)).rowcount
        con.commit()
        return bool(gone)

    def _books_with_origins(self):
        """[(book, folder, name)] for every library book, with the folder it arrived in."""
        verdicts = self._verdicts()
        found = []
        for book in self.hub.library.books():
            origin = verdicts.get(book["key"], (None, ""))[1]
            folder, _, _ = kinds.split_origin(origin)
            found.append((book, folder, _name(origin, book["title"], book["author"])))
        return found

    def covered(self, rule, books=None):
        """The library books a rule matches."""
        books = self._books_with_origins() if books is None else books
        return [book for book, folder, name in books if kinds._matches(rule, folder, name)]

    def sort_covered(self, rule_id):
        """Sort again the books a rule covers (not those a person chose a kind for), so they leave the queue."""
        rule = next((rule for rule in self.hub.learned_rules() if rule["id"] == rule_id), None)
        if not rule:
            return 0
        chosen = {row["book_key"] for row in self.hub.con().execute("SELECT book_key FROM book_kind WHERE source!='auto'")}
        books = [book for book in self.covered(rule) if book["key"] not in chosen and book["formats"]]
        return self.hub._sort(books, read_pages=True)

    def rules(self):
        """Every learned rule in plain words, with the books it matches."""
        books = self._books_with_origins()
        found = []
        for rule, row in zip(self.hub.learned_rules(), self._rule_rows()):
            matched = self.covered(rule, books)
            found.append(dict(rule, words=rule_words(rule), confirmations=row["confirmations"], note=row["note"],
                              created=row["created"], matches=len(matched),
                              examples=[book["shown_title"] for book in matched[:6]]))
        return {"rules": found, "merges": self.merge_suggestions(books)}

    def _rule_rows(self):
        rows = []
        for row in self.hub.con().execute("SELECT * FROM learned_rules ORDER BY id"):
            try:
                json.loads(row["sets"] or "{}"), json.loads(row["rejects"] or "{}")
            except ValueError:
                continue  # learned_rules() skips these too, so the two lists stay in step
            rows.append(row)
        return rows

    @staticmethod
    def _shape(rule):
        """What a rule looks like with its numbers taken out: rules with the same shape say the same thing."""
        numberless = lambda text: re.sub(r"\\d\+|\d+", "#", text or "").lower()  # noqa: E731
        return (numberless(rule.get("folder")).rsplit("/", 1)[0] if rule.get("folder") else "", numberless(rule.get("pattern")),
                numberless(json.dumps({field: value for field, value in sorted((rule.get("sets") or {}).items())})))

    def combined(self, rules):
        """One rule that covers several: the folders' shared part, the name with its numbers left open, and only
        what every rule agrees on. Returns (rule, fields left out) or None when they have nothing in common."""
        folders = [rule.get("folder") or "" for rule in rules]
        if len(set(folder.lower() for folder in folders)) == 1:
            folder = folders[0]
        else:
            split = [folder.split("/") for folder in folders]
            common = []
            for parts in zip(*split):
                if len({part.lower() for part in parts}) != 1:
                    break
                common.append(parts[0])
            folder = "/".join(common)
            if not folder:
                return None
        patterns = {rule.get("pattern") or "" for rule in rules}
        pattern = patterns.pop() if len(patterns) == 1 else re.sub(r"(?<!\\)\d+", r"\\d+", (rules[0].get("pattern") or ""))
        if not folder and not pattern:
            return None
        sets, left_out = {}, []
        for field in sorted({field for rule in rules for field in (rule.get("sets") or {})}):
            values = [json.dumps((rule.get("sets") or {}).get(field)) for rule in rules]
            if len(set(values)) == 1:
                sets[field] = (rules[0].get("sets") or {})[field]
            else:
                left_out.append(field)
        return {"folder": folder, "pattern": pattern, "sets": sets, "rejects": {}}, left_out

    def merge_suggestions(self, books=None):
        """Groups of rules that differ only by a number, each with the one rule that would replace them, what it
        would match, and what would change. Only suggestions: merge() does it, when an admin says so."""
        groups = {}
        for rule in self.hub.learned_rules():
            if rule.get("rejects"):
                continue  # a rejection is about one file; it is never folded into a wider rule
            groups.setdefault(self._shape(rule), []).append(rule)
        found = []
        for rules in groups.values():
            if len(rules) < 2:
                continue
            made = self.combined(rules)
            if not made:
                continue
            rule, left_out = made
            if books is None:
                books = self._books_with_origins()
            before = {book["id"] for old in rules for book in self.covered(old, books)}
            after = self.covered(rule, books)
            newly = [book["shown_title"] for book in after if book["id"] not in before]
            found.append({"ids": [old["id"] for old in rules], "words": rule_words(rule), "rule": rule,
                          "replaces": [rule_words(old) for old in rules], "matches": len(after),
                          "examples": [book["shown_title"] for book in after[:6]], "newly": newly[:12], "newly_count": len(newly),
                          "left_out": left_out})
        return found

    def merge(self, ids, profile_id=None):
        """Replace several rules with the one merge_suggestions() offered for them."""
        ids = sorted({int(rule_id) for rule_id in ids})
        rules = [rule for rule in self.hub.learned_rules() if rule["id"] in ids]
        if len(rules) < 2 or len(rules) != len(ids):
            raise ReviewError("Those rules have changed since the page was loaded. Reload it.")
        made = self.combined(rules)
        if not made:
            raise ReviewError("Those rules have nothing in common to combine.")
        rule, _ = made
        con = self.hub.con()
        confirmations = sum(row["confirmations"] for row in self._rule_rows() if row["id"] in ids)
        con.executemany("DELETE FROM learned_rules WHERE id=?", [(rule_id,) for rule_id in ids])
        con.commit()
        rule_id = self.save_rule(rule["folder"], rule["pattern"], rule["sets"], {}, confirmations,
                                 note="Combined from %d rules" % len(ids))
        self.sort_covered(rule_id)
        return rule_id

    # ------------------------------------------------------------ new game systems

    def add_game_line(self, game, edition="", publisher=""):
        """Teach the sorter a game (and edition) an admin confirmed. Later books in its folder sort themselves."""
        game = db.clean_tag(game)
        if not game:
            return False
        con = self.hub.con()
        row = con.execute("SELECT entry FROM game_lines WHERE tag=?", (game,)).fetchone()
        entry = game_line_entry(game, db.clean_tag(edition), publisher if publisher and publisher != PUBLISHERS.get(game) else "",
                                json.loads(row["entry"]) if row else None)
        con.execute("INSERT INTO game_lines(tag, entry, created) VALUES(?,?,?) ON CONFLICT(tag) DO UPDATE SET entry=excluded.entry",
                    (game, json.dumps(entry), db.now()))
        con.commit()
        return True

    # ------------------------------------------------------------ the full re-sort

    def resort(self, progress=None):
        """Sort the whole library again (hub.resort_books) and return how many books were looked at."""
        return self.hub.resort_books(progress=progress)

    def start_resort(self, app):
        """Sort the whole library again in the background, reading PDFs' first pages. False when busy."""
        if not self._lock.acquire(blocking=False):
            return False
        self.job = {"running": True, "what": "resort", "done": 0, "total": 0, "problems": [], "finished": None}

        def step(done, total):
            self.job["done"], self.job["total"] = done, total

        def work():
            try:
                with app.app_context():
                    self.resort(progress=step)
            except Exception as exc:  # the page must never show a re-sort running for ever
                self.job["problems"].append("The re-sort stopped: %s" % exc)
            finally:
                self.job["running"] = False
                self.job["finished"] = db.now()
                self._lock.release()

        threading.Thread(target=work, name="bookhub-resort", daemon=True).start()
        return True
