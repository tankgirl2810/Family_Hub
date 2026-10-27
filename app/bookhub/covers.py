"""Covers chosen in Family Hub.

A book's cover normally comes from the library. Here a person can replace it
with a picture they upload, one picked from Open Library, or a plain cover
made from the title. The chosen cover is kept in Family Hub's own data folder;
the library and the book file are never changed.
"""
import hashlib
import io
import textwrap

import requests
from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError

from . import catalogues, db

MAX_BYTES = 15 * 1024 * 1024
MAX_SIZE = (900, 1350)          # big enough for a tablet, small enough to load quickly
GENERATED = (600, 900)
MAX_OPTIONS = 12
TIMEOUT = 20
AGENT = {"User-Agent": "BookHub/1.0 (self-hosted family library; covers)"}
COVER_HOST = "covers.openlibrary.org"  # where pictures are fetched from (and AniList's cover store, anilist.cover_allowed)
COLORS = ["#7A4B2A", "#2F5D62", "#5B3A6B", "#8A3B3B", "#3B5B8A", "#4F6B3A", "#6B5A2F", "#3A4A5B"]


class CoverError(Exception):
    pass


def _file(book_key, jpeg):
    """A new name for every picture, so one being sent to a browser is never written over."""
    return "%s-%s.jpg" % (hashlib.sha1(book_key.encode("utf-8")).hexdigest()[:20], hashlib.sha1(jpeg).hexdigest()[:10])


def _jpeg(data):
    """Any picture a browser can show, as a JPEG no larger than MAX_SIZE."""
    try:
        picture = Image.open(io.BytesIO(data))
        picture.load()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        raise CoverError("That file is not a picture Family Hub can read. Use a JPEG, PNG or WebP.")
    if picture.width < 100 or picture.height < 100:
        raise CoverError("That picture is too small to use as a cover.")
    picture = picture.convert("RGB")
    picture.thumbnail(MAX_SIZE)
    jpeg = io.BytesIO()
    picture.save(jpeg, "JPEG", quality=88)
    return jpeg.getvalue()


def _allowed(url):
    from . import anilist
    # only ever two picture shops. the ".." check stops anyone sneaking off to another path on the same host
    return (isinstance(url, str) and url.startswith("https://%s/b/" % COVER_HOST) and ".." not in url) or anilist.cover_allowed(url)


def fetch(url):
    """Download one picture from Open Library's cover store, or AniList's. Nothing else is ever fetched."""
    if not _allowed(url):
        raise CoverError("Covers can only be taken from Open Library or AniList.")
    try:
        reply = requests.get(url, headers=AGENT, timeout=TIMEOUT, stream=True)
        if reply.status_code != 200:
            raise CoverError("Open Library does not have that cover any more.")
        data = reply.raw.read(MAX_BYTES + 1, decode_content=True)  # one byte over the limit is enough to know it's too big
    except requests.RequestException as exc:
        raise CoverError("Open Library could not be reached: %s" % str(exc)[:150])
    if len(data) > MAX_BYTES:
        raise CoverError("That picture is too large.")
    return data


def title_cover(title, author):
    """A plain cover: the title and author on a colour picked from the title."""
    width, height = GENERATED
    color = COLORS[int(hashlib.sha1((title or "").encode("utf-8")).hexdigest(), 16) % len(COLORS)]
    picture = Image.new("RGB", GENERATED, color)
    draw = ImageDraw.Draw(picture)
    draw.rectangle([24, 24, width - 24, height - 24], outline="#F6F3EC", width=3)
    title = (title or "Untitled").strip()
    size = 64 if len(title) < 30 else (50 if len(title) < 70 else 38)
    font, small = ImageFont.load_default(size=size), ImageFont.load_default(size=30)
    lines = textwrap.wrap(title, width=max(8, int(width * 1.55 / size)))[:9]
    for row, line in enumerate(lines):
        draw.text((60, 110 + row * int(size * 1.25)), line, font=font, fill="#F6F3EC")
    if (author or "").strip().lower() in catalogues.NO_AUTHOR:
        author = ""  # "Unknown" on a cover helps nobody
    names = textwrap.wrap((author or "").strip(), width=28)[:3]
    top = height - 80 - 40 * len(names)  # the author sits at the bottom, growing upwards
    for row, line in enumerate(names):
        draw.text((60, top + row * 40), line, font=small, fill="#F6F3EC")
    jpeg = io.BytesIO()
    picture.save(jpeg, "JPEG", quality=90)
    return jpeg.getvalue()


def options(book):
    """Covers Open Library has for this book: [{"url", "label"}], best guess first."""
    found, seen = [], set()

    def add(cover_id, label):
        if cover_id and cover_id > 0 and cover_id not in seen and len(found) < MAX_OPTIONS:
            seen.add(cover_id)
            found.append({"url": "https://%s/b/id/%d-L.jpg" % (COVER_HOST, cover_id), "label": label})

    url, fields = "https://openlibrary.org/search.json", "key,title,author_name,cover_i,first_publish_year"
    try:
        docs, work = [], None
        if book["isbn"]:
            docs = catalogues._get(url, {"q": "isbn:" + book["isbn"], "fields": fields, "limit": 3}).get("docs") or []
        if not docs:
            for title, author in catalogues.attempts(book):
                params = {"title": title, "fields": fields, "limit": 8}
                if catalogues.author_known(author):
                    params["author"] = author.split("&")[0].strip()
                hits = catalogues._get(url, params).get("docs") or []
                docs = [doc for doc in hits if catalogues.same_book(title, author, doc.get("title"), doc.get("author_name"))]
                if docs:
                    break
        for doc in docs:
            work = work or doc.get("key")
            add(doc.get("cover_i"), "Open Library" + (", %s" % doc["first_publish_year"] if doc.get("first_publish_year") else ""))
        if work:  # pro tip: other editions of the same book often have way nicer covers
            editions = catalogues._get("https://openlibrary.org%s/editions.json" % work, {"limit": 40}).get("entries") or []
            for edition in editions:
                for cover_id in edition.get("covers") or []:
                    add(cover_id, ", ".join(filter(None, [(edition.get("publishers") or [""])[0], edition.get("publish_date") or ""]))
                        or "Another edition")
    except catalogues.LookupError_ as exc:
        raise CoverError("Open Library could not be reached: %s" % exc)
    return found


class Covers:
    def __init__(self, hub):
        self.hub = hub
        self.folder = hub.config.data_dir / "covers"

    def index(self):
        """{book key: when its cover was chosen} for every book with a cover of its own."""
        return {row["book_key"]: row["at"] for row in self.hub.con().execute("SELECT book_key, at FROM covers")}

    def row(self, book_key):
        return self.hub.con().execute("SELECT * FROM covers WHERE book_key=?", (book_key,)).fetchone()

    def path(self, book_key):
        row = self.row(book_key)
        path = self.folder / row["file"] if row else None
        return path if path and path.is_file() else None

    def save(self, book_key, data, source, profile_id=None):
        """Keep ``data`` (any picture) as this book's cover."""
        if len(data) > MAX_BYTES:
            raise CoverError("That picture is too large. The limit is 15 MB.")
        jpeg = _jpeg(data)
        self.folder.mkdir(parents=True, exist_ok=True)
        name = _file(book_key, jpeg)
        old = self.row(book_key)
        # write to a side file then swap it in, so nobody ever downloads half a picture
        partial = self.folder / (name + ".part")
        partial.write_bytes(jpeg)
        partial.replace(self.folder / name)
        if old and old["file"] != name:
            self._discard(old["file"])
        con = self.hub.con()
        self.hub.ensure_meta(book_key)
        con.execute(
            "INSERT INTO covers(book_key, file, source, added_by, at) VALUES(?,?,?,?,?) ON CONFLICT(book_key) DO UPDATE SET "
            "file=excluded.file, source=excluded.source, added_by=excluded.added_by, at=excluded.at",
            (book_key, name, source, profile_id, db.now()))
        con.commit()

    def _discard(self, name):
        try:
            (self.folder / name).unlink(missing_ok=True)
        except OSError:
            pass  # still being sent to someone; a stray copy of a picture does no harm

    def remove(self, book_key):
        """Go back to the library's cover. The picture file is removed; it was only ever a copy."""
        row = self.row(book_key)
        if not row:
            return False
        self._discard(row["file"])
        con = self.hub.con()
        con.execute("DELETE FROM covers WHERE book_key=?", (book_key,))
        con.commit()
        return True
