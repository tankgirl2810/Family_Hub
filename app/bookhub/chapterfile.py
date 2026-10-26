"""A series followed chapter by chapter, kept as comic files (CBZ) from the start.

When chapters finish downloading on the series server, Family Hub joins them into one CBZ per part of
CHAPTERS_PER_PART chapters. Each chapter is a folder of its own inside the file ("0003 Chapter 3/0001.jpg"),
and ComicInfo.xml names the page each chapter starts on, so comic readers show a chapter list. The series then
downloads, reads and is tagged as one thing. A part is made again only when its chapters change, so new
chapters only touch the last part.

The files are Family Hub's own (data/chapter-files/<series id>); the book library is not touched.
"""
import hashlib
import logging
import re
import shutil
import tempfile
import threading
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

from . import seriesfile

CHAPTERS_PER_PART = 50  # the same parts as the reading app's book of the series (opds.py)
log = logging.getLogger("bookhub")
_lock = threading.Lock()
_making = {}  # series id -> part being made now
_failed = {}  # (series id, part) -> why it could not be made


def folder(hub, series_id):
    return Path(hub.config.data_dir) / "chapter-files" / str(int(series_id))


def chapter_name(c):
    return c["name"] or "Chapter %s" % c["number"]


def parts(info):
    kept = [c for c in info["chapters_list"] if c["downloaded"]]
    return [kept[i:i + CHAPTERS_PER_PART] for i in range(0, len(kept), CHAPTERS_PER_PART)]


def part_title(title, chapters, k, count):
    if count == 1:
        return title
    return "%s · Part %d (%s – %s)" % (title, k, chapter_name(chapters[0]), chapter_name(chapters[-1]))


def _path(hub, series_id, k, chapters):
    stamp = hashlib.sha1(",".join(str(c["id"]) for c in chapters).encode()).hexdigest()[:12]
    return folder(hub, series_id) / ("part%02d-%s.cbz" % (k, stamp))


def files(hub, info):
    """Each part of a series as a comic file: its name, chapters, and whether it is ready, being made or failed."""
    found = parts(info)
    out = []
    for k, chapters in enumerate(found, 1):
        path = _path(hub, info["id"], k, chapters)
        out.append({
            "part": k, "title": part_title(info["title"], chapters, k, len(found)), "chapters": len(chapters),
            "first": chapter_name(chapters[0]), "last": chapter_name(chapters[-1]),
            "state": "ready" if path.is_file() else "making" if _making.get(info["id"]) == k
            else "failed" if (info["id"], k) in _failed else "waiting",
            "size": path.stat().st_size if path.is_file() else 0, "error": _failed.get((info["id"], k), ""),
        })
    return out


def ready_file(hub, info, k):
    found = parts(info)
    if not 1 <= k <= len(found):
        return None, ""
    path = _path(hub, info["id"], k, found[k - 1])
    return (path if path.is_file() else None), part_title(info["title"], found[k - 1], k, len(found))


def _comicinfo(info, title, k, count, starts, pages):
    marks = "".join('<Page Image="%d" Bookmark="%s"/>' % (at, escape(name, {'"': "&quot;"})) for at, name in starts)
    return ('<?xml version="1.0" encoding="utf-8"?>\n<ComicInfo xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            "<Title>%s</Title><Series>%s</Series><Count>%d</Count><Number>%d</Number><Writer>%s</Writer>"
            "<PageCount>%d</PageCount><Notes>%s</Notes><Pages>%s</Pages></ComicInfo>" % (
                escape(title), escape(info["title"]), count, k, escape(info.get("author") or ""), pages,
                escape("Chapters: " + ", ".join(name for _, name in starts)), marks))


def build(hub, info, k, chapters, count, target):
    """Join one part's chapters into a CBZ. Pictures go in as they are; they are already compressed."""
    scratch = Path(tempfile.mkdtemp())
    part = target.with_name(target.name + ".part")
    try:
        starts, page = [], 0
        with zipfile.ZipFile(part, "w", zipfile.ZIP_STORED) as out:
            for i, c in enumerate(chapters, 1):
                file = scratch / "chapter.cbz"
                with hub.series.raw("/api/v1/chapter/%d/download" % c["id"], stream=True) as r, open(file, "wb") as f:
                    for block in r.iter_content(1 << 20):
                        f.write(block)
                with zipfile.ZipFile(file) as z:
                    names = sorted((n for n in z.namelist() if n.lower().endswith(seriesfile.PICTURES)), key=seriesfile._natural)
                    if not names:
                        continue
                    where = "%04d %s" % (i, re.sub(r'[\\/:*?"<>|]+', " ", chapter_name(c)).strip()[:80])
                    starts.append((page, chapter_name(c)))
                    for j, name in enumerate(names, 1):
                        out.writestr("%s/%04d%s" % (where, j, Path(name).suffix.lower()), z.read(name))
                        page += 1
            out.writestr("ComicInfo.xml", _comicinfo(info, part_title(info["title"], chapters, k, count), k, count, starts, page))
        part.replace(target)
    finally:
        part.unlink(missing_ok=True)
        shutil.rmtree(scratch, ignore_errors=True)


def keep_series(hub, info, busy=False):
    """Make the parts of one series that are missing, and drop files whose chapters have changed.
    While chapters are still downloading (busy), only full parts are made, so the last part is not made
    again for every chapter that arrives."""
    found = parts(info)
    wanted = set()
    for k, chapters in enumerate(found, 1):
        target = _path(hub, info["id"], k, chapters)
        wanted.add(target.name)
        if target.is_file() or (busy and len(chapters) < CHAPTERS_PER_PART):
            if busy and not target.is_file():  # an older, shorter copy of this part stays until the new one is made
                wanted.update(p.name for p in target.parent.glob("part%02d-*.cbz" % k))
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        _making[info["id"]] = k
        try:
            build(hub, info, k, chapters, len(found), target)
            _failed.pop((info["id"], k), None)
        except Exception as exc:  # one damaged chapter must not stop the other series
            _failed[(info["id"], k)] = str(exc)[:200]
            log.warning("could not make part %d of %s as a comic file: %s", k, info["title"], exc)
        finally:
            _making.pop(info["id"], None)
    place = folder(hub, info["id"])
    if place.is_dir():
        for old in place.glob("part*.cbz"):
            if old.name not in wanted:
                old.unlink(missing_ok=True)


def keep_all(hub):
    """Every followed series, as comic files. Runs in the background; a second call while one runs does nothing."""
    from . import series
    if not hub.series.configured or not _lock.acquire(blocking=False):
        return
    try:
        shelf = hub.series.shelf()
        busy = shelf["queued"] > 0
        for s in shelf["series"]:
            if not s["downloaded"]:
                continue
            try:
                keep_series(hub, hub.series.detail(s["id"]), busy=busy)
            except series.SeriesError as exc:
                log.warning("could not look at %s on the series server: %s", s["title"], exc)
    finally:
        _lock.release()


def start(hub, app):
    def run():
        with app.app_context():
            try:
                keep_all(hub)
            except Exception as exc:
                log.warning("could not keep the chapter series as comic files: %s", exc)
    threading.Thread(target=run, name="bookhub-chapter-files", daemon=True).start()
