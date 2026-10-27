"""The nightly copy to a second disk.

Two sorts of folder are copied, in two ways:

* Settings and databases (Family Hub's own data, the library manager's
  settings, the series server's, and so on) are small. A complete dated copy
  is written every night under ``settings/YYYY-MM-DD`` and never removed.
* The book library and the chapters are big. They are mirrored: only new and
  changed files are copied. A file that has changed or gone at the source is
  not lost on the copy; the old one is moved to ``removed/YYYY-MM-DD`` first.

Nothing on the second disk is ever deleted by Family Hub.
"""
import json
import os
import shutil
import sqlite3
import threading
import time
from datetime import date, datetime
from pathlib import Path

from . import db

KEY = "second_copy"
START_HOUR = 2  # the copy runs once a day, at the first chance after this hour
# Settings folders looked for inside the stack folder. Ones that do not exist are skipped.
SETTINGS = ["cwa-config", "suwayomi", "komga-config", "prowlarr-config", "qbittorrent-config",
            "radarr-config", "sonarr-config", "bazarr-config", "gluetun", "jellyfin-config"]
# Folders inside those that are caches, logs or programs: big, and rebuilt without loss.
SKIP_DIRS = {"processed_books", "thumbnails", "cache", ".cache", "webUI", "bin", "logs", "log_archive", "downloads", "local",
             "converted", "MediaCover", "ipc-socket", "lucene", "tmp",
             "series-files", "chapter-files"}  # joined series books and chapter files: gigabytes, made again from the library
SKIP_ENDS = (".tmp", ".lock", ".pid", "-journal")
_running = threading.Lock()


class Tally:
    def __init__(self):
        self.copied = self.kept = self.moved = self.bytes = 0
        self.errors = []

    def error(self, path, exc):
        if len(self.errors) < 20:
            self.errors.append("%s: %s" % (path, exc))


def folder(config):
    return config.second_disk_dir if config.second_disk_dir and config.second_disk_dir.is_dir() else None


def enabled(config):
    return folder(config) is not None and config.second_copy == "on"


def _is_sqlite(path):
    """Other programs use the .db ending too (the series server's is a different kind altogether)."""
    try:
        with open(path, "rb") as file:
            return file.read(16) == b"SQLite format 3\x00"  # every real SQLite file starts with exactly these 16 bytes
    except OSError:
        return False


def _copy_database(src, dest):
    """Copy an SQLite file so the copy is whole even while the database is in use."""
    try:
        source = sqlite3.connect("file:%s?mode=ro" % src.as_posix(), uri=True, timeout=5)
        target = sqlite3.connect(str(dest))
        try:
            with target:
                source.backup(target)
        finally:
            target.close()
            source.close()
        return
    except sqlite3.Error:
        pass  # read-only folder, or a fake .db. fine, plain copy it is
    Path(dest).unlink(missing_ok=True)
    shutil.copy2(src, dest)
    for extra in ("-wal", "-shm"):
        side = Path(str(src) + extra)
        if side.is_file():
            shutil.copy2(side, str(dest) + extra)


def _copy_file(src, dest, tally):
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    if src.suffix == ".db" and _is_sqlite(src):
        _copy_database(src, part)
    else:
        shutil.copy2(src, part)
    os.replace(part, dest)
    stamp = src.stat()
    os.utime(dest, (stamp.st_atime, stamp.st_mtime))  # copy the timestamp over, it's how tomorrow knows nothing changed
    tally.copied += 1
    tally.bytes += dest.stat().st_size


def _files(root, skip_dirs=()):
    for base, dirs, names in os.walk(root):
        # dirs[:] edits the list in place, which is how you tell os.walk "don't go in there"
        dirs[:] = [name for name in dirs if name not in skip_dirs]
        for name in names:
            if name.endswith(SKIP_ENDS) or name.endswith(("-wal", "-shm")):
                continue
            yield Path(base) / name


def snapshot(src, dest, tally):
    """A complete copy of a small folder."""
    for path in _files(src, SKIP_DIRS):
        try:
            _copy_file(path, dest / path.relative_to(src), tally)
        except OSError as exc:
            tally.error(path, exc)


def _unchanged(path, target):
    """Same time (and, apart from databases, same size) means the copy is still good."""
    here, there = path.stat(), target.stat()
    same_time = int(there.st_mtime) == int(here.st_mtime)
    # a database copy is made with SQLite's backup, so its size can differ even when nothing changed
    return same_time if path.suffix == ".db" else same_time and there.st_size == here.st_size


def mirror(src, dest, attic, tally):
    """Bring ``dest`` in line with ``src``. Anything replaced or no longer at the source goes to ``attic``."""
    seen = set()
    for path in _files(src):
        rel = path.relative_to(src)
        seen.add(rel)
        target = dest / rel
        try:
            if target.is_file():
                if _unchanged(path, target):
                    tally.kept += 1
                    continue
                _set_aside(target, attic / rel, tally)
            _copy_file(path, target, tally)
        except OSError as exc:
            tally.error(path, exc)
    if dest.is_dir():
        for path in list(_files(dest)):
            rel = path.relative_to(dest)
            if rel not in seen and not path.name.endswith(".part"):
                try:
                    _set_aside(path, attic / rel, tally)
                except OSError as exc:
                    tally.error(path, exc)


def _set_aside(path, to, tally):
    to.parent.mkdir(parents=True, exist_ok=True)
    if to.exists():  # second run today? don't trample the first one's set-aside copy
        to = to.with_name("%s.%s" % (to.name, datetime.now().strftime("%H%M%S")))
    os.replace(path, to)
    tally.moved += 1


def sources(config):
    """(small folders, big folders) as lists of (name, path)."""
    small = [("bookhub", config.data_dir)]
    if config.stack_dir and config.stack_dir.is_dir():
        small += [(name, config.stack_dir / name) for name in SETTINGS if (config.stack_dir / name).is_dir()]
    big = [("library", config.library_dir)]
    if config.series_dir and config.series_dir.is_dir():
        big.append(("series", config.series_dir))
    return small, [(name, path) for name, path in big if path and path.is_dir()]


def run(hub):
    """Make the copy now. Returns the result that is also kept for the Admin page."""
    config = hub.config
    target = folder(config)
    if target is None:
        return {"ok": False, "error": "No second disk is set up."}
    if not _running.acquire(blocking=False):
        return {"ok": False, "error": "A copy is already running."}
    started, tally, today = time.monotonic(), Tally(), date.today().isoformat()
    try:
        small, big = sources(config)
        for name, src in small:
            snapshot(src, target / "settings" / today / name, tally)
        for name, src in big:
            mirror(src, target / name, target / "removed" / today / name, tally)
        result = {"ok": not tally.errors, "at": db.now(), "day": today, "copied": tally.copied, "unchanged": tally.kept,
                  "set_aside": tally.moved, "bytes": tally.bytes, "seconds": round(time.monotonic() - started),
                  "folders": [name for name, _ in small + big], "errors": tally.errors,
                  "error": "%d files could not be copied. The first: %s" % (len(tally.errors), tally.errors[0]) if tally.errors else ""}
    except OSError as exc:
        result = {"ok": False, "at": db.now(), "day": today, "error": "The copy stopped: %s" % exc, "errors": [str(exc)]}
    finally:
        _running.release()
    con = hub.con()
    db.kv_set(con, KEY, json.dumps(result))
    con.commit()
    return result


def last(hub):
    try:
        return json.loads(db.kv_get(hub.con(), KEY) or "null")
    except ValueError:
        return None


def status(hub):
    config = hub.config
    target = folder(config)
    data = {"configured": target is not None, "on": config.second_copy == "on", "running": _running.locked(), "last": last(hub)}
    if target is not None:
        try:
            data["free"] = shutil.disk_usage(target).free
        except OSError:
            data["free"] = None
    return data


def due(hub):
    if not enabled(hub.config) or _running.locked() or datetime.now().hour < START_HOUR:
        return False
    done = last(hub)
    return not done or done.get("day") != date.today().isoformat()


def start(hub, app):
    """Make the copy in the background."""
    def work():
        with app.app_context():
            result = run(hub)
            if not result["ok"]:
                app.logger.warning("copy to the second disk: %s", result.get("error"))
    threading.Thread(target=work, name="bookhub-second-copy", daemon=True).start()


def test(config):
    target = folder(config)
    if target is None:
        return {"ok": False, "message": "No second disk is set up. Put its folder in the .env file as BACKUP_PATH and start Family Hub again; guide 6 has the steps."}
    probe = target / ".bookhub-write-test"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        free = shutil.disk_usage(target).free
    except OSError as exc:
        return {"ok": False, "message": "Family Hub cannot write to the second disk: %s" % exc}
    return {"ok": True, "message": "The second disk can be written to. It has %.1f GB free." % (free / 1e9)}
