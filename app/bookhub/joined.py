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


def describe(path):
    """(title, [(contents entry, first page)], pages) of a joined EPUB, read from the file itself."""
    with zipfile.ZipFile(path) as z:
        nav = z.read("OEBPS/nav.xhtml").decode("utf-8", "replace")
        opf = z.read("OEBPS/content.opf").decode("utf-8", "replace")
    title = re.search(r"<dc:title>(.*?)</dc:title>", opf, re.S)
    toc = [(html.unescape(label), int(n)) for n, label in NAV_ENTRY.findall(nav)]
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
    out = dict(row)
    out["parts"] = json.loads(out["parts"] or "[]")
    return out


def where(joined, percent):
    """For each volume or chapter: (key, "finished" | "reading" | None, percent within it)."""
    pages, parts = max(1, joined["pages"]), joined["parts"]
    page = max(1, math.ceil(percent / 100.0 * pages))
    out = []
    for i, part in enumerate(parts):
        end = parts[i + 1]["first"] if i + 1 < len(parts) else pages + 1
        if percent >= 99.5 or page >= end:
            out.append((part["key"], "finished", 100.0))
        elif page >= part["first"]:
            out.append((part["key"], "reading", round(100.0 * (page - part["first"] + 1) / max(1, end - part["first"]), 2)))
        else:
            out.append((part["key"], None, 0.0))
    return out


def apply(hub, profile_id, joined, percent, device="", progress="", finished=None):
    """Progress in a joined book, passed on to what is in it, and kept for the app to fetch back."""
    if joined["kind"] == "series":
        for key, state, within in where(joined, percent):
            if not key or not state:
                continue
            old = hub.con().execute("SELECT status FROM reading WHERE book_key=? AND profile_id=?", (key, profile_id)).fetchone()
            if old and old["status"] == "finished":
                continue  # read already; going back over it does not un-finish it
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
    known = {r["path"] for r in hub.con().execute("SELECT path FROM joined_books")}
    books = hub.library.books()
    count = 0
    for path in folder.glob("*-series.epub"):
        if str(path) in known:
            continue
        try:
            title, _, _ = describe(path)
        except (OSError, KeyError, zipfile.BadZipFile):
            continue
        vols = opds._volumes(title, books)
        if vols:
            record(hub, path, "series", title, {opds._part(v): v["key"] for v in vols})
            count += 1
    return count
