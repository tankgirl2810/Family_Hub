"""Reading-progress sync, speaking the KOReader sync protocol.

KOReader (on Kobo, Kindle e-readers and Android) and Readest can both sync to
a "custom KOReader sync server". Point them at  http://<book hub>/kosync
with the profile name as the user name and the profile's sync password.

A document is identified by KOReader's "partial MD5" of the file, which we
compute for every file in the library so progress lands on the right book.
"""
import hashlib
import hmac
import threading
import time

from flask import Blueprint, current_app, jsonify, request

from . import db

bp = Blueprint("kosync", __name__, url_prefix="/kosync")


def partial_md5(path):
    """Same sampling as KOReader's util.partialMD5 (1 KB at 0, 1K, 4K, 16K ... 1G)."""
    md5 = hashlib.md5()
    step = size = 1024
    with open(path, "rb") as f:
        for i in range(-1, 11):
            # LuaJIT's lshift(1024, -2) wraps round to 0, so the first sample is at offset 0.
            offset = 0 if i < 0 else step << (2 * i)
            f.seek(offset)
            sample = f.read(size)
            if not sample:
                break
            md5.update(sample)
    return md5.hexdigest()


def index_library(hub):
    """Record the sync hash of every book file so devices can be matched to books."""
    con = hub.con()
    known = {r["path"]: r["mtime"] for r in con.execute("SELECT path, mtime FROM doc_hashes")}
    changed = False
    for book in hub.library.all_books():  # a device may hold either copy
        for fmt in book["formats"]:
            path = hub.library.file_path(book, fmt)
            if not path:
                continue
            mtime = path.stat().st_mtime
            if known.get(str(path)) == mtime:
                continue
            try:
                digest = partial_md5(path)
            except OSError:
                continue
            con.execute("DELETE FROM doc_hashes WHERE path=?", (str(path),))
            con.execute(
                "INSERT OR REPLACE INTO doc_hashes(hash, book_key, fmt, path, mtime) VALUES(?,?,?,?,?)",
                (digest, book["key"], fmt, str(path), mtime),
            )
            changed = True
    if changed:
        con.commit()


MAX_TRIES, LOCK_SECONDS = 10, 60
_misses = {}  # wrong-key counters, per name; profile names are on the sign-in page, so guessing is the only bar
_misses_lock = threading.Lock()


def _profile():
    name = request.headers.get("x-auth-user", "")
    key = request.headers.get("x-auth-key", "")
    if not name or not key:
        return None
    who = name.strip().lower()
    with _misses_lock:
        entry = _misses.get(who, [0, 0])
        if entry[0] >= MAX_TRIES and time.time() - entry[1] < LOCK_SECONDS:
            return None
    row = current_app.hub.con().execute("SELECT * FROM profiles WHERE name=?", (name,)).fetchone()
    if row and row["sync_key"] and hmac.compare_digest(row["sync_key"], key.lower()):
        with _misses_lock:
            _misses.pop(who, None)
        return row
    with _misses_lock:
        entry = _misses.get(who, [0, 0])
        _misses[who] = [entry[0] + 1 if time.time() - entry[1] < LOCK_SECONDS else 1, time.time()]
    return None


def _unauthorized():
    return jsonify({"code": 2001, "message": "Unauthorized"}), 401


@bp.get("/healthcheck")
def healthcheck():
    return jsonify({"state": "OK"})


@bp.post("/users/create")
def create_user():
    # Accounts are created as profiles inside Family Hub, not from the device.
    return jsonify({"code": 2005, "message": "Create the profile in Family Hub, then log in here."}), 402


@bp.get("/users/auth")
def auth():
    if not _profile():
        return _unauthorized()
    return jsonify({"authorized": "OK"})


@bp.put("/syncs/progress")
def put_progress():
    profile = _profile()
    if not profile:
        return _unauthorized()
    data = request.get_json(silent=True) or {}
    document = str(data.get("document") or "")[:255]
    if not document:
        return jsonify({"code": 2003, "message": "Field 'document' not provided."}), 403
    try:
        percentage = max(0.0, min(1.0, float(data.get("percentage") or 0)))
    except (TypeError, ValueError):
        percentage = 0.0

    hub = current_app.hub
    con = hub.con()
    row = con.execute("SELECT book_key FROM doc_hashes WHERE hash=?", (document,)).fetchone()
    if not row:
        # A series Family Hub joined into one book: the progress goes to the volumes or chapters in it.
        from . import joined
        found = joined.find(hub, document)
        if found:
            joined.apply(hub, profile["id"], found, round(percentage * 100, 2), str(data.get("device") or "")[:80],
                         str(data.get("progress") or "")[:500])
            return jsonify({"document": document, "timestamp": int(time.time())})
        index_library(hub)
        row = con.execute("SELECT book_key FROM doc_hashes WHERE hash=?", (document,)).fetchone()
    # Unknown files are still stored (under their hash) so the device keeps working.
    book_key = row["book_key"] if row else "doc:" + document
    hub.save_reading(
        profile["id"], book_key, percent=round(percentage * 100, 2), source="device",
        device=str(data.get("device") or "")[:80], progress=str(data.get("progress") or "")[:500],
    )
    return jsonify({"document": document, "timestamp": int(__import__("time").time())})


@bp.get("/syncs/progress/<document>")
def get_progress(document):
    profile = _profile()
    if not profile:
        return _unauthorized()
    con = current_app.hub.con()
    row = con.execute("SELECT book_key FROM doc_hashes WHERE hash=?", (document,)).fetchone()
    if not row:
        from . import joined
        kept = joined.kept_progress(current_app.hub, profile["id"], document)
        if kept:
            from datetime import datetime, timezone
            ts = int(datetime.strptime(kept["updated"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp())
            return jsonify({"document": document, "percentage": kept["percent"] / 100.0, "progress": kept["progress"],
                            "device": kept["device"], "device_id": "", "timestamp": ts})
    book_key = row["book_key"] if row else "doc:" + document
    r = con.execute(
        "SELECT * FROM reading WHERE book_key=? AND profile_id=?", (book_key, profile["id"])
    ).fetchone()
    if not r or not r["progress"]:
        return jsonify({})
    from datetime import datetime, timezone
    ts = int(datetime.strptime(r["updated"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp())
    return jsonify({
        "document": document, "percentage": (r["percent"] or 0) / 100.0, "progress": r["progress"],
        "device": r["device"] or "", "device_id": "", "timestamp": ts,
    })


def sync_key_for(password):
    """KOReader sends md5(password) as its key, so that is what we store."""
    return hashlib.md5(password.encode("utf-8")).hexdigest()
