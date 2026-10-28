"""Talking to the other services: Prowlarr, qBittorrent and Hardcover.

Each of these is optional. Family Hub works as a library and tracker without
any of them configured.
"""
import requests

from . import db

TIMEOUT = 25
# A search asks every site and waits for the slowest; with many sites that is often more than half a minute.
SEARCH_TIMEOUT = 120
BOOK_CATEGORIES = [7000, 7020]  # Newznab/Torznab "Books" and "Books/EBook"


class IntegrationError(Exception):
    pass


# ---------------------------------------------------------------- Prowlarr

def prowlarr_search(config, query, limit=60):
    if not config.discover_configured:
        raise IntegrationError("Search is not set up yet. An admin can add Prowlarr under Admin, Hub, Connections.")
    try:
        reply = requests.get(
            config.prowlarr_url + "/api/v1/search",
            params={"query": query, "type": "search", "categories": BOOK_CATEGORIES, "limit": limit},
            headers={"X-Api-Key": config.prowlarr_api_key}, timeout=SEARCH_TIMEOUT,
        )
        reply.raise_for_status()
        releases = reply.json()
    except requests.Timeout:
        raise IntegrationError("Prowlarr took more than two minutes to ask every site. One of them is probably not answering: "
                               "open Prowlarr (Admin, Manage the programs) and remove or switch off sites that keep failing.")
    except (requests.RequestException, ValueError) as exc:
        raise IntegrationError("Could not reach Prowlarr: %s" % exc)
    if not isinstance(releases, list):
        raise IntegrationError("Prowlarr answered with something that is not a list of results.")
    results = [{
        "guid": release.get("guid"), "indexer_id": release.get("indexerId"), "indexer": release.get("indexer") or "",
        "title": release.get("title") or "", "size": release.get("size") or 0,
        "seeders": release.get("seeders"), "protocol": release.get("protocol") or "",
        "age_days": release.get("age"), "format": guess_format(release.get("title") or ""),
    } for release in releases]
    results.sort(key=lambda result: (result["format"] != "EPUB", -(result["seeders"] or 0)))
    return results


def prowlarr_grab(config, guid, indexer_id):
    """Ask Prowlarr to send a release to whichever download client it is set up with."""
    try:
        reply = requests.post(
            config.prowlarr_url + "/api/v1/search",
            json={"guid": guid, "indexerId": indexer_id},
            headers={"X-Api-Key": config.prowlarr_api_key}, timeout=TIMEOUT,
        )
    except requests.RequestException as exc:
        raise IntegrationError("Prowlarr could not send it to the download client: %s" % exc)
    if reply.status_code >= 400:
        if "download client isn't configured" in reply.text.lower():
            raise IntegrationError("Prowlarr has no download client yet. An admin can press Connect everything "
                                   "under Admin, Hub, Films and TV to give it qBittorrent.")
        raise IntegrationError("Prowlarr could not send it to the download client (%s)." % reply.status_code)
    return True


def prowlarr_status(config):
    if not config.discover_configured:
        return {"configured": False, "ok": False}
    try:
        reply = requests.get(config.prowlarr_url + "/api/v1/system/status",
                             headers={"X-Api-Key": config.prowlarr_api_key}, timeout=8)
        reply.raise_for_status()
        return {"configured": True, "ok": True, "version": reply.json().get("version")}
    except (requests.RequestException, ValueError) as exc:
        return {"configured": True, "ok": False, "error": str(exc)}


def guess_format(title):
    low = title.lower()
    return next((fmt.upper() for fmt in ("epub", "pdf", "mobi", "azw3", "cbz", "m4b", "mp3") if fmt in low), "")


# ------------------------------------------------------------- qBittorrent

def qbit_session(config):
    """A signed-in session with qBittorrent. Raises IntegrationError when it cannot sign in."""
    session = requests.Session()
    session.headers["Referer"] = config.qbit_url  # qBittorrent rejects requests without a matching Referer, CSRF thing
    if config.qbit_user:
        try:
            login = session.post(config.qbit_url + "/api/v2/auth/login",
                           data={"username": config.qbit_user, "password": config.qbit_pass}, timeout=8)
        except requests.RequestException as exc:
            raise IntegrationError("Could not reach qBittorrent: %s" % exc)
        # qBittorrent 5 says "yep" with an empty 204, older ones with 200 "Ok.". gotta accept both
        if not (login.status_code == 204 or (login.status_code == 200 and "ok" in login.text.lower())):
            raise IntegrationError("qBittorrent refused the user name or password.")
    return session


def qbit_queue(config):
    """Current torrents, for the Downloads panel. Optional."""
    if not config.qbit_url:
        return None
    session = qbit_session(config)
    try:
        params = {"sort": "added_on", "reverse": "true", "limit": 25}
        if config.qbit_category:
            params["category"] = config.qbit_category
        reply = session.get(config.qbit_url + "/api/v2/torrents/info", params=params, timeout=8)
        reply.raise_for_status()
        torrents = reply.json()
    except (requests.RequestException, ValueError) as exc:
        raise IntegrationError("Could not reach qBittorrent: %s" % exc)
    return [{
        "name": torrent.get("name"), "progress": round((torrent.get("progress") or 0) * 100),
        "state": torrent.get("state"), "done": (torrent.get("progress") or 0) >= 1,
    } for torrent in torrents]


# --------------------------------------------------------------- Hardcover
# EXPERIMENTAL: written against Hardcover's published API docs but not yet
# tested with a live account. Status ids: 2 = currently reading, 3 = read.

HARDCOVER_URL = "https://api.hardcover.app/v1/graphql"
HARDCOVER_QUERY = """
query BookHubSync {
  me {
    user_books(where: {status_id: {_in: [2, 3]}}) {
      status_id
      book { title pages contributions { author { name } } }
      user_book_reads { started_at finished_at }
    }
  }
}
"""


def hardcover_sync(hub, profile):
    """Pull "currently reading" and "read" books (for example Kindle reads) into a profile."""
    token = (profile["hardcover_token"] or "").strip()
    if not token:
        raise IntegrationError("No Hardcover API key saved for this profile.")
    if not token.lower().startswith("bearer "):
        token = "Bearer " + token
    try:
        reply = requests.post(HARDCOVER_URL, json={"query": HARDCOVER_QUERY},
                              headers={"authorization": token}, timeout=TIMEOUT)
        reply.raise_for_status()
        payload = reply.json()
    except (requests.RequestException, ValueError) as exc:
        raise IntegrationError("Could not reach Hardcover: %s" % exc)
    if not isinstance(payload, dict):
        raise IntegrationError("Hardcover sent an answer Family Hub does not understand.")
    if payload.get("errors"):
        first = payload["errors"][0] if isinstance(payload["errors"], list) else payload["errors"]
        raise IntegrationError("Hardcover returned an error: %s" % (first.get("message") if isinstance(first, dict) else first))
    me = (payload.get("data") or {}).get("me")
    if isinstance(me, list):  # sometimes a list of one, sometimes not. ¯\_(ツ)_/¯
        me = me[0] if me else {}
    count = 0
    for user_book in (me or {}).get("user_books") or []:  # a null there would crash a plain .get(..., [])
        book = user_book.get("book") or {}
        title = book.get("title")
        if not title:
            continue
        authors = [credit["author"]["name"] for credit in book.get("contributions") or [] if (credit.get("author") or {}).get("name")]
        author = " & ".join(authors[:2])
        key = db.make_key(title, author)
        match = next((owned for owned in hub.library.books() if db.make_key(owned["title"], owned["author"]) == key), None)
        if match:
            key = match["key"]
        reads = user_book.get("user_book_reads") or []
        started = next((read.get("started_at") for read in reads if read.get("started_at")), None)
        finished = next((read.get("finished_at") for read in reads if read.get("finished_at")), None)
        status = "finished" if user_book.get("status_id") == 3 else "reading"
        existing = hub.con().execute("SELECT source, status FROM reading WHERE book_key=? AND profile_id=?",
                                     (key, profile["id"])).fetchone()
        # the e-reader or a human typed it in? that wins over Hardcover. the one exception: Hardcover saying "finished"
        if existing and existing["source"] != "hardcover" and not (status == "finished" and existing["status"] != "finished"):
            continue
        hub.save_reading(
            profile["id"], key, status=status, source="hardcover",
            started=(started or "")[:10] or None, finished=(finished or "")[:10] or None,
            title=title, author=author, pages=book.get("pages"),
            percent=None if status == "finished" else (existing_percent(hub, key, profile["id"])),
        )
        count += 1
    return count


def existing_percent(hub, key, profile_id):
    row = hub.con().execute("SELECT percent FROM reading WHERE book_key=? AND profile_id=?", (key, profile_id)).fetchone()
    return row["percent"] if row else 0.0
