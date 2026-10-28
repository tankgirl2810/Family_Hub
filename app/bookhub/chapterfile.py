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
import time
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

from . import seriesfile

CHAPTERS_PER_PART = 50  # the same parts as the reading app's book of the series (opds.py)
FORMAT = 2  # raised when the files are made differently, so every part is made (and fetched by reading apps) again
MIN_FREE = 3 * 1024 ** 3  # a book or comic file is not started with less space than this left on the disk
# while the series server still has chapters queued, an unfinished part is made again at most this often. the
# queue can sit at hundreds of chapters for days, so "wait until it's empty" meant new chapters never arrived
BUSY_REMAKE = 3600
log = logging.getLogger("bookhub")


class NoRoom(OSError):
    pass


def room_for(place):
    """Stop before making a big file on a nearly full disk: a full disk stops every program on it, Docker too."""
    place = Path(place)
    # the folder might not exist yet, so walk up until we hit one that does and ask about that disk
    while not place.exists() and place.parent != place:
        place = place.parent
    free = shutil.disk_usage(place).free
    if free < MIN_FREE:
        raise NoRoom("Only %.1f GB is free on the disk; at least %d GB is kept free, so it was not made. Free some space and it is made by itself."
                     % (free / 1024 ** 3, MIN_FREE // 1024 ** 3))


_lock = threading.Lock()
_making = {}  # series id -> part being made now
_failed = {}  # (series id, part) -> why it could not be made


def folder(hub, series_id):
    return Path(hub.config.data_dir) / "chapter-files" / str(int(series_id))


def chapter_name(chapter):
    """The chapter as a contents entry: "Chapter 21", or "Chapter 21 · Light (3)" when the site gives it a title."""
    number, name = chapter.get("number"), (chapter.get("name") or "").strip()
    if number is None or number < 0:
        return name or "Chapter"
    # regex goblin: peels "Ch. 21", "Chapter 021", "Ch. 21.0", "Chapter 21." off the front so only a real title is
    # left. the (?!\.?\d) bit stops "21" eating the start of "215". sneaky little lookahead
    rest = re.sub(r"^\s*(?:ch(?:apter)?|ep(?:isode)?)?\.?\s*0*%s(?:\.0+)?(?!\.?\d)\s*" % re.escape("%g" % number), "", name, flags=re.I)
    rest = rest.strip(" -:–·").lstrip(". -:–·")
    return "Chapter %g" % number + (" · " + rest if rest else "")


def labels(chapters):
    """Each chapter's contents entry (chapter_name), made unique ("Extra", "Extra (2)") so a reading app's place
    in a joined book is matched to the right chapter (joined.py finds chapters by their entry)."""
    unique, seen = [], {}
    for chapter in chapters:
        label = chapter_name(chapter)
        seen[label] = seen.get(label, 0) + 1
        unique.append(label if seen[label] == 1 else "%s (%d)" % (label, seen[label]))
    return unique


def parts(info):
    """The downloaded chapters, one copy of each (series.unique), in parts of CHAPTERS_PER_PART."""
    from . import series
    one_each = series.unique(info.get("every_chapter") or info["chapters_list"], among_downloaded=True)[0]
    kept = [chapter for chapter in one_each if chapter["downloaded"]]
    return [kept[start:start + CHAPTERS_PER_PART] for start in range(0, len(kept), CHAPTERS_PER_PART)]


def version(chapters):
    """What a reading app has to fetch again when it changes: the chapters in a part and how the files are made.
    The chapters themselves count, not only how many, so a better copy of a chapter (series.unique) is fetched too."""
    ids = hashlib.sha1(",".join(str(chapter["id"]) for chapter in chapters).encode()).hexdigest()[:6]
    return "%d.%d.%s" % (FORMAT, len(chapters), ids)


def part_title(title, chapters, part_no, count):
    if count == 1:
        return title
    return "%s · Part %d (%s – %s)" % (title, part_no, chapter_name(chapters[0]), chapter_name(chapters[-1]))


def _path(hub, series_id, part_no, chapters):
    stamp = hashlib.sha1(("%d|" % FORMAT + ",".join(str(chapter["id"]) for chapter in chapters)).encode()).hexdigest()[:12]
    return folder(hub, series_id) / ("part%02d-%s.cbz" % (part_no, stamp))


def stamp_of(path):
    """"part03-1a2b3c4d5e6f.cbz" -> "1a2b3c4d5e6f": names one made copy of a part."""
    return Path(path).stem.partition("-")[2]


def files(hub, info):
    """Each part of a series as a comic file: its name, chapters, and whether it is ready, being made or failed."""
    found = parts(info)
    listed = []
    for part_no, chapters in enumerate(found, 1):
        path = _path(hub, info["id"], part_no, chapters)
        state = _state(info["id"], part_no, path)
        # not made with the newest chapters yet, but a shorter copy can still be downloaded meanwhile (ready_file older=True)
        older = None if state == "ready" or not path.parent.is_dir() else next(iter(sorted(
            path.parent.glob("part%02d-*.cbz" % part_no), key=lambda file: file.stat().st_mtime, reverse=True)), None)
        listed.append({
            "part": part_no, "title": part_title(info["title"], chapters, part_no, len(found)), "chapters": len(chapters),
            "first": chapter_name(chapters[0]), "last": chapter_name(chapters[-1]), "state": state, "older": bool(older),
            "size": path.stat().st_size if path.is_file() else older.stat().st_size if older else 0,
            "error": _failed.get((info["id"], part_no), ""),
        })
    return listed


def _state(series_id, part_no, path):
    if path.is_file():
        return "ready"
    if _making.get(series_id) == part_no:
        return "making"
    return "failed" if (series_id, part_no) in _failed else "waiting"


def ready_file(hub, info, part_no, older=False):
    """The comic file of a part as it should be now. With older: the last one made when the current one is still
    being made (new chapters arrived), so a download gets the chapters so far rather than nothing."""
    found = parts(info)
    if not 1 <= part_no <= len(found):
        return None, ""
    path = _path(hub, info["id"], part_no, found[part_no - 1])
    title = part_title(info["title"], found[part_no - 1], part_no, len(found))
    if path.is_file() or not older:
        return (path if path.is_file() else None), title
    made = sorted(folder(hub, info["id"]).glob("part%02d-*.cbz" % part_no), key=lambda file: file.stat().st_mtime)
    return (made[-1] if made else None), title


def made_parts(hub, series_id):
    """How many parts of a series have a comic file made (any version)."""
    place = folder(hub, series_id)
    # "part03-abc.cbz" -> "part03", so old and new copies of a part only count once
    return len({file.name[:6] for file in place.glob("part*.cbz")}) if place.is_dir() else 0


def _comicinfo(info, title, part_no, count, starts, pages):
    marks = "".join('<Page Image="%d" Bookmark="%s"/>' % (at, escape(name, {'"': "&quot;"})) for at, name in starts)
    return ('<?xml version="1.0" encoding="utf-8"?>\n<ComicInfo xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            "<Title>%s</Title><Series>%s</Series><Count>%d</Count><Number>%d</Number><Writer>%s</Writer>"
            "<PageCount>%d</PageCount><Notes>%s</Notes><Pages>%s</Pages></ComicInfo>" % (
                escape(title), escape(info["title"]), count, part_no, escape(info.get("author") or ""), pages,
                escape("Chapters: " + ", ".join(name for _, name in starts)), marks))


def record(hub, info, path, chapters):
    """Tell joined.py about a made part, so a reading app's place in it is understood (and carried over to the
    next copy of the part). The chapter list comes from the file's own ComicInfo, so it is always what's inside."""
    from . import joined
    joined.record(hub, path, "chapters", info["id"], dict(zip(labels(chapters), (chapter["id"] for chapter in chapters))))


def build(hub, info, part_no, chapters, count, target):
    """Join one part's chapters into a CBZ. Pictures go in as they are; they are already compressed."""
    scratch = Path(tempfile.mkdtemp())
    unfinished = target.with_name(target.name + ".part")
    try:
        starts, page = [], 0
        # unique entries (labels): two chapters both called "Notice" must still be told apart by joined.py
        with zipfile.ZipFile(unfinished, "w", zipfile.ZIP_STORED) as cbz:
            for chapter_no, (chapter, label) in enumerate(zip(chapters, labels(chapters)), 1):
                download = scratch / "chapter.cbz"
                with hub.series.raw("/api/v1/chapter/%d/download" % chapter["id"], stream=True) as reply, open(download, "wb") as file:
                    for block in reply.iter_content(1 << 20):
                        file.write(block)
                with zipfile.ZipFile(download) as chapter_zip:
                    names = sorted((name for name in chapter_zip.namelist() if name.lower().endswith(seriesfile.PICTURES)),
                                   key=seriesfile._natural)
                    if not names:
                        continue
                    where = "%04d %s" % (chapter_no, re.sub(r'[\\/:*?"<>|]+', " ", label).strip()[:80])
                    starts.append((page, label))
                    for picture_no, name in enumerate(names, 1):
                        cbz.writestr("%s/%04d%s" % (where, picture_no, Path(name).suffix.lower()), chapter_zip.read(name))
                        page += 1
            cbz.writestr("ComicInfo.xml", _comicinfo(info, part_title(info["title"], chapters, part_no, count), part_no, count, starts, page))
        unfinished.replace(target)
    finally:
        unfinished.unlink(missing_ok=True)
        shutil.rmtree(scratch, ignore_errors=True)


def _recorded(hub, path):
    return hub.con().execute("SELECT 1 FROM joined_books WHERE path=?", (str(path),)).fetchone() is not None


def keep_series(hub, info, busy=False):
    """Make the parts of one series that are missing, and drop files whose chapters have changed.
    While chapters are still downloading (busy), an unfinished part that already has a copy is made again at
    most every BUSY_REMAKE seconds, so it isn't remade for every chapter that arrives, but still keeps up."""
    found = parts(info)
    wanted = set()
    older_copies = lambda target, part_no: sorted(target.parent.glob("part%02d-*.cbz" % part_no), key=lambda file: file.stat().st_mtime)  # noqa: E731
    for part_no, chapters in enumerate(found, 1):
        target = _path(hub, info["id"], part_no, chapters)
        wanted.add(target.name)
        if target.is_file():
            if not _recorded(hub, target):  # made before parts were recorded
                record(hub, info, target, chapters)
            continue
        older = older_copies(target, part_no) if target.parent.is_dir() else []
        if busy and len(chapters) < CHAPTERS_PER_PART and older and time.time() - older[-1].stat().st_mtime < BUSY_REMAKE:
            wanted |= {file.name for file in older}  # keep the shorter old copy around until the longer one's made
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        _making[info["id"]] = part_no
        try:
            room_for(target.parent)
            build(hub, info, part_no, chapters, len(found), target)
            record(hub, info, target, chapters)
            _failed.pop((info["id"], part_no), None)
        except Exception as exc:  # one busted chapter shouldn't take every other series down with it
            _failed[(info["id"], part_no)] = str(exc)[:200]
            wanted |= {file.name for file in older}  # the older copy stays
            log.warning("could not make part %d of %s as a comic file: %s", part_no, info["title"], exc)
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
        for followed in shelf["series"]:
            if not followed["downloaded"]:
                continue
            try:
                info = hub.series.detail(followed["id"])
                keep_series(hub, info, busy=busy)
                if info.get("doubles") and hub.series.drop_doubles(info):
                    log.info("deleted the second copies of twice-listed chapters of %s", followed["title"])
            except series.SeriesError as exc:
                log.warning("could not look at %s on the series server: %s", followed["title"], exc)
            except Exception as exc:  # an odd answer or a file gone mid-way: the other series still get their turn
                log.warning("could not keep %s as comic files: %s", followed["title"], exc)
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
