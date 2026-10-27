"""Community tags from public catalogues: Open Library, Wikidata, Google Books and Comic Vine.

Each book is looked up once per catalogue, by ISBN when it has one and by
title and author otherwise. A match is only accepted when the title and the
author both agree, so a book with scrambled details gets no tags rather than
somebody else's. What is sent out is the title, author and ISBN; nothing
about the family or what they read.

Comics are matched differently. A comic file is named after its series
("Star Trek (Gold Key) 07") and has no author worth the name, so Comic Vine is
asked for the series, and the publisher or year in brackets settles which one.
"""
import re
import threading
import time
from datetime import datetime, timedelta, timezone

import requests

from . import db, kinds, tags

TIMEOUT = 20
AGENT = {"User-Agent": "BookHub/1.0 (self-hosted family library; tag lookup)"}
PER_SOURCE = 10          # tags kept per book from one catalogue
RETRY_NONE_DAYS = 30     # look again for books a catalogue did not know
RETRY_ERROR_HOURS = 1
PAUSE = 1.0              # seconds between books, to stay a polite guest
GAP = {"www.wikidata.org": 2.0, "openlibrary.org": 0.5, "comicvine.gamespot.com": 2.0}  # seconds between two questions to the same site
REST_MINUTES = 15        # leave a catalogue alone this long after it says "slow down"

LABELS = {"openlibrary": "Open Library", "wikidata": "Wikidata", "googlebooks": "Google Books", "comicvine": "Comic Vine"}
NEEDS_KEY = {"googlebooks": "google_books_key", "comicvine": "comic_vine_key"}  # catalogue -> the setting holding its key
NO_AUTHOR = {"", "unknown", "anonymous", "various", "admin", "user", "owner"}


class LookupError_(Exception):
    """A catalogue could not be reached or refused the request."""


class SlowDown(LookupError_):
    """The catalogue is answering, but wants fewer questions."""


def label(source):
    if source.startswith("pack:"):
        return "tag pack " + source[5:]
    return LABELS.get(source, source)


# --------------------------------------------------------------- matching

def _words(text):
    return [word for word in db.slug((text or "").replace("&", " and ")).split("-") if word and word != "and"]


def query_title(title):
    """A title as a catalogue would write it: no file ending, brackets or underscores."""
    title = re.sub(r"\.(pdf|epub|mobi|azw3?|docx?|cb[zr])$", "", title or "", flags=re.I)
    title = re.sub(r"[\[(][^\])]*[\])]", " ", title.replace("_", " "))
    return re.sub(r"\s+", " ", title).strip(" -:")


def author_known(author):
    """False for the placeholders and user names that files carry instead of an author."""
    words = _words(author)
    if not words or (author or "").strip().lower() in NO_AUTHOR:
        return False
    # one short word ("elenak", "admin") smells like a username. known casualty: Homer. sorry Homer
    return len(words) > 1 or len(words[0]) > 5


def same_title(title, their_title):
    """The same title, with or without a subtitle on either side."""
    ours, theirs = _words(query_title(title)), _words(their_title)
    if not ours or not theirs:
        return False
    main = _words(query_title(title).split(":")[0])
    short = _words((their_title or "").split(":")[0])
    return ours == theirs or main == theirs or ours == short or main == short


def same_book(title, author, their_title, their_authors):
    """Do a catalogue entry's title and authors agree with ours?"""
    ours, theirs = _words(query_title(title)), _words(their_title)
    if not ours or not theirs:
        return False
    their_names = {word for name in their_authors or [] for word in _words(name)}
    title_ok = same_title(title, their_title)
    if author_known(author):
        mine = {word for word in _words(author) if len(word) > 2}
        return title_ok and bool(mine & their_names)
    # no usable author, so we get picky: either the author's name is tacked on the end of our title
    # ("Title, Author Name"), or it's a long title (4+ words) matching exactly. short titles are too risky
    if len(theirs) >= 2 and ours[:len(theirs)] == theirs and ours[len(theirs):] and set(ours[len(theirs):]) <= their_names:
        return True
    return title_ok and len(theirs) >= 4


def attempts(book):
    """The (title, author) pairs worth asking about, most exact first."""
    title, author = query_title(book["title"]), book["author"]
    tries = [(title, author)]
    if ":" in title:
        tries.append((title.split(":")[0].strip(), author))  # without ": A Novel"
    if not author_known(author) and "," in title:
        head, _, tail = title.rpartition(",")
        if 1 < len(_words(tail)) < 4 and _words(head):
            tries.append((head.strip(), tail.strip()))  # "Title, Author Name"
    return tries


_last_asked = {}


def _get(url, params, headers=None):
    host = url.split("/")[2]
    # be a polite guest: keep a per-site gap between questions so nobody rate-limits us into the ground
    wait = _last_asked.get(host, 0) + GAP.get(host, 0) - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    try:
        reply = requests.get(url, params=params, headers=dict(AGENT, **(headers or {})), timeout=TIMEOUT)
        _last_asked[host] = time.monotonic()
        if reply.status_code in (420, 429):
            raise SlowDown("it asked for fewer questions; trying again later")
        reply.raise_for_status()
        return reply.json()
    except (requests.RequestException, ValueError) as exc:
        # requests helpfully puts the WHOLE url in its errors, API key and all. scrub it before it hits a log
        text = str(exc)
        for name in ("key", "api_key"):
            secret = str((params or {}).get(name) or "")
            if secret:
                text = text.replace(secret, "(key)").replace(requests.utils.quote(secret, safe=""), "(key)")
        raise LookupError_(text[:200])


# ----------------------------------------------------------- the catalogues

def open_library_doc(book, fields):
    """Open Library's record of a book, with the fields asked for, or None when it has no such book."""
    url = "https://openlibrary.org/search.json"
    docs = []
    if book["isbn"]:
        docs = _get(url, {"q": "isbn:" + book["isbn"], "fields": fields, "limit": 3}).get("docs") or []
        if docs:  # an ISBN is proof enough
            return docs[0]
    for title, author in attempts(book):
        if author_known(author):
            params = {"title": title, "author": re.split(r"\s*&\s*", author)[0], "fields": fields, "limit": 5}
        else:
            params = {"q": title, "fields": fields, "limit": 5}
        for doc in _get(url, params).get("docs") or []:
            if same_book(title, author, doc.get("title"), doc.get("author_name")):
                return doc
    return None


def open_library(config, book):
    doc = open_library_doc(book, "key,title,author_name,subject")
    return None if doc is None else (doc.get("subject") or [])


WIKIDATA = "https://www.wikidata.org/w/api.php"


def _claim_ids(entity, prop):
    ids = []
    # wikidata's JSON is nested like a russian doll: claims -> mainsnak -> datavalue -> value -> id
    for claim in (entity.get("claims") or {}).get(prop, []):
        value = ((claim.get("mainsnak") or {}).get("datavalue") or {}).get("value")
        if isinstance(value, dict) and value.get("id"):
            ids.append(value["id"])
    return ids


def wikidata(config, book):
    """Genre (P136) and main subject (P921) of the work whose author (P50) matches."""
    title, author = attempts(book)[-1]  # the shortest form of the title is how Wikidata labels a work
    if not author_known(author):
        return None  # too many films and songs share a title to go by title alone
    found = _get(WIKIDATA, {"action": "wbsearchentities", "search": title, "language": "en", "type": "item",
                            "limit": 5, "format": "json"}).get("search") or []
    hits = [hit for hit in found if same_title(title, hit.get("label"))]
    if not hits:
        return None
    entities = _get(WIKIDATA, {"action": "wbgetentities", "ids": "|".join(hit["id"] for hit in hits),
                               "props": "claims", "format": "json"}).get("entities") or {}
    wanted = set()
    for entity in entities.values():
        for prop in ("P50", "P136", "P921"):
            wanted.update(_claim_ids(entity, prop))
    if not wanted:
        return None
    names = {}
    ids = sorted(wanted)
    for start in range(0, len(ids), 50):  # wikidata takes 50 ids per question, max
        got = _get(WIKIDATA, {"action": "wbgetentities", "ids": "|".join(ids[start:start + 50]), "props": "labels",
                              "languages": "en", "format": "json"}).get("entities") or {}
        for qid, entity in got.items():
            names[qid] = ((entity.get("labels") or {}).get("en") or {}).get("value") or ""
    for hit in hits:
        entity = entities.get(hit["id"]) or {}
        authors = [names.get(qid, "") for qid in _claim_ids(entity, "P50")]
        if authors and same_book(title, author, hit.get("label"), authors):
            return [names.get(qid, "") for qid in _claim_ids(entity, "P136") + _claim_ids(entity, "P921")]
    return None


GOOGLE = "https://www.googleapis.com/books/v1/volumes"


def google_books(config, book):
    """Needs a (free) API key; Google no longer answers without one."""
    info = google_books_info(config, book)
    return None if info is None else (info.get("categories") or [])


def google_books_info(config, book, full=True):
    """Google's record of a book (its volumeInfo), or None when it has no such book."""
    key = config.google_books_key
    title, author = attempts(book)[-1]
    if book["isbn"]:
        query = "isbn:" + book["isbn"]
    else:
        query = 'intitle:"%s"' % title
        if author_known(author):
            query += ' inauthor:"%s"' % re.split(r"\s*&\s*", author)[0]
    items = _get(GOOGLE, {"q": query, "maxResults": 5, "key": key}).get("items") or []
    for item in items:
        info = item.get("volumeInfo") or {}
        if book["isbn"] or same_book(title, author, info.get("title"), info.get("authors")):
            if not full:
                return info
            # the search result is stingy (top category only), the volume page has the whole list. one more trip
            return dict(info, **(_get(GOOGLE + "/" + item["id"], {"key": key}).get("volumeInfo") or {}))
    return None


COMIC_VINE = "https://comicvine.gamespot.com/api"
COMIC_TAGS = 4  # most-seen characters and themes kept from a series
_volumes = {}   # (series, hint) -> tags, so forty issues of one series cost one lookup


def comic_series(title):
    """("Star Trek: Deep Space Nine", "Malibu") from "Star Trek - Deep Space Nine (Malibu) 23 - Lost Orb 01".

    The second value is the first thing in brackets: a publisher or a year.
    """
    title = re.sub(r"\.(cb[zr7c]|pdf|epub)$", "", (title or "").replace("_", " "), flags=re.I)
    bracket = re.search(r"[\[(]([^\])]*)[\])]", title)
    name = title[:bracket.start()] if bracket else title
    if not bracket:
        name = re.split(r"\s+#?\d{1,4}\s+-\s+", name + " ")[0]  # "Saga 012 - Chapter Twelve"
    name = re.sub(r"(\s+|^)(#|v(ol(ume)?)?\.?\s*)?\d{1,4}\s*$", "", name.strip(" -"), flags=re.I)  # the issue number
    name = re.sub(r"\s+v(ol(ume)?)?\.?\s*\d{1,2}$", "", name, flags=re.I)  # "Batman v2"
    name = re.sub(r"\s+-\s+", ": ", name).strip(" -:")
    return name, (bracket.group(1).strip() if bracket else "")


def _comic_vine_get(path, key, params):
    data = _get(COMIC_VINE + path, dict(params, api_key=key, format="json"))
    code = data.get("status_code")
    if code == 107:
        raise SlowDown("it asked for fewer questions; trying again later")
    if code == 100:
        raise LookupError_("it refused the API key")
    if code != 1:
        raise LookupError_(str(data.get("error") or "unexpected answer")[:200])
    return data.get("results") or []


def comic_vine(config, book):
    """The publisher, main characters and themes of the series a comic belongs to."""
    key = config.comic_vine_key
    name, hint = comic_series(book.get("series") or book["title"])
    if not _words(name):
        return None
    cached = (" ".join(_words(name)), hint.lower())
    if cached in _volumes:
        return _volumes[cached]
    found = _comic_vine_get("/search/", key, {
        "resources": "volume", "query": name, "limit": 20,
        "field_list": "id,name,publisher,start_year,count_of_issues"})
    same = [volume for volume in found if _words(volume.get("name")) == _words(name)]
    if hint:  # "(Malibu)" or "(1993)" in the file name picks between runs with the same name
        wanted = set(_words(hint))
        same = [volume for volume in same if str(volume.get("start_year") or "") == hint
                or wanted <= set(_words((volume.get("publisher") or {}).get("name")))]
    if not same:
        _volumes[cached] = None
        return None
    volume = max(same, key=lambda volume: int(volume.get("count_of_issues") or 0))  # the longest run is usually The One
    detail = _comic_vine_get("/volume/4050-%s/" % volume["id"], key, {"field_list": "publisher,characters,concepts"})
    if isinstance(detail, list):  # comic vine sends an empty answer as [] instead of {}. because of course it does
        detail = {}

    def top(items):
        ranked = sorted(items or [], key=lambda item: -int(item.get("count") or 0))
        return [item.get("name") or "" for item in ranked[:COMIC_TAGS]]

    publisher = (detail.get("publisher") or volume.get("publisher") or {}).get("name") or ""
    _volumes[cached] = [tag for tag in [publisher] + top(detail.get("concepts")) + top(detail.get("characters")) if tag]
    return _volumes[cached]


SOURCES = [("openlibrary", open_library), ("wikidata", wikidata), ("googlebooks", google_books), ("comicvine", comic_vine)]
COMIC_FORMATS = kinds.COMIC_FORMATS
TEST_BOOKS = {
    "comicvine": {"title": "Watchmen 01", "author": "", "authors": [], "isbn": "", "series": ""},
    None: {"title": "Pride and Prejudice", "author": "Jane Austen", "authors": ["Jane Austen"], "isbn": "", "series": ""},
}


def active(config):
    """The catalogues that can be used with the current settings."""
    if config.tag_lookups != "on":
        return []
    return [name for name, _ in SOURCES if name not in NEEDS_KEY or getattr(config, NEEDS_KEY[name])]


def suits(source, book, kind):
    """Comic Vine is only asked about comics. The others are asked about everything."""
    if source != "comicvine":
        return True
    return kind == kinds.GRAPHIC or bool(set(book["formats"]) & COMIC_FORMATS)


# ------------------------------------------------------------- the lookups

class Catalogues:
    def __init__(self, hub):
        self.hub = hub
        self._lock = threading.Lock()
        self.running = False
        self._resting = {}  # catalogue -> when it may be asked again

    def _due(self, book_key, source, rows):
        row = rows.get((book_key, source))
        if not row:
            return True
        age = datetime.now(timezone.utc) - datetime.strptime(row["at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        if row["status"] == "error":
            return age > timedelta(hours=RETRY_ERROR_HOURS)
        return row["status"] == "none" and age > timedelta(days=RETRY_NONE_DAYS)

    def _awake(self):
        return [source for source in active(self.hub.config) if self._resting.get(source, 0) < time.monotonic()]

    def pending(self):
        """Library books that still have a catalogue to ask."""
        sources = self._awake()
        if not sources:
            return []
        rows = {(row["book_key"], row["source"]): row for row in self.hub.con().execute("SELECT * FROM tag_lookups")}
        kind_of = self.hub.kind_index()
        return [book for book in self.hub.library.books()
                if any(suits(source, book, kind_of.get(book["key"])) and self._due(book["key"], source, rows) for source in sources)]

    def look_up(self, book, force=False):
        """Ask each catalogue about one book. Returns {source: number of tags added}."""
        hub, con = self.hub, self.hub.con()
        rows = {(row["book_key"], row["source"]): row for row in con.execute("SELECT * FROM tag_lookups WHERE book_key=?", (book["key"],))}
        skip = [book["title"], book["author"], book.get("publisher", "")] + list(book["authors"])
        result = {}
        kind = hub.kind_index().get(book["key"])
        for source, fetch in SOURCES:
            if source not in self._awake() or not (force or self._due(book["key"], source, rows)):
                continue
            if not suits(source, book, kind):
                continue
            try:
                raw = fetch(hub.config, book)
            except LookupError_ as exc:
                status, found, note = "error", [], str(exc)
                if isinstance(exc, SlowDown):
                    self._resting[source] = time.monotonic() + REST_MINUTES * 60
            else:
                found = tags.tidy(raw or [], skip=skip, limit=PER_SOURCE, plain=True)
                status, note = ("found" if found else "none"), ""
            if status != "error":  # fresh answer wipes the old one. an error keeps what we had (better than nothing)
                con.execute("DELETE FROM community_tags WHERE book_key=? AND source=?", (book["key"], source))
            hub.ensure_meta(book["key"])
            for tag in found:
                con.execute("INSERT OR IGNORE INTO community_tags(book_key, tag, source) VALUES(?,?,?)",
                            (book["key"], tag, source))
            con.execute(
                "INSERT INTO tag_lookups(book_key, source, status, note, at) VALUES(?,?,?,?,?) "
                "ON CONFLICT(book_key, source) DO UPDATE SET status=excluded.status, note=excluded.note, at=excluded.at",
                (book["key"], source, status, note, db.now()))
            result[source] = len(found) if status != "error" else None
        con.commit()
        if any(result.values()):
            self._guess_kind(book)
            hub.touch()
        return result

    def _guess_kind(self, book):
        """A book nobody has sorted may now have tags that say what it is."""
        con = self.hub.con()
        row = con.execute("SELECT kind, source FROM book_kind WHERE book_key=?", (book["key"],)).fetchone()
        if row and (row["source"] != "auto" or row["kind"] != kinds.UNSORTED):
            return
        found = [row["tag"] for row in con.execute("SELECT tag FROM community_tags WHERE book_key=?", (book["key"],))]
        kind, _ = kinds.guess(book["title"], book["author"], book["series"], book["tags"] + found, book["formats"])
        if kind != kinds.UNSORTED:
            self.hub.set_kind(book["key"], kind, source="auto")
            con.commit()

    def run(self, limit=None):
        """Work through books that are due. Returns how many were asked about."""
        if not self._lock.acquire(blocking=False):
            return 0
        self.running = True
        try:
            done = 0
            for book in self.pending()[:limit]:
                self.look_up(book)
                done += 1
                time.sleep(PAUSE)
            return done
        finally:
            self.running = False
            self._lock.release()

    def start(self, app):
        """Look everything up now, in the background."""
        if self.running:
            return False

        def work():
            with app.app_context():
                try:
                    self.run()
                except Exception as exc:
                    app.logger.warning("tag lookup stopped: %s", exc)
        threading.Thread(target=work, name="bookhub-tags", daemon=True).start()
        return True

    def progress(self):
        con = self.hub.con()
        keys = set(self.hub.library.keys())
        sources = active(self.hub.config)
        asked, matched, errors = set(), set(), 0
        for row in con.execute("SELECT book_key, source, status FROM tag_lookups"):
            if row["book_key"] not in keys:
                continue
            asked.add(row["book_key"])
            if row["status"] == "found":
                matched.add(row["book_key"])
            errors += row["status"] == "error"  # True counts as 1. sneaky but handy
        return {
            "on": self.hub.config.tag_lookups == "on", "running": self.running,
            "sources": [label(source) for source in sources], "books": len(keys),
            "asked": len(asked), "matched": len(matched), "errors": errors, "waiting": len(self.pending()),
        }

    def test(self, config):
        """One real question to each catalogue, for the Test button."""
        notes, ok = [], True
        for source, fetch in SOURCES:
            if source in NEEDS_KEY and not getattr(config, NEEDS_KEY[source]):
                notes.append("%s: no key entered, not used" % label(source))
                continue
            try:
                found = fetch(config, TEST_BOOKS.get(source, TEST_BOOKS[None]))
                notes.append("%s: %s" % (label(source), "answered" if found else "answered, but did not know the test book"))
            except LookupError_ as exc:
                ok = False
                notes.append("%s: could not be reached (%s)" % (label(source), exc))
        return {"ok": ok, "message": ". ".join(notes) + "."}


HARDCOVER = "https://api.hardcover.app/v1/graphql"


def hardcover_info(config, book):
    """Hardcover's record of a book, or None. EXPERIMENTAL: written from Hardcover's published API notes and
    not yet tried with a real key."""
    token = (config.hardcover_key or "").strip()
    if not token.lower().startswith("bearer "):
        token = "Bearer " + token
    title, author = attempts(book)[-1]
    query = 'query($q: String!) { search(query: $q, query_type: "Book", per_page: 5, page: 1) { results } }'
    wanted = title + (" " + author if author_known(author) else "")
    now = time.monotonic()
    wait = _last_asked.get("hardcover", 0) + 1.5 - now
    if wait > 0:
        time.sleep(wait)
    _last_asked["hardcover"] = time.monotonic()
    try:
        reply = requests.post(HARDCOVER, json={"query": query, "variables": {"q": wanted}},
                              headers=dict(AGENT, authorization=token), timeout=TIMEOUT)
        if reply.status_code == 429:
            raise SlowDown("Hardcover asked us to slow down")
        if reply.status_code in (401, 403):
            raise LookupError_("it refused the API key")
        reply.raise_for_status()
        payload = reply.json()
    except (requests.RequestException, ValueError) as exc:
        raise LookupError_(str(exc))
    results = ((payload.get("data") or {}).get("search") or {}).get("results") or {}
    for hit in results.get("hits") or []:
        doc = hit.get("document") or {}
        if same_book(title, author, doc.get("title"), doc.get("author_names")):
            return doc
    return None
