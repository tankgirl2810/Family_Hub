"""Joined books (a comic series or a series by chapter made into one EPUB, see opds.py) and the reading apps'
progress in them.

A reading app reports progress against a fingerprint of its file (kosync.partial_md5). The joined books are
made by Family Hub, not kept in the library, so each one is recorded when it is made: its fingerprint, the
series, and the page each volume or chapter starts on. Progress reported for one is then turned into progress
on the volumes themselves: the volume being read gets its own percentage, the ones before it are finished,
and finishing the book finishes them all. Chapters are marked read on the series server. Records are kept after
the file itself is cleared away, since the reading app still holds its copy.
"""
import html
import json
import math
import re
import zipfile
from pathlib import Path

from . import db

NAV_ENTRY = re.compile(r'<a href="pages/p(\d+)\.xhtml">([^<]*)</a>')


COMIC_PAGE = re.compile(r'<Page Image="(\d+)" Bookmark="([^"]*)"/>')


def describe(path):
    """(title, [(contents entry, first page)], pages) of a joined EPUB or a chapter part's CBZ, read from the file itself."""
    if str(path).lower().endswith(".cbz"):
        with zipfile.ZipFile(path) as cbz:
            info = cbz.read("ComicInfo.xml").decode("utf-8", "replace")
        title, pages = re.search(r"<Title>(.*?)</Title>", info, re.S), re.search(r"<PageCount>(\d+)</PageCount>", info)
        # ComicInfo counts pictures from 0; the EPUBs (and joined_books) count pages from 1
        toc = [(html.unescape(label), int(at) + 1) for at, label in COMIC_PAGE.findall(info)]
        return html.unescape(title.group(1)) if title else "", toc, int(pages.group(1)) if pages else 0
    with zipfile.ZipFile(path) as epub:
        nav = epub.read("OEBPS/nav.xhtml").decode("utf-8", "replace")
        opf = epub.read("OEBPS/content.opf").decode("utf-8", "replace")
    title = re.search(r"<dc:title>(.*?)</dc:title>", opf, re.S)
    toc = [(html.unescape(label), int(page_no)) for page_no, label in NAV_ENTRY.findall(nav)]
    return html.unescape(title.group(1)) if title else "", toc, opf.count("<itemref")


def record(hub, path, kind, name, keys, digest=None, kept_as=None):
    """Remember a joined book just made. kind: "series" (keys: contents entry -> library book key) or
    "chapters" (name: the series server's id; keys: contents entry -> chapter id)."""
    from .kosync import partial_md5
    title, toc, pages = describe(path)
    parts = [{"label": label, "first": first, "key": keys.get(label, "")} for label, first in toc]
    con = hub.con()
    con.execute("INSERT OR REPLACE INTO joined_books(hash, kind, name, title, parts, pages, path, made) VALUES(?,?,?,?,?,?,?,?)",
                (digest or partial_md5(path), kind, str(name), title, json.dumps(parts), pages, str(kept_as or path), db.now()))
    con.commit()


def find(hub, digest):
    row = hub.con().execute("SELECT * FROM joined_books WHERE hash=?", (digest,)).fetchone()
    if not row:
        return None
    joined = dict(row)
    joined["parts"] = json.loads(joined["parts"] or "[]")
    return joined


def where(joined, percent):
    """For each volume or chapter: (key, "finished" | "reading" | None, percent within it)."""
    pages, parts = max(1, joined["pages"]), joined["parts"]
    page = max(1, math.ceil(percent / 100.0 * pages))
    # each part ends where the next one starts; the last one runs off the end of the book
    ends = [following["first"] for following in parts[1:]] + [pages + 1]
    states = []
    for part, end in zip(parts, ends):
        if percent >= 99.5 or page >= end:
            states.append((part["key"], "finished", 100.0))
        elif page >= part["first"]:
            states.append((part["key"], "reading", round(100.0 * (page - part["first"] + 1) / max(1, end - part["first"]), 2)))
        else:
            states.append((part["key"], None, 0.0))
    return states


def apply(hub, profile_id, joined, percent, device="", progress="", finished=None):
    """Progress in a joined book, passed on to what is in it, and kept for the app to fetch back."""
    if joined["kind"] == "series":
        for key, state, within in where(joined, percent):
            if not key or not state:
                continue
            old = hub.con().execute("SELECT status FROM reading WHERE book_key=? AND profile_id=?", (key, profile_id)).fetchone()
            if old and old["status"] == "finished":
                continue  # flicking back through a finished volume shouldn't un-finish it
            if state == "finished":
                hub.save_reading(profile_id, key, status="finished", source="device", device=device or None, finished=finished)
            else:
                hub.save_reading(profile_id, key, percent=within, source="device", device=device or None)
    elif joined["kind"] == "chapters" and hub.series.configured:
        from .series import SeriesError
        done = [int(key) for key, state, _ in where(joined, percent) if state == "finished" and str(key).isdigit()]
        if done:
            try:
                hub.series.mark(done, True)
            except SeriesError:
                pass  # the series server is away; the next report marks them
    db.kv_set(hub.con(), "joined:%d:%s" % (profile_id, joined["hash"]),
              json.dumps({"percent": percent, "progress": progress, "device": device, "updated": db.now()}))
    hub.con().commit()


def kept_progress(hub, profile_id, digest):
    value = db.kv_get(hub.con(), "joined:%d:%s" % (profile_id, digest))
    return json.loads(value) if value else None


def _page(joined, kept):
    """The page (counting from 1) a kept report points at. Comic apps report the page they're on counting from 0
    (Readest sends its page index as "progress"); anything else falls back to the percentage."""
    progress = str(kept.get("progress") or "")
    if progress.isdigit():
        return int(progress) + 1
    return max(1, math.ceil((kept.get("percent") or 0) / 100.0 * max(1, joined["pages"])))


def carried_progress(hub, profile_id, digest):
    """A new copy of a chapter part the app hasn't reported on yet (it grew, or arrived as a CBZ instead of an
    EPUB): the place from the copy read most recently, moved to the same page of the same chapter in this one.
    None when the place is in a chapter this copy doesn't have.

    Readest treats every new file as a brand new book, so without this the longer part opens on page 1."""
    this = find(hub, digest)
    if not this or this["kind"] != "chapters":
        return None
    starts = {str(part["key"]): part["first"] for part in this["parts"] if part["key"]}
    places = []
    for row in hub.con().execute("SELECT * FROM joined_books WHERE kind='chapters' AND name=? AND hash!=?", (this["name"], digest)):
        kept = kept_progress(hub, profile_id, row["hash"])
        if kept:
            places.append((kept["updated"], dict(row, parts=json.loads(row["parts"] or "[]")), kept))
    for _, old, kept in sorted(places, key=lambda place: place[0], reverse=True):
        page = _page(old, kept)
        # the chapter the page is in: the last one starting on or before it
        chapter = next((part for part in reversed(old["parts"]) if part["first"] <= page), None)
        if chapter and str(chapter["key"]) in starts:
            moved = min(this["pages"], starts[str(chapter["key"])] + page - chapter["first"])
            # "progress" goes back as the page counting from 1: Readest opens page (progress - 1) counting from 0
            return {"percent": round(100.0 * moved / max(1, this["pages"]), 2), "progress": str(moved),
                    "device": kept["device"], "updated": db.now()}
    return None


def adopt(hub):
    """Progress stored under a bare fingerprint before its joined book was recorded: pass it on now."""
    con = hub.con()
    moved = 0
    for row in con.execute("SELECT * FROM reading WHERE book_key LIKE 'doc:%'").fetchall():
        joined = find(hub, row["book_key"][4:])
        if not joined:
            continue
        apply(hub, row["profile_id"], joined, row["percent"] or 0.0, row["device"] or "", row["progress"] or "",
              finished=row["finished"])
        con.execute("DELETE FROM reading WHERE book_key=? AND profile_id=?", (row["book_key"], row["profile_id"]))
        moved += 1
    if moved:
        con.commit()
        hub.touch()
    return moved


def record_existing(hub):
    """Joined books made before they were recorded: match each to its series by title."""
    from . import opds
    folder = Path(hub.config.data_dir) / "series-files"
    if not folder.is_dir():
        return 0
    known = {row["path"] for row in hub.con().execute("SELECT path FROM joined_books")}
    books = hub.library.books()
    count = 0
    for path in folder.glob("*-series.epub"):
        if str(path) in known:
            continue
        try:
            title, _, _ = describe(path)
        except (OSError, KeyError, zipfile.BadZipFile):
            continue
        volumes = opds._volumes(title, books)
        if volumes:
            record(hub, path, "series", title, {opds._part(volume): volume["key"] for volume in volumes})
            count += 1
    return count
