"""The web API used by the Family Hub pages."""
import csv
import functools
import io
import ipaddress
import json
import shutil
import subprocess
import threading
import time
import re
from datetime import date
from urllib.parse import urlparse

import requests

from flask import Blueprint, Response, current_app, jsonify, request, send_file, session
from werkzeug.security import check_password_hash, generate_password_hash

from . import bookfix, calibre, catalogues, visuals, watchlist, covers, db, devices, duplicates, goodreads, integrations, kinds, kosync, looks, media, mediasort, readers, recommend, search, secondcopy, series, settings

bp = Blueprint("api", __name__, url_prefix="/api")

COLORS = ["#F0B04A", "#7FB2E5", "#9BD1A8", "#E59AC0", "#C9A7F0", "#F08A6B"]
MAX_TRIES, LOCK_SECONDS = 5, 60


def hub():
    return current_app.hub


def fail(message, code=400):
    return jsonify({"error": message}), code


@bp.before_request
def json_is_an_object():
    """Every page sends a JSON object. Anything else (a list, a bare number) would fail deep inside a view."""
    if request.is_json and request.content_length:
        data = request.get_json(silent=True)
        if data is not None and not isinstance(data, dict):
            return fail("That request was not understood.")


# ------------------------------------------------------------------- who

def me():
    """The signed-in profile row, the string 'guest', or None."""
    pid = session.get("pid")
    if pid == "guest":
        # Checked on every request, not only at sign-in: a phone signed in as a guest at home keeps its cookie when it leaves.
        config = hub().config
        return "guest" if config.guest_enabled and (not config.guest_lan_only or on_home_network()) else None
    if pid:
        return hub().profile(pid)
    return None


def is_admin(who):
    return who not in (None, "guest") and who["role"] == "admin"


def signed_in(view):
    @functools.wraps(view)
    def wrapper(*args, **kwargs):
        who = me()
        if who is None:
            return fail("Choose a profile first.", 401)
        return view(who, *args, **kwargs)
    return wrapper


def profile_only(view):
    @functools.wraps(view)
    def wrapper(*args, **kwargs):
        who = me()
        if who is None:
            return fail("Choose a profile first.", 401)
        if who == "guest":
            return fail("Guests can browse and download only.", 403)
        return view(who, *args, **kwargs)
    return wrapper


def admin_only(view):
    @functools.wraps(view)
    def wrapper(*args, **kwargs):
        who = me()
        if not is_admin(who):
            return fail("Only an admin can do that.", 403)
        return view(who, *args, **kwargs)
    return wrapper


def client_ip():
    if hub().config.trust_proxy:
        # The proxy adds the address it saw to the end. Anything before that came from the browser, which can
        # claim to be on the home network, so only the last entry is believed.
        forwarded = request.headers.get("X-Forwarded-For", "")
        if forwarded:
            return forwarded.split(",")[-1].strip()
    return request.remote_addr or ""


def on_home_network():
    try:
        ip = ipaddress.ip_address(client_ip())
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback


_attempts_lock = threading.Lock()


def check_passcode(profile, passcode):
    """True if the passcode is right. Locks a profile briefly after repeated misses."""
    attempts = hub().attempts
    # Each try is counted before the (slow) check, so tries sent side by side cannot all slip under the limit.
    with _attempts_lock:
        entry = attempts.get(profile["id"], [0, 0])
        if entry[0] >= MAX_TRIES and time.time() - entry[1] < LOCK_SECONDS:
            return None
        count = entry[0] + 1 if time.time() - entry[1] < LOCK_SECONDS else 1
        attempts[profile["id"]] = [count, time.time()]
    if profile["passcode_hash"] and check_password_hash(profile["passcode_hash"], str(passcode or "")):
        with _attempts_lock:
            attempts.pop(profile["id"], None)
        return True
    return False


def can_series(who, blocked=None):
    """Series by chapter are for everyone signed in, unless an admin has switched them off for a profile. Never for guests."""
    if who in (None, "guest"):
        return False
    return who["role"] == "admin" or who["id"] not in (series.blocked_ids(hub()) if blocked is None else blocked)


def can_follow(who):
    """Following a series downloads all of it, so it goes with being allowed to find new books."""
    return is_admin(who) or bool(who["can_request"])


def series_only(view):
    @functools.wraps(view)
    def wrapper(*args, **kwargs):
        who = me()
        if who is None:
            return fail("Choose a profile first.", 401)
        if not can_series(who):
            return fail("Series are not switched on for this profile.", 403)
        try:
            return view(who, *args, **kwargs)
        except series.SeriesError as exc:
            return fail(str(exc), 502)
    return wrapper


MEDIA_KEY = "media:allowed"


def media_allowed_ids(h):
    """Members an admin has allowed to request films and TV. Admins always can; guests never."""
    try:
        return {int(i) for i in json.loads(db.kv_get(h.con(), MEDIA_KEY) or "[]")}
    except ValueError:
        return set()


def can_request_media(who, allowed=None):
    if who in (None, "guest"):
        return False
    if who["role"] == "admin":
        return True
    return who["id"] in (allowed if allowed is not None else media_allowed_ids(hub()))


def public_profile(p, full=False, blocked=None):
    data = {
        "id": p["id"], "name": p["name"], "role": p["role"], "color": p["color"],
        "has_passcode": bool(p["passcode_hash"]), "can_request": bool(p["can_request"]),
        "can_tag": bool(p["can_tag"]), "tracker_linked": bool(p["hardcover_token"]),
        "can_series": can_series(p, blocked),
        "can_request_media": can_request_media(p),
    }
    if full:
        data["sync_ready"] = bool(p["sync_key"])
    return data


# ----------------------------------------------------------------- state

@bp.get("/state")
def state():
    h = hub()
    who = me()
    profiles = h.profiles()
    blocked = series.blocked_ids(h)
    return jsonify({
        "setup_needed": not profiles,
        "me": None if who is None else ({"guest": True, "name": "Guest"} if who == "guest" else
                                        dict(public_profile(who, True, blocked), via_admin=bool(session.get("via_admin")))),
        "profiles": [public_profile(p, blocked=blocked) for p in profiles],
        "series": h.series.configured and can_series(who, blocked),
        "guest_enabled": h.config.guest_enabled and (not h.config.guest_lan_only or on_home_network()),
        "discover_configured": h.config.discover_configured,
        "library_available": h.library.available,
        "cwa_url": h.config.cwa_url,
        "can_convert": bool(shutil.which("ebook-convert")),
        "kinds": kinds.KINDS,
        "device_kinds": devices.KINDS,
        "look": looks.current(h.con()),
        # The visuals' version (so a new one from GitHub is fetched) and, for someone signed in, the home town.
        "visuals": {"version": visuals.version(h),
                    "place": _home_place(h) if who else None},
        "media": {"films": h.media.configured("film"), "shows": h.media.configured("show"),
                  "jellyfin": bool(h.config.jellyfin_url and h.config.jellyfin_api_key)},
    })


def _home_place(h):
    c = h.config
    try:
        return {"name": c.home_place, "lat": float(c.home_lat), "lon": float(c.home_lon)} if c.home_lat and c.home_lon else None
    except ValueError:
        return None


@bp.get("/admin/visuals")
@admin_only
def admin_visuals(who):
    return jsonify(visuals.status(hub()))


@bp.post("/admin/visuals/check")
@admin_only
def admin_visuals_check(who):
    """Look at the visuals repository now, instead of waiting for the hourly check."""
    return jsonify(visuals.sync(hub(), force=True))


@bp.post("/setup")
def setup():
    h = hub()
    if h.profiles():
        return fail("Setup has already been done.", 409)
    data = request.get_json(silent=True) or {}
    name, passcode = db.clean_tag(data.get("name")), str(data.get("passcode") or "")
    if not name or len(passcode) < 4:
        return fail("Enter a name and a passcode of at least 4 characters.")
    con = h.con()
    cur = con.execute(
        "INSERT INTO profiles(name, role, passcode_hash, color, created) VALUES(?, 'admin', ?, ?, ?)",
        (name, generate_password_hash(passcode), COLORS[0], db.now()),
    )
    con.commit()
    session.clear()
    session["pid"] = cur.lastrowid
    session.permanent = True
    return jsonify({"ok": True})


@bp.post("/login")
def login():
    h = hub()
    data = request.get_json(silent=True) or {}
    if data.get("guest"):
        if not h.config.guest_enabled:
            return fail("Guest access is turned off.", 403)
        if h.config.guest_lan_only and not on_home_network():
            return fail("Guest access only works on the home network.", 403)
        session.clear()
        session["pid"] = "guest"
        return jsonify({"ok": True})
    target = h.profile(data.get("profile_id") or 0)
    if not target:
        return fail("That profile does not exist.", 404)

    via_admin = False
    if data.get("admin_id"):
        admin = h.profile(data["admin_id"])
        if not admin or admin["role"] != "admin":
            return fail("That is not an admin profile.", 403)
        ok = check_passcode(admin, data.get("admin_passcode"))
        via_admin = True
    elif not target["passcode_hash"]:
        ok = True
    else:
        ok = check_passcode(target, data.get("passcode"))
    if ok is None:
        return fail("Too many wrong tries. Wait a minute and try again.", 429)
    if not ok:
        return fail("That passcode is not right.", 403)
    session.clear()
    session["pid"] = target["id"]
    session["via_admin"] = via_admin
    session.permanent = True
    return jsonify({"ok": True})


@bp.post("/logout")
def logout():
    session.clear()
    return jsonify({"ok": True})


# -------------------------------------------------------------- profiles

@bp.post("/profiles")
@admin_only
def create_profile(who):
    data = request.get_json(silent=True) or {}
    name = db.clean_tag(data.get("name"))
    if not name:
        return fail("Give the profile a name.")
    con = hub().con()
    if con.execute("SELECT 1 FROM profiles WHERE name=?", (name,)).fetchone():
        return fail("There is already a profile with that name.", 409)
    count = con.execute("SELECT COUNT(*) FROM profiles").fetchone()[0]
    passcode = str(data.get("passcode") or "")
    cur = con.execute(
        "INSERT INTO profiles(name, role, passcode_hash, color, can_request, can_tag, created) VALUES(?,?,?,?,?,?,?)",
        (name, "admin" if data.get("role") == "admin" else "member",
         generate_password_hash(passcode) if passcode else None, COLORS[count % len(COLORS)],
         1 if data.get("can_request", True) else 0, 1 if data.get("can_tag", True) else 0, db.now()),
    )
    con.commit()
    return jsonify({"id": cur.lastrowid})


@bp.patch("/profiles/<int:profile_id>")
@profile_only
def update_profile(who, profile_id):
    h = hub()
    target = h.profile(profile_id)
    if not target:
        return fail("That profile does not exist.", 404)
    admin = is_admin(who)
    if not admin and who["id"] != profile_id:
        return fail("You can only change your own profile.", 403)
    data = request.get_json(silent=True) or {}
    sets, args = [], []
    if "passcode" in data:
        passcode = str(data["passcode"] or "")
        if passcode and len(passcode) < 4:
            return fail("Use at least 4 characters for a passcode.")
        sets.append("passcode_hash=?")
        args.append(generate_password_hash(passcode) if passcode else None)
    if "sync_password" in data:
        password = str(data["sync_password"] or "")
        sets.append("sync_key=?")
        args.append(kosync.sync_key_for(password) if password else None)
    if "hardcover_token" in data:
        sets.append("hardcover_token=?")
        args.append(str(data["hardcover_token"] or "").strip() or None)
    if admin:
        if "name" in data and db.clean_tag(data["name"]):
            sets.append("name=?")
            args.append(db.clean_tag(data["name"]))
        if "role" in data and data["role"] in ("admin", "member"):
            admins = [p for p in h.profiles() if p["role"] == "admin"]
            if data["role"] == "member" and target["role"] == "admin" and len(admins) <= 1:
                return fail("There has to be at least one admin.")
            sets.append("role=?")
            args.append(data["role"])
        for flag in ("can_request", "can_tag"):
            if flag in data:
                sets.append(flag + "=?")
                args.append(1 if data[flag] else 0)
        if "can_series" in data:
            series.allow(h, profile_id, bool(data["can_series"]))
        if "can_request_media" in data:
            allowed = media_allowed_ids(h)
            (allowed.add if data["can_request_media"] else allowed.discard)(profile_id)
            db.kv_set(h.con(), MEDIA_KEY, json.dumps(sorted(allowed)))
    if sets:
        con = h.con()
        try:
            con.execute("UPDATE profiles SET %s WHERE id=?" % ", ".join(sets), (*args, profile_id))
        except Exception:
            return fail("There is already a profile with that name.", 409)
        con.commit()
    return jsonify({"ok": True})


@bp.delete("/profiles/<int:profile_id>")
@admin_only
def delete_profile(who, profile_id):
    h = hub()
    if profile_id == who["id"]:
        return fail("You cannot delete the profile you are using.")
    if not h.profile(profile_id):
        return fail("That profile does not exist.", 404)
    h.backup_now()  # keep a copy of their ratings and tags before removing them
    con = h.con()
    for table in ("personal_tags", "ratings", "reading", "watch"):
        con.execute("DELETE FROM %s WHERE profile_id=?" % table, (profile_id,))
    h.devices.forget_profile(profile_id)
    goodreads.forget_profile(h, profile_id)
    recommend.forget_profile(h, profile_id)
    series.forget_profile(h, profile_id)
    allowed = media_allowed_ids(h)
    if profile_id in allowed:  # nothing left behind in the films and TV permission list
        db.kv_set(con, MEDIA_KEY, json.dumps(sorted(allowed - {profile_id})))
    con.execute("DELETE FROM profiles WHERE id=?", (profile_id,))
    con.commit()
    return jsonify({"ok": True})


def _for_profile(who, profile_id):
    """The profile an action is for: yourself, or anyone when you are an admin."""
    if not profile_id or profile_id == who["id"]:
        return who
    if not is_admin(who) or not isinstance(profile_id, int):
        return None
    return hub().profile(profile_id)


@bp.get("/profiles/<int:profile_id>/devices")
@profile_only
def list_devices(who, profile_id):
    target = _for_profile(who, profile_id)
    if not target:
        return fail("You can only see your own devices.", 403)
    h = hub()
    return jsonify({
        "profile": {"id": target["id"], "name": target["name"], "sync_ready": bool(target["sync_key"])},
        "devices": [h.devices.describe(d) for d in h.devices.rows(target["id"])],
        "mail_ready": h.config.mail_configured, "sender": h.config.mail_sender if h.config.mail_configured else "",
        "limit_mb": h.devices.limit_mb(),
    })


@bp.post("/profiles/<int:profile_id>/devices")
@profile_only
def add_device(who, profile_id):
    target = _for_profile(who, profile_id)
    if not target:
        return fail("You can only change your own devices.", 403)
    try:
        return jsonify({"id": hub().devices.add(target["id"], request.get_json(silent=True) or {})})
    except ValueError as exc:
        return fail(str(exc))


def _device_or_fail(who, device_id):
    device = hub().devices.get(device_id)
    if not device or not _for_profile(who, device["profile_id"]):
        return None
    return device


@bp.patch("/devices/<int:device_id>")
@profile_only
def change_device(who, device_id):
    device = _device_or_fail(who, device_id)
    if not device:
        return fail("That device does not exist.", 404)
    try:
        hub().devices.update(device, request.get_json(silent=True) or {})
    except ValueError as exc:
        return fail(str(exc))
    return jsonify({"ok": True})


@bp.delete("/devices/<int:device_id>")
@profile_only
def remove_device(who, device_id):
    device = _device_or_fail(who, device_id)
    if not device:
        return fail("That device does not exist.", 404)
    hub().devices.remove(device_id)
    return jsonify({"ok": True})


# ---------------------------------------------------------------- Goodreads

def _goodreads_view(who, target):
    """A profile's Goodreads link. Only its owner is shown the link itself; an
    admin helping someone is told whether one is saved and how the last sync went."""
    h = hub()
    data = goodreads.describe(h, target["id"])
    data["busy"] = target["id"] in h.goodreads_busy
    data["mine"] = target["id"] == who["id"]
    if not data["mine"]:
        data["url"] = data["user_id"] = ""
    return data


def _goodreads_sync_later(profile_id, full):
    h = hub()
    if profile_id in h.goodreads_busy:
        return False
    h.goodreads_busy.add(profile_id)
    app = current_app._get_current_object()

    def work():
        with app.app_context():
            try:
                goodreads.sync(app.hub, profile_id, full=full)
            except goodreads.GoodreadsError:
                pass  # the reason is kept with the link and shown on the page
            except Exception as exc:
                app.logger.warning("Goodreads sync stopped: %s", exc)
            finally:
                app.hub.goodreads_busy.discard(profile_id)

    threading.Thread(target=work, name="bookhub-goodreads", daemon=True).start()
    return True


@bp.get("/profiles/<int:profile_id>/goodreads")
@profile_only
def goodreads_get(who, profile_id):
    target = _for_profile(who, profile_id)
    if not target:
        return fail("You can only see your own Goodreads link.", 403)
    return jsonify(_goodreads_view(who, target))


@bp.post("/profiles/<int:profile_id>/goodreads")
@profile_only
def goodreads_set(who, profile_id):
    """Save or remove the link ({"link": ...}), or read the shelves again ({"sync": true})."""
    target = _for_profile(who, profile_id)
    if not target:
        return fail("You can only change your own Goodreads link.", 403)
    data = request.get_json(silent=True) or {}
    h = hub()
    if "link" in data:
        try:
            linked = goodreads.set_link(h, target["id"], data.get("link"))
        except goodreads.GoodreadsError as exc:
            return fail(str(exc))
        if linked:
            _goodreads_sync_later(target["id"], full=True)
    elif data.get("sync"):
        if not goodreads.link(h, target["id"]):
            return fail("Save a Goodreads link first.")
        _goodreads_sync_later(target["id"], full=True)
    return jsonify(_goodreads_view(who, target))


@bp.post("/profiles/<int:profile_id>/goodreads/import")
@profile_only
def goodreads_import(who, profile_id):
    """Take a Goodreads export file, for people whose profile is private."""
    target = _for_profile(who, profile_id)
    if not target:
        return fail("You can only import into your own profile.", 403)
    upload = request.files.get("file")
    if not upload:
        return fail("Choose the export file first.")
    try:
        items = goodreads.parse_csv(upload.read(20 * 1024 * 1024).decode("utf-8", "replace"))
    except (goodreads.GoodreadsError, csv.Error) as exc:
        return fail(str(exc))
    changed = goodreads.apply(hub(), target["id"], items)
    return jsonify({"books": len(items), "changed": changed})


@bp.get("/admin/elsewhere")
@admin_only
def admin_elsewhere(who):
    """Books on people's Goodreads shelves that the library does not hold, and whose they are."""
    h = hub()
    names = h.profile_names()
    books = [{"key": key, "title": e["title"], "author": e["author"], "goodreads_id": e["gr_id"],
              "people": [names.get(p, "?") for p in e["profiles"]]} for key, e in goodreads.elsewhere(h).items()]
    books.sort(key=lambda b: (-len(b["people"]), b["title"].lower()))
    return jsonify({"books": books})


@bp.post("/me/tracker-sync")
@profile_only
def tracker_sync(who):
    try:
        count = integrations.hardcover_sync(hub(), who)
    except integrations.IntegrationError as exc:
        return fail(str(exc), 502)
    return jsonify({"updated": count})


@bp.post("/me/refresh")
@profile_only
def refresh_mine(who):
    """The Refresh button on Personal and My books: catch up now instead of at the next background round.
    Reads the library again (a book that has just arrived), fetches Hardcover if this person linked it, and
    starts putting together the comic series on their list, so their reading app's next check finds them."""
    h = hub()
    h.library.refresh()
    out = {"hardcover": None, "hardcover_error": "", "series_ready": 0, "series_making": 0}
    if who["hardcover_token"]:
        try:
            out["hardcover"] = integrations.hardcover_sync(h, who)
        except integrations.IntegrationError as exc:
            out["hardcover_error"] = str(exc)
    for item in _my_series(h, who):
        for part in item["parts"]:
            out["series_ready" if part["state"] == "ready" else "series_making"] += 1
    return jsonify(out)


def _my_series(h, who):
    """The comic series on this person's list and where each part stands for their reading app (starting any
    not started). [{name, parts: [{title, state, percent, error}]}]"""
    from . import opds
    shelf = h.devices.shelf(who["id"])
    books = [b for b in (h.library.by_key(k) for k in shelf) if b]
    return [{"name": item["series"], "parts": opds.series_status(h, item["volumes"])}
            for item in opds._gather(books, h.library.books()) if "volumes" in item]


@bp.get("/me/series")
@profile_only
def my_series(who):
    """For My books to show, and look at again while a series is being put together."""
    return jsonify({"series": _my_series(hub(), who)})


@bp.post("/me/outside")
@profile_only
def add_outside(who):
    """Record a book read somewhere else (for example on Kindle)."""
    data = request.get_json(silent=True) or {}
    title = db.clean_tag(data.get("title"))
    if not title:
        return fail("Enter the book's title.")
    author = db.clean_tag(data.get("author"))
    key = db.make_key(title, author)
    match = next((b for b in hub().library.books() if db.make_key(b["title"], b["author"]) == key), None)
    if match:
        key = match["key"]
    pages = data.get("pages")
    hub().save_reading(
        who["id"], key, status=data.get("status") or "finished", source="manual",
        percent=float(data["percent"]) if data.get("percent") not in (None, "") else None,
        finished=(data.get("finished") or None), title=title, author=author,
        pages=int(pages) if str(pages or "").isdigit() else None,
    )
    return jsonify({"ok": True, "book_key": key})


@bp.get("/me/shelf")
@profile_only
def my_shelf(who):
    """Where each book on this person's list stands on each of their devices."""
    h = hub()
    shelf = h.devices.shelf(who["id"])
    books = {}
    for book in h.library.books():
        if book["key"] in shelf:
            books[book["id"]] = {"added": shelf[book["key"]], "devices": h.devices.book_view(who["id"], book)["devices"]}
    return jsonify({"devices": [{"id": d["id"], "name": d["name"], "kind": d["kind"]} for d in h.devices.rows(who["id"])],
                    "books": books})


# ----------------------------------------------------------------- books

def _user_maps(h, who):
    con = h.con()
    if who == "guest":
        return {}, {}
    reading = {r["book_key"]: r for r in h.reading_rows(who["id"])}
    ratings = {r["book_key"]: r["rating"] for r in
               con.execute("SELECT book_key, rating FROM ratings WHERE profile_id=?", (who["id"],))}
    return reading, ratings


def _summary(book, tags, reading, ratings, pages, kind_of, shelf=()):
    r = reading.get(book["key"])
    return {
        "id": book["id"], "key": book["key"], "title": book.get("shown_title") or book["title"],
        "author": book.get("shown_author", book["author"]), "part": book.get("part", ""), "subtitle": book.get("subtitle", ""),
        "kind": kind_of.get(book["key"], kinds.UNSORTED),
        "series": book["series"], "series_index": book.get("series_index"), "collection": book.get("collection") or book["series"],
        "formats": sorted(book["formats"]), "has_cover": book["has_cover"],
        "in_library": True, "added": book["added"][:10], "pages": pages.get(book["key"]),
        "tags": tags.get(book["key"], {"epub": book["tags"], "community": [], "family": [], "mine": []}),
        "status": r["status"] if r else "none", "percent": r["percent"] if r else 0,
        "finished": r["finished"] if r else None, "device": r["device"] if r else None,
        "rating": ratings.get(book["key"], 0), "on_shelf": book["key"] in shelf,
    }


@bp.get("/home")
@signed_in
def home(who):
    """What is newest on the family server, for the Home page."""
    h = hub()
    library = sorted(h.library.books(), key=lambda b: b["added"], reverse=True)
    chosen = h.covers.index()
    month = date.today().isoformat()[:7]
    return jsonify({
        "books": [{"id": b["id"], "title": b.get("shown_title") or b["title"], "author": b.get("shown_author", b["author"]), "added": b["added"][:10],
                   "formats": sorted(b["formats"]), "has_cover": b["has_cover"] or b["key"] in chosen,
                   "cover_v": chosen.get(b["key"])} for b in library[:12]],
        "books_month": sum(1 for b in library if b["added"][:7] == month),
    })


@bp.get("/books")
@signed_in
def books(who):
    h = hub()
    pid = None if who == "guest" else who["id"]
    tags = h.tag_index(pid)
    reading, ratings = _user_maps(h, who)
    pages = {r["book_key"]: r["pages"] for r in h.con().execute("SELECT book_key, pages FROM book_meta")}
    kind_of = h.kind_index()
    shelf = h.devices.shelf(pid) if pid else {}
    items = [_summary(b, tags, reading, ratings, pages, kind_of, shelf) for b in h.library.books()]

    keys = h.library.keys()
    # Guests see the library and nothing else. Everyone else also sees books somebody
    # in the family has on Goodreads but the library lacks, without being told whose.
    elsewhere = goodreads.elsewhere(h) if pid else {}
    mine = goodreads.book_ids(h, pid) if pid else {}

    def outside(key, title, author, book_pages, r=None):
        return {
            "id": None, "key": key, "title": title or key, "author": author or "", "series": "",
            "kind": kind_of.get(key, kinds.UNSORTED),
            "formats": [], "has_cover": False, "in_library": False, "added": "", "pages": book_pages,
            "tags": tags.get(key, {"epub": [], "community": [], "family": [], "mine": []}),
            "status": r["status"] if r else "none", "percent": r["percent"] if r else 0,
            "finished": r["finished"] if r else None, "device": None,
            "rating": ratings.get(key, 0), "on_shelf": False, "kindle": key in elsewhere,
            "goodreads_id": mine.get(key) or (elsewhere[key]["gr_id"] if key in elsewhere else ""),
        }

    chosen = h.covers.index()
    for item in items:
        item["kindle"] = False
        item["goodreads_id"] = mine.get(item["key"], "")
        if item["key"] in chosen:  # a cover picked in Family Hub; the date makes browsers fetch the new picture
            item["has_cover"], item["cover_v"] = True, chosen[item["key"]]
    for key, r in reading.items():  # books read elsewhere (Kindle and so on)
        if key in keys or key.startswith("doc:"):
            continue
        items.append(outside(key, r["title"], r["author"], r["pages"], r))
    for key, e in elsewhere.items():
        if key not in reading:
            items.append(outside(key, e["title"], e["author"], e["pages"]))

    public = readers.index(h)
    for item in items:
        item["readers"] = public.get(item["key"])  # what readers at large think; None when nobody knows

    q = (request.args.get("q") or "").strip().lower()
    tag = (request.args.get("tag") or "").strip().lower()
    scopes = [s for s in (request.args.get("scopes") or "epub,community,family,mine").split(",") if s]
    flt = request.args.get("filter") or "all"
    only_kinds = {k for k in (request.args.get("kinds") or "").split(",") if k}
    series = (request.args.get("series") or "").strip().lower()
    collection = (request.args.get("collection") or "").strip().lower()
    try:
        terms = search.clean(json.loads(request.args.get("terms") or "[]"))
    except ValueError:
        terms = []
    matches = search.matcher(terms)

    def series_tags(item):
        """Tags put on the item's series (or set of runs) as a whole."""
        out = {"family": [], "mine": []}
        for name in {item.get("series") or "", item.get("collection") or ""} - {""}:
            got = tags.get(series_key(name))
            if got:
                out["family"] += got["family"]
                out["mine"] += got["mine"]
        return out

    def keep(item):
        if only_kinds and item["kind"] not in only_kinds:
            return False
        if series and (item.get("series") or "").lower() != series:
            return False
        if collection and (item.get("collection") or "").lower() != collection:
            return False
        if q:
            hay = " ".join([item["title"], item["author"], item["series"], item.get("subtitle") or ""] +
                           [t for g in item["tags"].values() for t in g]).lower()
            if q not in hay:
                return False
        if tag and not any(t.lower() == tag for s in scopes for t in item["tags"].get(s, []) + series_tags(item).get(s, [])):
            return False
        if terms and not matches(item, series_tags(item)):
            return False
        return {
            "all": True,
            "library": item["in_library"],
            "shelf": item["on_shelf"],
            "outside": not item["in_library"] and item["status"] != "none",
            "kindle": item["kindle"],
            "reading": item["status"] == "reading",
            "unread": item["status"] == "none",
            "read": item["status"] == "finished",
            "rated4": item["rating"] >= 4,
            "pdf": "PDF" in item["formats"] and "EPUB" not in item["formats"],
        }.get(flt, True)

    items = [i for i in items if keep(i)]
    if flt == "foryou" and pid:
        # Unread library books, best match with what this person has finished and rated first.
        mine = recommend.for_you(h, pid)
        items = [dict(i, why=mine["books"][i["key"]]["why"]) for i in items if i["in_library"] and i["key"] in mine["books"]]
        items.sort(key=lambda i: -mine["books"][i["key"]]["score"])
        return jsonify({"books": items, "total": len(items),
                        "taste": {"ready": mine["ready"], "have": mine["have"], "need": mine["need"]}})
    if series or collection:  # one series or set, run by run, in reading order
        items.sort(key=lambda i: ((i.get("series") or "").lower(), i.get("series_index") or 0, i["title"].lower()))
    # The series' own ratings and tags, by series name in lower case, for the series cards.
    info = {}
    for name in {n for i in items for n in (i.get("series"), i.get("collection")) if n}:
        k = series_key(name)
        info[name.lower()] = {"rating": ratings.get(k, 0),
                              "tags": {"family": tags[k]["family"], "mine": tags[k]["mine"]} if k in tags else {"family": [], "mine": []}}
    return jsonify({"books": items, "total": len(items), "series_info": info})


def series_key(name):
    """A series (or set of runs) is tagged and rated as a whole under this key, beside its volumes' own."""
    return "series:" + db.slug(name)


def _series_name():
    data = request.get_json(silent=True) or {}
    asked = " ".join(str(data.get("name") or request.args.get("name") or "").split()).lower()
    # The library's own spelling of the series; a long name is used whole, not cut like a tag.
    for book in hub().library.books():
        for name in (book["series"] or "", book.get("collection") or ""):
            if asked and name.lower() == asked:
                return name, data
    if asked and hub().series.configured:  # a series followed chapter by chapter is tagged and rated the same way
        try:
            for s in hub().series.shelf()["series"]:
                if s["title"].lower() == asked:
                    return s["title"], data
        except series.SeriesError:
            pass
    return "", data


@bp.get("/series-info")
@signed_in
def series_info(who):
    """A series' own tags and rating, and the average of the ratings given to its volumes."""
    name, _ = _series_name()
    if not name:
        return fail("Nothing in the library belongs to that series.", 404)
    h = hub()
    pid = None if who == "guest" else who["id"]
    key = series_key(name)
    tagged = h.tag_index(pid).get(key, {"family": [], "mine": []})
    reading, ratings = _user_maps(h, who)
    low = name.lower()
    vols = [ratings.get(b["key"]) for b in h.library.books() if low in ((b["series"] or "").lower(), (b.get("collection") or "").lower())]
    rated = [r for r in vols if r]
    return jsonify({"name": name, "key": key, "tags": {"family": tagged["family"], "mine": tagged["mine"]},
                    "rating": ratings.get(key, 0), "volume_average": round(sum(rated) / len(rated), 1) if rated else None,
                    "volumes_rated": len(rated), "volumes": len(vols)})


@bp.post("/series-info/tags")
@profile_only
def series_add_tag(who):
    name, data = _series_name()
    if not name:
        return fail("Nothing in the library belongs to that series.", 404)
    kind = "family" if data.get("kind") == "family" else "mine"
    if kind == "family" and not (who["can_tag"] or is_admin(who)):
        return fail("This profile cannot change family tags.", 403)
    h = hub()
    h.ensure_meta(series_key(name), title=name, source="series")
    if not h.add_tag(kind, series_key(name), data.get("tag"), who["id"]):
        return fail("Type a tag first.")
    return jsonify({"ok": True})


@bp.delete("/series-info/tags")
@profile_only
def series_remove_tag(who):
    name, data = _series_name()
    if not name:
        return fail("Nothing in the library belongs to that series.", 404)
    kind = "family" if data.get("kind") == "family" else "mine"
    if kind == "family" and not (who["can_tag"] or is_admin(who)):
        return fail("This profile cannot change family tags.", 403)
    hub().remove_tag(kind, series_key(name), data.get("tag") or "", who["id"])
    return jsonify({"ok": True})


@bp.post("/series-info/rating")
@profile_only
def series_rate(who):
    name, data = _series_name()
    if not name:
        return fail("Nothing in the library belongs to that series.", 404)
    h = hub()
    h.ensure_meta(series_key(name), title=name, source="series")
    h.set_rating(series_key(name), who["id"], int(data.get("rating") or 0))
    return jsonify({"ok": True})


def _book_or_404(book_id):
    return hub().library.get(book_id)


@bp.delete("/books/<int:book_id>")
@admin_only
def delete_book(who, book_id):
    """Delete a book from the library, with every copy of it. Admins only; the library's database is copied first."""
    h = hub()
    book = _book_or_404(book_id)
    if not book:
        return fail("That book is not in the library.", 404)
    from . import librarymanager
    if not librarymanager.configured(h.config):
        return fail("The library manager's sign-in is not set up yet. Add it under Admin, Hub, Connections, Library manager.")
    ids = [book["id"]] + [c["id"] for c in book["copies"]]
    done = 0
    try:
        saved = h.duplicates.backup()
        for entry in ids:
            h.manager.delete(entry)
            done += 1
    except (OSError, librarymanager.ManagerError) as exc:
        if done:
            return fail("%s %d of %d entries were already deleted before it stopped." % (exc, done, len(ids)), 502)
        return fail(str(exc), 502)
    finally:
        h.library.refresh(force=True)
    return jsonify({"removed": book["title"], "entries": len(ids), "backup": saved.name})


@bp.get("/books/<int:book_id>")
@signed_in
def book_detail(who, book_id):
    h = hub()
    book = _book_or_404(book_id)
    if not book:
        return fail("That book is not in the library.", 404)
    if book["copy_of"]:  # an old link to a copy: show the book it is a copy of
        book = h.library.get(book["copy_of"]) or book
        book_id = book["id"]
    pid = None if who == "guest" else who["id"]
    tags = h.tag_index(pid)
    reading, ratings = _user_maps(h, who)
    con = h.con()
    pages = {book["key"]: (con.execute("SELECT pages FROM book_meta WHERE book_key=?", (book["key"],)).fetchone() or [None])[0]}
    data = _summary(book, tags, reading, ratings, pages, h.kind_index(), h.devices.shelf(pid) if pid else {})
    data["synopsis"] = book["synopsis"]
    data["pubdate"] = book["pubdate"]
    data["goodreads_id"] = goodreads.book_ids(h, pid).get(book["key"], "") if pid else ""
    data["readers"] = readers.index(h).get(book["key"])
    chosen = h.covers.row(book["key"])
    data["own_cover"] = chosen["source"] if chosen else ""
    if chosen:
        data["has_cover"], data["cover_v"] = True, chosen["at"]
    names = h.profile_names()
    data["family_ratings"] = [] if who == "guest" else [
        {"name": names.get(r["profile_id"], "?"), "rating": r["rating"]}
        for r in con.execute("SELECT profile_id, rating FROM ratings WHERE book_key=? ORDER BY profile_id", (book["key"],))
        if r["profile_id"] != who["id"]
    ]
    data["community_sources"] = sorted({catalogues.label(r["source"]) for r in con.execute(
        "SELECT source FROM community_tags WHERE book_key=?", (book["key"],))})
    data["can_look_up"] = bool(catalogues.active(h.config))
    if pid:
        data["shelf"] = h.devices.book_view(pid, book)
    if is_admin(who):  # admins look after other people's shelves, such as Nan's
        theirs = {r["profile_id"]: r["status"] for r in con.execute(
            "SELECT profile_id, status FROM reading WHERE book_key=?", (book["key"],))}
        data["family_shelves"] = [
            dict(h.devices.book_view(p["id"], book), profile_id=p["id"], name=p["name"], status=theirs.get(p["id"], "none"))
            for p in h.profiles() if p["id"] != pid]
    if book["series"]:  # the whole series' own tags and rating apply to each of its volumes
        key = series_key(book["series"])
        own = tags.get(key, {})
        data["from_series"] = {"name": book["series"], "rating": ratings.get(key) or 0,
                               "tags": {"family": own.get("family", []), "mine": own.get("mine", [])}}
    data["converted"] = (h.config.converted_dir / ("%d.epub" % book_id)).exists()
    data["convert_status"] = db.kv_get(con, "convert:%d" % book_id)
    if is_admin(who):
        data["copies"] = [{"id": c["id"], "added": c["added"][:10],
                           "files": [{"format": f, "size": c["sizes"].get(f, 0)} for f in sorted(c["own_formats"])]}
                          for c in book["copies"]]
    return jsonify(data)


@bp.get("/books/<int:book_id>/cover")
@signed_in
def cover(who, book_id):
    book = _book_or_404(book_id)
    path = (hub().covers.path(book["key"]) or hub().library.cover_path(book)) if book else None
    if not path:
        return "", 404
    return send_file(path, mimetype="image/jpeg", max_age=86400)


def _cover_book(who, book_id):
    """(book, None) when this profile may change the book's cover, else (None, error)."""
    book = _book_or_404(book_id)
    if not book:
        return None, fail("That book is not in the library.", 404)
    if not (who["can_tag"] or is_admin(who)):
        return None, fail("This profile cannot change covers.", 403)
    return book, None


@bp.get("/books/<int:book_id>/cover/options")
@profile_only
def cover_options(who, book_id):
    """Covers Open Library has for this book. Only the title, author and ISBN are sent."""
    book, error = _cover_book(who, book_id)
    if error:
        return error
    try:
        found = covers.options(book)
    except covers.CoverError as exc:
        return fail(str(exc), 502)
    return jsonify({"options": found})


@bp.get("/covers/preview")
@profile_only
def cover_preview(who):
    """Pass one Open Library picture through, so browsers at home never contact it themselves."""
    try:
        data = covers.fetch(request.args.get("url"))
    except covers.CoverError as exc:
        return fail(str(exc), 404)
    return send_file(io.BytesIO(data), mimetype="image/jpeg", max_age=86400)


@bp.post("/books/<int:book_id>/cover")
@profile_only
def set_cover(who, book_id):
    """Give a book its own cover: an uploaded picture, one from Open Library ({"url"}),
    or one made from the title ({"generated": true})."""
    book, error = _cover_book(who, book_id)
    if error:
        return error
    data = request.get_json(silent=True) or {}
    upload = request.files.get("file")
    try:
        if upload:
            picture, source = upload.read(covers.MAX_BYTES + 1), "upload"
        elif data.get("url"):
            picture, source = covers.fetch(data["url"]), "openlibrary"
        elif data.get("generated"):
            picture, source = covers.title_cover(book["title"], book["author"]), "generated"
        else:
            return fail("Choose a picture first.")
        hub().covers.save(book["key"], picture, source, who["id"])
    except covers.CoverError as exc:
        return fail(str(exc))
    return jsonify({"ok": True, "source": source})


@bp.delete("/books/<int:book_id>/cover")
@profile_only
def remove_cover(who, book_id):
    book, error = _cover_book(who, book_id)
    if error:
        return error
    hub().covers.remove(book["key"])
    return jsonify({"ok": True})


@bp.get("/books/<int:book_id>/download/<fmt>")
@signed_in
def download(who, book_id, fmt):
    h = hub()
    book = _book_or_404(book_id)
    if not book:
        return fail("That book is not in the library.", 404)
    if fmt.lower() == "converted":
        path = h.config.converted_dir / ("%d.epub" % book_id)
        ext = "epub"
        if not path.exists():
            return fail("There is no converted copy yet.", 404)
    else:
        path = h.library.file_path(book, fmt)
        ext = fmt.lower()
        if not path:
            return fail("That format is not available.", 404)
    # The name the file is saved under: no slashes or other characters a computer refuses, no "Unknown" author.
    author = "" if book["author"].lower() in ("", "unknown") else book["author"]
    return send_file(path, as_attachment=True, download_name=devices.safe_filename(book["title"], author, ext))


@bp.get("/books/series-file")
@signed_in
def download_series(who):
    """Every volume of a series in one file: joined into one comic for comics, one ZIP for anything else."""
    from . import seriesfile
    name = (request.args.get("series") or "").strip()
    volumes = sorted((b for b in hub().library.books() if name and (b["series"] or "").lower() == name.lower()),
                     key=lambda b: (b.get("series_index") or 0, b["title"].lower()))
    if not volumes:
        return fail("Nothing in the library belongs to that series.", 404)
    try:
        path, download_name = seriesfile.build(hub().library, volumes, volumes[0]["series"])
    except seriesfile.SeriesFileError as exc:
        return fail(str(exc), 422)
    response = send_file(path, as_attachment=True, download_name=download_name)
    response.call_on_close(lambda: path.unlink(missing_ok=True))
    return response


@bp.post("/books/<int:book_id>/tags")
@profile_only
def add_tag(who, book_id):
    book = _book_or_404(book_id)
    if not book:
        return fail("That book is not in the library.", 404)
    data = request.get_json(silent=True) or {}
    kind = "family" if data.get("kind") == "family" else "mine"
    if kind == "family" and not (who["can_tag"] or is_admin(who)):
        return fail("This profile cannot change family tags.", 403)
    if not hub().add_tag(kind, book["key"], data.get("tag"), who["id"]):
        return fail("Type a tag first.")
    return jsonify({"ok": True})


@bp.delete("/books/<int:book_id>/tags")
@profile_only
def remove_tag(who, book_id):
    book = _book_or_404(book_id)
    if not book:
        return fail("That book is not in the library.", 404)
    data = request.get_json(silent=True) or {}
    kind = "family" if data.get("kind") == "family" else "mine"
    if kind == "family" and not (who["can_tag"] or is_admin(who)):
        return fail("This profile cannot change family tags.", 403)
    hub().remove_tag(kind, book["key"], data.get("tag") or "", who["id"])
    return jsonify({"ok": True})


@bp.post("/books/<int:book_id>/lookup")
@profile_only
def look_up_tags(who, book_id):
    """Ask the catalogues about one book again, now."""
    book = _book_or_404(book_id)
    if not book:
        return fail("That book is not in the library.", 404)
    if not (who["can_tag"] or is_admin(who)):
        return fail("This profile cannot change tags.", 403)
    h = hub()
    if not catalogues.active(h.config):
        return fail("Looking up tags is switched off. An admin can turn it on under Admin, Hub, Connections.")
    result = h.catalogues.look_up(book, force=True)
    failed = [catalogues.label(s) for s, n in result.items() if n is None]
    return jsonify({"added": sum(n or 0 for n in result.values()), "failed": failed,
                    "sources": [catalogues.label(s) for s, n in result.items() if n]})


@bp.post("/books/kind")
@profile_only
def set_kind(who):
    """Set the kind of one or many books, and optionally add a family tag to them."""
    if not (who["can_tag"] or is_admin(who)):
        return fail("This profile cannot change how books are sorted.", 403)
    data = request.get_json(silent=True) or {}
    kind, tag = data.get("kind"), db.clean_tag(data.get("tag"))
    if kind and kind not in kinds.KINDS:
        return fail("Unknown kind of book.")
    if not kind and not tag:
        return fail("Choose a kind or type a tag.")
    h = hub()
    count = 0
    for book_id in data.get("ids") or []:
        book = h.library.get(book_id) if isinstance(book_id, int) else None
        if not book:
            continue
        if kind:
            h.set_kind(book["key"], kind)
        if tag:
            h.add_tag("family", book["key"], tag, who["id"])
        count += 1
    h.con().commit()
    h.touch()
    return jsonify({"updated": count})


@bp.post("/books/<int:book_id>/rating")
@profile_only
def rate(who, book_id):
    book = _book_or_404(book_id)
    if not book:
        return fail("That book is not in the library.", 404)
    data = request.get_json(silent=True) or {}
    hub().set_rating(book["key"], who["id"], int(data.get("rating") or 0))
    return jsonify({"ok": True})


@bp.post("/books/<int:book_id>/reading")
@profile_only
def set_reading(who, book_id):
    book = _book_or_404(book_id)
    if not book:
        return fail("That book is not in the library.", 404)
    data = request.get_json(silent=True) or {}
    status = data.get("status")
    if status not in ("reading", "finished", "none", None):
        return fail("Unknown status.")
    target = _for_profile(who, data.get("profile_id"))
    if not target:
        return fail("Only an admin can set this for someone else.", 403)
    percent = data.get("percent")
    hub().save_reading(target["id"], book["key"], status=status,
                       percent=float(percent) if percent not in (None, "") else None, source="manual")
    return jsonify({"ok": True})


@bp.post("/books/<int:book_id>/shelf")
@profile_only
def set_shelf(who, book_id):
    """Add a book to "My books" or take it off. Admins can do this for anyone."""
    book = _book_or_404(book_id)
    if not book:
        return fail("That book is not in the library.", 404)
    data = request.get_json(silent=True) or {}
    target = _for_profile(who, data.get("profile_id"))
    if not target:
        return fail("Only an admin can change someone else's books.", 403)
    h = hub()
    result = h.devices.set_shelf(target["id"], book, bool(data.get("on")), by=who["id"])
    if result["sending"]:
        h.devices.kick(current_app._get_current_object())
    return jsonify(result)


@bp.post("/books/<int:book_id>/send")
@profile_only
def send_to_device(who, book_id):
    book = _book_or_404(book_id)
    if not book:
        return fail("That book is not in the library.", 404)
    data = request.get_json(silent=True) or {}
    device = _device_or_fail(who, data.get("device_id") if isinstance(data.get("device_id"), int) else 0)
    if not device:
        return fail("That device does not exist.", 404)
    h = hub()
    try:
        h.devices.queue(device, book)
    except ValueError as exc:
        return fail(str(exc))
    h.devices.kick(current_app._get_current_object())
    return jsonify({"ok": True, "device": device["name"]})


@bp.post("/books/<int:book_id>/convert")
@profile_only
def convert(who, book_id):
    h = hub()
    book = _book_or_404(book_id)
    if not book:
        return fail("That book is not in the library.", 404)
    source = h.library.file_path(book, "PDF")
    if not source:
        return fail("This book has no PDF to convert.")
    tool = shutil.which("ebook-convert")
    if not tool:
        hint = " Open it in the library manager and use its Convert button." if h.config.cwa_url else ""
        return fail("The converter is not installed in Family Hub." + hint, 501)
    h.config.converted_dir.mkdir(parents=True, exist_ok=True)
    target = h.config.converted_dir / ("%d.epub" % book_id)
    key = "convert:%d" % book_id
    db.kv_set(h.con(), key, "running")
    app = current_app._get_current_object()

    def work():
        with app.app_context():
            try:
                subprocess.run([tool, str(source), str(target)], check=True, timeout=1800,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                db.kv_set(app.hub.con(), key, "done")
            except Exception:
                target.unlink(missing_ok=True)
                db.kv_set(app.hub.con(), key, "failed")

    threading.Thread(target=work, daemon=True).start()
    return jsonify({"ok": True, "status": "running"})


@bp.get("/tags")
@signed_in
def tags(who):
    h = hub()
    pid = None if who == "guest" else who["id"]
    scopes = [s for s in (request.args.get("scopes") or "epub,community,family,mine").split(",") if s]
    q = (request.args.get("q") or "").strip().lower()
    keys = set(h.library.keys())
    if pid:  # books this profile read elsewhere are listed in the library too
        keys |= {r["book_key"] for r in h.reading_rows(pid)}
    wanted = {k for k in (request.args.get("kinds") or "").split(",") if k}
    if wanted:  # only the tags of the part of the library being looked at
        kind_of = h.kind_index()
        keys = {k for k in keys if kind_of.get(k, kinds.UNSORTED) in wanted}
    counts = {}
    for key, groups in h.tag_index(pid).items():
        if key not in keys and not key.startswith("series:"):  # a series' own tags count too
            continue
        for scope in scopes:
            for tag in groups.get(scope, []):
                if q and q not in tag.lower():
                    continue
                entry = counts.setdefault((tag.lower(), scope), {"tag": tag, "source": scope, "count": 0})
                entry["count"] += 1
    result = sorted(counts.values(), key=lambda e: (-e["count"], e["tag"].lower()))
    return jsonify({"tags": result[:120]})


# ----------------------------------------------------------------- stats

@bp.get("/stats/me")
@profile_only
def stats_me(who):
    return jsonify(hub().personal_stats(who["id"], request.args.get("kind"), request.args.get("value")))


@bp.get("/stats/admin")
@admin_only
def stats_admin(who):
    h = hub()
    data = h.admin_stats()
    data["attention"] = h.intake.attention_items(limit=50)
    kind_of = h.kind_index()
    data["kinds"] = [[k, sum(1 for b in h.library.books() if kind_of.get(b["key"], kinds.UNSORTED) == k)] for k in kinds.KINDS]
    machine = h.machine_tags()
    data["tags"] = {
        "lookups": h.catalogues.progress(),
        "community": sum(1 for t in machine["tags"] if t["sources"] != ["Book file"] and t["becomes"] is None),
        "file": sum(1 for t in machine["tags"] if "Book file" in t["sources"] and t["becomes"] is None),
        "removed": len(machine["removed"]), "hidden": sum(1 for t in machine["tags"] if t["becomes"] is not None),
    }
    data["elsewhere"] = len(goodreads.elsewhere(h))
    sets = h.copy_sets()
    data["copies"] = {
        "sets": sum(1 for s in sets if s["together"] or s["partly"]),
        "hidden": len(h.library.all_books()) - len(h.library.books()),
        "apart": sum(1 for s in sets if not s["together"]),
        "spare": sum(s["spare"] for s in sets),
    }
    data["services"] = {
        "library": h.library.available,
        "search": integrations.prowlarr_status(h.config),
        "series": h.series.status(),
        "second_copy": secondcopy.status(h),
        "downloads_folder": bool(h.config.downloads_dir and h.config.downloads_dir.is_dir()),
        "ingest_folder": bool(h.config.ingest_dir and h.config.ingest_dir.is_dir()),
        "ingest_waiting": h.intake.waiting(h.config.ingest_dir),
        "ingest_stuck": h.ingest_stuck,
        "inbox_waiting": h.intake.waiting(h.config.inbox_dir),
        "remote_guest_blocked": h.config.guest_lan_only,
    }
    return jsonify(data)


@bp.get("/similar/<int:book_id>")
@signed_in
def similar(who, book_id):
    book = _book_or_404(book_id)
    if not book:
        return fail("That book is not in the library.", 404)
    pid = None if who == "guest" else who["id"]
    return jsonify({"base": book["title"], "books": hub().similar(book["key"], pid)})


@bp.get("/similar-by-key")
@profile_only
def similar_by_key(who):
    key = request.args.get("key") or ""
    return jsonify({"books": hub().similar(key, who["id"])})


# ------------------------------------------------------- recommendations

@bp.get("/recommendations")
@profile_only
def recommendations(who):
    """This week's books the library does not have, picked for this person."""
    h = hub()
    mine = recommend.taste(h, who["id"])
    data = recommend.listing(h, who["id"])
    data.update(ready=mine["ready"], have=mine["have"], need=mine["need"], on=h.config.tag_lookups == "on")
    return jsonify(data)


@bp.post("/recommendations/refresh")
@profile_only
def recommendations_refresh(who):
    h = hub()
    try:
        count = recommend.refresh(h, who["id"])
    except catalogues.LookupError_ as exc:
        return fail("Open Library could not be reached: %s" % exc, 502)
    if count is None and h.config.tag_lookups != "on":
        return fail("Looking things up outside is switched off. An admin can turn it on under Admin, Hub, Connections.")
    return recommendations.__wrapped__(who)


@bp.post("/recommendations/dismiss")
@profile_only
def recommendations_dismiss(who):
    recommend.dismiss(hub(), who["id"], (request.get_json(silent=True) or {}).get("key") or "")
    return jsonify({"ok": True})


# -------------------------------------------------------------- discover

def can_request(who):
    return who not in (None, "guest") and (who["role"] == "admin" or who["can_request"])


def query_variants(q):
    """The spellings to ask the sites for. They match whole words exactly, so "Saviors" misses "Savior's" and "Cafe"
    may miss "Café". Asked for as typed; with accents and apostrophes taken out; and, for a long title, without its
    first word or two (which is where possessives and "The" usually are)."""
    import unicodedata
    folded = unicodedata.normalize("NFKD", q).encode("ascii", "ignore").decode()
    folded = re.sub(r"['’`]", "", folded)
    folded = re.sub(r"[^A-Za-z0-9]+", " ", folded).strip()
    words = folded.split()
    out = [q]
    for v in (folded, " ".join(words[1:]) if len(words) >= 4 else "", " ".join(words[2:]) if len(words) >= 5 else ""):
        if v and v.lower() not in [x.lower() for x in out]:
            out.append(v)
    return out


def search_variants(config, q):
    """Ask for every spelling at once and put the answers together, the closest matches to what was typed first."""
    from concurrent.futures import ThreadPoolExecutor
    variants = query_variants(q)
    with ThreadPoolExecutor(max_workers=len(variants)) as pool:
        answers = list(pool.map(lambda v: _try_search(config, v), variants))
    if all(isinstance(a, integrations.IntegrationError) for a in answers):
        raise answers[0]
    typed = set(db.slug(query_variants(q)[1] if len(variants) > 1 else q).split("-"))
    merged, seen = [], set()
    for answer in answers:
        for r in answer if isinstance(answer, list) else []:
            key = r.get("guid") or r["title"]
            if key not in seen:
                seen.add(key)
                merged.append(r)
    score = lambda r: len(typed & set(db.slug(r["title"]).split("-")))  # noqa: E731
    merged.sort(key=lambda r: (-score(r), r["format"] != "EPUB", -(r["seeders"] or 0)))
    return merged


def _try_search(config, q):
    try:
        return integrations.prowlarr_search(config, q)
    except integrations.IntegrationError as exc:
        return exc


@bp.get("/discover/search")
@profile_only
def discover_search(who):
    if not can_request(who):
        return fail("This profile cannot search for new books.", 403)
    q = (request.args.get("q") or "").strip()
    if len(q) < 2:
        return fail("Type a title or author.")
    h = hub()
    try:
        results = search_variants(h.config, q)
    except integrations.IntegrationError as exc:
        return fail(str(exc), 502)
    titles = [(db.slug(b["title"]), b["id"]) for b in h.library.books()]
    for r in results:
        s = db.slug(r["title"])
        r["library_id"] = next((bid for t, bid in titles if len(t) > 3 and t in s), None)
    return jsonify({"results": results})


@bp.post("/discover/grab")
@profile_only
def discover_grab(who):
    if not can_request(who):
        return fail("This profile cannot download new books.", 403)
    data = request.get_json(silent=True) or {}
    if not data.get("guid") or data.get("indexer_id") is None:
        return fail("Pick a result first.")
    h = hub()
    try:
        integrations.prowlarr_grab(h.config, data["guid"], data["indexer_id"])
    except integrations.IntegrationError as exc:
        return fail(str(exc), 502)
    con = h.con()
    con.execute("INSERT INTO requests(profile_id, title, indexer, guid, status, created) VALUES(?,?,?,?,?,?)",
                (who["id"], str(data.get("title") or "")[:300], str(data.get("indexer") or "")[:80],
                 str(data["guid"])[:500], "sent", db.now()))
    con.commit()
    return jsonify({"ok": True})


@bp.get("/discover/queue")
@profile_only
def discover_queue(who):
    h = hub()
    names = h.profile_names()
    con = h.con()
    requests_ = [{"title": r["title"], "indexer": r["indexer"], "by": names.get(r["profile_id"], "?"),
                  "created": r["created"]} for r in
                 con.execute("SELECT * FROM requests ORDER BY id DESC LIMIT 15")]
    imported = [{"name": r["path"].replace("\\", "/").split("/")[-1], "at": r["at"]} for r in
                con.execute("SELECT * FROM imported_files ORDER BY at DESC LIMIT 10")]
    client, client_error = None, None
    try:
        client = integrations.qbit_queue(h.config)
    except integrations.IntegrationError as exc:
        client_error = str(exc)
    return jsonify({"requests": requests_, "imported": imported, "client": client, "client_error": client_error})


@bp.get("/watch")
@profile_only
def watch_list(who):
    rows = hub().con().execute("SELECT * FROM watch WHERE profile_id=? ORDER BY kind, value", (who["id"],))
    return jsonify({"watch": [{"id": r["id"], "kind": r["kind"], "value": r["value"]} for r in rows]})


@bp.post("/watch")
@profile_only
def watch_add(who):
    data = request.get_json(silent=True) or {}
    kind = "author" if data.get("kind") == "author" else "tag"
    value = db.clean_tag(data.get("value"))
    if not value:
        return fail("Type an author or a tag to follow.")
    con = hub().con()
    con.execute("INSERT OR IGNORE INTO watch(profile_id, kind, value, created) VALUES(?,?,?,?)",
                (who["id"], kind, value, db.now()))
    con.execute("DELETE FROM kv WHERE key=?", ("new:%d" % who["id"],))
    con.commit()
    return jsonify({"ok": True})


@bp.delete("/watch/<int:watch_id>")
@profile_only
def watch_remove(who, watch_id):
    con = hub().con()
    con.execute("DELETE FROM watch WHERE id=? AND profile_id=?", (watch_id, who["id"]))
    con.execute("DELETE FROM kv WHERE key=?", ("new:%d" % who["id"],))
    con.commit()
    return jsonify({"ok": True})


# ------------------------------------------------------------- watchlist
# Authors, films and TV shows a person is waiting for, checked every few hours. See watchlist.py.

def _watch_row(who, watch_id):
    return hub().con().execute("SELECT * FROM watch WHERE id=? AND profile_id=?", (watch_id, who["id"])).fetchone()


@bp.get("/watchlist")
@signed_in
def watchlist_page(who):
    if who == "guest":
        return fail("Choose your profile to keep a watchlist.", 403)
    rows = hub().con().execute("SELECT * FROM watch WHERE profile_id=? ORDER BY kind, value COLLATE NOCASE", (who["id"],)).fetchall()
    items = [watchlist.describe(r) if r["kind"] in watchlist.KINDS else {"id": r["id"], "kind": r["kind"], "value": r["value"], "found": [], "new": 0}
             for r in rows]
    return jsonify({"items": items, "news": sum(i["new"] for i in items), "can_request_media": can_request_media(who),
                    "films": hub().media.configured("film"), "shows": hub().media.configured("show")})


@bp.post("/watchlist")
@profile_only
def watchlist_add(who):
    data = request.get_json(silent=True) or {}
    kind, value = data.get("kind"), db.clean_tag(data.get("value"))
    if kind not in watchlist.KINDS:
        return fail("Choose an author, a film or a TV show.")
    if not value:
        return fail("Type a name to watch for.")
    if kind in ("film", "show") and not hub().media.configured(kind):
        return fail("Films and TV are not set up yet.")
    auto = bool(data.get("auto")) and kind in ("film", "show")
    if auto and not can_request_media(who):
        return fail("This profile cannot request films and TV, so it cannot get them automatically.", 403)
    con = hub().con()
    con.execute("INSERT OR IGNORE INTO watch(profile_id, kind, value, created, auto) VALUES(?,?,?,?,?)",
                (who["id"], kind, value, db.now(), int(auto)))
    con.execute("DELETE FROM kv WHERE key=?", ("new:%d" % who["id"],))
    con.commit()
    row = con.execute("SELECT * FROM watch WHERE profile_id=? AND kind=? AND value=?", (who["id"], kind, value)).fetchone()
    return jsonify(watchlist.check(hub(), row))


@bp.post("/watchlist/<int:watch_id>/check")
@profile_only
def watchlist_check(who, watch_id):
    row = _watch_row(who, watch_id)
    if not row or row["kind"] not in watchlist.KINDS:
        return fail("That is not on your watchlist.", 404)
    return jsonify(watchlist.check(hub(), row))


@bp.post("/watchlist/<int:watch_id>/auto")
@profile_only
def watchlist_auto(who, watch_id):
    row = _watch_row(who, watch_id)
    if not row or row["kind"] not in ("film", "show"):
        return fail("Only films and shows can be got automatically.", 404)
    on = bool((request.get_json(silent=True) or {}).get("on"))
    if on and not can_request_media(who):
        return fail("This profile cannot request films and TV.", 403)
    con = hub().con()
    con.execute("UPDATE watch SET auto=? WHERE id=?", (int(on), watch_id))
    con.commit()
    return jsonify(watchlist.check(hub(), _watch_row(who, watch_id)) if on else watchlist.describe(_watch_row(who, watch_id)))


@bp.post("/watchlist/seen")
@profile_only
def watchlist_seen(who):
    """The person has looked: nothing on the list is new any more."""
    con = hub().con()
    for r in con.execute("SELECT id, found FROM watch WHERE profile_id=?", (who["id"],)).fetchall():
        con.execute("UPDATE watch SET seen=? WHERE id=?", (json.dumps([f["key"] for f in json.loads(r["found"] or "[]")]), r["id"]))
    con.commit()
    return jsonify({"ok": True})


@bp.get("/discover/new")
@profile_only
def discover_new(who):
    """Suggestions from the watchlist. Cached for 12 hours per profile."""
    h = hub()
    con = h.con()
    cache_key = "new:%d" % who["id"]
    cached = db.kv_get(con, cache_key)
    if cached and not request.args.get("refresh"):
        data = json.loads(cached)
        if time.time() - data.get("at", 0) < 12 * 3600:
            return jsonify(data)
    items = []
    index = h.tag_index(who["id"])
    keys = h.library.keys()
    meta = {r["book_key"]: r for r in con.execute("SELECT * FROM book_meta")}
    finished = {r["book_key"] for r in h.reading_rows(who["id"]) if r["status"] == "finished"}
    for w in con.execute("SELECT * FROM watch WHERE profile_id=?", (who["id"],)).fetchall():
        if w["kind"] == "tag":
            low = w["value"].lower()
            for key, groups in index.items():
                if key in finished or not any(t.lower() == low for g in groups.values() for t in g):
                    continue
                m = meta.get(key)
                lib = keys.get(key)
                if not lib and not m:
                    continue
                items.append({"reason": "Tagged " + w["value"], "title": lib["title"] if lib else m["title"],
                              "author": lib["author"] if lib else m["author"],
                              "in_library": bool(lib), "id": lib["id"] if lib else None})
        elif can_request(who) and h.config.discover_configured:
            owned = [db.slug(b["title"]) for b in h.library.books() if w["value"].lower() in b["author"].lower()]
            try:
                results = integrations.prowlarr_search(h.config, w["value"], limit=40)
            except integrations.IntegrationError:
                results = []
            seen = 0
            for r in results:
                s = db.slug(r["title"])
                if any(len(t) > 3 and t in s for t in owned):
                    continue
                items.append({"reason": "By " + w["value"], "title": r["title"], "author": w["value"],
                              "in_library": False, "id": None, "release": r})
                seen += 1
                if seen >= 5:
                    break
    data = {"at": time.time(), "items": items[:40]}
    db.kv_set(con, cache_key, json.dumps(data))
    return jsonify(data)


# ----------------------------------------------------------------- admin

@bp.get("/admin/export")
@admin_only
def admin_export(who):
    payload = json.dumps(hub().export_data(), indent=1, ensure_ascii=False).encode("utf-8")
    return send_file(io.BytesIO(payload), mimetype="application/json", as_attachment=True,
                     download_name="bookhub-bookdata-%s.json" % date.today().isoformat())


@bp.post("/admin/import")
@admin_only
def admin_import(who):
    try:
        if request.files.get("file"):
            data = json.load(request.files["file"].stream)
        else:
            data = request.get_json(force=True)
        count = hub().import_data(data)
    except (ValueError, TypeError) as exc:
        return fail(str(exc) or "That file could not be read.")
    return jsonify({"books": count})


@bp.post("/admin/backup")
@admin_only
def admin_backup(who):
    path = hub().backup_now()
    return jsonify({"file": path.name, "at": db.kv_get(hub().con(), "last_backup")})


@bp.post("/admin/rescan")
@admin_only
def admin_rescan(who):
    h = hub()
    h.library.refresh()
    tags_added = h.load_tagpacks()
    kosync.index_library(h)
    h.fill_page_counts(limit=500)
    tidied = h.intake.tidy_inbox()
    copied = h.import_downloads()
    h.sort_new_books()
    return jsonify({"books": len(h.library.books()), "pack_tags": tags_added,
                    "imported": tidied["delivered"] + copied})


@bp.get("/admin/copies")
@admin_only
def admin_copies(who):
    h = hub()
    return jsonify({"sets": h.copy_sets(), "entries": len(h.library.all_books()), "books": len(h.library.books())})


@bp.post("/admin/copies")
@admin_only
def admin_copies_choose(who):
    """Show library entries that share a name as one book, or keep them apart."""
    data = request.get_json(silent=True) or {}
    h = hub()
    key = data.get("key")
    if not any(s["key"] == key for s in h.library.sets()):
        return fail("Those entries are no longer in the library.", 404)
    choice = data.get("choice")
    h.set_copy_choice(key, choice if choice in (calibre.TOGETHER, calibre.APART) else None)
    return jsonify({"ok": True})


@bp.get("/admin/tags")
@admin_only
def admin_tags(who):
    h = hub()
    return jsonify(dict(h.machine_tags(), lookups=h.catalogues.progress()))


@bp.post("/admin/tags/rule")
@admin_only
def admin_tag_rule(who):
    """Hide a machine tag everywhere, rename it, or put it back."""
    data = request.get_json(silent=True) or {}
    becomes = None if data.get("restore") else ("" if data.get("hide") else data.get("becomes"))
    if becomes is not None and not data.get("hide") and not db.clean_tag(becomes):
        return fail("Type the new name for the tag.")
    if not hub().set_tag_rule(data.get("tag"), becomes):
        return fail("Choose a tag first.")
    return jsonify({"ok": True})


@bp.post("/admin/tags/lookup")
@admin_only
def admin_tags_lookup(who):
    h = hub()
    if not catalogues.active(h.config):
        return fail("Looking up tags is switched off. Turn it on under Connections first.")
    data = request.get_json(silent=True) or {}
    if data.get("again"):  # forget the books the catalogues did not know, and ask about them again
        h.con().execute("DELETE FROM tag_lookups WHERE status!='found'")
        h.con().commit()
    started = h.catalogues.start(current_app._get_current_object())
    return jsonify({"started": started, "lookups": h.catalogues.progress()})


@bp.get("/admin/connections")
@admin_only
def admin_connections(who):
    return jsonify({"groups": settings.describe(hub()), "in_file": settings.env_file(hub().config) is not None})


# The programs behind Family Hub, for the admin's links to their own pages: (id, name, what it does, address inside Docker).
SERVICES = [
    ("radarr", "Radarr", "Films: the list, picking versions, filing", "http://radarr:7878"),
    ("sonarr", "Sonarr", "TV: the list, picking versions, filing", "http://sonarr:8989"),
    ("prowlarr", "Prowlarr", "The torrent sites everything searches", "http://prowlarr:9696"),
    ("qbittorrent", "qBittorrent", "Downloads, through the VPN", "http://gluetun:8080"),
    ("bazarr", "Bazarr", "Subtitles", "http://bazarr:6767"),
    ("jellyfin", "Jellyfin", "Watching films and TV", "http://jellyfin:8096"),
    ("cwa", "Library manager", "The Calibre library (Calibre-Web Automated)", "http://calibre-web-automated:8083"),
    ("suwayomi", "Suwayomi", "Manga by chapter", "http://suwayomi:4567"),
    ("komga", "Komga", "Comics and manga reader", "http://komga:25600"),
]


@bp.get("/admin/services")
@admin_only
def admin_services(who):
    """Each program's port (the page builds the link from the address it was opened with) and whether it answers."""
    import requests as http
    from concurrent.futures import ThreadPoolExecutor
    c = hub().config
    inside = {"radarr": c.radarr_url, "sonarr": c.sonarr_url, "prowlarr": c.prowlarr_url, "qbittorrent": c.qbit_url,
              "bazarr": c.bazarr_url, "jellyfin": c.jellyfin_url, "cwa": c.cwa_api_url}

    def check(service):
        sid, name, what, default = service
        try:
            http.get(inside.get(sid) or default, timeout=2, allow_redirects=False)
            running = True
        except http.RequestException:
            running = False
        link = c.jellyfin_link if sid == "jellyfin" else c.cwa_url if sid == "cwa" else ""
        return {"id": sid, "name": name, "what": what, "port": c.service_ports[sid], "link": link, "running": running}

    with ThreadPoolExecutor(max_workers=len(SERVICES)) as pool:
        return jsonify({"services": list(pool.map(check, SERVICES))})


@bp.post("/admin/setup-file")
@admin_only
def admin_setup_file(who):
    """The whole setup as one zip locked with a passphrase (setupfile.py). It holds every password."""
    from . import setupfile
    phrase = (request.get_json(silent=True) or {}).get("passphrase")
    if not isinstance(phrase, str):
        return fail("Choose a passphrase.")
    try:
        path = setupfile.build(hub(), phrase, host=request.host.rsplit(":", 1)[0] or "<server address>")
    except setupfile.SetupError as exc:
        return fail(str(exc))
    data = path.read_bytes()
    path.unlink(missing_ok=True)  # never left lying on the server
    return send_file(io.BytesIO(data), mimetype="application/zip", as_attachment=True, download_name=path.name, max_age=0)


@bp.post("/admin/connections")
@admin_only
def admin_connections_save(who):
    data = request.get_json(silent=True) or {}
    try:
        settings.save(hub(), data.get("values") or {})
    except ValueError as exc:
        return fail(str(exc))
    return jsonify({"groups": settings.describe(hub())})


@bp.get("/admin/look")
@admin_only
def admin_look(who):
    h = hub()
    return jsonify({"designs": looks.catalogue(), "look": looks.current(h.con())})


@bp.post("/admin/look")
@admin_only
def admin_look_save(who):
    h = hub()
    problem = looks.save(h.con(), request.get_json(silent=True) or {})
    if problem:
        return fail(problem)
    return jsonify({"look": looks.current(h.con())})


@bp.post("/admin/connections/test")
@admin_only
def admin_connections_test(who):
    data = request.get_json(silent=True) or {}
    try:
        return jsonify(settings.test(hub(), data.get("group"), data.get("values")))
    except ValueError as exc:
        return jsonify({"ok": False, "message": str(exc)})


@bp.post("/admin/attention/retry")
@admin_only
def admin_attention_retry(who):
    """Put everything that was set aside back through the inbox."""
    h = hub()
    moved = h.intake.retry_attention()
    result = h.intake.tidy_inbox(min_age=0)
    return jsonify({"retried": moved, "delivered": len(result["delivered"]), "attention": result["attention"]})


# ---------------------------------------------------------------- series
# Manga, manhwa and other series read chapter by chapter, kept by the series server.

@bp.get("/series")
@series_only
def series_shelf(who):
    h = hub()
    if not h.series.configured:
        return jsonify({"configured": False, "series": [], "queued": 0})
    return jsonify(dict(h.series.shelf(), configured=True))


@bp.get("/series/search")
@series_only
def series_search(who):
    if not can_follow(who):
        return fail("This profile can read series but not follow new ones.", 403)
    q = (request.args.get("q") or "").strip()
    if len(q) < 2:
        return fail("Type at least two letters.")
    return jsonify(hub().series.search(q))


@bp.post("/series/check")
@series_only
def series_check(who):
    hub().series.check_now()
    return jsonify({"ok": True})


@bp.post("/series/chapters/read")
@series_only
def series_mark(who):
    data = request.get_json(silent=True) or {}
    ids = [i for i in data.get("ids") or [] if isinstance(i, int)]
    if not ids:
        return fail("No chapters given.")
    hub().series.mark(ids, data.get("read", True))
    return jsonify({"ok": True})


@bp.get("/series/<int:series_id>")
@series_only
def series_detail(who, series_id):
    from . import chapterfile
    info = hub().series.detail(series_id, refresh=request.args.get("refresh") == "1")
    return jsonify(dict(info, mine=series_id in series.my_books(hub(), who["id"]), files=chapterfile.files(hub(), info)))


@bp.get("/series/<int:series_id>/file/<int:part>")
@series_only
def series_part_file(who, series_id, part):
    """One part of a series by chapter as a comic file, every chapter in it, once the server has made it."""
    from . import chapterfile
    from .devices import safe_filename
    path, title = chapterfile.ready_file(hub(), hub().series.detail(series_id), part)
    if not path:
        return fail("That part is still being put together on the server. Try again in a few minutes.", 404)
    return send_file(path, as_attachment=True, download_name=safe_filename(title, "", "cbz"), mimetype="application/vnd.comicbook+zip")


@bp.post("/series/<int:series_id>/follow")
@series_only
def series_follow(who, series_id):
    h = hub()
    if not can_follow(who):
        return fail("This profile can read series but not follow or stop following them.", 403)
    if (request.get_json(silent=True) or {}).get("on", True):
        return jsonify({"ok": True, "queued": h.series.follow(series_id)})
    h.series.unfollow(series_id)
    return jsonify({"ok": True, "queued": 0})


@bp.post("/series/<int:series_id>/download")
@series_only
def series_download(who, series_id):
    if not can_follow(who):
        return fail("This profile can read series but not download more of them.", 403)
    return jsonify({"ok": True, "queued": hub().series.download(series_id)})


def _passed_on(upstream, name=None):
    headers = {"Cache-Control": "private, max-age=86400", "X-Content-Type-Options": "nosniff"}
    if name:
        headers["Content-Disposition"] = 'attachment; filename="%s"' % name
    if upstream.headers.get("Content-Length"):
        headers["Content-Length"] = upstream.headers["Content-Length"]
    return Response(upstream.iter_content(65536), headers=headers,
                    mimetype=_safe_type(upstream.headers.get("Content-Type"), allow_files=bool(name)))


def _safe_type(given, allow_files=False):
    """The type to pass on for something another server sent. Pages come from reading sites, which nobody here
    controls, so anything that is not a plain picture (a web page, an SVG with a script in it) goes out as a
    file to save instead of something the browser would run as part of Family Hub."""
    kind = (given or "").split(";")[0].strip().lower()
    if kind.startswith("image/") and "svg" not in kind:
        return kind
    if allow_files and kind in ("application/zip", "application/x-cbz", "application/vnd.comicbook+zip"):
        return kind
    return "application/octet-stream"


@bp.get("/series/<int:series_id>/cover")
@series_only
def series_cover(who, series_id):
    return _passed_on(hub().series.raw("/api/v1/manga/%d/thumbnail" % series_id, stream=True))


@bp.post("/series/<int:series_id>/mine")
@series_only
def series_mine(who, series_id):
    """Put a followed series in My books (or take it out): the reading app gets every kept chapter as one book."""
    on = bool((request.get_json(silent=True) or {}).get("on"))
    if on:
        hub().series.detail(series_id)  # it must be on the series server
    series.set_my_book(hub(), who["id"], series_id, on)
    return jsonify({"mine": on})


@bp.get("/series/chapters/<int:chapter_id>/pages")
@series_only
def series_pages(who, chapter_id):
    return jsonify({"pages": hub().series.pages(chapter_id)})


@bp.get("/series/<int:series_id>/chapter/<int:order>/page/<int:page>")
@series_only
def series_page(who, series_id, order, page):
    return _passed_on(hub().series.raw("/api/v1/manga/%d/chapter/%d/page/%d" % (series_id, order, page), stream=True))


@bp.get("/series/chapters/<int:chapter_id>/file")
@series_only
def series_file(who, chapter_id):
    """The chapter as one CBZ file, for reading without a connection in a comic reader app."""
    upstream = hub().series.raw("/api/v1/chapter/%d/download" % chapter_id, stream=True)
    name = "chapter-%d.cbz" % chapter_id
    given = upstream.headers.get("Content-Disposition") or ""
    if "filename=" in given:
        name = given.split("filename=")[-1].strip('"; ').encode("ascii", "ignore").decode() or name
    return _passed_on(upstream, name)


@bp.post("/admin/second-copy")
@admin_only
def admin_second_copy(who):
    """Start the copy to the second disk now."""
    h = hub()
    if secondcopy.folder(h.config) is None:
        return fail("No second disk is set up. Guide 6 has the steps.")
    if secondcopy.status(h)["running"]:
        return fail("A copy is already running.", 409)
    secondcopy.start(h, current_app._get_current_object())
    return jsonify({"ok": True})


# ------------------------------------------------------- spare copies
# The one place Family Hub changes the library, by asking the library manager. See duplicates.py.

@bp.get("/admin/duplicates")
@admin_only
def admin_duplicates(who):
    h = hub()
    plans = h.duplicates.plans()
    return jsonify({
        "blocked": h.duplicates.ready(), "job": h.duplicates.job, "sets": plans, "log": h.duplicates.log(),
        "counts": {cls: sum(1 for p in plans if p["cls"] == cls) for cls in duplicates.AUTOMATIC + (duplicates.REVIEW,)},
        "set_aside": "needs-attention/" + duplicates.SET_ASIDE,
    })


@bp.post("/admin/duplicates")
@admin_only
def admin_duplicates_apply(who):
    """Carry out one set, every set of one sort, or a person's choice on a set that needed review."""
    h = hub()
    blocked = h.duplicates.ready()
    if blocked:
        return fail(blocked)
    data = request.get_json(silent=True) or {}
    plans = h.duplicates.plans()
    keep = data.get("keep")
    if data.get("key"):
        wanted = [p for p in plans if p["key"] == data["key"]]
        if not wanted:
            return fail("Those entries are no longer in the library as a set.", 404)
        if keep is None and wanted[0]["cls"] not in duplicates.AUTOMATIC:
            return fail("This set needs a person to choose which entry to keep.")
        if keep is not None and keep not in [b["id"] for b in wanted[0]["books"]]:
            return fail("That entry is not part of this set.")
    elif data.get("cls") in duplicates.AUTOMATIC:
        wanted, keep = [p for p in plans if p["cls"] == data["cls"]], None
    else:
        return fail("Say which set, or which sort of set.")
    if not wanted:
        return fail("There is nothing of that sort to do.")
    if not h.duplicates.start(current_app._get_current_object(), wanted, keep):
        return fail("A batch is already running. Wait for it to finish.", 409)
    return jsonify({"ok": True, "started": len(wanted)})


# ------------------------------------------------------- book details
# Books whose title or author is a file name or a user name. Changed by the library manager. See bookfix.py.

@bp.get("/admin/book-details")
@admin_only
def admin_book_details(who):
    h = hub()
    return jsonify({"blocked": h.bookfix.ready(), "books": h.bookfix.needing()})


@bp.get("/admin/book-details/<int:book_id>")
@admin_only
def admin_book_details_find(who, book_id):
    try:
        return jsonify(hub().bookfix.candidates(book_id))
    except bookfix.FixError as exc:
        return fail(str(exc), 404)


@bp.post("/admin/book-details/<int:book_id>")
@admin_only
def admin_book_details_apply(who, book_id):
    """Give a book its real details, or with {"leave": true} say it is right as it is."""
    data = request.get_json(silent=True) or {}
    h = hub()
    if data.get("leave") is True:
        h.bookfix.leave(book_id)
        return jsonify({"ok": True})
    authors = data.get("authors")
    if isinstance(authors, str):
        authors = [a for a in re.split(r"\s*(?:&|;)\s*", authors) if a]
    texts = [data.get(k, "") for k in ("title", "synopsis", "publisher", "cover")]
    if not isinstance(authors, list) or not all(isinstance(a, str) for a in authors) or not all(isinstance(t, str) for t in texts):
        return fail("Give a title and authors as text.")
    try:
        done = h.bookfix.apply(book_id, data.get("title", ""), authors, data.get("synopsis", ""), data.get("publisher", ""),
                               data.get("cover", ""), who["id"])
    except bookfix.FixError as exc:
        return fail(str(exc))
    return jsonify(dict(done, message="Now %s by %s." % (done["title"], done["author"])))


# ------------------------------------------------------------ films and TV

def media_call(fn):
    """Run a films-and-TV job, turning a service's refusal into a plain message for the page."""
    try:
        return fn()
    except media.MediaError as exc:
        return fail(str(exc), 502)


def _kind(kind):
    return kind if kind in media.KINDS else None


@bp.get("/media/library")
@profile_only
def media_library(who):
    kind = _kind(request.args.get("kind"))
    if not kind:
        return fail("Say films or shows.")
    return media_call(lambda: jsonify({"items": hub().media.library(kind)}))


@bp.get("/media/item/<kind>/<int:arr_id>")
@profile_only
def media_item(who, kind, arr_id):
    if not _kind(kind):
        return fail("Say films or shows.", 404)
    return media_call(lambda: jsonify(hub().media.item(kind, arr_id)))


@bp.get("/media/activity/<kind>/<int:arr_id>")
@profile_only
def media_activity(who, kind, arr_id):
    """What the search for one film or show has done so far: indexers asked, version picked, download, filing."""
    if not _kind(kind):
        return fail("Say films or shows.", 404)
    return media_call(lambda: jsonify(hub().media.activity(kind, arr_id)))


@bp.get("/media/tracking")
@profile_only
def media_tracking(who):
    """The current line of "What's happening" for a few films, shows and books at once, for the corner pop-up.
    items=film:3,show:1 and book=<release name> (at most six of each). A removed film or show comes back as gone."""
    wanted = []
    for part in (request.args.get("items") or "").split(",")[:6]:
        kind, _, num = part.partition(":")
        if _kind(kind) and num.isdigit():
            wanted.append((kind, int(num)))
    books = [b for b in request.args.getlist("book") if b.strip()][:6]

    def build():
        out = []
        if books:
            # qBittorrent being away (the VPN restarting, say) must not stop the films and shows from updating.
            try:
                torrents = hub().media.torrents()
            except media.MediaError:
                torrents = None
            for title in books:
                b = hub().media.book_status(title, torrents or [])
                if torrents is None and not b["finished"]:
                    b["now"] = dict(b["now"], text="qBittorrent cannot be reached at the moment", state="bad")
                out.append(dict(b, kind="book", id=title))
        for kind, arr_id in wanted:
            try:
                a = hub().media.activity(kind, arr_id)
                out.append({"kind": kind, "id": arr_id, "title": a["title"], "now": a["now"], "finished": a["finished"],
                            "downloaded": a.get("downloaded", False), "filing": a.get("filing")})
            except media.MediaError as exc:
                if str(exc).startswith("404"):
                    out.append({"kind": kind, "id": arr_id, "gone": True})
                else:  # Radarr or Sonarr not answering: say so, and keep following it
                    out.append({"kind": kind, "id": arr_id, "title": "", "finished": False, "unreachable": True,
                                "now": {"text": str(exc), "state": "bad", "progress": 0}})
        return jsonify({"items": out})
    return media_call(build)


@bp.get("/media/search")
@profile_only
def media_search(who):
    if not can_request_media(who):
        return fail("This profile cannot request films and TV. An admin can switch that on.", 403)
    kind, q = _kind(request.args.get("kind")), (request.args.get("q") or "").strip()
    if not kind or len(q) < 2:
        return fail("Type at least two letters.")
    return media_call(lambda: jsonify({"results": hub().media.lookup(kind, q)}))


@bp.post("/media/request")
@profile_only
def media_request(who):
    if not can_request_media(who):
        return fail("This profile cannot request films and TV. An admin can switch that on.", 403)
    data = request.get_json(silent=True) or {}
    kind = _kind(data.get("kind"))
    if not kind or not data.get("id"):
        return fail("Choose a film or show first.")
    seasons = data.get("seasons") if data.get("seasons") in ("all", "latest", "pick") else "all"
    try:
        external_id, picked = int(data["id"]), [int(n) for n in (data.get("picked") or [])]
    except (TypeError, ValueError):
        return fail("Choose a film or show first.")
    return media_call(lambda: jsonify(hub().media.request(kind, external_id, who, seasons, picked)))


@bp.get("/media/queue")
@profile_only
def media_queue(who):
    h = hub()

    def build():
        q = h.media.queue()
        return jsonify(dict(q, history=h.media.history(), vpn=h.media.vpn()))
    return media_call(build)


@bp.get("/media/torrents")
@profile_only
def media_torrents(who):
    """Every download in qBittorrent, films, TV and books, for the Downloads page."""
    return media_call(lambda: jsonify({"torrents": hub().media.torrents(), "can_change": can_request_media(who),
                                       "can_change_books": bool(can_request(who)), "can_delete": is_admin(who)}))


@bp.delete("/media/torrents/<torrent_hash>")
@admin_only
def media_torrent_delete(who, torrent_hash):
    """Delete a download, its files, and the film or show it is for, from the whole hub."""
    return media_call(lambda: jsonify({"message": hub().media.delete_download(torrent_hash)}))


@bp.get("/media/speed")
@profile_only
def media_speed(who):
    return media_call(lambda: jsonify(dict(hub().media.speed(), can_change=bool(can_request_media(who) or can_request(who)))))


@bp.post("/media/speed")
@profile_only
def media_speed_save(who):
    """Download and upload limits, and slow mode, for every download. Anyone who may download things may change them."""
    if not (can_request_media(who) or can_request(who)):
        return fail("This profile cannot change download speeds. An admin can switch that on.", 403)
    return media_call(lambda: jsonify(hub().media.set_speed(request.get_json(silent=True) or {})))


@bp.post("/media/torrents/<torrent_hash>/<action>")
@profile_only
def media_torrent_action(who, torrent_hash, action):
    if action not in media.TORRENT_ACTIONS:
        return fail("That is not something a download can do.", 404)

    def go():
        # Books need "Can find new books"; films and TV need "Can request films and TV".
        found = next((t for t in hub().media.torrents() if t["hash"].lower() == torrent_hash.lower()), None)
        if not found:
            return fail("That download is not in qBittorrent any more.", 404)
        if not (can_request(who) if found["type"] == "Book" else can_request_media(who)):
            return fail("This profile cannot change that download. An admin can switch that on.", 403)
        hub().media.torrent_action(torrent_hash, action)
        return jsonify({"ok": True})
    return media_call(go)


@bp.get("/media/watch/<kind>/<int:arr_id>")
@profile_only
def media_watch(who, kind, arr_id):
    """Where to watch a film in Jellyfin, or a show's seasons and episodes with where to watch each."""
    if not _kind(kind):
        return fail("Say films or shows.", 404)
    return media_call(lambda: jsonify(hub().media.watch(kind, arr_id)))


@bp.get("/search-progress")
@profile_only
def search_progress(who):
    """How many torrent sites have answered a search that is still running. q=the search, ago=seconds since it began."""
    q = (request.args.get("q") or "").strip()
    ago = request.args.get("ago", default=0, type=float)
    if not q:
        return fail("Say what is being searched for.")
    return media_call(lambda: jsonify(hub().media.search_progress(q, min(ago, 600))))


@bp.get("/media/latest")
@profile_only
def media_latest(who):
    return media_call(lambda: jsonify({"items": hub().media.latest()}))


@bp.post("/media/look-again/<kind>/<int:arr_id>")
@profile_only
def media_look_again(who, kind, arr_id):
    if not can_request_media(who):
        return fail("This profile cannot request films and TV. An admin can switch that on.", 403)
    if not _kind(kind):
        return fail("Say films or shows.", 404)
    return media_call(lambda: jsonify({"message": hub().media.look_again(kind, arr_id)}))


@bp.post("/media/looking/<kind>/<int:arr_id>")
@profile_only
def media_looking(who, kind, arr_id):
    if not can_request_media(who):
        return fail("This profile cannot request films and TV. An admin can switch that on.", 403)
    if not _kind(kind):
        return fail("Say films or shows.", 404)
    on = bool((request.get_json(silent=True) or {}).get("on"))
    return media_call(lambda: jsonify({"message": hub().media.set_looking(kind, arr_id, on)}))


@bp.get("/media/releases/<kind>/<int:arr_id>")
@profile_only
def media_releases(who, kind, arr_id):
    if not can_request_media(who):
        return fail("This profile cannot request films and TV. An admin can switch that on.", 403)
    if not _kind(kind):
        return fail("Say films or shows.", 404)
    season = request.args.get("season", type=int)
    return media_call(lambda: jsonify({"releases": hub().media.releases(kind, arr_id, season)}))


@bp.post("/media/versions/<kind>/<int:arr_id>")
@profile_only
def media_versions_start(who, kind, arr_id):
    """Start looking for every version of a film or a season; the page asks for what has been found every moment."""
    if not can_request_media(who):
        return fail("This profile cannot request films and TV. An admin can switch that on.", 403)
    if not _kind(kind):
        return fail("Say films or shows.", 404)
    season = request.args.get("season", type=int)
    return media_call(lambda: jsonify({"job": hub().media.start_version_search(kind, arr_id, season)}))


@bp.get("/media/versions/<job_id>")
@profile_only
def media_versions(who, job_id):
    return media_call(lambda: jsonify(hub().media.version_search(job_id)))


@bp.post("/media/versions/<job_id>/grab")
@profile_only
def media_versions_grab(who, job_id):
    """Download one of the versions found so far, before the full search has finished."""
    if not can_request_media(who):
        return fail("This profile cannot request films and TV. An admin can switch that on.", 403)
    data = request.get_json(silent=True) or {}
    if not data.get("guid"):
        return fail("Choose a version first.")
    return media_call(lambda: jsonify(hub().media.grab_found(job_id, data["guid"], bool(data.get("force")))))


@bp.post("/media/grab")
@profile_only
def media_grab(who):
    if not can_request_media(who):
        return fail("This profile cannot request films and TV. An admin can switch that on.", 403)
    data = request.get_json(silent=True) or {}
    kind = _kind(data.get("kind"))
    if not kind or not data.get("guid"):
        return fail("Choose a release first.")

    def go():
        hub().media.grab(kind, data["guid"], data.get("indexer_id"))
        return jsonify({"ok": True})
    return media_call(go)


@bp.delete("/media/<kind>/<int:arr_id>")
@admin_only
def media_remove(who, kind, arr_id):
    if not _kind(kind):
        return fail("Say films or shows.", 404)
    return media_call(lambda: jsonify({"removed": hub().media.remove(kind, arr_id)}))


# Films and TV already on the disk but not filed: what each is, filing it, deleting spare copies. See mediasort.py.

@bp.get("/admin/media/sort")
@admin_only
def media_sort_list(who):
    kind = _kind(request.args.get("kind"))
    if not kind:
        return fail("Say films or shows.")
    return media_call(lambda: jsonify({"items": hub().sorter.found(kind),
                                       "folders": {"downloads": "stack/data/" + mediasort.FOLDERS[kind][0][len("/data/"):],
                                                   "unsorted": "stack/data/" + mediasort.UNSORTED[kind]}}))


@bp.post("/admin/media/sort")
@admin_only
def media_sort_file(who):
    data = request.get_json(silent=True) or {}
    kind, path = _kind(data.get("kind")), data.get("path")
    if not kind or not isinstance(path, str) or not path:
        return fail("Say which file.")
    external = data.get("external_id")
    if external is not None and (isinstance(external, bool) or not str(external).isdigit()):
        return fail("That is not a film or show from the search.")
    return media_call(lambda: jsonify(hub().sorter.file(kind, path, external)))


@bp.post("/admin/media/sort/remove")
@admin_only
def media_sort_remove(who):
    data = request.get_json(silent=True) or {}
    kind, path = _kind(data.get("kind")), data.get("path")
    if not kind or not isinstance(path, str) or not path:
        return fail("Say which file.")
    return media_call(lambda: jsonify({"message": hub().sorter.remove(kind, path)}))


@bp.get("/admin/media/status")
@admin_only
def media_status(who):
    return jsonify(hub().media.status())


@bp.post("/admin/media/connect")
@admin_only
def media_connect(who):
    steps = hub().media.connect_everything()
    return jsonify({"steps": [{"step": a, "ok": b, "message": c} for a, b, c in steps]})


POSTER_HOSTS = ("image.tmdb.org", "artworks.thetvdb.com", "www.thetvdb.com", "thetvdb.com", "assets.fanart.tv")


def _image(fetch):
    try:
        r = fetch()
    except requests.RequestException:
        return fail("No picture.", 404)
    if r.status_code != 200 or _safe_type(r.headers.get("Content-Type")) == "application/octet-stream":
        return fail("No picture.", 404)
    if len(r.content) > 8 * 1024 * 1024:
        return fail("No picture.", 404)
    return send_file(io.BytesIO(r.content), mimetype=_safe_type(r.headers.get("Content-Type")), max_age=86400)


@bp.get("/media/poster/<kind>/<int:arr_id>")
@profile_only
def media_poster(who, kind, arr_id):
    """A poster Radarr or Sonarr keeps, passed through so the browser needs no key."""
    if not _kind(kind):
        return fail("No picture.", 404)
    try:
        arr = hub().media.arr(kind)
    except media.MediaError:
        return fail("No picture.", 404)
    return _image(lambda: requests.get("%s/api/v3/mediacover/%d/poster-500.jpg" % (arr.url, arr_id),
                                       headers={"X-Api-Key": arr.key}, timeout=15))


@bp.get("/media/poster-remote")
@profile_only
def media_poster_remote(who):
    """A poster from the film and TV databases, for search results. Only those hosts are fetched."""
    url = request.args.get("url") or ""
    parts = urlparse(url)
    if parts.scheme not in ("http", "https") or (parts.hostname or "") not in POSTER_HOSTS:
        return fail("No picture.", 404)
    return _image(lambda: requests.get(url, timeout=15, allow_redirects=False))


@bp.get("/media/jellyfin-image/<item_id>")
@profile_only
def media_jellyfin_image(who, item_id):
    c = hub().config
    if not (c.jellyfin_url and c.jellyfin_api_key) or not item_id.isalnum():
        return fail("No picture.", 404)
    return _image(lambda: requests.get("%s/Items/%s/Images/Primary" % (c.jellyfin_url, item_id),
                                       params={"maxHeight": 450}, headers=media.jellyfin_auth(c.jellyfin_api_key), timeout=15))
