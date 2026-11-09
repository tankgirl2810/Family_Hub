"""The web API used by the Family Hub pages."""
import csv
import functools
import hashlib
import io
import ipaddress
import json
import math
import shutil
import sqlite3
import subprocess
import threading
import time
import re
from datetime import date, timedelta
from urllib.parse import urlparse

import requests

from flask import Blueprint, Response, current_app, jsonify, request, send_file, session
from werkzeug.security import check_password_hash, generate_password_hash

from . import bookfix, calibre, catalogues, visuals, watchlist, covers, db, devices, duplicates, goodreads, integrations, kinds, kosync, looks, media, mediasort, readers, recommend, search, secondcopy, series, settings, tagmerge
from . import tabletop  # game books: the Tabletop view, nested tags, what each profile hides
from . import review  # the review queue: the sorter's suggestions, confirmed into the library, and learned rules
from . import kindle  # a person's Kindle library and the diary of how far through each book they are
from . import librarymanager  # its refusals, for the duplicates queue
from . import peek  # a look inside a book's first pages, next to its details

bp = Blueprint("api", __name__, url_prefix="/api")

COLORS = ["#F0B04A", "#7FB2E5", "#9BD1A8", "#E59AC0", "#C9A7F0", "#F08A6B"]
MAX_TRIES, LOCK_SECONDS = 5, 60


def hub():
    return current_app.hub


def fail(message, code=400):
    return jsonify({"error": message}), code


def clean(value):
    """db.clean_tag for something a page sent: a number or a list (a broken page, a hand-made request) counts as blank."""
    return db.clean_tag(value if isinstance(value, str) else "")


def number(value, kind=float):
    """A number a page sent, or None when it's blank or not a number at all."""
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        found = kind(value)
        return found if math.isfinite(found) else None  # float("nan") is a "number" too, sadly
    except (TypeError, ValueError, OverflowError):
        return None


def id_list(value):
    """Only the whole numbers out of a list of ids. Anything that isn't a list gives none."""
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, int) and not isinstance(item, bool)]


@bp.before_request
def json_is_an_object():
    """Every page sends a JSON object. Anything else (a list, a bare number) would fail deep inside a view."""
    if request.is_json and request.content_length:
        data = request.get_json(silent=True)
        if data is not None and not isinstance(data, dict):
            return fail("That request was not understood.")


@bp.before_request
def only_from_our_own_pages():
    """Changes only come from Family Hub's own pages. SameSite=Lax still sends the cookie from any page on the same
    host (Radarr's, qBittorrent's, another port), so a form or script there could otherwise act as whoever's signed in.
    Browsers say where a request came from in Sec-Fetch-Site; no header = not a browser, so no cookie riding along."""
    if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get("Sec-Fetch-Site") in ("cross-site", "same-site"):
        return fail("That request came from another site.", 403)


@bp.after_request
def no_sniffing(reply):
    reply.headers.setdefault("X-Content-Type-Options", "nosniff")
    return reply


# ------------------------------------------------------------------- who

def passcode_stamp(profile):
    """A short fingerprint of the profile's passcode, kept in the sign-in cookie: a new passcode ends older sign-ins."""
    return hashlib.sha256((profile["passcode_hash"] or "").encode("utf-8")).hexdigest()[:16]


def me():
    """The signed-in profile row, the string 'guest', or None."""
    pid = session.get("pid")
    if pid == "guest":
        # re-checked on EVERY request: a phone that signed in as a guest at home still has the cookie at the pub
        config = hub().config
        return "guest" if config.guest_enabled and (not config.guest_lan_only or on_home_network()) else None
    if pid:
        profile = hub().profile(pid)
        if profile is None:
            return None
        if "pc" not in session:
            session["pc"] = passcode_stamp(profile)  # a cookie from before stamps: it carries on, from now on stamped
        elif session["pc"] != passcode_stamp(profile):
            return None  # the passcode was changed since this sign-in
        return profile
    return None


def is_admin(who):
    return who not in (None, "guest") and who["role"] == "admin"


def pid_of(who):
    """The profile id, or None for a guest (whose tags, ratings and shelves don't exist)."""
    return None if who in (None, "guest") else who["id"]


def can_tag(who):
    """Family tags, covers and sorting books into kinds all go with "can tag"."""
    return is_admin(who) or bool(who["can_tag"])


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
        # the proxy tacks the address it really saw onto the END. everything before that came from the browser,
        # which can lie and claim to be "on the home network". so: trust only the last one
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
    # count the try BEFORE the slow hash check. otherwise someone firing 50 guesses at once gets them all in
    # before the counter catches up
    with _attempts_lock:
        misses, last_miss = attempts.get(profile["id"], [0, 0])
        if misses >= MAX_TRIES and time.time() - last_miss < LOCK_SECONDS:
            return None
        count = misses + 1 if time.time() - last_miss < LOCK_SECONDS else 1
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
    return bool(can_request(who))


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


def media_allowed_ids(family_hub):
    """Members an admin has allowed to request films and TV. Admins always can; guests never."""
    try:
        return {int(profile_id) for profile_id in json.loads(db.kv_get(family_hub.con(), MEDIA_KEY) or "[]")}
    except (TypeError, ValueError):  # TypeError: a bare number saved where the list should be
        return set()


def can_request_media(who, allowed=None):
    if who in (None, "guest"):
        return False
    if who["role"] == "admin":
        return True
    return who["id"] in (allowed if allowed is not None else media_allowed_ids(hub()))


def media_requests_only(view):
    """Goes under @profile_only: the view also needs "can request films and TV"."""
    @functools.wraps(view)
    def wrapper(who, *args, **kwargs):
        if not can_request_media(who):
            return fail("This profile cannot request films and TV. An admin can switch that on.", 403)
        return view(who, *args, **kwargs)
    return wrapper


def public_profile(profile, full=False, blocked=None, allowed=None):
    """What anyone may know about a profile: never the passcode or keys themselves, only whether they're set."""
    data = {
        "id": profile["id"], "name": profile["name"], "role": profile["role"], "color": profile["color"],
        "has_passcode": bool(profile["passcode_hash"]), "can_request": bool(profile["can_request"]),
        "can_tag": bool(profile["can_tag"]), "tracker_linked": bool(profile["hardcover_token"]),
        "can_series": can_series(profile, blocked),
        "can_request_media": can_request_media(profile, allowed),
    }
    if full:
        data["sync_ready"] = bool(profile["sync_key"])
    return data


# ----------------------------------------------------------------- state

@bp.get("/state")
def state():
    family_hub = hub()
    who = me()
    profiles = family_hub.profiles()
    blocked, allowed = series.blocked_ids(family_hub), media_allowed_ids(family_hub)
    return jsonify({
        "setup_needed": not profiles,
        "me": None if who is None else ({"guest": True, "name": "Guest"} if who == "guest" else
                                        dict(public_profile(who, True, blocked, allowed), via_admin=bool(session.get("via_admin")))),
        "profiles": [public_profile(profile, blocked=blocked, allowed=allowed) for profile in profiles],
        "series": family_hub.series.configured and can_series(who, blocked),
        "guest_enabled": family_hub.config.guest_enabled and (not family_hub.config.guest_lan_only or on_home_network()),
        "discover_configured": family_hub.config.discover_configured,
        "library_available": family_hub.library.available,
        "cwa_url": family_hub.config.cwa_url,
        "can_convert": bool(shutil.which("ebook-convert")),
        "kinds": kinds.KINDS,
        "device_kinds": devices.KINDS,
        "look": looks.current(family_hub.con()),
        # The visuals' version (so a new one from GitHub is fetched) and, for someone signed in, the home town.
        "visuals": {"version": visuals.version(family_hub),
                    "place": _home_place(family_hub) if who else None},
        "media": {"films": family_hub.media.configured("film"), "shows": family_hub.media.configured("show"),
                  "jellyfin": bool(family_hub.config.jellyfin_url and family_hub.config.jellyfin_api_key),
                  # members only see Downloads once there is a download client to show
                  "downloads": bool(family_hub.config.qbit_url)},
    })


def _home_place(family_hub):
    config = family_hub.config
    if not (config.home_lat and config.home_lon):
        return None
    try:
        lat, lon = float(config.home_lat), float(config.home_lon)
    except ValueError:
        return None
    # a hand-typed "nan" in .env would put NaN in /state, which no browser can read as JSON
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return {"name": config.home_place, "lat": lat, "lon": lon}


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
    family_hub = hub()
    if family_hub.profiles():
        return fail("Setup has already been done.", 409)
    data = request.get_json(silent=True) or {}
    name, passcode = clean(data.get("name")), str(data.get("passcode") or "")
    if not name or len(passcode) < 4:
        return fail("Enter a name and a passcode of at least 4 characters.")
    con = family_hub.con()
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
    family_hub = hub()
    data = request.get_json(silent=True) or {}
    if data.get("guest"):
        if not family_hub.config.guest_enabled:
            return fail("Guest access is turned off.", 403)
        if family_hub.config.guest_lan_only and not on_home_network():
            return fail("Guest access only works on the home network.", 403)
        session.clear()
        session["pid"] = "guest"
        return jsonify({"ok": True})
    target = family_hub.profile(number(data.get("profile_id"), int) or 0)
    if not target:
        return fail("That profile does not exist.", 404)

    via_admin = False
    if data.get("admin_id"):
        admin = family_hub.profile(number(data["admin_id"], int) or 0)
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
    session["pc"] = passcode_stamp(target)
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
    name = clean(data.get("name"))
    if not name:
        return fail("Give the profile a name.")
    con = hub().con()
    if con.execute("SELECT 1 FROM profiles WHERE name=?", (name,)).fetchone():
        return fail("There is already a profile with that name.", 409)
    count = con.execute("SELECT COUNT(*) FROM profiles").fetchone()[0]
    passcode = str(data.get("passcode") or "")
    if passcode and len(passcode) < 4:  # same rule as setup and changing a passcode
        return fail("Use at least 4 characters for a passcode.")
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
    family_hub = hub()
    target = family_hub.profile(profile_id)
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
        if "name" in data and clean(data["name"]):
            sets.append("name=?")
            args.append(clean(data["name"]))
        if "role" in data and data["role"] in ("admin", "member"):
            admins = [profile for profile in family_hub.profiles() if profile["role"] == "admin"]
            if data["role"] == "member" and target["role"] == "admin" and len(admins) <= 1:
                return fail("There has to be at least one admin.")
            sets.append("role=?")
            args.append(data["role"])
        for flag in ("can_request", "can_tag"):
            if flag in data:
                sets.append(flag + "=?")
                args.append(1 if data[flag] else 0)
    if sets:
        con = family_hub.con()
        try:
            con.execute("UPDATE profiles SET %s WHERE id=?" % ", ".join(sets), (*args, profile_id))
        except sqlite3.IntegrityError:
            con.rollback()
            return fail("There is already a profile with that name.", 409)
        con.commit()
        if "passcode" in data and profile_id == who["id"]:
            # a new passcode ends every other sign-in of this profile, but not the one that just changed it
            session["pc"] = passcode_stamp(family_hub.profile(profile_id))
    # these two live outside the profiles table, so only once the update above has gone through.
    # (a clashing name used to leave them half-saved)
    if admin and "can_series" in data:
        series.allow(family_hub, profile_id, bool(data["can_series"]))
    if admin and "can_request_media" in data:
        allowed = media_allowed_ids(family_hub)
        if data["can_request_media"]:
            allowed.add(profile_id)
        else:
            allowed.discard(profile_id)
        db.kv_set(family_hub.con(), MEDIA_KEY, json.dumps(sorted(allowed)))
    return jsonify({"ok": True})


@bp.delete("/profiles/<int:profile_id>")
@admin_only
def delete_profile(who, profile_id):
    family_hub = hub()
    if profile_id == who["id"]:
        return fail("You cannot delete the profile you are using.")
    if not family_hub.profile(profile_id):
        return fail("That profile does not exist.", 404)
    family_hub.backup_now()  # keep a copy of their ratings and tags before removing them
    con = family_hub.con()
    for table in ("personal_tags", "ratings", "reading", "watch"):
        con.execute("DELETE FROM %s WHERE profile_id=?" % table, (profile_id,))
    family_hub.devices.forget_profile(profile_id)
    goodreads.forget_profile(family_hub, profile_id)
    recommend.forget_profile(family_hub, profile_id)
    series.forget_profile(family_hub, profile_id)
    allowed = media_allowed_ids(family_hub)
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


def _target_or_fail(who, profile_id, refusal):
    """(profile, None) for _for_profile, else (None, error). An admin asking about a profile that's
    gone gets told so, not the members' "only your own" refusal."""
    target = _for_profile(who, profile_id)
    if target:
        return target, None
    if is_admin(who) and isinstance(profile_id, int):
        return None, fail("That profile does not exist.", 404)
    return None, fail(refusal, 403)


@bp.get("/profiles/<int:profile_id>/devices")
@profile_only
def list_devices(who, profile_id):
    target, error = _target_or_fail(who, profile_id, "You can only see your own devices.")
    if error:
        return error
    family_hub = hub()
    return jsonify({
        "profile": {"id": target["id"], "name": target["name"], "sync_ready": bool(target["sync_key"])},
        "devices": [family_hub.devices.describe(device) for device in family_hub.devices.rows(target["id"])],
        "mail_ready": family_hub.config.mail_configured, "sender": family_hub.config.mail_sender if family_hub.config.mail_configured else "",
        "limit_mb": family_hub.devices.limit_mb(),
    })


@bp.post("/profiles/<int:profile_id>/devices")
@profile_only
def add_device(who, profile_id):
    target, error = _target_or_fail(who, profile_id, "You can only change your own devices.")
    if error:
        return error
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
    family_hub = hub()
    data = goodreads.describe(family_hub, target["id"])
    data["busy"] = target["id"] in family_hub.goodreads_busy
    data["mine"] = target["id"] == who["id"]
    if not data["mine"]:
        data["url"] = data["user_id"] = ""
    return data


def _goodreads_sync_later(profile_id, full):
    family_hub = hub()
    if profile_id in family_hub.goodreads_busy:
        return False
    family_hub.goodreads_busy.add(profile_id)
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
    target, error = _target_or_fail(who, profile_id, "You can only see your own Goodreads link.")
    if error:
        return error
    return jsonify(_goodreads_view(who, target))


@bp.post("/profiles/<int:profile_id>/goodreads")
@profile_only
def goodreads_set(who, profile_id):
    """Save or remove the link ({"link": ...}), or read the shelves again ({"sync": true})."""
    target, error = _target_or_fail(who, profile_id, "You can only change your own Goodreads link.")
    if error:
        return error
    data = request.get_json(silent=True) or {}
    family_hub = hub()
    if "link" in data:
        try:
            linked = goodreads.set_link(family_hub, target["id"], data.get("link"))
        except goodreads.GoodreadsError as exc:
            return fail(str(exc))
        if linked:
            _goodreads_sync_later(target["id"], full=True)
    elif data.get("sync"):
        if not goodreads.link(family_hub, target["id"]):
            return fail("Save a Goodreads link first.")
        _goodreads_sync_later(target["id"], full=True)
    return jsonify(_goodreads_view(who, target))


@bp.post("/profiles/<int:profile_id>/goodreads/import")
@profile_only
def goodreads_import(who, profile_id):
    """Take a Goodreads export file, for people whose profile is private."""
    target, error = _target_or_fail(who, profile_id, "You can only import into your own profile.")
    if error:
        return error
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
    family_hub = hub()
    names = family_hub.profile_names()
    books = [{"key": key, "title": entry["title"], "author": entry["author"], "goodreads_id": entry["gr_id"],
              "people": [names.get(profile_id, "?") for profile_id in entry["profiles"]]}
             for key, entry in goodreads.elsewhere(family_hub).items()]
    books.sort(key=lambda book: (-len(book["people"]), book["title"].lower()))
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
    family_hub = hub()
    family_hub.library.refresh()
    out = {"hardcover": None, "hardcover_error": "", "series_ready": 0, "series_making": 0}
    if who["hardcover_token"]:
        try:
            out["hardcover"] = integrations.hardcover_sync(family_hub, who)
        except integrations.IntegrationError as exc:
            out["hardcover_error"] = str(exc)
    for item in _my_series(family_hub, who):
        for part in item["parts"]:
            out["series_ready" if part["state"] == "ready" else "series_making"] += 1
    return jsonify(out)


def _my_series(family_hub, who):
    """The comic series on this person's list and where each part stands for their reading app (starting any
    not started). [{name, parts: [{title, state, percent, error}]}]"""
    from . import opds
    shelf = family_hub.devices.shelf(who["id"])
    books = [book for book in (family_hub.library.by_key(key) for key in shelf) if book]
    return [{"name": item["series"], "parts": opds.series_status(family_hub, item["volumes"])}
            for item in opds._gather(books, family_hub.library.books()) if "volumes" in item]


def reading_app(family_hub, who):
    """Everything on this person's list as their reading app gets it, and where each stands:
    "on_app" (the app downloaded this version), "newer" (it has an older, shorter copy), "ready" (not downloaded
    yet), "making" (being put together, with percent), "waiting" or "failed"."""
    from . import chapterfile, opds
    shelf = family_hub.devices.shelf(who["id"])
    books = [book for book in (family_hub.library.by_key(key) for key in shelf) if book]
    comics_elsewhere = opds.comics_elsewhere(family_hub)
    if comics_elsewhere:  # read in Paperback, from Komga: not this app's business
        books = opds.without_comics(family_hub, books)
    listed = []
    con = family_hub.con()
    # if the app's sent us a reading position for it, it's obviously ON the app, whatever our download notes say
    synced = {row["book_key"] for row in family_hub.reading_rows(who["id"]) if row["source"] == "device"}

    def placed(path):
        row = con.execute("SELECT hash FROM joined_books WHERE path=?", (path,)).fetchone()
        return bool(row and db.kv_get(con, "joined:%d:%s" % (who["id"], row["hash"])))

    def seen(item, version, ready_state, has=False):
        if ready_state != "ready":
            return ready_state
        got = opds.fetched_version(family_hub, who["id"], item)
        # same version as offered = on the app. an older one = "a newer copy's waiting". never fetched = ready
        return "on_app" if got == str(version) or has else "newer" if got else "ready"

    for item in opds._gather(books, family_hub.library.books()):
        if "volumes" in item:
            for part in opds.series_status(family_hub, item["volumes"]):
                listed.append({"title": part["title"], "kind": "series", "item": part["item"], "version": part["version"],
                               "state": seen(part["item"], part["version"], part["state"], placed(part["path"])),
                               "percent": part["percent"], "error": part["error"], "link": "#/shelf/" + item["series"]})
        else:
            listed.append({"title": item["title"], "kind": "book", "item": "book:%d" % item["id"], "version": "file",
                           "state": seen("book:%d" % item["id"], "file", "ready", item["key"] in synced),
                           "percent": 0, "error": "", "link": "#/book/%d" % item["id"]})
    if family_hub.series.configured and can_series(who) and not comics_elsewhere:
        for series_id in series.my_books(family_hub, who["id"]):
            try:
                info = family_hub.series.detail(series_id)
            except series.SeriesError:
                continue
            parts = opds._chapter_parts(info)
            for part_no, kept in enumerate(parts, 1):
                if not kept:
                    continue
                title = info["title"] if len(parts) == 1 else "%s · Part %d" % (info["title"], part_no)
                item_name = "chapters:%d:%d" % (series_id, part_no)
                # the reading app gets the CBZ the server keeps (or the last one made while a longer one's on its way)
                offered = chapterfile.ready_file(family_hub, info, part_no, older=True)[0]
                newest = chapterfile.files(family_hub, info)[part_no - 1]
                version = chapterfile.stamp_of(offered) if offered else ""
                listed.append({"title": title, "kind": "chapters", "item": item_name, "version": version,
                               "state": seen(item_name, version, "ready") if offered else newest["state"],
                               "percent": 0, "error": newest["error"], "link": "#/series/%d" % series_id})
    return listed


@bp.get("/me/reading-app")
@profile_only
def my_reading_app(who):
    """For My books, and the note shown on every page while something is being put together."""
    items = reading_app(hub(), who)
    from . import opds
    return jsonify({"items": items, "on_app": sum(1 for item in items if item["state"] == "on_app"), "total": len(items),
                    "making": [item for item in items if item["state"] in ("making", "waiting")],
                    "comics_elsewhere": opds.comics_elsewhere(hub())})


@bp.get("/me/comics-app")
@profile_only
def my_comics_app(who):
    """For My books: what this person's comic app (Paperback) can get from Komga, and what's new in it."""
    from . import komga, opds
    family_hub = hub()
    if not opds.comics_elsewhere(family_hub):
        return jsonify({"ready": False, "used": False})
    try:
        found = komga.overview(family_hub, who["id"])
    except komga.KomgaError as exc:
        found = {"ready": False, "message": "Komga did not answer just now: %s" % exc}
    found["used"] = True
    found["seen"] = db.kv_get(family_hub.con(), "comicsapp:seen:%d" % who["id"]) or ""
    return jsonify(found)


@bp.post("/me/comics-app/seen")
@profile_only
def my_comics_app_seen(who):
    """Everything added so far has been looked at: "New" marks start again from now."""
    con = hub().con()
    db.kv_set(con, "comicsapp:seen:%d" % who["id"], db.now())
    con.commit()
    return jsonify({"ok": True})


@bp.post("/me/reading-app/have")
@profile_only
def my_reading_app_have(who):
    """"It's on my app": for books the reading app downloaded before Family Hub noted downloads."""
    from . import opds
    data = request.get_json(silent=True) or {}
    wanted = {item["item"]: item for item in reading_app(hub(), who)}
    row = wanted.get(str(data.get("item") or ""))
    if not row:
        return fail("That is not on your list.", 404)
    opds.fetched(hub(), who, row["item"], row["version"])
    return jsonify({"ok": True})


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
    title = clean(data.get("title"))
    if not title:
        return fail("Enter the book's title.")
    author = clean(data.get("author"))
    key = db.make_key(title, author)
    match = next((book for book in hub().library.books() if db.make_key(book["title"], book["author"]) == key), None)
    if match:
        key = match["key"]
    status = data.get("status") or "finished"
    if status not in ("finished", "reading"):
        return fail("Unknown status.")
    percent, pages = number(data.get("percent")), number(data.get("pages"), int)
    if percent is None and data.get("percent") not in (None, ""):
        return fail("The percent read should be a number.")
    finished = data.get("finished")
    hub().save_reading(
        who["id"], key, status=status, source="manual", percent=percent,
        finished=finished if isinstance(finished, str) and finished else None, title=title, author=author,
        pages=pages if pages and pages > 0 else None,
    )
    return jsonify({"ok": True, "book_key": key})


@bp.get("/me/kindle")
@profile_only
def my_kindle(who):
    """This person's Kindle books: finished or not, and the days each one moved on."""
    return jsonify(kindle.library(hub(), who["id"]))


@bp.post("/me/kindle")
@profile_only
def bring_in_kindle(who):
    """A file saved by the Kindle export script (static/kindle-export.js)."""
    try:
        counts = kindle.bring_in(hub(), who["id"], request.get_json(silent=True))
    except kindle.KindleError as exc:
        return fail(str(exc))
    return jsonify(dict(counts, **kindle.library(hub(), who["id"])))


@bp.post("/me/kindle/<asin>/finished")
@profile_only
def kindle_finished(who, asin):
    """Finished (or not after all): Amazon does not say how long a book is, so the reader does."""
    data = request.get_json(silent=True) or {}
    book = next((entry for entry in kindle.library(hub(), who["id"])["books"] if entry["asin"] == asin), None)
    if book is None:
        return fail("That book is not in your Kindle library here.", 404)
    day = data.get("day") if isinstance(data.get("day"), str) and re.match(r"^\d{4}-\d{2}-\d{2}$", data.get("day") or "") else None
    was = next((row["status"] for row in hub().reading_rows(who["id"]) if row["book_key"] == book["key"]), None)
    if data.get("finished", True):
        hub().save_reading(who["id"], book["key"], status="finished", source="manual", finished=day or (book["days"][-1] if book["days"] else None),
                           device="Kindle", title=book["title"], author=book["author"])
    else:
        hub().save_reading(who["id"], book["key"], status="reading" if book["position"] > 1 else "none", source="manual",
                           device="Kindle", title=book["title"], author=book["author"])
    reply = kindle.library(hub(), who["id"])
    if data.get("finished", True) and was != "finished":
        reply.update(finished_this_year(hub(), who, who, book["key"]))
    return jsonify(reply)


@bp.get("/me/shelf")
@profile_only
def my_shelf(who):
    """Where each book on this person's list stands on each of their devices."""
    family_hub = hub()
    shelf = family_hub.devices.shelf(who["id"])
    books = {}
    for book in family_hub.library.books():
        if book["key"] in shelf:
            books[book["id"]] = {"added": shelf[book["key"]], "devices": family_hub.devices.book_view(who["id"], book)["devices"]}
    return jsonify({"devices": [{"id": device["id"], "name": device["name"], "kind": device["kind"]}
                                for device in family_hub.devices.rows(who["id"])],
                    "books": books})


# ----------------------------------------------------------------- books

def _user_maps(family_hub, who):
    con = family_hub.con()
    if who == "guest":
        return {}, {}
    reading = {row["book_key"]: row for row in family_hub.reading_rows(who["id"])}
    ratings = {row["book_key"]: row["rating"] for row in
               con.execute("SELECT book_key, rating FROM ratings WHERE profile_id=?", (who["id"],))}
    return reading, ratings


def _summary(book, tags, reading, ratings, pages, kind_of, shelf=()):
    progress = reading.get(book["key"])
    return {
        "id": book["id"], "key": book["key"], "title": book.get("shown_title") or book["title"],
        "author": book.get("shown_author", book["author"]), "part": book.get("part", ""), "subtitle": book.get("subtitle", ""),
        "kind": kind_of.get(book["key"], kinds.UNSORTED),
        "series": book["series"], "series_index": book.get("series_index"), "collection": book.get("collection") or book["series"],
        "formats": sorted(book["formats"]), "has_cover": book["has_cover"],
        "in_library": True, "added": book["added"][:10], "added_at": book["added"], "pages": pages.get(book["key"]),
        "tags": tags.get(book["key"], {"epub": book["tags"], "community": [], "family": [], "mine": []}),
        "status": progress["status"] if progress else "none", "percent": progress["percent"] if progress else 0,
        "finished": progress["finished"] if progress else None, "device": progress["device"] if progress else None,
        "rating": ratings.get(book["key"], 0), "on_shelf": book["key"] in shelf,
    }


@bp.get("/home")
@signed_in
def home(who):
    """What is newest on the family server, for the Home page."""
    family_hub = hub()
    library = sorted(family_hub.library.books(), key=lambda book: book["added"], reverse=True)
    chosen = family_hub.covers.index()
    month = date.today().isoformat()[:7]
    return jsonify({
        "books": [{"id": book["id"], "title": book.get("shown_title") or book["title"], "author": book.get("shown_author", book["author"]),
                   "added": book["added"][:10], "formats": sorted(book["formats"]), "has_cover": book["has_cover"] or book["key"] in chosen,
                   "cover_v": chosen.get(book["key"])} for book in library[:12]],
        "books_month": sum(1 for book in library if book["added"][:7] == month),
        "week": _this_week(family_hub, who, library, chosen),
    })


def _this_week(family_hub, who, library, chosen):
    """Home's "This week at home": the last seven days. Books that arrived; for a profile, the book it is reading
    and how many it finished; how many the whole family finished (a number only: whose is theirs to tell)."""
    since = (date.today() - timedelta(days=6)).isoformat()
    added = [{"id": book["id"], "title": book.get("shown_title") or book["title"], "author": book.get("shown_author", book["author"]),
              "added": book["added"][:10], "formats": sorted(book["formats"]), "has_cover": book["has_cover"] or book["key"] in chosen,
              "cover_v": chosen.get(book["key"])} for book in library if book["added"][:10] >= since][:12]
    pid = pid_of(who)
    if not pid:
        return {"since": since, "added": added, "mine": None, "family_finished": None}
    finished = lambda row: row["status"] == "finished" and (row["finished"] or "") >= since
    keys = family_hub.library.keys()
    rows = family_hub.reading_rows(pid)
    going = sorted((row for row in rows if row["status"] == "reading"), key=lambda row: row["updated"] or "", reverse=True)
    reading = None
    if going:
        row = going[0]
        book = keys.get(row["book_key"]) or {}
        reading = {"id": book.get("id"), "title": book.get("title") or row["title"] or row["book_key"], "author": row["author"] or "",
                   "percent": round(row["percent"] or 0), "device": row["device"] or "", "updated": row["updated"]}
    return {"since": since, "added": added,
            "mine": {"reading": reading, "finished": sum(1 for row in rows if finished(row))},
            "family_finished": sum(1 for row in family_hub.reading_rows() if finished(row))}


@bp.get("/books")
@signed_in
def books(who):
    family_hub = hub()
    pid = pid_of(who)
    tags = family_hub.tag_index(pid)
    reading, ratings = _user_maps(family_hub, who)
    pages = {row["book_key"]: row["pages"] for row in family_hub.con().execute("SELECT book_key, pages FROM book_meta")}
    kind_of = family_hub.kind_index()
    shelf = family_hub.devices.shelf(pid) if pid else {}
    items = [_summary(book, tags, reading, ratings, pages, kind_of, shelf) for book in family_hub.library.books()]

    keys = family_hub.library.keys()
    # Guests see the library and nothing else. Everyone else also sees books somebody
    # in the family has on Goodreads but the library lacks, without being told whose.
    elsewhere = goodreads.elsewhere(family_hub) if pid else {}
    mine_on_kindle = set(kindle.keys(family_hub, pid)) if pid else set()
    mine = goodreads.book_ids(family_hub, pid) if pid else {}

    def outside(key, title, author, book_pages, progress=None):
        """A book read somewhere else (Kindle, Goodreads) dressed up like a library item, minus the files."""
        return {
            "id": None, "key": key, "title": title or key, "author": author or "", "series": "",
            "kind": kind_of.get(key, kinds.UNSORTED),
            "formats": [], "has_cover": False, "in_library": False, "added": "", "pages": book_pages,
            "tags": tags.get(key, {"epub": [], "community": [], "family": [], "mine": []}),
            "status": progress["status"] if progress else "none", "percent": progress["percent"] if progress else 0,
            "finished": progress["finished"] if progress else None, "device": None,
            "rating": ratings.get(key, 0), "on_shelf": False, "kindle": key in elsewhere,
            "goodreads_id": mine.get(key) or (elsewhere[key]["gr_id"] if key in elsewhere else ""),
        }

    chosen = family_hub.covers.index()
    for item in items:
        item["kindle"] = False
        item["goodreads_id"] = mine.get(item["key"], "")
        if item["key"] in chosen:  # a cover picked in Family Hub; the date makes browsers fetch the new picture
            item["has_cover"], item["cover_v"] = True, chosen[item["key"]]
    for key, progress in reading.items():  # books read elsewhere (Kindle and so on)
        if key in keys or key.startswith("doc:"):
            continue
        items.append(outside(key, progress["title"], progress["author"], progress["pages"], progress))
    for key, entry in elsewhere.items():
        if key not in reading:
            items.append(outside(key, entry["title"], entry["author"], entry["pages"]))
    # this person's own Kindle books not started yet, so they can be found in the library search too
    listed = {item["key"] for item in items}
    for key, (title, author) in (kindle.keys(family_hub, pid).items() if pid else []):
        if key not in listed:
            items.append(outside(key, title, author, pages.get(key)))
            listed.add(key)
    for item in items:
        if item["key"] in mine_on_kindle:
            item["kindle"] = True

    tabletop.annotate(family_hub, items)  # game, edition, role, product line, language
    public = readers.index(family_hub)
    for item in items:
        item["readers"] = public.get(item["key"])  # what readers at large think; None when nobody knows
    shown = request.args.get("filter") or "all"
    # series by chapter only make sense in the broad lists, never inside one library series
    if not (request.args.get("series") or request.args.get("collection")) and shown in ("all", "reading", "unread", "read"):
        items += chapter_items(family_hub, who, tags, ratings)

    query = (request.args.get("q") or "").strip().lower()
    tag = (request.args.get("tag") or "").strip().lower()
    scopes = [scope for scope in (request.args.get("scopes") or "epub,community,family,mine").split(",") if scope]
    only_kinds = {kind for kind in (request.args.get("kinds") or "").split(",") if kind}
    one_series = (request.args.get("series") or "").strip().lower()
    collection = (request.args.get("collection") or "").strip().lower()
    try:
        terms = search.clean(json.loads(request.args.get("terms") or "[]"))
    except ValueError:
        terms = []
    nested = tabletop.descendants(family_hub.tag_parents())  # asking for "Pathfinder" means "Pathfinder 2e" too
    matches = search.matcher(terms, nested)
    tag_and_under = tabletop.under(nested, tag) if tag else set()

    def series_tags(item):
        """Tags put on the item's series (or set of runs) as a whole."""
        found = {"family": [], "mine": []}
        for name in {item.get("series") or "", item.get("collection") or ""} - {""}:
            got = tags.get(series_key(name))
            if got:
                found["family"] += got["family"]
                found["mine"] += got["mine"]
        return found

    def keep(item):
        # a gauntlet of cheap checks first; whatever survives faces the filter at the bottom
        if only_kinds and item["kind"] not in only_kinds:
            return False
        if one_series and (item.get("series") or "").lower() != one_series:
            return False
        if collection and (item.get("collection") or "").lower() != collection:
            return False
        if query:
            haystack = " ".join([item["title"], item["author"], item["series"], item.get("subtitle") or ""] +
                                [found for group in item["tags"].values() for found in group]).lower()
            if query not in haystack:
                return False
        if tag:
            every_tag = [found for scope in scopes for found in item["tags"].get(scope, []) + series_tags(item).get(scope, [])]
            if not any(found.lower() in tag_and_under for found in every_tag):
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
        }.get(shown, True)

    items = [item for item in items if keep(item)]
    if request.args.get("view") == "tabletop":  # every game book, hidden or not, as Game › Edition › Product line
        items = [item for item in items if tabletop.is_game_book(item)]
        return jsonify({"books": items, "total": len(items), "tree": tabletop.tree(items), "hidden": tabletop.hidden(family_hub, pid)})
    hidden_count = 0
    if request.args.get("feed") == "1" and not (terms or query or tag):  # a search finds hidden books; the feed leaves them out
        items, hidden_count = tabletop.feed(family_hub, pid, items, nested)
    if shown == "foryou" and pid:
        # Unread library books, best match with what this person has finished and rated first.
        mine = recommend.for_you(family_hub, pid, tags)
        items = [dict(item, why=mine["books"][item["key"]]["why"]) for item in items if item["in_library"] and item["key"] in mine["books"]]
        items.sort(key=lambda item: -mine["books"][item["key"]]["score"])
        return jsonify({"books": items, "total": len(items),
                        "taste": {"ready": mine["ready"], "have": mine["have"], "need": mine["need"]}})
    if one_series or collection:  # one series or set, run by run, in reading order
        items.sort(key=lambda item: ((item.get("series") or "").lower(), item.get("series_index") or 0, item["title"].lower()))
    return jsonify({"books": items, "total": len(items), "series_info": _series_cards(items, tags, ratings), "hidden": hidden_count})


def _series_cards(items, tags, ratings):
    """The series' own ratings and tags, by series name in lower case, for the series cards."""
    info = {}
    for name in {name for item in items for name in (item.get("series"), item.get("collection")) if name}:
        key = series_key(name)
        own = tags.get(key, {})
        info[name.lower()] = {"rating": ratings.get(key, 0),
                              "tags": {"family": own.get("family", []), "mine": own.get("mine", [])}}
    return info


def chapter_items(family_hub, who, tags, ratings):
    """Series followed chapter by chapter, as items in the library beside the books: tagged "Chapter by chapter"
    and with how far publication has got (Ongoing, Hiatus, Finished), the reading site's own genres as community
    tags, and the tags and rating given to the series as a whole."""
    if not family_hub.series.configured or not can_series(who):
        return []
    from . import chapterfile
    items = []
    mine = set(series.my_books(family_hub, who["id"]))
    for followed in family_hub.series.shelf_cached()["series"]:
        own = tags.get(series_key(followed["title"]), {})
        badges = [series.CHAPTER_TAG] + ([followed["status"]] if followed["status"] else [])
        if followed["chapters"] and not followed["unread"]:
            status = "finished"
        else:
            status = "reading" if followed["read"] else "none"
        items.append({
            "id": None, "key": "chapters:%d" % followed["id"], "series_id": followed["id"], "chapter_series": True,
            "title": followed["title"], "author": followed["author"], "series": "", "collection": "", "part": "", "subtitle": "",
            "kind": kinds.GRAPHIC, "formats": [], "has_cover": True, "in_library": True, "added": "", "added_at": "",
            "pages": None, "publication": followed["status"], "source": followed["source"],
            "chapters": followed["chapters"], "read": followed["read"], "unread": followed["unread"],
            "downloaded": followed["downloaded"], "next": followed["next"],
            "tags": {"epub": badges, "community": list(followed["genres"]), "family": own.get("family", []), "mine": own.get("mine", [])},
            "status": status, "percent": round(100 * followed["read"] / followed["chapters"]) if followed["chapters"] else 0,
            "finished": None, "device": None, "file_parts": chapterfile.made_parts(family_hub, followed["id"]),
            "rating": ratings.get(series_key(followed["title"]), 0), "on_shelf": followed["id"] in mine,
            "kindle": False, "goodreads_id": "", "readers": None,
        })
    return items


series_key = db.series_key  # a series is tagged and rated as a whole under this key


def _tag_kind(who, data):
    """("family" or "mine", None), or (None, error) when this profile may not touch family tags."""
    kind = "family" if data.get("kind") == "family" else "mine"
    if kind == "family" and not can_tag(who):
        return None, fail("This profile cannot change family tags.", 403)
    return kind, None


def _tag_text(data):
    """The tag to take off, exactly as the page has it (a list or number used to reach the database and 500)."""
    tag = data.get("tag")
    return tag if isinstance(tag, str) else ""


def _rating(data):
    """The 1-5 rating a page sent (0 takes it away), or None when it isn't a number."""
    if data.get("rating") in (None, "", 0):
        return 0
    return number(data.get("rating"), int)


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
            for followed in hub().series.shelf()["series"]:
                if followed["title"].lower() == asked:
                    return followed["title"], data
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
    family_hub = hub()
    pid = pid_of(who)
    key = series_key(name)
    tagged = family_hub.tag_index(pid).get(key, {"family": [], "mine": []})
    reading, ratings = _user_maps(family_hub, who)
    low = name.lower()
    volume_ratings = [ratings.get(book["key"]) for book in family_hub.library.books()
                      if low in ((book["series"] or "").lower(), (book.get("collection") or "").lower())]
    rated = [rating for rating in volume_ratings if rating]
    return jsonify({"name": name, "key": key, "tags": {"family": tagged["family"], "mine": tagged["mine"]},
                    "rating": ratings.get(key, 0), "volume_average": round(sum(rated) / len(rated), 1) if rated else None,
                    "volumes_rated": len(rated), "volumes": len(volume_ratings)})


@bp.post("/series-info/tags")
@profile_only
def series_add_tag(who):
    name, data = _series_name()
    if not name:
        return fail("Nothing in the library belongs to that series.", 404)
    kind, error = _tag_kind(who, data)
    if error:
        return error
    family_hub = hub()
    family_hub.ensure_meta(series_key(name), title=name, source="series")
    if not family_hub.add_tag(kind, series_key(name), clean(data.get("tag")), who["id"]):
        return fail("Type a tag first.")
    return jsonify({"ok": True})


@bp.delete("/series-info/tags")
@profile_only
def series_remove_tag(who):
    name, data = _series_name()
    if not name:
        return fail("Nothing in the library belongs to that series.", 404)
    kind, error = _tag_kind(who, data)
    if error:
        return error
    hub().remove_tag(kind, series_key(name), _tag_text(data), who["id"])
    return jsonify({"ok": True})


@bp.post("/series-info/rating")
@profile_only
def series_rate(who):
    name, data = _series_name()
    if not name:
        return fail("Nothing in the library belongs to that series.", 404)
    rating = _rating(data)
    if rating is None:
        return fail("Give a rating from 1 to 5.")
    family_hub = hub()
    family_hub.ensure_meta(series_key(name), title=name, source="series")
    family_hub.set_rating(series_key(name), who["id"], rating)
    return jsonify({"ok": True})


def _library_book(book_id):
    """The library entry, or None (callers send "That book is not in the library." 404)."""
    return hub().library.get(book_id)


@bp.delete("/books/<int:book_id>")
@admin_only
def delete_book(who, book_id):
    """Delete a book from the library, with every copy of it. Admins only; the library's database is copied first."""
    family_hub = hub()
    book = _library_book(book_id)
    if not book:
        return fail("That book is not in the library.", 404)
    from . import librarymanager
    if not librarymanager.configured(family_hub.config):
        return fail("Calibre-Web's sign-in is not set up yet. Add it under Admin, Hub, Connections, Calibre-Web.")
    ids = [book["id"]] + [copy["id"] for copy in book["copies"]]
    done = 0
    try:
        saved = family_hub.duplicates.backup()
        for entry in ids:
            family_hub.manager.delete(entry)
            done += 1
    except (OSError, sqlite3.Error, librarymanager.ManagerError) as exc:  # sqlite3: the backup copy failed
        if done:
            return fail("%s %d of %d entries were already deleted before it stopped." % (exc, done, len(ids)), 502)
        return fail(str(exc), 502)
    finally:
        family_hub.library.refresh(force=True)
    return jsonify({"removed": book["title"], "entries": len(ids), "backup": saved.name})


@bp.get("/books/<int:book_id>")
@signed_in
def book_detail(who, book_id):
    family_hub = hub()
    book = _library_book(book_id)
    if not book:
        return fail("That book is not in the library.", 404)
    if book["copy_of"]:  # an old link to a copy: show the book it is a copy of
        book = family_hub.library.get(book["copy_of"]) or book
        book_id = book["id"]
    pid = pid_of(who)
    tags = family_hub.tag_index(pid)
    reading, ratings = _user_maps(family_hub, who)
    con = family_hub.con()
    pages = {book["key"]: (con.execute("SELECT pages FROM book_meta WHERE book_key=?", (book["key"],)).fetchone() or [None])[0]}
    data = _summary(book, tags, reading, ratings, pages, family_hub.kind_index(), family_hub.devices.shelf(pid) if pid else {})
    data["synopsis"] = book["synopsis"]
    data["pubdate"] = book["pubdate"]
    data["goodreads_id"] = goodreads.book_ids(family_hub, pid).get(book["key"], "") if pid else ""
    data["readers"] = readers.index(family_hub).get(book["key"])
    chosen = family_hub.covers.row(book["key"])
    data["own_cover"] = chosen["source"] if chosen else ""
    if chosen:
        data["has_cover"], data["cover_v"] = True, chosen["at"]
    names = family_hub.profile_names()
    data["family_ratings"] = [] if who == "guest" else [
        {"name": names.get(row["profile_id"], "?"), "rating": row["rating"]}
        for row in con.execute("SELECT profile_id, rating FROM ratings WHERE book_key=? ORDER BY profile_id", (book["key"],))
        if row["profile_id"] != who["id"]
    ]
    data["community_sources"] = sorted({catalogues.label(row["source"]) for row in con.execute(
        "SELECT source FROM community_tags WHERE book_key=?", (book["key"],))})
    data["can_look_up"] = bool(catalogues.active(family_hub.config))
    if pid:
        data["shelf"] = family_hub.devices.book_view(pid, book)
    if is_admin(who):  # admins look after other people's shelves, such as Nan's
        theirs = {row["profile_id"]: row["status"] for row in con.execute(
            "SELECT profile_id, status FROM reading WHERE book_key=?", (book["key"],))}
        data["family_shelves"] = [
            dict(family_hub.devices.book_view(profile["id"], book), profile_id=profile["id"], name=profile["name"],
                 status=theirs.get(profile["id"], "none"))
            for profile in family_hub.profiles() if profile["id"] != pid]
    if book["series"]:  # the whole series' own tags and rating apply to each of its volumes
        key = series_key(book["series"])
        own = tags.get(key, {})
        data["from_series"] = {"name": book["series"], "rating": ratings.get(key) or 0,
                               "tags": {"family": own.get("family", []), "mine": own.get("mine", [])}}
    data["converted"] = (family_hub.config.converted_dir / ("%d.epub" % book_id)).exists()
    data["convert_status"] = db.kv_get(con, "convert:%d" % book_id)
    if is_admin(who):
        data["copies"] = [{"id": copy["id"], "added": copy["added"][:10],
                           "files": [{"format": fmt, "size": copy["sizes"].get(fmt, 0)} for fmt in sorted(copy["own_formats"])]}
                          for copy in book["copies"]]
    return jsonify(data)


@bp.get("/books/<int:book_id>/cover")
@signed_in
def cover(who, book_id):
    book = _library_book(book_id)
    path = (hub().covers.path(book["key"]) or hub().library.cover_path(book)) if book else None
    if not path:
        return "", 404
    return send_file(path, mimetype="image/jpeg", max_age=86400)


def _cover_book(who, book_id):
    """(book, None) when this profile may change the book's cover, else (None, error)."""
    book = _library_book(book_id)
    if not book:
        return None, fail("That book is not in the library.", 404)
    if not can_tag(who):
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
    family_hub = hub()
    book = _library_book(book_id)
    if not book:
        return fail("That book is not in the library.", 404)
    if fmt.lower() == "converted":
        path = family_hub.config.converted_dir / ("%d.epub" % book_id)
        ext = "epub"
        if not path.exists():
            return fail("There is no converted copy yet.", 404)
    else:
        path = family_hub.library.file_path(book, fmt)
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
    volumes = sorted((book for book in hub().library.books() if name and (book["series"] or "").lower() == name.lower()),
                     key=lambda book: (book.get("series_index") or 0, book["title"].lower()))
    if not volumes:
        return fail("Nothing in the library belongs to that series.", 404)
    try:
        path, download_name = seriesfile.build(hub().library, volumes, volumes[0]["series"])
    except seriesfile.SeriesFileError as exc:
        return fail(str(exc), 422)
    response = send_file(path, as_attachment=True, download_name=download_name)
    response.call_on_close(lambda: path.unlink(missing_ok=True))  # temp file, binned once it's been sent
    return response


@bp.post("/books/<int:book_id>/tags")
@profile_only
def add_tag(who, book_id):
    book = _library_book(book_id)
    if not book:
        return fail("That book is not in the library.", 404)
    data = request.get_json(silent=True) or {}
    kind, error = _tag_kind(who, data)
    if error:
        return error
    if not hub().add_tag(kind, book["key"], clean(data.get("tag")), who["id"]):
        return fail("Type a tag first.")
    return jsonify({"ok": True})


@bp.delete("/books/<int:book_id>/tags")
@profile_only
def remove_tag(who, book_id):
    book = _library_book(book_id)
    if not book:
        return fail("That book is not in the library.", 404)
    data = request.get_json(silent=True) or {}
    kind, error = _tag_kind(who, data)
    if error:
        return error
    hub().remove_tag(kind, book["key"], _tag_text(data), who["id"])
    return jsonify({"ok": True})


@bp.post("/books/<int:book_id>/lookup")
@profile_only
def look_up_tags(who, book_id):
    """Ask the catalogues about one book again, now."""
    book = _library_book(book_id)
    if not book:
        return fail("That book is not in the library.", 404)
    if not can_tag(who):
        return fail("This profile cannot change tags.", 403)
    family_hub = hub()
    if not catalogues.active(family_hub.config):
        return fail("Looking up tags is switched off. An admin can turn it on under Admin, Hub, Connections.")
    result = family_hub.catalogues.look_up(book, force=True)
    failed = [catalogues.label(source) for source, added in result.items() if added is None]  # None = it errored
    return jsonify({"added": sum(added or 0 for added in result.values()), "failed": failed,
                    "sources": [catalogues.label(source) for source, added in result.items() if added]})


@bp.post("/books/kind")
@profile_only
def set_kind(who):
    """Set the kind of one or many books, and optionally add a family tag to them."""
    if not can_tag(who):
        return fail("This profile cannot change how books are sorted.", 403)
    data = request.get_json(silent=True) or {}
    kind, tag = data.get("kind"), clean(data.get("tag"))
    if kind and kind not in kinds.KINDS:
        return fail("Unknown kind of book.")
    if not kind and not tag:
        return fail("Choose a kind or type a tag.")
    family_hub = hub()
    count = 0
    for book_id in id_list(data.get("ids")):
        book = family_hub.library.get(book_id)
        if not book:
            continue
        if kind:
            family_hub.set_kind(book["key"], kind)
        if tag:
            family_hub.add_tag("family", book["key"], tag, who["id"])
        count += 1
    family_hub.con().commit()
    family_hub.touch()
    return jsonify({"updated": count})


@bp.post("/books/<int:book_id>/rating")
@profile_only
def rate(who, book_id):
    book = _library_book(book_id)
    if not book:
        return fail("That book is not in the library.", 404)
    rating = _rating(request.get_json(silent=True) or {})
    if rating is None:
        return fail("Give a rating from 1 to 5.")
    hub().set_rating(book["key"], who["id"], rating)
    return jsonify({"ok": True})


@bp.post("/books/<int:book_id>/reading")
@profile_only
def set_reading(who, book_id):
    book = _library_book(book_id)
    if not book:
        return fail("That book is not in the library.", 404)
    data = request.get_json(silent=True) or {}
    status = data.get("status")
    if status not in ("reading", "finished", "none", None):
        return fail("Unknown status.")
    target, error = _target_or_fail(who, data.get("profile_id"), "Only an admin can set this for someone else.")
    if error:
        return error
    percent = number(data.get("percent"))
    if percent is None and data.get("percent") not in (None, ""):
        return fail("The percent read should be a number.")
    family_hub = hub()
    was = next((row["status"] for row in family_hub.reading_rows(target["id"]) if row["book_key"] == book["key"]), None)
    family_hub.save_reading(target["id"], book["key"], status=status, percent=percent, source="manual")
    reply = {"ok": True}
    if status == "finished" and was != "finished":
        reply.update(finished_this_year(family_hub, target, who, book["key"]))
    return jsonify(reply)


def finished_this_year(family_hub, target, who, book_key):
    """Where a book just finished comes in the year: "your 10th book of 2026", or "Nan's 4th" when an admin records it.
    Nothing for a book finished on a day in another year: it is not part of this year's count."""
    year = str(date.today().year)
    rows = family_hub.reading_rows(target["id"])
    if not any(row["book_key"] == book_key and (row["finished"] or "").startswith(year) for row in rows):
        return {}
    count = sum(1 for row in rows if row["status"] == "finished" and (row["finished"] or "").startswith(year))
    return {"finished": {"count": count, "year": int(year), "name": None if target["id"] == who["id"] else target["name"]}}


@bp.post("/books/<int:book_id>/shelf")
@profile_only
def set_shelf(who, book_id):
    """Add a book to "My books" or take it off. Admins can do this for anyone."""
    book = _library_book(book_id)
    if not book:
        return fail("That book is not in the library.", 404)
    data = request.get_json(silent=True) or {}
    target, error = _target_or_fail(who, data.get("profile_id"), "Only an admin can change someone else's books.")
    if error:
        return error
    family_hub = hub()
    result = family_hub.devices.set_shelf(target["id"], book, bool(data.get("on")), by=who["id"])
    if result["sending"]:
        family_hub.devices.kick(current_app._get_current_object())
    return jsonify(result)


@bp.post("/books/<int:book_id>/send")
@profile_only
def send_to_device(who, book_id):
    book = _library_book(book_id)
    if not book:
        return fail("That book is not in the library.", 404)
    data = request.get_json(silent=True) or {}
    device = _device_or_fail(who, data.get("device_id") if isinstance(data.get("device_id"), int) else 0)
    if not device:
        return fail("That device does not exist.", 404)
    family_hub = hub()
    try:
        family_hub.devices.queue(device, book)
    except ValueError as exc:
        return fail(str(exc))
    family_hub.devices.kick(current_app._get_current_object())
    return jsonify({"ok": True, "device": device["name"]})


@bp.post("/books/<int:book_id>/convert")
@profile_only
def convert(who, book_id):
    family_hub = hub()
    book = _library_book(book_id)
    if not book:
        return fail("That book is not in the library.", 404)
    source = family_hub.library.file_path(book, "PDF")
    if not source:
        return fail("This book has no PDF to convert.")
    tool = shutil.which("ebook-convert")
    if not tool:
        hint = " Open it in Calibre-Web and use its Convert button." if family_hub.config.cwa_url else ""
        return fail("The converter is not installed in Family Hub." + hint, 501)
    family_hub.config.converted_dir.mkdir(parents=True, exist_ok=True)
    target = family_hub.config.converted_dir / ("%d.epub" % book_id)
    key = "convert:%d" % book_id
    db.kv_set(family_hub.con(), key, "running")
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
    family_hub = hub()
    pid = pid_of(who)
    scopes = [scope for scope in (request.args.get("scopes") or "epub,community,family,mine").split(",") if scope]
    query = (request.args.get("q") or "").strip().lower()
    keys = set(family_hub.library.keys())
    if pid:  # books this profile read elsewhere are listed in the library too
        keys |= {row["book_key"] for row in family_hub.reading_rows(pid)}
    wanted = {kind for kind in (request.args.get("kinds") or "").split(",") if kind}
    if wanted:  # only the tags of the part of the library being looked at
        kind_of = family_hub.kind_index()
        keys = {key for key in keys if kind_of.get(key, kinds.UNSORTED) in wanted}
    tagged = family_hub.tag_index(pid)
    index = {key: groups for key, groups in tagged.items() if key in keys or key.startswith("series:")}  # a series' own tags count too
    if not wanted or kinds.GRAPHIC in wanted:
        for item in chapter_items(family_hub, who, tagged, {}):
            index.pop(series_key(item["title"]), None)  # its own tags are in its entry; counted once
            index[item["key"]] = item["tags"]
    merged = request.args.get("merged") == "1"  # one entry per name, whichever sorts of tag carry it
    counts = {}
    for key, groups in index.items():
        for scope in scopes:
            for tag in groups.get(scope, []):
                if query and query not in tag.lower():
                    continue
                # keys is a SET of books, so a book tagged "Fantasy" by both family and file still counts once
                entry = counts.setdefault(tag.lower() if merged else (tag.lower(), scope),
                                          {"tag": tag, "source": scope, "sources": [], "keys": set()})
                if scope not in entry["sources"]:
                    entry["sources"].append(scope)
                entry["keys"].add(key)
    if merged:
        tabletop.roll_up(counts, family_hub.tag_parents())
    result = sorted(({"tag": entry["tag"], "source": entry["source"], "sources": entry["sources"], "count": len(entry["keys"]),
                      "parent": entry.get("parent", "")}
                     for entry in counts.values()),
                    key=lambda entry: (-entry["count"], entry["tag"].lower()))
    return jsonify({"tags": result[:120]})


# ---- game books (tabletop.py): what each profile hides from its own feed, and nesting tags

@bp.get("/me/hidden")
@profile_only
def my_hidden(who):
    """The tags and roles this profile hides from its library feed, and the roles that can be hidden."""
    return jsonify({"hidden": tabletop.hidden(hub(), who["id"]), "roles": kinds.ROLES})


@bp.put("/me/hidden")
@profile_only
def my_hidden_set(who):
    """Replace the list. Hidden books still turn up in searches and in the Tabletop view."""
    names = (request.get_json(silent=True) or {}).get("hidden")
    if not isinstance(names, list):
        return fail("Send the list of tags to hide.")
    return jsonify({"ok": True, "hidden": tabletop.set_hidden(hub(), who["id"], names)})


@bp.get("/admin/tag-parents")
@admin_only
def admin_tag_parents(who):
    parents = hub().tag_parents()
    return jsonify({"parents": [{"tag": tag, "parent": parent} for tag, parent in sorted(parents.items(), key=lambda pair: (pair[1].lower(), pair[0].lower()))]})


@bp.post("/admin/tag-parents")
@admin_only
def admin_tag_parent_set(who):
    """Nest a tag under another; an empty parent un-nests it."""
    data = request.get_json(silent=True) or {}
    tag, parent = clean(data.get("tag")), clean(data.get("parent"))
    if not tag:
        return fail("Choose the tag to nest.")
    family_hub = hub()
    if not family_hub.set_tag_parent(tag, parent):
        return fail("\"%s\" can't go under \"%s\": that would put it inside itself." % (tag, parent))
    family_hub.touch()
    return jsonify({"ok": True})


# ----------------------------------------------------------------- stats

@bp.get("/stats/me")
@profile_only
def stats_me(who):
    return jsonify(hub().personal_stats(who["id"], request.args.get("kind"), request.args.get("value")))


@bp.get("/stats/admin")
@admin_only
def stats_admin(who):
    family_hub = hub()
    data = family_hub.admin_stats()
    data["attention"] = family_hub.intake.attention_items(limit=50)
    kind_of = family_hub.kind_index()
    library = family_hub.library.books()
    data["kinds"] = [[kind, sum(1 for book in library if kind_of.get(book["key"], kinds.UNSORTED) == kind)] for kind in kinds.KINDS]
    machine = family_hub.machine_tags()
    data["tags"] = {
        "lookups": family_hub.catalogues.progress(),
        "community": sum(1 for tag in machine["tags"] if tag["sources"] != ["Book file"] and tag["becomes"] is None),
        "file": sum(1 for tag in machine["tags"] if "Book file" in tag["sources"] and tag["becomes"] is None),
        "removed": len(machine["removed"]), "hidden": sum(1 for tag in machine["tags"] if tag["becomes"] is not None),
    }
    data["elsewhere"] = len(goodreads.elsewhere(family_hub))
    sets = family_hub.copy_sets()
    data["copies"] = {
        "sets": sum(1 for group in sets if group["together"] or group["partly"]),
        "hidden": len(family_hub.library.all_books()) - len(library),
        "apart": sum(1 for group in sets if not group["together"]),
        "spare": sum(group["spare"] for group in sets),
    }
    data["services"] = {
        "library": family_hub.library.available,
        "search": integrations.prowlarr_status(family_hub.config),
        "series": family_hub.series.status(),
        "second_copy": secondcopy.status(family_hub),
        "downloads_folder": bool(family_hub.config.downloads_dir and family_hub.config.downloads_dir.is_dir()),
        "ingest_folder": bool(family_hub.config.ingest_dir and family_hub.config.ingest_dir.is_dir()),
        "ingest_waiting": family_hub.intake.waiting(family_hub.config.ingest_dir),
        "ingest_stuck": family_hub.ingest_stuck,
        "inbox_waiting": family_hub.intake.waiting(family_hub.config.inbox_dir),
        "remote_guest_blocked": family_hub.config.guest_lan_only,
    }
    return jsonify(data)


@bp.get("/similar/<int:book_id>")
@signed_in
def similar(who, book_id):
    book = _library_book(book_id)
    if not book:
        return fail("That book is not in the library.", 404)
    pid = pid_of(who)
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
    family_hub = hub()
    mine = recommend.taste(family_hub, who["id"])
    data = recommend.listing(family_hub, who["id"])
    data.update(ready=mine["ready"], have=mine["have"], need=mine["need"], on=family_hub.config.tag_lookups == "on")
    return jsonify(data)


@bp.post("/recommendations/refresh")
@profile_only
def recommendations_refresh(who):
    family_hub = hub()
    try:
        count = recommend.refresh(family_hub, who["id"])
    except catalogues.LookupError_ as exc:
        return fail("Open Library could not be reached: %s" % exc, 502)
    if count is None and family_hub.config.tag_lookups != "on":
        return fail("Looking things up outside is switched off. An admin can turn it on under Admin, Hub, Connections.")
    return recommendations.__wrapped__(who)  # __wrapped__ = the view without its sign-in check (we've done that already)


@bp.get("/recommendations/manga")
@profile_only
def recommendations_manga(who):
    """Manga and manhwa to try next, from AniList readers' recommendations for the series this person enjoyed.
    ?look=1 asks AniList about a few more liked series now; otherwise only answers already kept are used."""
    from . import anilist
    family_hub = hub()
    data = anilist.suggestions(family_hub, who["id"], look=request.args.get("look") == "1")
    data.update(on=family_hub.config.tag_lookups == "on", series=bool(family_hub.series.configured and can_series(who)))
    return jsonify(data)


@bp.post("/recommendations/manga/dismiss")
@profile_only
def recommendations_manga_dismiss(who):
    from . import anilist
    try:
        anilist.dismiss(hub(), who["id"], int((request.get_json(silent=True) or {}).get("id")))
    except (TypeError, ValueError):
        return fail("Say which one.")
    return jsonify({"ok": True})


@bp.get("/me/authors")
@profile_only
def my_authors(who):
    """The authors this profile likes and does not, for suggestions; the library's authors to pick from."""
    names = sorted({author.strip() for book in hub().library.books() for author in (book["author"] or "").split("&")
                    if author.strip() and catalogues.author_known(author.strip())}, key=str.lower)
    return jsonify(dict(recommend.author_prefs(hub(), who["id"]), known=names))


@bp.post("/me/authors")
@profile_only
def my_authors_set(who):
    """Like an author, not like one, or take them off both lists: {"author", "how": "like" | "dislike" | "none"}."""
    data = request.get_json(silent=True) or {}
    if data.get("how") not in ("like", "dislike", "none") or not isinstance(data.get("author"), str) or not data["author"].strip():
        return fail("Say which author, and like or not.")
    return jsonify(recommend.set_author_pref(hub(), who["id"], data["author"], data["how"]))


@bp.post("/recommendations/dismiss")
@profile_only
def recommendations_dismiss(who):
    key = (request.get_json(silent=True) or {}).get("key")
    recommend.dismiss(hub(), who["id"], key if isinstance(key, str) else "")
    return jsonify({"ok": True})


# -------------------------------------------------------------- discover

def can_request(who):
    return who not in (None, "guest") and (who["role"] == "admin" or who["can_request"])


def query_variants(query):
    """The spellings to ask the sites for. They match whole words exactly, so "Saviors" misses "Savior's" and "Cafe"
    may miss "Café". Asked for as typed; with accents and apostrophes taken out; and, for a long title, without its
    first word or two (which is where possessives and "The" usually are)."""
    import unicodedata
    # NFKD splits "é" into "e" + an accent mark, then the ascii encode quietly drops the mark. café -> cafe
    folded = unicodedata.normalize("NFKD", query).encode("ascii", "ignore").decode()
    folded = re.sub(r"['’`]", "", folded)
    folded = re.sub(r"[^A-Za-z0-9]+", " ", folded).strip()
    words = folded.split()
    variants = [query]
    for variant in (folded, " ".join(words[1:]) if len(words) >= 4 else "", " ".join(words[2:]) if len(words) >= 5 else ""):
        if variant and variant.lower() not in [known.lower() for known in variants]:
            variants.append(variant)
    return variants


def search_variants(config, query):
    """Ask for every spelling at once and put the answers together, the closest matches to what was typed first."""
    from concurrent.futures import ThreadPoolExecutor
    variants = query_variants(query)
    with ThreadPoolExecutor(max_workers=len(variants)) as pool:
        answers = list(pool.map(lambda variant: _try_search(config, variant), variants))
    if all(isinstance(answer, integrations.IntegrationError) for answer in answers):
        raise answers[0]  # only give up if EVERY spelling failed; one working is good enough
    # every word typed, apostrophes out. (variants[1] isn't always the folded one: it can be the shortened one)
    typed = set(db.slug(re.sub(r"['’`]", "", query)).split("-"))
    merged, seen = [], set()
    for answer in answers:
        for result in answer if isinstance(answer, list) else []:
            key = result.get("guid") or result["title"]
            if key not in seen:
                seen.add(key)
                merged.append(result)
    score = lambda result: len(typed & set(db.slug(result["title"]).split("-")))  # noqa: E731  (words in common)
    merged.sort(key=lambda result: (-score(result), result["format"] != "EPUB", -(result["seeders"] or 0)))
    return merged


def _try_search(config, query):
    """The answer, or the error itself (handed back, not raised), so one failing spelling can't sink the others."""
    try:
        return integrations.prowlarr_search(config, query)
    except integrations.IntegrationError as exc:
        return exc


@bp.get("/discover/search")
@profile_only
def discover_search(who):
    if not can_request(who):
        return fail("This profile cannot search for new books.", 403)
    query = (request.args.get("q") or "").strip()
    if len(query) < 2:
        return fail("Type a title or author.")
    family_hub = hub()
    try:
        results = search_variants(family_hub.config, query)
    except integrations.IntegrationError as exc:
        return fail(str(exc), 502)
    # longest first, so "Dune Messiah" wins over "Dune" when both are on the shelf
    titles = sorted(((db.slug(book["title"]), book["id"]) for book in family_hub.library.books()), key=lambda pair: -len(pair[0]))
    for result in results:
        release = "-%s-" % db.slug(result["title"])
        # "already got it?" = one of our titles (4+ letters, so "it" doesn't match everything) inside the release name,
        # as whole words: "Emma" isn't in "Emmanuel"
        result["library_id"] = next((book_id for title, book_id in titles if len(title) > 3 and "-%s-" % title in release), None)
    return jsonify({"results": results})


@bp.post("/discover/grab")
@profile_only
def discover_grab(who):
    if not can_request(who):
        return fail("This profile cannot download new books.", 403)
    data = request.get_json(silent=True) or {}
    if not data.get("guid") or data.get("indexer_id") is None:
        return fail("Pick a result first.")
    family_hub = hub()
    try:
        integrations.prowlarr_grab(family_hub.config, data["guid"], data["indexer_id"])
    except integrations.IntegrationError as exc:
        return fail(str(exc), 502)
    con = family_hub.con()
    con.execute("INSERT INTO requests(profile_id, title, indexer, guid, status, created) VALUES(?,?,?,?,?,?)",
                (who["id"], str(data.get("title") or "")[:300], str(data.get("indexer") or "")[:80],
                 str(data["guid"])[:500], "sent", db.now()))
    con.commit()
    return jsonify({"ok": True})


@bp.get("/discover/queue")
@profile_only
def discover_queue(who):
    family_hub = hub()
    names = family_hub.profile_names()
    con = family_hub.con()
    asked = [{"title": row["title"], "indexer": row["indexer"], "by": names.get(row["profile_id"], "?"),
              "created": row["created"], "swapped": row["status"] == "swapped"} for row in
             con.execute("SELECT * FROM requests ORDER BY id DESC LIMIT 15")]
    imported = [{"name": row["path"].replace("\\", "/").split("/")[-1], "at": row["at"]} for row in
                con.execute("SELECT * FROM imported_files ORDER BY at DESC LIMIT 10")]
    client, client_error = None, None
    try:
        client = integrations.qbit_queue(family_hub.config)
    except integrations.IntegrationError as exc:
        client_error = str(exc)
    return jsonify({"requests": asked, "imported": imported, "client": client, "client_error": client_error})


def _suggestions_key(who):
    return "new:%d" % who["id"]


def _forget_suggestions(con, who):
    """Discover's "new for you" is cached; following something new means working it out again."""
    con.execute("DELETE FROM kv WHERE key=?", (_suggestions_key(who),))


@bp.get("/watch")
@profile_only
def watch_list(who):
    rows = hub().con().execute("SELECT * FROM watch WHERE profile_id=? ORDER BY kind, value", (who["id"],))
    return jsonify({"watch": [{"id": row["id"], "kind": row["kind"], "value": row["value"]} for row in rows]})


@bp.post("/watch")
@profile_only
def watch_add(who):
    data = request.get_json(silent=True) or {}
    kind = "author" if data.get("kind") == "author" else "tag"
    value = clean(data.get("value"))
    if not value:
        return fail("Type an author or a tag to follow.")
    con = hub().con()
    con.execute("INSERT OR IGNORE INTO watch(profile_id, kind, value, created) VALUES(?,?,?,?)",
                (who["id"], kind, value, db.now()))
    _forget_suggestions(con, who)
    con.commit()
    return jsonify({"ok": True})


@bp.delete("/watch/<int:watch_id>")
@profile_only
def watch_remove(who, watch_id):
    con = hub().con()
    con.execute("DELETE FROM watch WHERE id=? AND profile_id=?", (watch_id, who["id"]))
    _forget_suggestions(con, who)
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
    # "tag" rows are Discover's, not the watchlist's: shown plain, never checked
    items = [watchlist.describe(row) if row["kind"] in watchlist.KINDS else
             {"id": row["id"], "kind": row["kind"], "value": row["value"], "found": [], "new": 0}
             for row in rows]
    return jsonify({"items": items, "news": sum(item["new"] for item in items), "can_request_media": can_request_media(who),
                    "films": hub().media.configured("film"), "shows": hub().media.configured("show")})


@bp.post("/watchlist")
@profile_only
def watchlist_add(who):
    data = request.get_json(silent=True) or {}
    kind, value = data.get("kind"), clean(data.get("value"))
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
    _forget_suggestions(con, who)
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
    for row in con.execute("SELECT id, found FROM watch WHERE profile_id=?", (who["id"],)).fetchall():
        everything_found = [item["key"] for item in json.loads(row["found"] or "[]")]
        con.execute("UPDATE watch SET seen=? WHERE id=?", (json.dumps(everything_found), row["id"]))
    con.commit()
    return jsonify({"ok": True})


@bp.get("/discover/new")
@profile_only
def discover_new(who):
    """Suggestions from the watchlist. Cached for 12 hours per profile."""
    family_hub = hub()
    con = family_hub.con()
    cache_key = _suggestions_key(who)
    cached = db.kv_get(con, cache_key)
    if cached and not request.args.get("refresh"):
        data = json.loads(cached)
        if time.time() - data.get("at", 0) < 12 * 3600:
            return jsonify(data)
    items = []
    index = family_hub.tag_index(who["id"])
    keys = family_hub.library.keys()
    meta = {row["book_key"]: row for row in con.execute("SELECT * FROM book_meta")}
    finished = {row["book_key"] for row in family_hub.reading_rows(who["id"]) if row["status"] == "finished"}
    for watched in con.execute("SELECT * FROM watch WHERE profile_id=?", (who["id"],)).fetchall():
        if watched["kind"] == "tag":
            low = watched["value"].lower()
            for key, groups in index.items():
                if key in finished or not any(tag.lower() == low for group in groups.values() for tag in group):
                    continue
                book_meta, owned = meta.get(key), keys.get(key)
                if not owned and not book_meta:
                    continue
                known = owned or book_meta
                items.append({"reason": "Tagged " + watched["value"], "title": known["title"], "author": known["author"],
                              "in_library": bool(owned), "id": owned["id"] if owned else None})
        elif watched["kind"] == "author" and can_request(who) and family_hub.config.discover_configured:  # films and shows are not books
            owned_titles = [db.slug(book["title"]) for book in family_hub.library.books() if watched["value"].lower() in book["author"].lower()]
            try:
                results = integrations.prowlarr_search(family_hub.config, watched["value"], limit=40)
            except integrations.IntegrationError:
                results = []
            fresh = [result for result in results
                     if not any(len(title) > 3 and title in db.slug(result["title"]) for title in owned_titles)]
            for result in fresh[:5]:  # five per author is plenty, or one prolific author drowns out the rest
                items.append({"reason": "By " + watched["value"], "title": result["title"], "author": watched["value"],
                              "in_library": False, "id": None, "release": result})
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
    except (AttributeError, KeyError):  # right label, wrong insides (a hand-edited file, say)
        return fail("That file could not be read.")
    return jsonify({"books": count})


@bp.post("/admin/backup")
@admin_only
def admin_backup(who):
    path = hub().backup_now()
    return jsonify({"file": path.name, "at": db.kv_get(hub().con(), "last_backup")})


@bp.post("/admin/rescan")
@admin_only
def admin_rescan(who):
    family_hub = hub()
    family_hub.library.refresh()
    tags_added = family_hub.load_tagpacks()
    kosync.index_library(family_hub)
    family_hub.fill_page_counts(limit=500)
    tidied = family_hub.intake.tidy_inbox()
    copied = family_hub.import_downloads()
    family_hub.sort_new_books()
    return jsonify({"books": len(family_hub.library.books()), "pack_tags": tags_added,
                    "imported": tidied["delivered"] + copied})


@bp.get("/admin/copies")
@admin_only
def admin_copies(who):
    family_hub = hub()
    return jsonify({"sets": family_hub.copy_sets(), "entries": len(family_hub.library.all_books()), "books": len(family_hub.library.books())})


@bp.post("/admin/copies")
@admin_only
def admin_copies_choose(who):
    """Show library entries that share a name as one book, or keep them apart."""
    data = request.get_json(silent=True) or {}
    family_hub = hub()
    key = data.get("key")
    if not any(group["key"] == key for group in family_hub.library.sets()):
        return fail("Those entries are no longer in the library.", 404)
    choice = data.get("choice")
    family_hub.set_copy_choice(key, choice if choice in (calibre.TOGETHER, calibre.APART) else None)
    return jsonify({"ok": True})


@bp.get("/admin/tags")
@admin_only
def admin_tags(who):
    family_hub = hub()
    return jsonify(dict(family_hub.machine_tags(), lookups=family_hub.catalogues.progress(),
                        merged=[merge["tag"] for merge in tagmerge.merges(family_hub.con())]))


@bp.post("/admin/tags/rule")
@admin_only
def admin_tag_rule(who):
    """Hide a machine tag everywhere, rename it, or put it back."""
    data = request.get_json(silent=True) or {}
    # three-way switch: None = put it back, "" = hide it, anything else = rename it to that
    becomes = None if data.get("restore") else ("" if data.get("hide") else data.get("becomes"))
    if becomes is not None and not data.get("hide") and not clean(becomes):
        return fail("Type the new name for the tag.")
    if not hub().set_tag_rule(data.get("tag"), becomes):
        return fail("Choose a tag first.")
    return jsonify({"ok": True})


# ---- merging tags (tagmerge.py): one spelling into another, across family, book file and community tags

def _merge_request():
    """(names to merge away, name kept) from the page."""
    data = request.get_json(silent=True) or {}
    names = data.get("tags") if isinstance(data.get("tags"), list) else []
    return [clean(name) for name in names if clean(name)], clean(data.get("keep"))


@bp.get("/admin/tags/merges")
@admin_only
def admin_tag_merges(who):
    """Merges made so far, suggested ones, and every tag to pick from."""
    family_hub = hub()
    used = tagmerge.usage(family_hub)
    con = family_hub.con()
    return jsonify({"merges": tagmerge.merges(con), "suggestions": tagmerge.suggestions(con, used), "tags": tagmerge.all_tags(used)})


@bp.post("/admin/tags/merge/preview")
@admin_only
def admin_tag_merge_preview(who):
    names, keep = _merge_request()
    if not keep:
        return fail("Choose the name to keep.")
    return jsonify(tagmerge.preview(tagmerge.usage(hub()), names, keep))


@bp.post("/admin/tags/merge")
@admin_only
def admin_tag_merge(who):
    names, keep = _merge_request()
    if not keep:
        return fail("Choose the name to keep.")
    family_hub = hub()
    changed = tagmerge.merge(family_hub.con(), names, keep)
    if changed is None:
        return fail("Choose at least one other tag to merge into \"%s\"." % keep)
    family_hub.touch()
    return jsonify({"ok": True, "family": changed})


@bp.post("/admin/tags/merge/undo")
@admin_only
def admin_tag_merge_undo(who):
    family_hub = hub()
    restored = tagmerge.undo(family_hub.con(), clean((request.get_json(silent=True) or {}).get("tag")))
    if restored is None:
        return fail("That merge is no longer there.", 404)
    family_hub.touch()
    return jsonify({"ok": True, "restored": restored})


@bp.post("/admin/tags/merge/dismiss")
@admin_only
def admin_tag_merge_dismiss(who):
    """Don't suggest these tags as one again. The page sends every name in the suggestion."""
    names, _keep = _merge_request()
    if len(names) < 2:
        return fail("Choose the suggestion to dismiss.")
    tagmerge.dismiss(hub().con(), names)
    return jsonify({"ok": True})


@bp.post("/admin/tags/lookup")
@admin_only
def admin_tags_lookup(who):
    family_hub = hub()
    if not catalogues.active(family_hub.config):
        return fail("Looking up tags is switched off. Turn it on under Connections first.")
    data = request.get_json(silent=True) or {}
    if data.get("again"):  # forget the books the catalogues did not know, and ask about them again
        family_hub.con().execute("DELETE FROM tag_lookups WHERE status!='found'")
        family_hub.con().commit()
    started = family_hub.catalogues.start(current_app._get_current_object())
    return jsonify({"started": started, "lookups": family_hub.catalogues.progress()})


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
    ("cwa", "Calibre-Web", "The Calibre library (Calibre-Web Automated)", "http://calibre-web-automated:8083"),
    ("suwayomi", "Suwayomi", "Manga by chapter", "http://suwayomi:4567"),
    ("komga", "Komga", "Comics and manga reader", "http://komga:25600"),
]


@bp.get("/admin/services")
@admin_only
def admin_services(who):
    """Each program's port (the page builds the link from the address it was opened with) and whether it answers."""
    import requests as http
    from concurrent.futures import ThreadPoolExecutor
    config = hub().config
    inside = {"radarr": config.radarr_url, "sonarr": config.sonarr_url, "prowlarr": config.prowlarr_url, "qbittorrent": config.qbit_url,
              "bazarr": config.bazarr_url, "jellyfin": config.jellyfin_url, "cwa": config.cwa_api_url}
    links = {"jellyfin": config.jellyfin_link, "cwa": config.cwa_url}

    def check(service):
        service_id, name, what, default = service
        try:
            # ANY answer counts as running, even a 401. we only want to know if something's home
            http.get(inside.get(service_id) or default, timeout=2, allow_redirects=False)
            running = True
        except http.RequestException:
            running = False
        return {"id": service_id, "name": name, "what": what, "port": config.service_ports[service_id],
                "link": links.get(service_id, ""), "running": running}

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
    values = (request.get_json(silent=True) or {}).get("values") or {}
    if not isinstance(values, dict):
        return fail("That request was not understood.")
    try:
        settings.save(hub(), values)
    except ValueError as exc:
        return fail(str(exc))
    return jsonify({"groups": settings.describe(hub())})


@bp.get("/admin/look")
@admin_only
def admin_look(who):
    family_hub = hub()
    return jsonify({"designs": looks.catalogue(), "look": looks.current(family_hub.con())})


@bp.post("/admin/look")
@admin_only
def admin_look_save(who):
    family_hub = hub()
    problem = looks.save(family_hub.con(), request.get_json(silent=True) or {})
    if problem:
        return fail(problem)
    return jsonify({"look": looks.current(family_hub.con())})


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
    family_hub = hub()
    moved = family_hub.intake.retry_attention()
    result = family_hub.intake.tidy_inbox(min_age=0)
    return jsonify({"retried": moved, "delivered": len(result["delivered"]), "attention": result["attention"]})


# ---------------------------------------------------------------- series
# Manga, manhwa and other series read chapter by chapter, kept by the series server.

@bp.get("/series")
@series_only
def series_shelf(who):
    family_hub = hub()
    if not family_hub.series.configured:
        return jsonify({"configured": False, "series": [], "queued": 0})
    return jsonify(dict(family_hub.series.shelf(), configured=True))


@bp.get("/series/search")
@series_only
def series_search(who):
    if not can_follow(who):
        return fail("This profile can read series but not follow new ones.", 403)
    query = (request.args.get("q") or "").strip()
    if len(query) < 2:
        return fail("Type at least two letters.")
    return jsonify(hub().series.search(query))


@bp.post("/series/check")
@series_only
def series_check(who):
    hub().series.check_now()
    return jsonify({"ok": True})


@bp.post("/series/chapters/read")
@series_only
def series_mark(who):
    data = request.get_json(silent=True) or {}
    ids = id_list(data.get("ids"))
    if not ids:
        return fail("No chapters given.")
    hub().series.mark(ids, data.get("read", True))
    return jsonify({"ok": True})


@bp.get("/series/<int:series_id>")
@series_only
def series_detail(who, series_id):
    from . import chapterfile
    info = hub().series.detail(series_id, refresh=request.args.get("refresh") == "1")
    detail = dict(info, mine=series_id in series.my_books(hub(), who["id"]), files=chapterfile.files(hub(), info))
    detail.pop("every_chapter", None)  # the twice-listed copies are not shown
    return jsonify(detail)


@bp.get("/series/<int:series_id>/file/<int:part>")
@series_only
def series_part_file(who, series_id, part):
    """One part of a series by chapter as a comic file, every chapter in it, once the server has made it."""
    from . import chapterfile
    from .devices import safe_filename
    path, title = chapterfile.ready_file(hub(), hub().series.detail(series_id), part, older=True)
    if not path:
        return fail("That part is still being put together on the server. Try again in a few minutes.", 404)
    return send_file(path, as_attachment=True, download_name=safe_filename(title, "", "cbz"), mimetype="application/vnd.comicbook+zip")


@bp.post("/series/<int:series_id>/follow")
@series_only
def series_follow(who, series_id):
    family_hub = hub()
    if not can_follow(who):
        return fail("This profile can read series but not follow or stop following them.", 403)
    if (request.get_json(silent=True) or {}).get("on", True):
        return jsonify({"ok": True, "queued": family_hub.series.follow(series_id)})
    family_hub.series.unfollow(series_id)
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
        # ascii only: a fancy unicode filename in a header is a recipe for a 500 somewhere down the line
        # and no quotes or backslashes left inside, or our own header comes out broken
        given = re.sub(r'["\\]', "", given.split("filename=")[-1].strip('"; '))
        name = given.encode("ascii", "ignore").decode() or name
    return _passed_on(upstream, name)


@bp.post("/admin/second-copy")
@admin_only
def admin_second_copy(who):
    """Start the copy to the second disk now."""
    family_hub = hub()
    if secondcopy.folder(family_hub.config) is None:
        return fail("No second disk is set up. Guide 6 has the steps.")
    if secondcopy.status(family_hub)["running"]:
        return fail("A copy is already running.", 409)
    secondcopy.start(family_hub, current_app._get_current_object())
    return jsonify({"ok": True})


# ------------------------------------------------------- spare copies
# The one place Family Hub changes the library, by asking the library manager. See duplicates.py.

@bp.get("/admin/duplicates")
@admin_only
def admin_duplicates(who):
    family_hub = hub()
    plans = family_hub.duplicates.plans()
    return jsonify({
        "blocked": family_hub.duplicates.ready(), "job": family_hub.duplicates.job, "sets": plans, "log": family_hub.duplicates.log(),
        "counts": {cls: sum(1 for plan in plans if plan["cls"] == cls) for cls in duplicates.AUTOMATIC + (duplicates.REVIEW,)},
        "set_aside": "needs-attention/" + duplicates.SET_ASIDE,
    })


@bp.post("/admin/duplicates")
@admin_only
def admin_duplicates_apply(who):
    """Carry out one set, every set of one sort, or a person's choice on a set that needed review."""
    family_hub = hub()
    blocked = family_hub.duplicates.ready()
    if blocked:
        return fail(blocked)
    data = request.get_json(silent=True) or {}
    plans = family_hub.duplicates.plans()
    keep = data.get("keep")
    if data.get("key"):
        wanted = [plan for plan in plans if plan["key"] == data["key"]]
        if not wanted:
            return fail("Those entries are no longer in the library as a set.", 404)
        if keep is None and wanted[0]["cls"] not in duplicates.AUTOMATIC:
            return fail("This set needs a person to choose which entry to keep.")
        if keep is not None and keep not in [book["id"] for book in wanted[0]["books"]]:
            return fail("That entry is not part of this set.")
    elif data.get("cls") in duplicates.AUTOMATIC:
        wanted, keep = [plan for plan in plans if plan["cls"] == data["cls"]], None
    else:
        return fail("Say which set, or which sort of set.")
    if not wanted:
        return fail("There is nothing of that sort to do.")
    if not family_hub.duplicates.start(current_app._get_current_object(), wanted, keep):
        return fail("A batch is already running. Wait for it to finish.", 409)
    return jsonify({"ok": True, "started": len(wanted)})


# The same, one set at a time: the copies side by side, keep one and put its details right, or tell them apart.

@bp.get("/admin/duplicates/queue")
@admin_only
def admin_duplicates_queue(who):
    return jsonify(hub().duplicates.queue(request.args.get("key")))


@bp.get("/admin/duplicates/cover/<int:book_id>")
@admin_only
def admin_duplicates_cover(who, book_id):
    """The library's own cover of one entry, not Family Hub's, so two copies show their own."""
    book = _library_book(book_id)
    path = hub().library.cover_path(book) if book else None
    if not path:
        return "", 404
    return send_file(path, mimetype="image/jpeg", max_age=3600)


@bp.post("/admin/duplicates/queue")
@admin_only
def admin_duplicates_queue_answer(who):
    """{"key", "action": "keep", "keep": id, "fields": {...}} or {"key", "action": "apart", "names": {id: {...}}}."""
    data = request.get_json(silent=True) or {}
    family_hub = hub()
    try:
        if data.get("action") == "keep":
            done = family_hub.duplicates.keep(data.get("key"), data.get("keep"), data.get("fields"))
            words = "Kept. The other copy was deleted." if done["cls"] == duplicates.IDENTICAL else "Kept. The other copy is set aside in needs-attention."
            return jsonify(dict(done, ok=True, message=words))
        if data.get("action") == "apart":
            done = family_hub.duplicates.tell_apart(data.get("key"), data.get("names"))
            return jsonify(dict(done, ok=True, message="Saved as separate books."))
    except duplicates.Busy as exc:
        return fail(str(exc), 409)
    except (ValueError, OSError, sqlite3.Error, librarymanager.ManagerError) as exc:
        return fail(str(exc))
    return fail("Say whether to keep a copy or tell them apart.")


# ------------------------------------------------------- book details
# Books whose title or author is a file name or a user name. Changed by the library manager. See bookfix.py.

@bp.get("/admin/book-details")
@admin_only
def admin_book_details(who):
    family_hub = hub()
    return jsonify({"blocked": family_hub.bookfix.ready(), "books": family_hub.bookfix.needing()})


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
    family_hub = hub()
    if data.get("leave") is True:
        family_hub.bookfix.leave(book_id)
        return jsonify({"ok": True})
    authors = data.get("authors")
    if isinstance(authors, str):
        authors = [name for name in re.split(r"\s*(?:&|;)\s*", authors) if name]
    texts = [data.get(field, "") for field in ("title", "synopsis", "publisher", "cover")]
    if not isinstance(authors, list) or not all(isinstance(name, str) for name in authors) or not all(isinstance(text, str) for text in texts):
        return fail("Give a title and authors as text.")
    try:
        done = family_hub.bookfix.apply(book_id, data.get("title", ""), authors, data.get("synopsis", ""), data.get("publisher", ""),
                               data.get("cover", ""), who["id"])
    except bookfix.FixError as exc:
        return fail(str(exc))
    return jsonify(dict(done, message="Now %s by %s." % (done["title"], done["author"])))


# ------------------------------------------------------- a look inside
# The first pages of one library entry (not its copies'), for the details editor and the copies queue. See peek.py.

def _peek_book(book_id):
    book = hub().library.get(book_id)
    if not book:
        raise peek.PeekError("That book is not in the library any more.")
    return book


@bp.get("/admin/books/<int:book_id>/peek")
@admin_only
def admin_peek(who, book_id):
    try:
        return jsonify(peek.describe(hub().library, _peek_book(book_id)))
    except peek.PeekError as exc:
        return fail(str(exc), 404)


@bp.get("/admin/books/<int:book_id>/peek.pdf")
@admin_only
def admin_peek_pdf(who, book_id):
    """The first ?pages= pages (3 unless asked; at most 30) as a PDF the browser shows in place."""
    try:
        book = _peek_book(book_id)
        path = peek.file_of(hub().library, book, "PDF")
        if not path:
            return fail("This book has no PDF.", 404)
        data, total = peek.pdf_pages(path, number(request.args.get("pages"), int) or peek.PAGES)
    except peek.PeekError as exc:
        return fail(str(exc), 404)
    response = send_file(io.BytesIO(data), mimetype="application/pdf", max_age=0)
    response.headers["Content-Disposition"] = "inline"
    response.headers["X-Pages"] = str(total)
    return response


@bp.get("/admin/books/<int:book_id>/peek/page/<int:number_>.png")
@admin_only
def admin_peek_page(who, book_id, number_):
    """One PDF page as a picture, for marking where the title and author are (peek.py)."""
    try:
        path = peek.file_of(hub().library, _peek_book(book_id), "PDF")
        if not path:
            return fail("This book has no PDF.", 404)
        data, _ = peek.pdf_page_picture(path, number_)
    except peek.PeekError as exc:
        return fail(str(exc), 404)
    return send_file(io.BytesIO(data), mimetype="image/png", max_age=3600)


@bp.post("/admin/books/<int:book_id>/peek/box")
@admin_only
def admin_peek_box(who, book_id):
    """The text inside a box drawn on a page: {page, box: [left, top, right, bottom] as fractions}."""
    body = request.get_json(silent=True) or {}
    try:
        path = peek.file_of(hub().library, _peek_book(book_id), "PDF")
        if not path:
            return fail("This book has no PDF.", 404)
        page = number(body.get("page"), int)
        box = peek.clean_box(body.get("box"))
        if page is None or not box:
            return fail("Draw a box on a page first.")
        text = peek.pdf_box_text(path, page, box)
    except peek.PeekError as exc:
        return fail(str(exc), 404)
    return jsonify({"text": text, "title": peek.as_title(text), "authors": peek.as_authors(text), "page": page, "box": box})


@bp.get("/admin/books/<int:book_id>/peek/picture/<int:number_>")
@admin_only
def admin_peek_picture(who, book_id, number_):
    try:
        book = _peek_book(book_id)
        fmt = peek.pick(peek.own(book))
        path = peek.file_of(hub().library, book, fmt)
        if not path:
            return fail("There is no file to look inside.", 404)
        data, mime = peek.comic_picture(path, number_)
    except peek.PeekError as exc:
        return fail(str(exc), 404)
    return send_file(io.BytesIO(data), mimetype=mime, max_age=3600)


@bp.get("/admin/review/book/<int:book_id>")
@admin_only
def admin_review_book(who, book_id):
    """One library entry as a queue item, whatever the sorter thought of it: "Edit details" from anywhere."""
    try:
        return jsonify(dict(hub().review.queue("none", 0), item=hub().review.item_for(book_id)))
    except review.ReviewError as exc:
        return fail(str(exc), 404)


# ------------------------------------------------------- the review queue
# The sorter's suggestions, waiting for an admin. Confirming writes through the library manager. See review.py.

@bp.get("/admin/review")
@admin_only
def admin_review(who):
    """One tab of the queue: ?tab=tabletop|books|graphic and ?limit= (how many waiting books to send)."""
    limit = number(request.args.get("limit"), int) or 40
    return jsonify(hub().review.queue(request.args.get("tab"), max(1, min(limit, 2000))))


@bp.post("/admin/review/ready/<int:rule_id>")
@admin_only
def admin_review_write_ready(who, rule_id):
    """Write every book a learned rule sorted to the library, in the background, after one backup."""
    family_hub = hub()
    try:
        if not family_hub.review.start_ready(current_app._get_current_object(), rule_id, who["id"]):
            return fail("A batch or a re-sort is already running. Wait for it to finish.", 409)
    except review.ReviewError as exc:
        return fail(str(exc))
    return jsonify({"ok": True, "job": family_hub.review.job})


@bp.get("/admin/review/job")
@admin_only
def admin_review_job(who):
    """How a batch or the full re-sort is getting on, without building the whole queue."""
    return jsonify(hub().review.job)


@bp.get("/admin/review/<int:book_id>/credits")
@admin_only
def admin_review_credits(who, book_id):
    try:
        return jsonify({"authors": hub().review.find_credits(book_id)})
    except review.ReviewError as exc:
        return fail(str(exc), 404)


@bp.post("/admin/review/<int:book_id>")
@admin_only
def admin_review_answer(who, book_id):
    """{"action": "confirm", "fields": {...}, "new_game": {...}}, {"action": "confirm_all", "batch": ..., "fields": ...}
    or {"action": "reject"}."""
    data = request.get_json(silent=True) or {}
    family_hub = hub()
    action = data.get("action")
    fields = data.get("fields") if isinstance(data.get("fields"), dict) else {}
    new_game = data.get("new_game") if isinstance(data.get("new_game"), dict) else None
    try:
        if action == "reject":
            family_hub.review.reject(book_id, who["id"])
            return jsonify({"ok": True, "message": "Left as it is. Family Hub won't suggest that again."})
        if action == "confirm":
            done = family_hub.review.confirm(book_id, fields, who["id"], new_game)
            if not done["done"]:
                return fail(" ".join(done["problems"]) or "Nothing was changed.")
            first = done["done"][0]
            return jsonify(dict(done, ok=True, message="Now %s by %s." % (first["title"], first["author"])))
        if action == "confirm_all":
            if not isinstance(data.get("batch"), str):
                return fail("Say which batch.")
            if not family_hub.review.start_batch(current_app._get_current_object(), data["batch"], book_id, fields, who["id"], new_game):
                return fail("A batch or a re-sort is already running. Wait for it to finish.", 409)
            return jsonify({"ok": True, "job": family_hub.review.job})
    except review.ReviewError as exc:
        return fail(str(exc))
    return fail("Say confirm, confirm_all or reject.")


@bp.post("/admin/review/resort")
@admin_only
def admin_review_resort(who):
    """Sort the whole library again in the background; the queue fills as it goes."""
    family_hub = hub()
    if not family_hub.review.start_resort(current_app._get_current_object()):
        return fail("A batch or a re-sort is already running. Wait for it to finish.", 409)
    return jsonify({"ok": True, "job": family_hub.review.job})


@bp.get("/admin/review/rules")
@admin_only
def admin_review_rules(who):
    return jsonify(hub().review.rules())


@bp.delete("/admin/review/rules/<int:rule_id>")
@admin_only
def admin_review_rule_delete(who, rule_id):
    if not hub().review.delete_rule(rule_id):
        return fail("That rule is gone already.", 404)
    return jsonify({"ok": True})


@bp.post("/admin/review/rules/merge")
@admin_only
def admin_review_rules_merge(who):
    ids = (request.get_json(silent=True) or {}).get("ids")
    if not isinstance(ids, list) or not all(isinstance(rule_id, int) for rule_id in ids):
        return fail("Say which rules to combine.")
    try:
        rule_id = hub().review.merge(ids, who["id"])
    except review.ReviewError as exc:
        return fail(str(exc))
    return jsonify({"ok": True, "rule": rule_id})


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
        kind, _, number = part.partition(":")
        if _kind(kind) and number.isdigit():
            wanted.append((kind, int(number)))
    books = [title for title in request.args.getlist("book") if title.strip()][:6]

    def build():
        items = []
        if books:
            # qBittorrent being away (the VPN restarting, say) must not stop the films and shows from updating.
            try:
                torrents = hub().media.torrents()
            except media.MediaError:
                torrents = None
            for title in books:
                status = hub().media.book_status(title, torrents or [])
                if torrents is None and not status["finished"]:
                    status["now"] = dict(status["now"], text="qBittorrent cannot be reached at the moment", state="bad")
                items.append(dict(status, kind="book", id=title))
        for kind, arr_id in wanted:
            try:
                activity = hub().media.activity(kind, arr_id)
                items.append({"kind": kind, "id": arr_id, "title": activity["title"], "now": activity["now"], "finished": activity["finished"],
                              "downloaded": activity.get("downloaded", False), "filing": activity.get("filing")})
            except media.MediaError as exc:
                if str(exc).startswith("404"):
                    items.append({"kind": kind, "id": arr_id, "gone": True})
                else:  # Radarr or Sonarr not answering: say so, and keep following it
                    items.append({"kind": kind, "id": arr_id, "title": "", "finished": False, "unreachable": True,
                                  "now": {"text": str(exc), "state": "bad", "progress": 0}})
        return jsonify({"items": items})
    return media_call(build)


@bp.get("/media/search")
@profile_only
@media_requests_only
def media_search(who):
    kind, query = _kind(request.args.get("kind")), (request.args.get("q") or "").strip()
    if not kind or len(query) < 2:
        return fail("Type at least two letters.")
    return media_call(lambda: jsonify({"results": hub().media.lookup(kind, query)}))


@bp.post("/media/request")
@profile_only
@media_requests_only
def media_request(who):
    data = request.get_json(silent=True) or {}
    kind = _kind(data.get("kind"))
    if not kind or not data.get("id"):
        return fail("Choose a film or show first.")
    seasons = data.get("seasons") if data.get("seasons") in ("all", "latest", "pick") else "all"
    try:
        external_id, picked = int(data["id"]), [int(season) for season in (data.get("picked") or [])]
    except (TypeError, ValueError):
        return fail("Choose a film or show first.")
    return media_call(lambda: jsonify(hub().media.request(kind, external_id, who, seasons, picked)))


@bp.get("/media/queue")
@profile_only
def media_queue(who):
    family_hub = hub()

    def build():
        queue = family_hub.media.queue()
        return jsonify(dict(queue, history=family_hub.media.history(), vpn=family_hub.media.vpn()))
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


@bp.post("/media/torrents/forget")
@admin_only
def media_torrents_forget(who):
    """Take downloads whose files are missing off qBittorrent's list. Their files, wherever they are, are not touched."""
    data = request.get_json(silent=True) or {}

    def go():
        missing = {torrent["hash"].lower() for torrent in hub().media.torrents() if torrent["missing"]}
        # only ones that really ARE missing: this endpoint can't be used to quietly drop healthy downloads
        hashes = data.get("hashes") if isinstance(data.get("hashes"), list) else []
        asked = [torrent_hash for torrent_hash in hashes
                 if isinstance(torrent_hash, str) and torrent_hash.lower() in missing]
        forgotten = hub().media.forget(asked)
        return jsonify({"forgotten": forgotten,
                        "message": "%d download%s taken off the list. No files were touched." % (forgotten, "" if forgotten == 1 else "s")})
    return media_call(go)


def _torrent(torrent_hash):
    """qBittorrent's entry for this download, or None. (Hashes come in either case.)"""
    return next((torrent for torrent in hub().media.torrents() if torrent["hash"].lower() == torrent_hash.lower()), None)


def _book_download(who, torrent_hash):
    """The unfinished book download with this hash, or an error to send back."""
    found = _torrent(torrent_hash)
    if not found or found["type"] != "Book" or found["done"]:
        return None, fail("That is not a book still downloading.", 404)
    if not can_request(who):
        return None, fail("This profile cannot download new books.", 403)
    return found, None


@bp.get("/media/torrents/<torrent_hash>/others")
@profile_only
def media_torrent_others(who, torrent_hash):
    """Other copies of a book download's book that someone is sharing, the best first."""
    from . import bookswap

    def go():
        found, err = _book_download(who, torrent_hash)
        if err:
            return err
        try:
            picks = bookswap.others(hub().config, found["name"])
        except integrations.IntegrationError as exc:
            return fail(str(exc), 502)
        return jsonify({"others": picks[:8], "looked_for": " ".join(bookswap.words(found["name"])[:8])})
    return media_call(go)


@bp.post("/media/torrents/<torrent_hash>/swap")
@profile_only
def media_torrent_swap(who, torrent_hash):
    """Download the chosen copy instead, and delete the stuck one with its unfinished files."""
    from . import bookswap
    data = request.get_json(silent=True) or {}
    if not data.get("guid") or data.get("indexer_id") is None:
        return fail("Pick a copy first.")

    def go():
        found, err = _book_download(who, torrent_hash)
        if err:
            return err
        pick = {"guid": data["guid"], "indexer_id": data["indexer_id"], "title": str(data.get("title") or "")[:300],
                "indexer": str(data.get("indexer") or ""), "seeders": data.get("seeders")}
        try:
            bookswap.swap(hub(), integrations.qbit_session(hub().config), found["hash"], found["name"], pick)
        except integrations.IntegrationError as exc:
            return fail(str(exc), 502)
        return jsonify({"ok": True})
    return media_call(go)


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
        found = _torrent(torrent_hash)
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
    query = (request.args.get("q") or "").strip()
    ago = request.args.get("ago", default=0, type=float)
    if not query:
        return fail("Say what is being searched for.")
    return media_call(lambda: jsonify(hub().media.search_progress(query, min(ago, 600))))


@bp.get("/media/latest")
@profile_only
def media_latest(who):
    return media_call(lambda: jsonify({"items": hub().media.latest()}))


@bp.post("/media/look-again/<kind>/<int:arr_id>")
@profile_only
@media_requests_only
def media_look_again(who, kind, arr_id):
    if not _kind(kind):
        return fail("Say films or shows.", 404)
    return media_call(lambda: jsonify({"message": hub().media.look_again(kind, arr_id)}))


@bp.post("/media/looking/<kind>/<int:arr_id>")
@profile_only
@media_requests_only
def media_looking(who, kind, arr_id):
    if not _kind(kind):
        return fail("Say films or shows.", 404)
    on = bool((request.get_json(silent=True) or {}).get("on"))
    return media_call(lambda: jsonify({"message": hub().media.set_looking(kind, arr_id, on)}))


@bp.get("/media/releases/<kind>/<int:arr_id>")
@profile_only
@media_requests_only
def media_releases(who, kind, arr_id):
    if not _kind(kind):
        return fail("Say films or shows.", 404)
    season = request.args.get("season", type=int)
    return media_call(lambda: jsonify({"releases": hub().media.releases(kind, arr_id, season)}))


@bp.post("/media/versions/<kind>/<int:arr_id>")
@profile_only
@media_requests_only
def media_versions_start(who, kind, arr_id):
    """Start looking for every version of a film or a season; the page asks for what has been found every moment."""
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
@media_requests_only
def media_versions_grab(who, job_id):
    """Download one of the versions found so far, before the full search has finished."""
    data = request.get_json(silent=True) or {}
    if not data.get("guid") or not isinstance(data["guid"], str):  # a list or object here would crash the lookup
        return fail("Choose a version first.")
    return media_call(lambda: jsonify(hub().media.grab_found(job_id, data["guid"], bool(data.get("force")))))


@bp.post("/media/grab")
@profile_only
@media_requests_only
def media_grab(who):
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
    return jsonify({"steps": [{"step": step, "ok": ok, "message": message} for step, ok, message in steps]})


POSTER_HOSTS = ("image.tmdb.org", "artworks.thetvdb.com", "www.thetvdb.com", "thetvdb.com", "assets.fanart.tv")


def _image(fetch):
    """Pass a picture from another server through to the browser, but only if it really is a (smallish) picture."""
    try:
        reply = fetch()
    except requests.RequestException:
        return fail("No picture.", 404)
    # _safe_type says octet-stream for anything that isn't a plain image. no sneaky HTML or SVG gets through
    if reply.status_code != 200 or _safe_type(reply.headers.get("Content-Type")) == "application/octet-stream":
        return fail("No picture.", 404)
    if len(reply.content) > 8 * 1024 * 1024:
        return fail("No picture.", 404)
    return send_file(io.BytesIO(reply.content), mimetype=_safe_type(reply.headers.get("Content-Type")), max_age=86400)


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
    # no redirects: otherwise an allowed host could bounce us anywhere on the internet (or the home network!)
    return _image(lambda: requests.get(url, timeout=15, allow_redirects=False))


@bp.get("/media/jellyfin-image/<item_id>")
@profile_only
def media_jellyfin_image(who, item_id):
    config = hub().config
    if not (config.jellyfin_url and config.jellyfin_api_key) or not item_id.isalnum():
        return fail("No picture.", 404)
    return _image(lambda: requests.get("%s/Items/%s/Images/Primary" % (config.jellyfin_url, item_id),
                                       params={"maxHeight": 450}, headers=media.jellyfin_auth(config.jellyfin_api_key), timeout=15))
