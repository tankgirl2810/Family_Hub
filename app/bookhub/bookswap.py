"""A book download whose sharers have all gone is swapped for another copy of the same book that someone is sharing.

Books and graphic novels from Discover only (qBittorrent's books category). A download that had people sharing it
(seeds seen, or bytes arriving) and then has nobody for SWAP_AFTER minutes is looked for again through Prowlarr.
The best other copy of the same book with someone sharing it (EPUB first, then the most seeders) is sent to
qBittorrent, and the stuck download is deleted with its unfinished files. When no other copy has anyone sharing it,
the search is tried again after an hour, then after three hours (RETRY_MINUTES). After that it gives up: the stuck
download is paused, left out of waiting for seed's checks, and the Downloads page says so; "Find a new copy" there
tries again by hand, and Continue starts it again.

A download that never had anyone sharing it is left to waiting for seed: nothing shows another copy would do better.
Nor is one waiting its turn in qBittorrent's line or paused by someone (rather than by waiting for seed) swapped:
nobody is sharing it only because it is not running.
"""
import json
import logging
import math
import re
import time

import requests

from . import db, integrations, search

SWAP_AFTER = 30            # minutes with nobody sharing before the first look
RETRY_MINUTES = (60, 180)  # then again after an hour, then after three hours; then it gives up
KEY = "books:swap"  # torrent hash -> {"seeded", "quiet_since", "tries", "next", "gave_up"}
LOG_KEY = "books:swapped"  # the last swaps made, newest first, for the Downloads page
# Words in a release name that say how it was made, not which book it is.
NOISE = {"epub", "mobi", "azw3", "azw", "pdf", "cbz", "cbr", "retail", "ebook", "ebooks", "digital", "scan", "scans",
         "repack", "english", "eng", "v2", "v3", "unabridged", "kepub", "true", "web", "dl", "hq"}
log = logging.getLogger("bookhub")


def words(name):
    """The words that say which book a release is: no bracketed tags, years, file endings or format words."""
    name = re.sub(r"\[[^\]]*\]|\{[^}]*\}|\((?:19|20)\d\d\)", " ", name or "")
    name = re.sub(r"\.(epub|mobi|azw3|pdf|cbz|cbr|zip|rar)$", " ", name, flags=re.I)
    return [word for word in search.norm(re.sub(r"[._]+", " ", name)).split()
            if word not in NOISE and not re.fullmatch(r"(19|20)\d\d", word)]


def _numbers(release_words):
    """Every number in the words, leading zeros off, so "v03", "#3" and "3" all count as 3."""
    return {number.lstrip("0") or "0" for word in release_words for number in re.findall(r"\d+", word)}


def _letters(release_words):
    return {re.sub(r"\d+", "", word) for word in release_words} - {""}


def same_book(stuck, other):
    """True when another release is the same book: most of its words, and every number (volume, issue) the same."""
    stuck_words, other_words = words(stuck), words(other)
    if not stuck_words:
        return False
    # numbers have to match exactly, otherwise volume 3 happily "replaces" volume 4. not ideal
    if _numbers(stuck_words) != _numbers(other_words):
        return False
    want, have = _letters(stuck_words), _letters(other_words)
    return len(want & have) >= math.ceil(0.8 * len(want))


def given_up(hub):
    """The book downloads given up on: no other shared copy was found after the last look."""
    return {torrent_hash for torrent_hash, watch in json.loads(db.kv_get(hub.con(), KEY) or "{}").items() if watch.get("gave_up")}


def start_over(hub, torrent_hash):
    """Continue was pressed: it is looked after again from the start."""
    state = json.loads(db.kv_get(hub.con(), KEY) or "{}")
    if state.pop(torrent_hash, None) is not None:
        db.kv_set(hub.con(), KEY, json.dumps(state))


def others(config, stuck_name):
    """Other copies of the same book that someone is sharing, the best first (EPUB, then most seeders).
    Raises IntegrationError when Prowlarr cannot be asked."""
    query = " ".join(words(stuck_name)[:8])
    if len(query) < 3:
        return []
    results = integrations.prowlarr_search(config, query)
    same = search.norm(stuck_name)
    picks = [result for result in results if (result["seeders"] or 0) > 0 and (result["protocol"] or "torrent") == "torrent"
             and search.norm(result["title"]) != same and same_book(stuck_name, result["title"])]
    # False sorts before True, so EPUBs come first; then the busiest
    picks.sort(key=lambda result: (result["format"] != "EPUB", -(result["seeders"] or 0)))
    return picks


def best_other(config, stuck_name):
    """The release to swap to, or None."""
    try:
        picks = others(config, stuck_name)
    except integrations.IntegrationError:
        return None
    return picks[0] if picks else None


def swap(hub, session, torrent_hash, stuck_name, pick):
    """Send another copy to qBittorrent, then delete the stuck download with its unfinished files, and note it.
    Raises IntegrationError when Prowlarr will not send it (the stuck one is then left alone)."""
    config, con = hub.config, hub.con()
    integrations.prowlarr_grab(config, pick["guid"], pick["indexer_id"])
    try:
        session.post(config.qbit_url + "/api/v2/torrents/delete", data={"hashes": torrent_hash, "deleteFiles": "true"}, timeout=8)
    except requests.RequestException:
        pass  # the new copy is on its way; the stuck one can be deleted by hand
    asked = con.execute("SELECT profile_id FROM requests WHERE title=? ORDER BY id DESC LIMIT 1", (stuck_name,)).fetchone()
    con.execute("INSERT INTO requests(profile_id, title, indexer, guid, status, created) VALUES(?,?,?,?,?,?)",
                (asked["profile_id"] if asked else None, pick["title"][:300], (pick.get("indexer") or "")[:80],
                 str(pick["guid"])[:500], "swapped", db.now()))
    swapped = json.loads(db.kv_get(con, LOG_KEY) or "[]")
    swapped.insert(0, {"from": stuck_name, "to": pick["title"], "seeders": pick.get("seeders"), "at": db.now()})
    db.kv_set(con, LOG_KEY, json.dumps(swapped[:20]))
    con.commit()
    log.info("swapped %s for %s (%s sharing)", stuck_name, pick["title"], pick.get("seeders"))


def _not_running(torrent, parked):
    """Waiting its turn, paused by a person, or in trouble: nobody sharing it says nothing about the copy."""
    held = torrent.get("state") or ""
    if held.startswith(("queued", "checking", "moving")) or held in ("error", "missingFiles"):
        return True
    # paused by waiting-for-seed still counts as "nobody's sharing", only a human pause gets a pass
    return held.startswith(("paused", "stopped")) and torrent.get("hash") not in parked


def run(hub, now=None):
    """Run every minute by the background jobs."""
    config = hub.config
    if not config.qbit_url or not config.discover_configured:
        return
    now = now or time.time()
    con = hub.con()
    state = json.loads(db.kv_get(con, KEY) or "{}")
    try:
        session = integrations.qbit_session(config)
        found = session.get(config.qbit_url + "/api/v2/torrents/info", params={"category": config.qbit_category or "books"},
                      timeout=8).json() or []
    except (integrations.IntegrationError, requests.RequestException, ValueError):
        return
    parked = hub.media.parked()
    for torrent in found:
        torrent_hash, name = torrent.get("hash"), torrent.get("name") or ""
        if not torrent_hash or (torrent.get("progress") or 0) >= 1:
            state.pop(torrent_hash, None)
            continue
        watch = state.setdefault(torrent_hash, {"seeded": False, "quiet_since": None, "tries": 0, "next": 0})
        if (torrent.get("num_seeds") or 0) > 0 or (torrent.get("dlspeed") or 0) > 0:
            watch.update(seeded=True, quiet_since=None, tries=0, next=0, gave_up=False)  # alive again, fresh start
            continue
        if not watch["seeded"] or watch.get("gave_up"):
            continue  # never had anyone sharing it (waiting for seed looks after it), or already given up on
        if _not_running(torrent, parked):
            watch["quiet_since"] = None
            continue
        watch["quiet_since"] = watch["quiet_since"] or now
        if now - watch["quiet_since"] < SWAP_AFTER * 60 or now < watch["next"]:
            continue
        watch["tries"] += 1
        pick = best_other(config, name)
        if not pick:
            _no_copy_yet(hub, session, torrent_hash, name, watch, now)
            continue
        try:
            swap(hub, session, torrent_hash, name, pick)
        except integrations.IntegrationError as exc:
            log.warning("could not send %s instead of %s: %s", pick["title"], name, exc)
            continue
        state.pop(torrent_hash, None)
    known = {torrent.get("hash") for torrent in found}
    db.kv_set(con, KEY, json.dumps({torrent_hash: watch for torrent_hash, watch in state.items() if torrent_hash in known}))


def _no_copy_yet(hub, session, torrent_hash, name, watch, now):
    """Nobody's sharing another copy either: try again later, or after the last retry, pause it and give up."""
    if watch["tries"] <= len(RETRY_MINUTES):
        wait = RETRY_MINUTES[watch["tries"] - 1]
        watch["next"] = now + wait * 60
        log.info("no shared copy yet instead of %s; looking again in %d minutes", name, wait)
        return
    watch["gave_up"] = True
    try:
        hub.media._start_stop(session, [torrent_hash], False)
    except requests.RequestException:
        pass
    log.info("no shared copy of %s after %d looks: paused", name, watch["tries"])
