"""Films and TV: Family Hub as the front end of Radarr, Sonarr, Bazarr and Jellyfin.

Radarr (films) and Sonarr (TV) find releases through Prowlarr and hand them to qBittorrent,
which only ever reaches the internet through the VPN container (gluetun). Bazarr fetches
subtitles. Jellyfin is where the family watches. Family Hub adds the requests, the downloads
list and one button that connects all of them (connect_everything).

Paths such as /data/media/movies are the ones inside the containers (docker-compose.yml mounts
DATA_ROOT at /data in each of them), not paths on this computer.
"""
import json
import time as _time
import re
from pathlib import Path
from urllib.parse import urlparse

import requests

from . import db

TIMEOUT = 15
# Asking every indexer for releases is a live search and often takes a minute or more.
SEARCH_TIMEOUT = 150
FILMS_ROOT, TV_ROOT = "/data/media/movies", "/data/media/tv"
TORRENTS = {"movies": "/data/torrents/movies", "tv": "/data/torrents/tv"}
PROFILE_NAME = "Family Hub"
# Quality names left out of the household profile: too large to keep, or not a playable file.
SKIP_QUALITIES = ("remux", "br-disk", "raw-hd", "dvd-r", "unknown", "workprint", "cam", "telesync", "telecine", "dvdscr", "regional", "tba")
# Standard definition is allowed as a last resort, so a film with no HD release still arrives and is upgraded later.
RESOLUTIONS = (480, 576, 720, 1080, 2160)
# Subtitle sources that need no account. OpenSubtitles.com is added when a sign-in is saved.
FREE_SUBTITLE_SOURCES = ["podnapisi", "yifysubtitles", "embeddedsubtitles", "tvsubtitles"]
KINDS = {"film": "radarr", "show": "sonarr"}
# qBittorrent's queue, so downloads can be put in order: three download at once, slow ones do not hold up the rest.
QUEUE_PREFS = {"queueing_enabled": True, "max_active_downloads": 3, "max_active_uploads": -1,
               "max_active_torrents": -1, "dont_count_slow_torrents": True}
# A finished download is checked once more, then stopped: no sharing afterwards. The files stay where they are
# for Radarr, Sonarr and the book intake to file.
FINISHED_PREFS = {"recheck_completed_torrents": True, "max_ratio_enabled": True, "max_ratio": 0,
                  "max_seeding_time_enabled": True, "max_seeding_time": 0, "max_ratio_act": 0}
# qBittorrent's states in plain words, for the Downloads page.
TORRENT_WORDS = {
    "downloading": "Downloading", "forcedDL": "Downloading", "metaDL": "Getting its details", "forcedMetaDL": "Getting its details",
    "stalledDL": "Waiting for someone to share it", "queuedDL": "Waiting its turn", "pausedDL": "Paused", "stoppedDL": "Paused",
    "checkingDL": "Checking", "checkingUP": "Checking", "checkingResumeData": "Checking", "allocating": "Making room",
    "uploading": "Done · sharing", "forcedUP": "Done · sharing", "stalledUP": "Done · sharing", "queuedUP": "Done",
    "pausedUP": "Done", "stoppedUP": "Done", "moving": "Moving", "error": "Something went wrong", "missingFiles": "Files are missing",
}
# A download nobody is sharing for this long goes to the back of the line, paused ("waiting for seed"). Every
# half hour it is woken for a few minutes to look for someone sharing it; if it finds someone it carries on.
STALL_MINUTES, CHECK_EVERY_MINUTES, CHECK_FOR_MINUTES = 10, 30, 5
TORRENT_KINDS = {"movies": "Film", "tv": "TV", "radarr": "Film", "sonarr": "TV"}
TORRENT_ACTIONS = ("pause", "resume", "top", "up", "down", "bottom")


class MediaError(Exception):
    pass


def _explain(exc):
    text = str(exc)
    if "401" in text or "Unauthorized" in text:
        return "it refused the API key"
    if "Name or service not known" in text or "getaddrinfo" in text or "NameResolution" in text:
        return "the address could not be found"
    if "Connection refused" in text or "actively refused" in text or "Max retries" in text:
        return "nothing answered at that address"
    if "timed out" in text.lower():
        return "it did not answer in time"
    return text[:160]


# How a download's status reads on the "What's happening" list.
QUEUE_WORDS = {
    "queued": "Waiting in qBittorrent for its turn",
    "paused": "Paused in qBittorrent",
    "completed": "Downloaded · filing it on the server",
    "delay": "Waiting a little in case a better version turns up",
    "downloadclientunavailable": "Waiting: qBittorrent cannot be reached or refused the sign-in",
    "warning": "qBittorrent reports a problem with this download",
    "failed": "The download failed",
}
QUEUE_TROUBLE = ("downloadclientunavailable", "warning", "failed")


def _plain(text):
    """A title or search reduced to bare words, without a leading "The", "A" or "An": Radarr and Sonarr
    drop those and the punctuation when they search."""
    words = re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()
    return re.sub(r"^(the|a|an) ", "", words)


# Version searches still running or recently finished, by job id. Kept in memory only; half an hour is plenty.
_JOBS = {}
QUALITY_RANK = {"2160p": 5, "1080p": 4, "720p": 3, "576p": 2, "480p": 2, "SD": 1, "Remux": 0, "Cam": -1, "": 0}


def _clear_old_jobs():
    for key, job in list(_JOBS.items()):
        if _time.time() - job["started"] > 1800:
            _JOBS.pop(key, None)


def _quality_from_name(name):
    """A rough quality from a release's name, until Radarr or Sonarr have checked it properly."""
    low = name.lower()
    if re.search(r"\b(cam|hdcam|ts|telesync|hdts)\b", low):
        return "Cam"
    if "remux" in low:
        return "Remux"
    for word, label in (("2160p", "2160p"), ("4k", "2160p"), ("uhd", "2160p"), ("1080p", "1080p"), ("720p", "720p"),
                        ("576p", "576p"), ("480p", "480p"), ("dvdrip", "SD"), ("dvd", "SD"), ("sdtv", "SD")):
        if re.search(r"\b%s\b" % word, low):
            return label
    return ""


def _seconds_between(a, b):
    from datetime import datetime
    try:
        parse = lambda t: datetime.fromisoformat(t.replace("Z", "+00:00")[:25])
        return abs((parse(b) - parse(a)).total_seconds())
    except ValueError:
        return 1e9


def jellyfin_auth(key):
    """Jellyfin 12 no longer accepts the old X-Emby-Token header; this form works on 10.x too."""
    return {"Authorization": 'MediaBrowser Token="%s"' % key}


def _call(method, url, headers, **kw):
    try:
        r = requests.request(method, url, headers=headers, timeout=kw.pop("timeout", TIMEOUT), **kw)
        if r.status_code >= 400:
            detail = ""
            try:
                body = r.json()
                if isinstance(body, list) and body and isinstance(body[0], dict):
                    detail = body[0].get("errorMessage") or ""
                elif isinstance(body, dict):
                    detail = body.get("message") or body.get("error") or ""
            except ValueError:
                pass
            raise MediaError("%s %s" % (r.status_code, detail or r.reason))
        if not r.content:
            return None
        try:
            return r.json()
        except ValueError:
            return r.text
    except requests.RequestException as exc:
        raise MediaError(_explain(exc))


def key_from_files(stack_dir, service):
    """The API key a service wrote into its own settings file, read from the stack folder (read-only)."""
    if not stack_dir:
        return ""
    stack_dir = Path(stack_dir)
    try:
        if service == "bazarr":
            text = (stack_dir / "bazarr-config" / "config" / "config.yaml").read_text(encoding="utf-8")
            m = re.search(r"^auth:\s*$(.*?)(?=^\S)", text + "\nend:", re.M | re.S)
            m = re.search(r"apikey:\s*'?([0-9a-fA-F]{20,})", m.group(1) if m else text)
        else:
            text = (stack_dir / (service + "-config") / "config.xml").read_text(encoding="utf-8")
            m = re.search(r"<ApiKey>([^<]+)</ApiKey>", text)
        return m.group(1).strip() if m else ""
    except OSError:
        return ""


class Arr:
    """Radarr or Sonarr: the same API, version 3."""

    def __init__(self, name, url, key):
        self.name, self.url, self.key = name, url.rstrip("/"), key

    def __call__(self, method, path, **kw):
        return _call(method, self.url + "/api/v3/" + path, {"X-Api-Key": self.key}, **kw)

    def get(self, path, **params):
        return self("GET", path, params=params or None)


class Media:
    def __init__(self, hub, config=None):
        """config: settings to try instead of the saved ones (the Test connection buttons)."""
        self.hub = hub
        self._config = config

    @property
    def config(self):
        return self._config or self.hub.config

    # ---------------------------------------------------------- services

    def _key(self, service):
        key = getattr(self.config, service + "_api_key", "")
        return key or key_from_files(self.config.stack_dir, service)

    def arr(self, kind):
        """The Radarr (kind 'film') or Sonarr ('show') to use, or a plain message why there is none."""
        service = KINDS[kind]
        url, key = getattr(self.config, service + "_url"), self._key(service)
        what = "Films" if kind == "film" else "TV shows"
        if not url:
            raise MediaError("%s are not set up yet. An admin can add them under Admin, Hub, Connections." % what)
        if not key:
            raise MediaError("%s: no API key yet. An admin can press Connect everything under Admin, Hub, Films and TV." % what)
        return Arr(service, url, key)

    def configured(self, kind):
        return bool(getattr(self.config, KINDS[kind] + "_url"))

    def jellyfin(self, method, path, **kw):
        if not (self.config.jellyfin_url and self.config.jellyfin_api_key):
            raise MediaError("Jellyfin is not set up yet.")
        return _call(method, self.config.jellyfin_url + path, jellyfin_auth(self.config.jellyfin_api_key), **kw)

    def bazarr(self, method, path, **kw):
        key = self._key("bazarr")
        if not (self.config.bazarr_url and key):
            raise MediaError("Bazarr is not set up yet.")
        return _call(method, self.config.bazarr_url + "/api/" + path, {"X-API-KEY": key}, **kw)

    def gluetun(self, path):
        if not self.config.gluetun_url:
            raise MediaError("The VPN's address is not set.")
        return _call("GET", self.config.gluetun_url + path, {"X-API-Key": self.config.gluetun_api_key}, timeout=6)

    # ------------------------------------------------------------ status

    def vpn(self):
        """Whether torrents can run: the VPN is set up, connected, and which port it forwards."""
        if not self.config.vpn_configured:
            return {"configured": False, "ok": False, "message": "Waiting for VPN · not set up"}
        try:
            ip = self.gluetun("/v1/publicip/ip") or {}
        except MediaError as exc:
            return {"configured": True, "ok": False, "message": "VPN not connected · %s" % exc}
        port = self.forwarded_port()
        where = ", ".join(x for x in (ip.get("city"), ip.get("country")) if x)
        if self.config.qbit_url and not self._qbit_answers():
            # qBittorrent shares the VPN container's network. When that container restarts, qBittorrent is left
            # cut off (safe: it cannot leak) until it is restarted too. Only Docker can do that.
            return {"configured": True, "ok": False, "port": port,
                    "message": "VPN connected, but qBittorrent lost its connection when the VPN restarted. "
                               "Restart it: docker compose restart qbittorrent"}
        return {"configured": True, "ok": True, "port": port,
                "message": "VPN connected" + (" · " + where if where else "") + (" · port %s forwarded" % port if port else "")}

    def _qbit_answers(self):
        """Whether qBittorrent's page answers at all (signed in or not)."""
        try:
            requests.request("GET", self.config.qbit_url + "/api/v2/app/version", timeout=4)
            return True
        except requests.RequestException:
            return False

    def forwarded_port(self):
        for path in ("/v1/portforward", "/v1/openvpn/portforwarded"):
            try:
                answer = self.gluetun(path) or {}
            except MediaError:
                continue
            port = answer.get("port") or (answer.get("ports") or [0])[0]
            if port:
                return int(port)
        return 0

    def status(self):
        out = {}
        for kind, label in (("film", "Films (Radarr)"), ("show", "TV shows (Sonarr)")):
            item = {"label": label, "configured": self.configured(kind), "ok": False}
            if item["configured"]:
                try:
                    item["version"] = self.arr(kind).get("system/status").get("version")
                    item["ok"] = True
                except MediaError as exc:
                    item["error"] = str(exc)
            out[KINDS[kind]] = item
        sub = {"label": "Subtitles (Bazarr)", "configured": bool(self.config.bazarr_url), "ok": False}
        if sub["configured"]:
            try:
                sub["version"] = ((self.bazarr("GET", "system/status") or {}).get("data") or {}).get("bazarr_version")
                sub["ok"] = True
            except MediaError as exc:
                sub["error"] = str(exc)
        out["bazarr"] = sub
        jf = {"label": "Watching (Jellyfin)", "configured": bool(self.config.jellyfin_url), "ok": False}
        if jf["configured"]:
            try:
                jf["version"] = (self.jellyfin("GET", "/System/Info/Public") or {}).get("Version")
                jf["ok"] = True
            except MediaError as exc:
                jf["error"] = str(exc)
        out["jellyfin"] = jf
        out["vpn"] = self.vpn()
        return out

    # ------------------------------------------------------------ library

    def poster(self, item, kind):
        img = next((i for i in item.get("images") or [] if i.get("coverType") == "poster"), None)
        if item.get("id") and img:
            return "/api/media/poster/%s/%s" % (kind, item["id"])
        if img and img.get("remoteUrl"):
            return "/api/media/poster-remote?url=" + requests.utils.quote(img["remoteUrl"], safe="")
        return ""

    def summary(self, item, kind, requested=None, coming=None):
        stats = item.get("statistics") or {}
        out = {
            "kind": kind, "id": item.get("id"), "title": item.get("title") or "", "year": item.get("year"),
            "overview": item.get("overview") or "", "runtime": item.get("runtime"),
            "tmdb": item.get("tmdbId"), "tvdb": item.get("tvdbId"), "poster": self.poster(item, kind),
            "added": (item.get("added") or "")[:10], "monitored": bool(item.get("monitored")),
            "genres": (item.get("genres") or [])[:3], "network": item.get("network") or item.get("studio") or "",
            "requested_by": (requested or {}).get(item.get("id")),
        }
        d = (coming or {}).get(item.get("id"))
        out["downloading"] = {"progress": d["progress"], "episodes": d["episodes"], "stuck": d.get("stuck", False)} if d else None
        if kind == "film":
            out["downloaded"] = bool(item.get("hasFile"))
            out["size"] = item.get("sizeOnDisk") or (item.get("movieFile") or {}).get("size") or 0
            out["quality"] = (((item.get("movieFile") or {}).get("quality") or {}).get("quality") or {}).get("name", "")
        else:
            have, want = stats.get("episodeFileCount", 0), stats.get("episodeCount", 0)
            out["downloaded"] = bool(want) and have >= want
            out["episodes"] = {"have": have, "want": want, "total": stats.get("totalEpisodeCount", 0)}
            out["size"] = stats.get("sizeOnDisk", 0)
            out["seasons"] = [{"number": s["seasonNumber"], "monitored": s.get("monitored", False),
                               "have": (s.get("statistics") or {}).get("episodeFileCount", 0),
                               "want": (s.get("statistics") or {}).get("episodeCount", 0)}
                              for s in item.get("seasons") or [] if s.get("seasonNumber", 0) > 0]
            out["ended"] = item.get("status") == "ended"
        return out

    def requested_by(self, kind):
        rows = self.hub.con().execute(
            "SELECT r.arr_id, p.name FROM media_requests r LEFT JOIN profiles p ON p.id = r.profile_id WHERE r.kind=?", (kind,))
        return {row["arr_id"]: row["name"] or "someone" for row in rows}

    def downloading(self, kind):
        """What is coming in now, from Radarr's or Sonarr's queue: {film or show id: {"progress", "episodes", "ids"}}.
        "ids" are the episodes downloading, for the episode list."""
        try:
            records = (self.arr(kind).get("queue", pageSize=500) or {}).get("records", [])
        except MediaError:
            return {}
        out = {}
        for r in records:
            owner = r.get("movieId") if kind == "film" else r.get("seriesId")
            if owner is None:
                continue
            d = out.setdefault(owner, {"size": 0, "left": 0, "episodes": 0, "ids": {}, "stuck": False})
            d["size"] += r.get("size") or 0
            d["left"] += r.get("sizeleft") or 0
            d["episodes"] += 1
            # Downloaded, but Radarr or Sonarr report a problem filing it: it will not arrive by itself.
            if not r.get("sizeleft") and (r.get("trackedDownloadStatus") or "").lower() in ("warning", "error"):
                d["stuck"] = True
            if r.get("episodeId"):
                size = r.get("size") or 0
                d["ids"][r["episodeId"]] = int(100 * (size - (r.get("sizeleft") or 0)) / size) if size else 0
        for d in out.values():
            d["progress"] = int(100 * (d["size"] - d["left"]) / d["size"]) if d["size"] else 0
        return out

    def library(self, kind):
        arr = self.arr(kind)
        items = arr.get("movie" if kind == "film" else "series")
        requested, coming = self.requested_by(kind), self.downloading(kind)
        out = [self.summary(i, kind, requested, coming) for i in items]
        out.sort(key=lambda x: x["added"], reverse=True)
        return out

    def item(self, kind, arr_id):
        arr = self.arr(kind)
        return self.summary(arr.get(("movie/%d" if kind == "film" else "series/%d") % arr_id), kind, self.requested_by(kind),
                            self.downloading(kind))

    def activity(self, kind, arr_id):
        """What has happened so far for one film or show, oldest first, in plain words.
        Each step: {"at": ISO time or "", "state": "done" | "active" | "bad" | "wait", "text": ...}."""
        arr = self.arr(kind)
        film = kind == "film"
        item = arr.get(("movie/%d" if film else "series/%d") % arr_id)
        title = item.get("title") or ""
        steps = []

        con = self.hub.con()
        row = con.execute("SELECT p.name, r.created FROM media_requests r LEFT JOIN profiles p ON p.id = r.profile_id "
                          "WHERE r.kind=? AND r.arr_id=? ORDER BY r.id DESC LIMIT 1", (kind, arr_id)).fetchone()
        since = (row[1] if row else "") or item.get("added") or ""
        steps.append({"at": since, "state": "done", "stage": 5,
                      "text": "Requested by %s" % row[0] if row and row[0] else "Added to the list"})

        # The searches Radarr or Sonarr ran for it.
        for c in arr.get("command") or []:
            body = c.get("body") or {}
            mine = arr_id in (body.get("movieIds") or []) if film else body.get("seriesId") == arr_id
            if not mine or not c.get("name", "").endswith("Search"):
                continue
            running = c.get("status") in ("queued", "started")
            steps.append({"at": c.get("started") or c.get("queued") or "", "state": "active" if running else "done",
                          "stage": 20 if running else 25,
                          "text": ("Searching every indexer…" if running else
                                   "Search finished: %s" % (c.get("message") or "done"))})

        # Each indexer Prowlarr asked on its behalf.
        grabbed_titles, lines = [], []
        if self.config.prowlarr_url and self._key("prowlarr"):
            pkey = {"X-Api-Key": self._key("prowlarr")}
            try:
                names = {i["id"]: i["name"] for i in _call("GET", self.config.prowlarr_url + "/api/v1/indexer", pkey) or []}
                records = (_call("GET", self.config.prowlarr_url + "/api/v1/history", pkey,
                                 params={"pageSize": 200, "sortKey": "date", "sortDirection": "descending"}) or {}).get("records") or []
            except MediaError:
                names, records = {}, []
            words = _plain(title)
            ext = str((item.get("tmdbId") if film else item.get("tvdbId")) or "")
            looked = {}
            for r in reversed(records):
                data = r.get("data") or {}
                if data.get("source") not in ("Radarr", "Sonarr") or (since and r.get("date", "") < since[:19]):
                    continue
                query = _plain(data.get("query"))
                if r.get("eventType") == "indexerQuery" and (words and words in query or ext and ext in json.dumps(data)):
                    where = names.get(r.get("indexerId"), "an indexer")
                    n = int(data.get("queryResults") or 0) if r.get("successful") else -1
                    # An indexer asked for several pages in one search shows as one line, with the results added up.
                    last = looked.get(where)
                    if last and _seconds_between(last["seen"], r.get("date", "")) < 15:
                        last["n"] = max(last["n"], 0) + max(n, 0) if n >= 0 or last["n"] >= 0 else -1
                        last["seen"] = r.get("date", "")
                        continue
                    looked[where] = {"at": r.get("date", ""), "seen": r.get("date", ""), "n": n, "where": where}
                    lines.append(looked[where])
                elif r.get("eventType") == "releaseGrabbed" and words and words in _plain(data.get("grabTitle")):
                    grabbed_titles.append((r.get("date", ""), data.get("grabTitle"), names.get(r.get("indexerId"), "an indexer")))
            for x in lines:
                n = x["n"]
                steps.append({"at": x["at"], "state": "bad" if n < 0 else "done", "stage": 30,
                              "text": "%s did not answer" % x["where"] if n < 0 else
                              "Looked at %s · %s" % (x["where"], "%d result%s" % (n, "" if n == 1 else "s") if n else "nothing there")})

        # What it picked, and whether the download client took it.
        history = arr.get("history/movie" if film else "history/series", **({"movieId": arr_id} if film else {"seriesId": arr_id})) or []
        took = [h for h in history if h.get("eventType") == "grabbed"]
        for h in took:
            q = ((h.get("quality") or {}).get("quality") or {}).get("name", "")
            steps.append({"at": h.get("date", ""), "state": "done", "stage": 50,
                          "text": "Best version found: %s%s · from %s" % (h.get("sourceTitle", ""), " (%s)" % q if q else "",
                                                                          (h.get("data") or {}).get("indexer") or "an indexer")})
        if grabbed_titles and not took:
            at, name, where = grabbed_titles[-1]
            steps.append({"at": at, "state": "bad",
                          "text": "Picked %s from %s, but qBittorrent refused it. Check its user name and password under "
                                  "Admin, Hub, Connections, then press Connect everything." % (name, where)})
        for h in history:
            if h.get("eventType") in ("downloadFailed", "importFailed"):
                steps.append({"at": h.get("date", ""), "state": "bad",
                              "text": "%s: %s" % ("Download failed" if h["eventType"] == "downloadFailed" else "Could not file it",
                                                  (h.get("data") or {}).get("message") or h.get("sourceTitle", ""))})
            elif h.get("eventType") in ("downloadFolderImported", "episodeFileImported" if not film else "movieFileImported"):
                steps.append({"at": h.get("date", ""), "state": "done", "stage": 98, "text": "Filed on the server: %s" % h.get("sourceTitle", "")})

        # Downloading now. Sonarr lists a whole season in one download once per episode: it is one line here.
        queue = arr.get("queue/details", **({"movieId": arr_id} if film else {"seriesId": arr_id, "includeEpisode": "true"})) or []
        parked = {h.lower() for h in self.parked()}
        shared = {}
        for q in queue:
            shared.setdefault(q.get("downloadId") or id(q), []).append(q.get("episode") or {})
        for key, eps in shared.items():
            q = next(x for x in queue if (x.get("downloadId") or id(x)) == key)
            size, left = q.get("size") or 0, q.get("sizeleft") or 0
            pct = int(100 * (size - left) / size) if size else 0
            status = (q.get("status") or "").lower()
            which = ""
            if not film and eps[0].get("seasonNumber") is not None:
                which = ("S%02dE%02d: " % (eps[0]["seasonNumber"], eps[0].get("episodeNumber") or 0) if len(eps) == 1
                         else "Season %d: " % eps[0]["seasonNumber"])
            problem = next((m for s in q.get("statusMessages") or [] for m in s.get("messages") or []), "")
            if (q.get("downloadId") or "").lower() in parked:
                steps.append({"at": q.get("added", "") or "", "state": "wait", "progress": pct, "stage": 50,
                              "text": which + "Waiting for seed · nobody is sharing it right now, so it is paused at the back "
                                      "of the line and checked every half hour · %d%%" % pct})
            elif status == "downloading":
                left_words = q.get("timeleft") if q.get("timeleft") and q.get("timeleft") != "00:00:00" else ""
                steps.append({"at": q.get("added", "") or "", "state": "active", "progress": pct, "stage": 50 + pct * 45 // 100,
                              "text": which + "Downloading through the VPN · %d%%%s" % (pct, " · %s left" % left_words if left_words else "")})
            elif status == "completed" and problem and (q.get("trackedDownloadStatus") or "").lower() in ("warning", "error"):
                # Downloaded, but it cannot be filed. The usual cause is qBittorrent saving into a folder Radarr and
                # Sonarr cannot see, which Connect everything puts right.
                hint = (" An admin can press Connect everything under Admin, Hub, Films and TV, which moves it where it can be filed."
                        if "eligible for import" in problem else "")
                steps.append({"at": q.get("added", "") or "", "state": "bad",
                              "text": which + "Downloaded, but %s could not file it: %s.%s" % (arr.name.title(), problem.rstrip("."), hint)})
            else:
                steps.append({"at": q.get("added", "") or "", "state": "bad" if status in QUEUE_TROUBLE else "active", "stage": 50,
                              "text": which + QUEUE_WORDS.get(status, "Waiting in qBittorrent (%s)" % (q.get("status") or "queued"))})

        steps.sort(key=lambda s: s["at"] or "")
        done = item.get("hasFile") if film else (item.get("statistics") or {}).get("percentOfEpisodes", 0) >= 100
        searching = any(s["state"] == "active" for s in steps)
        if done:
            steps.append({"at": "", "state": "done", "stage": 100, "text": "On the server, ready to watch"})
        elif not item.get("monitored") and not searching:
            steps.append({"at": "", "state": "wait", "text": "Stopped looking. Press Start looking again to carry on."})
        elif not searching and not took and not any(s["state"] == "bad" for s in steps):
            steps.append({"at": "", "state": "wait",
                          "text": "No watchable version found yet. %s keeps watching for new releases." % arr.name.title()})
        # One line for the top of the panel: what is happening now, and how far along it is.
        # Nothing under way but something wrong (a download that cannot be filed, say): that is the news.
        current = (next((x for x in reversed(steps) if x["state"] == "active"), None)
                   or (None if done else next((x for x in reversed(steps) if x["state"] == "bad"), None)) or steps[-1])
        now = {"text": current["text"], "state": current["state"],
               "progress": max((x.get("stage", 0) for x in steps if x["state"] != "bad"), default=0)}
        if lines:
            asked = sorted({x["where"] for x in lines})
            had = sorted({x["where"] for x in lines if x["n"] > 0})
            now["sites"] = "Asked %d site%s · %s" % (len(asked), "" if len(asked) == 1 else "s",
                                                    "%d had something (%s)" % (len(had), ", ".join(had)) if had else "none had it")
        # Downloaded but not filed yet: every download for it has finished, and Radarr or Sonarr still has to move
        # it into the library. The corner pop-up shows this as done, with a note, so nothing looks still under way.
        fetched = [q for q in queue if (q.get("status") or "").lower() == "completed" or ((q.get("size") or 0) and not q.get("sizeleft"))]
        pending = [q for q in queue if q not in fetched]
        downloaded = bool(not done and fetched and not pending)
        stuck = downloaded and any((q.get("trackedDownloadStatus") or "").lower() in ("warning", "error") for q in fetched)
        return {"title": title, "steps": steps, "finished": bool(done), "now": now,
                "downloaded": downloaded, "filing": ("stuck" if stuck else "filing") if downloaded else None}

    def lookup(self, kind, term):
        arr = self.arr(kind)
        found = arr.get("movie/lookup" if kind == "film" else "series/lookup", term=term) or []
        have = {(i.get("tmdbId") if kind == "film" else i.get("tvdbId")) for i in arr.get("movie" if kind == "film" else "series")}
        out = []
        for item in found[:20]:
            s = self.summary(dict(item, id=None), kind)
            ext = item.get("tmdbId") if kind == "film" else item.get("tvdbId")
            s["in_library"] = ext in have
            s["seasons"] = [{"number": x["seasonNumber"]} for x in item.get("seasons") or [] if x.get("seasonNumber", 0) > 0]
            out.append(s)
        return out

    # ----------------------------------------------------------- requests

    def _profile_id(self, arr):
        profiles = arr.get("qualityprofile")
        mine = next((p for p in profiles if p["name"] == PROFILE_NAME), None)
        if mine:
            return mine["id"]
        if not profiles:
            raise MediaError("%s has no quality profiles yet." % arr.name.title())
        return profiles[0]["id"]

    def _root(self, arr, wanted):
        roots = arr.get("rootfolder")
        if not roots:
            raise MediaError("%s has no media folder yet. An admin can press Connect everything under Admin, Hub, Films and TV." % arr.name.title())
        return next((r["path"] for r in roots if r["path"].rstrip("/") == wanted), roots[0]["path"])

    def request(self, kind, external_id, who, seasons="all", picked=()):
        """Add a film or show and start looking for it. seasons: 'all', 'latest' or 'pick' (with picked numbers)."""
        arr = self.arr(kind)
        if kind == "film":
            found = arr.get("movie/lookup/tmdb", tmdbId=external_id)
            if not found:
                raise MediaError("That film could not be found any more.")
            body = dict(found, qualityProfileId=self._profile_id(arr), rootFolderPath=self._root(arr, FILMS_ROOT),
                        monitored=True, minimumAvailability="released", addOptions={"searchForMovie": True})
            body.pop("id", None)
            added = self._add(arr, kind, "movie", body, external_id)
            if added.get("already"):
                return added
        else:
            found = arr.get("series/lookup", term="tvdb:%s" % external_id) or []
            if not found:
                raise MediaError("That show could not be found any more.")
            show = found[0]
            numbers = sorted(s["seasonNumber"] for s in show.get("seasons") or [] if s.get("seasonNumber", 0) > 0)
            if seasons == "latest":
                keep = set(numbers[-1:])
            elif seasons == "pick":
                keep = {int(n) for n in picked} & set(numbers)
                if not keep:
                    raise MediaError("Choose at least one season.")
            else:
                keep = set(numbers)
            for s in show.get("seasons") or []:
                s["monitored"] = s.get("seasonNumber") in keep
            options = {"searchForMissingEpisodes": True}
            if seasons in ("all", "latest"):
                options["monitor"] = "all" if seasons == "all" else "lastSeason"
            # With chosen seasons, the monitored flags set above are what Sonarr follows.
            body = dict(show, qualityProfileId=self._profile_id(arr), rootFolderPath=self._root(arr, TV_ROOT),
                        monitored=True, seasonFolder=True,
                        # New episodes keep coming whichever seasons were chosen.
                        monitorNewItems="all", addOptions=options)
            body.pop("id", None)
            added = self._add(arr, kind, "series", body, external_id)
            if added.get("already"):
                return added
        con = self.hub.con()
        con.execute("INSERT INTO media_requests(kind, arr_id, title, profile_id, created) VALUES(?, ?, ?, ?, ?)",
                    (kind, added["id"], added.get("title") or "", None if who == "guest" else who["id"], db.now()))
        con.commit()
        return self.summary(added, kind)

    def _add(self, arr, kind, path, body, external_id):
        """Add a film or show. One already on the list (asked for from two screens, say) is not an error:
        what is there comes back, marked already."""
        try:
            return arr("POST", path, json=body)
        except MediaError as exc:
            if "already" not in str(exc).lower():
                raise
            field = "tmdbId" if kind == "film" else "tvdbId"
            have = next((i for i in arr.get(path) or [] if str(i.get(field)) == str(external_id)), None)
            if not have:
                raise
        return dict(self.summary(have, kind, self.requested_by(kind)), already=True)

    def remove(self, kind, arr_id):
        """Take a film or show off the server, files and all. Returns what was removed."""
        arr = self.arr(kind)
        item = self.item(kind, arr_id)
        arr("DELETE", ("movie/%d" if kind == "film" else "series/%d") % arr_id,
            params={"deleteFiles": "true", "addImportListExclusion": "false"})
        con = self.hub.con()
        con.execute("DELETE FROM media_requests WHERE kind=? AND arr_id=?", (kind, arr_id))
        con.commit()
        return item

    def look_again(self, kind, arr_id):
        """Search every indexer again now, for a better copy of what is on the server or for anything still missing.
        Radarr and Sonarr only swap a file for a higher quality, or a fixed (repack) copy of the same one."""
        arr = self.arr(kind)
        item = self.item(kind, arr_id)
        if kind == "film":
            arr("POST", "command", json={"name": "MoviesSearch", "movieIds": [arr_id]})
            if not item["downloaded"]:
                return "Looking again for %s." % item["title"]
            if "2160" in item["quality"]:
                return ("Looking again. %s is already in 2160p, so it will only be swapped for a fixed copy "
                        "of the same release." % item["title"])
            return "Looking for something better than the %s copy on the server." % (item["quality"] or "current")
        arr("POST", "command", json={"name": "SeriesSearch", "seriesId": arr_id})
        return "Looking again for missing episodes of %s, and better copies of the ones below 2160p." % item["title"]

    def set_looking(self, kind, arr_id, on):
        """Stop or start Radarr or Sonarr looking for a film or show. Stopping also cancels a search waiting to run;
        anything already downloading carries on and is still filed."""
        arr = self.arr(kind)
        film = kind == "film"
        path = ("movie/%d" if film else "series/%d") % arr_id
        item = arr.get(path)
        item["monitored"] = bool(on)
        arr("PUT", path, json=item)
        title = item.get("title") or ""
        if on:
            arr("POST", "command", json={"name": "MoviesSearch", "movieIds": [arr_id]} if film else
                {"name": "SeriesSearch", "seriesId": arr_id})
            return "Looking for %s again." % title
        for c in arr.get("command") or []:
            body = c.get("body") or {}
            mine = arr_id in (body.get("movieIds") or []) if film else body.get("seriesId") == arr_id
            if mine and c.get("status") == "queued":
                try:
                    arr("DELETE", "command/%d" % c["id"])
                except MediaError:
                    pass  # it started in the meantime; it finishes on its own
        return "Stopped looking for %s. Anything already downloading still finishes." % title

    def releases(self, kind, arr_id, season=None):
        """Every release the indexers offer for one film, or one season of a show, best first. For admins choosing by hand.
        Sonarr only searches when given a season; without one it answers with its recent feed."""
        arr = self.arr(kind)
        if kind == "film":
            query = {"movieId": arr_id}
        else:
            if season is None:
                numbers = [s["seasonNumber"] for s in (arr.get("series/%d" % arr_id).get("seasons") or []) if s.get("seasonNumber")]
                season = max(numbers) if numbers else 1
            query = {"seriesId": arr_id, "seasonNumber": season}
        found = arr("GET", "release", params=query, timeout=SEARCH_TIMEOUT) or []
        out = [{
            "guid": r.get("guid"), "indexer_id": r.get("indexerId"), "title": r.get("title"),
            "quality": ((r.get("quality") or {}).get("quality") or {}).get("name", ""), "size": r.get("size") or 0,
            "indexer": r.get("indexer"), "seeders": r.get("seeders"), "rejected": bool(r.get("rejected")),
            "why": (r.get("rejections") or [""])[0],
        } for r in found]
        out.sort(key=lambda r: (r["rejected"], -(r["seeders"] or 0)))
        return out[:60]

    def grab(self, kind, guid, indexer_id):
        self.arr(kind)("POST", "release", json={"guid": guid, "indexerId": indexer_id})

    # ----------------------------------------------- versions as they arrive

    def start_version_search(self, kind, arr_id, season=None):
        """Look for every version of a film, or one season of a show, showing results as each site answers.
        Two searches run side by side: Family Hub asks each site through Prowlarr (fast, but unchecked), and
        Radarr or Sonarr runs its own full search (slow, but each version checked against the household rule).
        Returns a job id for version_search()."""
        import threading
        import uuid
        from concurrent.futures import ThreadPoolExecutor
        arr = self.arr(kind)
        film = kind == "film"
        item = arr.get(("movie/%d" if film else "series/%d") % arr_id)
        if not film and season is None:
            numbers = [s["seasonNumber"] for s in item.get("seasons") or [] if s.get("seasonNumber")]
            season = max(numbers) if numbers else 1
        query = "%s %s" % (item.get("title") or "", item.get("year") or "") if film else "%s S%02d" % (item.get("title") or "", season)
        pkey = {"X-Api-Key": self._key("prowlarr")} if self.config.prowlarr_url and self._key("prowlarr") else None
        sites = []
        if pkey:
            try:
                sites = [(i["id"], i["name"]) for i in _call("GET", self.config.prowlarr_url + "/api/v1/indexer", pkey) or [] if i.get("enable")]
            except MediaError:
                sites = []
        job = {"id": uuid.uuid4().hex, "kind": kind, "arr_id": arr_id, "season": season, "started": _time.time(),
               "sites": len(sites), "answered": 0, "found": {}, "checked": None, "checked_error": "", "lock": threading.Lock()}
        _clear_old_jobs()
        _JOBS[job["id"]] = job

        def ask(site):
            try:
                rows = _call("GET", self.config.prowlarr_url + "/api/v1/search", pkey, timeout=SEARCH_TIMEOUT, params={
                    "query": query.strip(), "type": "search", "indexerIds": site[0], "categories": 2000 if film else 5000, "limit": 100}) or []
            except MediaError:
                rows = []
            with job["lock"]:
                job["answered"] += 1
                for r in rows:
                    link = r.get("magnetUrl") or r.get("downloadUrl")
                    if link and r.get("guid") not in job["found"]:
                        job["found"][r["guid"]] = {
                            "guid": r["guid"], "title": r.get("title") or "", "size": r.get("size") or 0, "seeders": r.get("seeders"),
                            "indexer": r.get("indexer") or site[1], "quality": _quality_from_name(r.get("title") or ""),
                            "link": link, "published": r.get("publishDate") or ""}

        def checked():
            try:
                job["checked"] = self.releases(kind, arr_id, season)
            except MediaError as exc:
                job["checked_error"] = str(exc)
                job["checked"] = []

        threading.Thread(target=checked, daemon=True).start()
        if sites:
            pool = ThreadPoolExecutor(max_workers=10)
            for site in sites:
                pool.submit(ask, site)
            pool.shutdown(wait=False)
        return job["id"]

    @staticmethod
    def version_search(job_id):
        """Where a search from start_version_search() has got to: the versions found so far, best first, and
        Radarr's or Sonarr's checked list once it is ready."""
        job = _JOBS.get(job_id)
        if not job:
            raise MediaError("That search has finished or expired. Press Show versions again.")
        with job["lock"]:
            found = sorted(job["found"].values(), key=lambda r: (-QUALITY_RANK.get(r["quality"], 0), -(r["seeders"] or 0)))
            out = {"sites": job["sites"], "answered": job["answered"], "seconds": int(_time.time() - job["started"]),
                   "found": [{k: v for k, v in r.items() if k != "link"} for r in found[:150]],
                   "checked": job["checked"], "checked_error": job["checked_error"]}
        out["done"] = job["checked"] is not None and job["answered"] >= job["sites"]
        return out

    def grab_found(self, job_id, guid, force=False):
        """Download a version found before the full search finished. It is handed to Radarr or Sonarr, which check it
        and, if it fits, send it to qBittorrent and file it as usual. With force, one they turn down goes straight to
        qBittorrent in their folder instead. Returns {"ok": True} or {"ok": False, "why": ...}."""
        job = _JOBS.get(job_id)
        found = job and job["found"].get(guid)
        if not found:
            raise MediaError("That version is no longer listed. Press Show versions again.")
        kind = job["kind"]
        arr = self.arr(kind)
        if not force:
            answer = arr("POST", "release/push", json={
                "title": found["title"], "downloadUrl": found["link"], "protocol": "torrent",
                "publishDate": found["published"] or _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime()),
                "indexer": found["indexer"]}) or []
            first = answer[0] if isinstance(answer, list) and answer else answer if isinstance(answer, dict) else {}
            if first.get("rejected"):
                return {"ok": False, "why": "; ".join(first.get("rejections") or []) or "it does not fit the household rule"}
            return {"ok": True}
        from . import integrations
        s = integrations.qbit_session(self.config)
        category = "movies" if kind == "film" else "tv"
        link = found["link"]
        try:
            if link.startswith("magnet:"):
                r = s.post(self.config.qbit_url + "/api/v2/torrents/add", data={"urls": link, "category": category}, timeout=30)
            else:
                # Family Hub fetches the .torrent itself: qBittorrent only reaches the internet through the VPN.
                body = requests.get(link, timeout=60, allow_redirects=False)
                if body.is_redirect and (body.headers.get("Location") or "").startswith("magnet:"):
                    r = s.post(self.config.qbit_url + "/api/v2/torrents/add", data={"urls": body.headers["Location"], "category": category}, timeout=30)
                else:
                    body.raise_for_status()
                    r = s.post(self.config.qbit_url + "/api/v2/torrents/add", data={"category": category},
                               files={"torrents": ("release.torrent", body.content, "application/x-bittorrent")}, timeout=30)
        except (integrations.IntegrationError, requests.RequestException) as exc:
            raise MediaError("Could not send it to qBittorrent: %s" % exc)
        if r.status_code >= 400 or "fail" in (r.text or "").lower():
            raise MediaError("qBittorrent would not take it (%s)." % r.status_code)
        return {"ok": True, "direct": True}

    def queue(self):
        """What is downloading now for films and TV, with who asked for it."""
        out, problems = [], []
        for kind in ("film", "show"):
            if not self.configured(kind):
                continue
            try:
                arr = self.arr(kind)
                extra = {"includeMovie": "true"} if kind == "film" else {"includeSeries": "true", "includeEpisode": "true"}
                records = (arr.get("queue", pageSize=200, **extra) or {}).get("records", [])
            except MediaError as exc:
                problems.append("%s: %s" % ("Films" if kind == "film" else "TV shows", exc))
                continue
            requested = self.requested_by(kind)
            groups = {}
            for r in records:
                if kind == "film":
                    gkey, title = ("film", r.get("movieId")), (r.get("movie") or {}).get("title") or r.get("title")
                    owner = r.get("movieId")
                else:
                    season = (r.get("episode") or {}).get("seasonNumber")
                    gkey = ("show", r.get("seriesId"), season)
                    title = "%s · Season %s" % ((r.get("series") or {}).get("title") or r.get("title"), season)
                    owner = r.get("seriesId")
                g = groups.setdefault(gkey, {"kind": kind, "title": title, "size": 0, "left": 0, "items": 0,
                                             "status": r.get("trackedDownloadState") or r.get("status") or "",
                                             "requested_by": requested.get(owner), "eta": r.get("estimatedCompletionTime")})
                g["size"] += r.get("size") or 0
                g["left"] += r.get("sizeleft") or 0
                g["items"] += 1
                msgs = [m for s in r.get("statusMessages") or [] for m in s.get("messages") or []]
                if msgs:
                    g["problem"] = msgs[0]
            for g in groups.values():
                g["progress"] = round(100 * (1 - g["left"] / g["size"])) if g["size"] else 0
                out.append(g)
        return {"items": out, "problems": problems}

    def _need_qbit(self):
        if not self.config.qbit_url:
            raise MediaError("qBittorrent is not set up yet. An admin can add it under Admin, Hub, Connections.")

    def torrents(self):
        """Everything in qBittorrent (films, TV and books), unfinished ones first in queue order, for the Downloads page."""
        from . import integrations
        self._need_qbit()
        try:
            s = integrations.qbit_session(self.config)
            found = s.get(self.config.qbit_url + "/api/v2/torrents/info", timeout=8).json() or []
        except (integrations.IntegrationError, requests.RequestException, ValueError) as exc:
            raise MediaError("Could not reach qBittorrent: %s" % exc)
        # Radarr and Sonarr know which film or show each download is for, and who asked for it.
        names = {}
        for kind in ("film", "show"):
            if not self.configured(kind):
                continue
            try:
                extra = {"includeMovie": "true"} if kind == "film" else {"includeSeries": "true", "includeEpisode": "true"}
                records = (self.arr(kind).get("queue", pageSize=200, **extra) or {}).get("records", [])
            except MediaError:
                continue
            requested = self.requested_by(kind)
            episodes = {}
            for r in records:
                episodes.setdefault((r.get("downloadId") or "").lower(), []).append(r.get("episode") or {})
            for r in records:
                key = (r.get("downloadId") or "").lower()
                owner = r.get("movieId") if kind == "film" else r.get("seriesId")
                title = ((r.get("movie") if kind == "film" else r.get("series")) or {}).get("title")
                eps = episodes[key]
                if kind == "show" and title and eps[0].get("seasonNumber") is not None:
                    # One episode, or a whole season in one download.
                    title += (" · S%02dE%02d" % (eps[0]["seasonNumber"], eps[0].get("episodeNumber") or 0) if len(eps) == 1
                              else " · Season %d" % eps[0]["seasonNumber"])
                names[key] = {"title": title, "kind": kind, "id": owner, "requested_by": requested.get(owner)}
        books = self.config.qbit_category or "books"
        asked = {}
        for row in self.hub.con().execute("SELECT r.title, p.name FROM requests r LEFT JOIN profiles p ON p.id = r.profile_id ORDER BY r.id"):
            asked[_plain(row[0])] = row[1]
        parked = self.parked()
        out = []
        for t in found:
            h = t.get("hash") or ""
            known = names.get(h.lower(), {})
            if t.get("category") == books:
                known = {"requested_by": asked.get(_plain(t.get("name")))}
            state = t.get("state") or ""
            done = (t.get("progress") or 0) >= 1
            waiting = parked.get(h) if not done else None
            words = TORRENT_WORDS.get(state, state)
            if waiting:
                words = "Waiting for seed · checking for someone sharing it" if waiting.get("checking") else "Waiting for seed"
            out.append({
                "hash": h, "name": t.get("name") or "", "title": known.get("title") or t.get("name") or "",
                "kind": known.get("kind"), "id": known.get("id"), "requested_by": known.get("requested_by"),
                "type": "Book" if t.get("category") == books else TORRENT_KINDS.get(t.get("category") or "", "Other"),
                "progress": int((t.get("progress") or 0) * 100), "done": done, "state": words,
                "paused": state.startswith(("paused", "stopped")), "trouble": state in ("error", "missingFiles"),
                "size": t.get("size") or 0, "speed": t.get("dlspeed") or 0, "up_speed": t.get("upspeed") or 0,
                "eta": t.get("eta") if 0 < (t.get("eta") or 0) < 8640000 else None,
                "seeds": t.get("num_seeds") or 0, "position": t.get("priority") if (t.get("priority") or 0) > 0 else None,
                # Waiting for seed: when it was last checked, and when the next check is.
                "waiting_seed": bool(waiting), "checked": waiting and waiting.get("checked"),
                "next_check": waiting and not waiting.get("checking") and waiting.get("checked", 0) + CHECK_EVERY_MINUTES * 60,
            })
        # Waiting for seed goes after everything else still to download, as it is in qBittorrent's line.
        out.sort(key=lambda x: (x["done"], x["waiting_seed"], x["position"] is None, x["position"] or 0))
        return out

    def parked(self):
        """The downloads waiting for seed: {hash: {"since", "checked", "checking", "progress"}}."""
        return json.loads(db.kv_get(self.hub.con(), "media:parked") or "{}")

    def _start_stop(self, s, hashes, start):
        """qBittorrent 5 calls these start and stop; older versions resume and pause."""
        base, form = self.config.qbit_url + "/api/v2/torrents/", {"hashes": "|".join(hashes)}
        r = s.post(base + ("start" if start else "stop"), data=form, timeout=8)
        if r.status_code == 404:
            r = s.post(base + ("resume" if start else "pause"), data=form, timeout=8)
        return r

    def _queue_move(self, s, hashes, where):
        """Move downloads in qBittorrent's line: where is topPrio, increasePrio, decreasePrio or bottomPrio."""
        path, form = self.config.qbit_url + "/api/v2/torrents/" + where, {"hashes": "|".join(hashes)}
        r = s.post(path, data=form, timeout=8)
        if r.status_code == 409:  # the queue is switched off: switch it on, then try again
            s.post(self.config.qbit_url + "/api/v2/app/setPreferences", data={"json": json.dumps(QUEUE_PREFS)}, timeout=8)
            r = s.post(path, data=form, timeout=8)
        return r

    def park_stalled(self, now=None):
        """Run every minute by the background jobs. A download nobody has shared for STALL_MINUTES goes to the
        back of the line and is paused, so it does not hold up the rest. Every CHECK_EVERY_MINUTES it is started
        for CHECK_FOR_MINUTES to look for someone sharing it: if it finds someone it carries on from the back of
        the line, otherwise it is paused again. Pressing Continue on one takes it out of this."""
        from . import integrations
        if not self.config.qbit_url:
            return
        now = now or _time.time()
        con = self.hub.con()
        stalled = json.loads(db.kv_get(con, "media:stalled") or "{}")  # hash -> when it was first seen with nobody sharing
        parked = self.parked()
        try:
            s = integrations.qbit_session(self.config)
            found = {t.get("hash"): t for t in s.get(self.config.qbit_url + "/api/v2/torrents/info", timeout=8).json() or []}
        except (integrations.IntegrationError, requests.RequestException, ValueError):
            return
        park, wake, sleep, free = [], [], [], []
        for h in list(parked):
            t = found.get(h)
            if not t or (t.get("progress") or 0) >= 1:
                parked.pop(h)
                continue
            p, state = parked[h], t.get("state") or ""
            if p.get("checking"):
                if (t.get("num_seeds") or 0) > 0 or (t.get("dlspeed") or 0) > 0 or (t.get("progress") or 0) > p.get("progress", 0):
                    free.append(h)  # someone is sharing it again
                elif now - p["checking"] >= CHECK_FOR_MINUTES * 60:
                    sleep.append(h)
                    p.update(checking=None, checked=now)
            elif not state.startswith(("paused", "stopped")):
                parked.pop(h)  # started again by hand, or by qBittorrent: it is back to normal
                stalled[h] = now
            elif now - p.get("checked", 0) >= CHECK_EVERY_MINUTES * 60:
                wake.append(h)
                p.update(checking=now, progress=t.get("progress") or 0)
        for h in free:
            parked.pop(h)
        for h, t in found.items():
            if h in parked or (t.get("progress") or 0) >= 1:
                stalled.pop(h, None)
                continue
            state = t.get("state") or ""
            nobody = state == "stalledDL" or (state == "metaDL" and not (t.get("num_seeds") or 0) and not (t.get("num_leechs") or 0))
            if not nobody:
                stalled.pop(h, None)
            elif now - stalled.setdefault(h, now) >= STALL_MINUTES * 60:
                park.append(h)
                parked[h] = {"since": now, "checked": now, "checking": None, "progress": t.get("progress") or 0}
                stalled.pop(h)
        stalled = {h: at for h, at in stalled.items() if h in found}
        try:
            if park:
                self._queue_move(s, park, "bottomPrio")
                self._start_stop(s, park, False)
            if wake:  # forced, so it looks for people sharing it even though it is at the back of the line
                s.post(self.config.qbit_url + "/api/v2/torrents/setForceStart", data={"hashes": "|".join(wake), "value": "true"}, timeout=8)
            if sleep or free:
                s.post(self.config.qbit_url + "/api/v2/torrents/setForceStart", data={"hashes": "|".join(sleep + free), "value": "false"}, timeout=8)
            if sleep:
                self._start_stop(s, sleep, False)
        except requests.RequestException:
            return  # nothing is saved, so the same is tried next minute
        db.kv_set(con, "media:stalled", json.dumps(stalled))
        db.kv_set(con, "media:parked", json.dumps(parked))

    def unpark(self, torrent_hash):
        """Someone pressed Continue or moved a download waiting for seed: it is theirs to manage again."""
        con = self.hub.con()
        parked = self.parked()
        if parked.pop(torrent_hash, None) is not None:
            db.kv_set(con, "media:parked", json.dumps(parked))
            stalled = json.loads(db.kv_get(con, "media:stalled") or "{}")
            stalled[torrent_hash] = _time.time()  # the ten minutes start again
            db.kv_set(con, "media:stalled", json.dumps(stalled))

    def speed(self):
        """qBittorrent's speeds now and its limits, all in bytes per second (0 means no limit). "Slow mode" is
        qBittorrent's second set of limits, switched on and off with one press."""
        from . import integrations
        self._need_qbit()
        try:
            s = integrations.qbit_session(self.config)
            prefs = s.get(self.config.qbit_url + "/api/v2/app/preferences", timeout=8).json()
            info = s.get(self.config.qbit_url + "/api/v2/transfer/info", timeout=8).json()
            slow = s.get(self.config.qbit_url + "/api/v2/transfer/speedLimitsMode", timeout=8).text.strip() == "1"
            found = s.get(self.config.qbit_url + "/api/v2/torrents/info", timeout=8).json() or []
        except (integrations.IntegrationError, requests.RequestException, ValueError) as exc:
            raise MediaError("Could not reach qBittorrent: %s" % exc)
        # How many downloads are doing what, so the Speed panel can say why the speed is what it is.
        counts = {"downloading": 0, "waiting_turn": 0, "waiting_seed": 0, "paused": 0, "done": 0}
        parked = self.parked()
        for t in found:
            state = t.get("state") or ""
            if (t.get("progress") or 0) >= 1:
                counts["done"] += 1
            elif t.get("hash") in parked or state == "stalledDL":
                counts["waiting_seed"] += 1
            elif state.startswith(("paused", "stopped")):
                counts["paused"] += 1
            elif state.startswith("queued"):
                counts["waiting_turn"] += 1
            else:
                counts["downloading"] += 1
        return {"down_now": info.get("dl_info_speed") or 0, "up_now": info.get("up_info_speed") or 0,
                "down_limit": max(prefs.get("dl_limit") or 0, 0), "up_limit": max(prefs.get("up_limit") or 0, 0),
                "slow_down": max(prefs.get("alt_dl_limit") or 0, 0), "slow_up": max(prefs.get("alt_up_limit") or 0, 0),
                "slow_mode": slow, "counts": counts, "at_once": prefs.get("max_active_downloads") or 0}

    def set_speed(self, values):
        """Change any of down_limit, up_limit, slow_down, slow_up (bytes per second, 0 for no limit) and slow_mode."""
        from . import integrations
        self._need_qbit()
        names = {"down_limit": "dl_limit", "up_limit": "up_limit", "slow_down": "alt_dl_limit", "slow_up": "alt_up_limit"}
        prefs = {}
        for key, pref in names.items():
            if key in values:
                try:
                    prefs[pref] = max(0, int(values[key] or 0))
                except (TypeError, ValueError):
                    raise MediaError("Speeds must be numbers.")
        try:
            s = integrations.qbit_session(self.config)
            if prefs:
                s.post(self.config.qbit_url + "/api/v2/app/setPreferences", data={"json": json.dumps(prefs)}, timeout=8)
            if "slow_mode" in values:
                now = s.get(self.config.qbit_url + "/api/v2/transfer/speedLimitsMode", timeout=8).text.strip() == "1"
                if now != bool(values["slow_mode"]):
                    s.post(self.config.qbit_url + "/api/v2/transfer/toggleSpeedLimitsMode", timeout=8)
        except (integrations.IntegrationError, requests.RequestException) as exc:
            raise MediaError("Could not reach qBittorrent: %s" % exc)
        return self.speed()

    def torrent_action(self, torrent_hash, action):
        """Pause, resume or move a download in qBittorrent's queue."""
        from . import integrations
        self._need_qbit()
        if action not in TORRENT_ACTIONS or not re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", torrent_hash or ""):
            raise MediaError("That is not something a download can do.")
        try:
            s = integrations.qbit_session(self.config)
            if action in ("pause", "resume"):
                r = self._start_stop(s, [torrent_hash], action == "resume")
            else:
                r = self._queue_move(s, [torrent_hash], {"top": "topPrio", "up": "increasePrio", "down": "decreasePrio", "bottom": "bottomPrio"}[action])
        except (integrations.IntegrationError, requests.RequestException) as exc:
            raise MediaError("Could not reach qBittorrent: %s" % exc)
        if r.status_code >= 400:
            raise MediaError("qBittorrent said no (%s)." % r.status_code)
        if action == "resume":
            self.unpark(torrent_hash)
        return True

    def book_status(self, title, torrents=None):
        """Where a book sent from Discover has got to, in the same shape as "What's happening" for films:
        waiting for qBittorrent, downloading, then added to the library by Family Hub's intake."""
        torrents = self.torrents() if torrents is None else torrents
        want = _plain(title)
        t = next((x for x in torrents if x["type"] == "Book" and want and (_plain(x["name"]) == want or want in _plain(x["name"]))), None)
        if t and not t["done"]:
            text = "Downloading through the VPN · %d%%" % t["progress"] if t["state"] == "Downloading" else t["state"]
            return {"title": title, "finished": False,
                    "now": {"text": text, "state": "bad" if t["trouble"] else "active", "progress": 10 + t["progress"] * 80 // 100}}
        if self._book_filed(title):
            return {"title": title, "finished": True, "now": {"text": "In the library", "state": "done", "progress": 100}}
        if t:
            return {"title": title, "finished": False, "downloaded": True, "filing": "filing",
                    "now": {"text": "Downloaded · being added to the library", "state": "active", "progress": 95}}
        return {"title": title, "finished": False,
                "now": {"text": "Sent to qBittorrent · waiting for it to start", "state": "wait", "progress": 5}}

    def _book_filed(self, title):
        """Whether intake has added a file whose name shares most of its words with the release name."""
        words = set(_plain(title).split()) - {"epub", "pdf", "mobi", "azw3", "retail", "ebook"}
        if not words:
            return False
        for row in self.hub.con().execute("SELECT path FROM imported_files ORDER BY at DESC LIMIT 200"):
            name = _plain(Path(str(row[0]).replace("\\", "/")).stem)
            if len(words & set(name.split())) >= max(1, (len(words) * 6 + 9) // 10):
                return True
        return False

    def search_progress(self, query, seconds_ago):
        """How far a search that started seconds_ago has got: Prowlarr logs each site as it answers.
        Works for Discover book searches and for picking a version of a film or show."""
        from datetime import datetime, timedelta, timezone
        if not (self.config.prowlarr_url and self._key("prowlarr")):
            return {"sites": 0, "answered": 0, "names": []}
        pkey = {"X-Api-Key": self._key("prowlarr")}
        names = {i["id"]: i["name"] for i in _call("GET", self.config.prowlarr_url + "/api/v1/indexer", pkey) or [] if i.get("enable")}
        records = (_call("GET", self.config.prowlarr_url + "/api/v1/history", pkey,
                         params={"pageSize": 100, "sortKey": "date", "sortDirection": "descending",
                                 "eventType": 2}) or {}).get("records") or []  # 2 = a site was asked
        # A little slack for the two computers' clocks and for the search reaching Prowlarr.
        since = datetime.now(timezone.utc) - timedelta(seconds=max(0, seconds_ago) + 5)
        words = _plain(query)
        answered = []
        for r in records:
            try:
                at = datetime.fromisoformat(re.sub(r"\.\d+", "", r.get("date", "")).replace("Z", "+00:00"))
            except ValueError:
                continue
            if at.tzinfo is None:
                at = at.replace(tzinfo=timezone.utc)
            if at < since or r.get("eventType") not in ("indexerQuery", None) or r.get("indexerId") not in names:
                continue
            asked = _plain((r.get("data") or {}).get("query"))
            if words and (words in asked or asked in words) and names[r["indexerId"]] not in answered:
                answered.append(names[r["indexerId"]])
        return {"sites": len(names), "answered": min(len(answered), len(names)), "names": answered[:3]}

    def delete_download(self, torrent_hash):
        """Delete a download everywhere: from qBittorrent with its files and, when it is for a film or show,
        that film or show from the server too (Radarr or Sonarr, its files, and so Jellyfin). Returns what went."""
        from . import integrations
        found = next((t for t in self.torrents() if t["hash"].lower() == (torrent_hash or "").lower()), None)
        if not found:
            raise MediaError("That download is not in qBittorrent any more.")
        try:
            s = integrations.qbit_session(self.config)
            r = s.post(self.config.qbit_url + "/api/v2/torrents/delete",
                       data={"hashes": found["hash"], "deleteFiles": "true"}, timeout=8)
        except (integrations.IntegrationError, requests.RequestException) as exc:
            raise MediaError("Could not reach qBittorrent: %s" % exc)
        if r.status_code >= 400:
            raise MediaError("qBittorrent said no (%s)." % r.status_code)
        if found["kind"] and found["id"]:
            try:
                self.remove(found["kind"], found["id"])
            except MediaError as exc:
                if not str(exc).startswith("404"):  # already gone from the list is fine
                    raise MediaError("The download was deleted, but %s could not be taken off the server: %s" % (found["title"], exc))
            return "%s was deleted everywhere." % found["title"]
        return "%s and its files were deleted." % found["title"]

    def history(self, limit=8):
        """Recently added to the library: what Radarr and Sonarr filed lately."""
        out = []
        for kind in ("film", "show"):
            if not self.configured(kind):
                continue
            try:
                arr = self.arr(kind)
                records = (arr.get("history", pageSize=limit, sortKey="date", sortDirection="descending",
                                   eventType=3, **({"includeMovie": "true"} if kind == "film" else {"includeSeries": "true", "includeEpisode": "true"})) or {}).get("records", [])
            except MediaError:
                continue
            requested = self.requested_by(kind)
            for r in records:
                if kind == "film":
                    title, owner = (r.get("movie") or {}).get("title") or r.get("sourceTitle"), r.get("movieId")
                else:
                    ep = r.get("episode") or {}
                    title = "%s · S%02dE%02d" % ((r.get("series") or {}).get("title") or "", ep.get("seasonNumber") or 0, ep.get("episodeNumber") or 0)
                    owner = r.get("seriesId")
                out.append({"kind": kind, "title": title, "at": r.get("date"), "requested_by": requested.get(owner)})
        out.sort(key=lambda x: x["at"] or "", reverse=True)
        return out[:limit]

    # ------------------------------------------------------------ Jellyfin

    def latest(self, limit=12):
        """The newest films and shows on the family server, as Jellyfin sees them."""
        items = (self.jellyfin("GET", "/Items", params={
            "Recursive": "true", "IncludeItemTypes": "Movie,Series", "SortBy": "DateCreated", "SortOrder": "Descending",
            "Limit": limit, "Fields": "ProductionYear,DateCreated,Overview"}) or {}).get("Items", [])
        link = self.config.jellyfin_link
        return [{
            "id": i["Id"], "title": i.get("Name") or "", "year": i.get("ProductionYear"), "kind": "film" if i.get("Type") == "Movie" else "show",
            "added": (i.get("DateCreated") or "")[:10], "poster": "/api/media/jellyfin-image/" + i["Id"],
            "watch": (link + "/web/#/details?id=" + i["Id"]) if link else "",
        } for i in items]

    def _jellyfin_match(self, kind, item):
        """Jellyfin's id for a film or show, matched by its TMDB or TVDB number, or None."""
        provider, number = ("Tmdb", item.get("tmdbId")) if kind == "film" else ("Tvdb", item.get("tvdbId"))
        found = (self.jellyfin("GET", "/Items", params={
            "Recursive": "true", "IncludeItemTypes": "Movie" if kind == "film" else "Series",
            "searchTerm": item.get("title") or "", "Fields": "ProviderIds", "Limit": 20}) or {}).get("Items", [])
        for i in found:
            if str((i.get("ProviderIds") or {}).get(provider) or "") == str(number or "-"):
                return i["Id"]
        # A different spelling of the name: fall back to the only result, if there is just one.
        return found[0]["Id"] if len(found) == 1 else None

    def watch(self, kind, arr_id):
        """Where to watch a film, or each episode of a show, in Jellyfin. Episodes come from Sonarr, so ones not
        downloaded yet are listed too. {"jellyfin": {"link", "port"}, "watch_id"} or {..., "seasons": [...]}."""
        arr = self.arr(kind)
        item = arr.get(("movie/%d" if kind == "film" else "series/%d") % arr_id)
        out = {"jellyfin": {"link": self.config.jellyfin_link, "port": self.config.service_ports["jellyfin"]}}
        try:
            jid = self._jellyfin_match(kind, item)
        except MediaError:
            jid = None  # Jellyfin not set up or not answering: still list the episodes
        if kind == "film":
            out["watch_id"] = jid
            return out
        ids = {}
        if jid:
            try:
                for e in (self.jellyfin("GET", "/Shows/%s/Episodes" % jid) or {}).get("Items", []):
                    ids[(e.get("ParentIndexNumber"), e.get("IndexNumber"))] = e["Id"]
            except MediaError:
                pass
        seasons = {}
        coming = (self.downloading("show").get(arr_id) or {}).get("ids", {})
        for e in arr.get("episode", seriesId=arr_id) or []:
            if not e.get("seasonNumber"):
                continue  # specials
            seasons.setdefault(e["seasonNumber"], []).append({
                "number": e.get("episodeNumber"), "title": e.get("title") or "", "aired": (e.get("airDate") or "")[:10],
                "on_server": bool(e.get("hasFile")), "wanted": bool(e.get("monitored")), "downloading": coming.get(e.get("id")),
                "watch_id": ids.get((e["seasonNumber"], e.get("episodeNumber"))),
            })
        out["seasons"] = [{"number": n, "episodes": sorted(eps, key=lambda x: x["number"] or 0)} for n, eps in sorted(seasons.items())]
        return out

    # ------------------------------------------------------- VPN and ports

    def sync_vpn_port(self):
        """Tell qBittorrent which port the VPN forwards, so other peers can reach it. Run every few minutes."""
        if not (self.config.vpn_configured and self.config.qbit_url):
            return None
        port = self.forwarded_port()
        if not port:
            return None
        from . import integrations
        session = integrations.qbit_session(self.config)
        prefs = session.get(self.config.qbit_url + "/api/v2/app/preferences", timeout=8).json()
        if prefs.get("listen_port") != port:
            session.post(self.config.qbit_url + "/api/v2/app/setPreferences",
                         data={"json": json.dumps({"listen_port": port, "random_port": False, "upnp": False})}, timeout=8)
        return port

    # --------------------------------------------------- connect everything

    def connect_everything(self):
        """Wire the services together. Safe to run again: anything already in place is left alone.
        Returns [(step, ok, words)] for the Admin page."""
        steps = []

        def step(name, fn):
            try:
                steps.append((name, True, fn() or "Done"))
            except MediaError as exc:
                steps.append((name, False, str(exc)))
            except integrations.IntegrationError as exc:
                steps.append((name, False, str(exc)))
            except (KeyError, TypeError, ValueError, requests.RequestException) as exc:
                steps.append((name, False, "Unexpected answer: %s" % exc))

        from . import integrations, settings
        found = {}
        for service in ("prowlarr", "radarr", "sonarr", "bazarr"):
            if getattr(self.config, service + "_url") and not getattr(self.config, service + "_api_key"):
                key = key_from_files(self.config.stack_dir, service)
                if key:
                    found[service + "_api_key"] = key
        if found:
            settings.save(self.hub, found)
            steps.append(("API keys", True, "Read from the services' own settings: " + ", ".join(k.split("_")[0].title() for k in found)))

        # First, so the download-client steps below already use qBittorrent's new sign-in.
        if self.config.services_user and self.config.services_pass:
            step("Sign-in for each program's page", self._ensure_logins)
        for kind in ("film", "show"):
            if not self.configured(kind):
                steps.append((("Films" if kind == "film" else "TV shows"), False, "Not set up: no address under Connections."))
                continue
            label = "Radarr" if kind == "film" else "Sonarr"
            step(label + " → media folder", lambda k=kind: self._ensure_root(k))
            step(label + " → quality (2160p first)", lambda k=kind: self._ensure_profile(k))
            step(label + " → qBittorrent", lambda k=kind: self._ensure_client(k))
            step("Prowlarr → " + label, lambda k=kind: self._ensure_prowlarr_app(k))
        if self.config.qbit_url:
            step("qBittorrent → download folders", self._ensure_categories)
            if self.config.prowlarr_url and self._key("prowlarr"):
                step("Prowlarr → qBittorrent (books)", self._ensure_prowlarr_client)
        if self.config.bazarr_url:
            step("Bazarr → Radarr, Sonarr and English subtitles", self._ensure_bazarr)
        if self.config.jellyfin_url and self.config.jellyfin_api_key:
            step("Jellyfin → libraries", self._ensure_jellyfin)
        vpn = self.vpn()
        steps.append(("VPN", vpn["ok"], vpn["message"]))
        return steps

    def _ensure_logins(self):
        """The one user name and password (SERVICES_USER, SERVICES_PASSWORD in .env) on each program's own page."""
        from . import integrations, settings
        user, password = self.config.services_user, self.config.services_pass
        done = []
        for kind, name in (("film", "Radarr"), ("show", "Sonarr")):
            if self.configured(kind) and self._key(KINDS[kind]):
                arr = self.arr(kind)
                host = arr.get("config/host") or {}
                host.update(authenticationMethod="forms", authenticationRequired="enabled", username=user, password=password, passwordConfirmation=password)
                arr("PUT", "config/host", json=host)
                done.append(name)
        key = self._key("prowlarr")
        if self.config.prowlarr_url and key:
            url, head = self.config.prowlarr_url + "/api/v1/config/host", {"X-Api-Key": key}
            host = _call("GET", url, head) or {}
            host.update(authenticationMethod="forms", authenticationRequired="enabled", username=user, password=password, passwordConfirmation=password)
            _call("PUT", url, head, json=host)
            done.append("Prowlarr")
        if self.config.bazarr_url and self._key("bazarr"):
            self.bazarr("POST", "system/settings", data=[("settings-auth-type", "form"), ("settings-auth-username", user),
                                                         ("settings-auth-password", password)])
            done.append("Bazarr")
        if self.config.qbit_url and (self.config.qbit_user, self.config.qbit_pass) != (user, password):
            s = integrations.qbit_session(self.config)  # signed in with the old sign-in
            r = s.post(self.config.qbit_url + "/api/v2/app/setPreferences",
                       data={"json": json.dumps({"web_ui_username": user, "web_ui_password": password})}, timeout=8)
            if r.status_code >= 400:
                raise MediaError("qBittorrent would not change its sign-in (%s)." % r.status_code)
            settings.save(self.hub, {"qbit_user": user, "qbit_pass": password})  # so Family Hub keeps getting in
            done.append("qBittorrent")
        elif self.config.qbit_url:
            done.append("qBittorrent")
        return "User name %s on %s" % (user, ", ".join(done)) if done else "No programs set up yet"

    def _ensure_root(self, kind):
        arr, path = self.arr(kind), FILMS_ROOT if kind == "film" else TV_ROOT
        if any(r["path"].rstrip("/") == path for r in arr.get("rootfolder")):
            return "Already set: " + path
        arr("POST", "rootfolder", json={"path": path})
        return "Added " + path

    def _ensure_profile(self, kind):
        arr = self.arr(kind)
        schema = arr.get("qualityprofile/schema")
        items = schema["items"]

        def quality_of(item):
            q = item.get("quality") or {}
            return [q] if q else [x["quality"] for x in item.get("items") or [] if x.get("quality")]

        def wanted(item):
            qs = quality_of(item)
            name = (item.get("name") or (qs[0]["name"] if qs else "")).lower()
            return bool(qs) and not any(s in name for s in SKIP_QUALITIES) and \
                all((q.get("resolution") in RESOLUTIONS or q["name"] == "DVD") and not any(s in q["name"].lower() for s in SKIP_QUALITIES) for q in qs)

        best = None
        for item in items:  # the schema lists the lowest quality first
            ok = wanted(item)
            item["allowed"] = ok
            for sub in item.get("items") or []:
                sub["allowed"] = ok
            if ok:
                best = item
        if best is None:
            raise MediaError("No playable qualities were offered.")
        body = dict(schema, name=PROFILE_NAME, upgradeAllowed=True, items=items,
                    cutoff=best.get("id") or best["quality"]["id"])
        existing = next((p for p in arr.get("qualityprofile") if p["name"] == PROFILE_NAME), None)
        if existing:
            arr("PUT", "qualityprofile/%d" % existing["id"], json=dict(body, id=existing["id"]))
            return "Updated the Family Hub profile"
        arr("POST", "qualityprofile", json=body)
        return "Made the Family Hub profile: 2160p, then 1080p, then 720p, then the best there is, no remux"

    def _qbit_address(self):
        u = urlparse(self.config.qbit_url)
        return u.hostname, u.port or (443 if u.scheme == "https" else 80), u.scheme == "https"

    def _ensure_client(self, kind):
        arr = self.arr(kind)
        if not self.config.qbit_url:
            raise MediaError("qBittorrent's address is not set under Connections.")
        host, port, ssl = self._qbit_address()
        values = {"host": host, "port": port, "useSsl": ssl, "username": self.config.qbit_user, "password": self.config.qbit_pass,
                  "movieCategory": "movies", "tvCategory": "tv"}
        existing = next((c for c in arr.get("downloadclient") if c.get("implementation") == "QBittorrent"), None)
        if existing:
            # Keep its address and sign-in in step with Connections, so a changed qBittorrent password reaches it too.
            for f in existing.get("fields") or []:
                if f.get("name") in ("host", "port", "useSsl", "username", "password"):
                    f["value"] = values[f["name"]]
            arr("PUT", "downloadclient/%d" % existing["id"], params={"forceSave": "true"}, json=existing)
            return "Already connected · sign-in brought up to date"
        schema = next(s for s in arr.get("downloadclient/schema") if s.get("implementation") == "QBittorrent")
        for f in schema.get("fields") or []:
            if f["name"] in values:
                f["value"] = values[f["name"]]
        body = dict(schema, name="qBittorrent (through the VPN)", enable=True, priority=1)
        # Saved even while the VPN is down; Radarr and Sonarr test it again by themselves later.
        arr("POST", "downloadclient", params={"forceSave": "true"}, json=body)
        return "Connected to %s:%s" % (host, port)

    def _ensure_prowlarr_app(self, kind):
        if not (self.config.prowlarr_url and self.config.prowlarr_api_key):
            raise MediaError("Prowlarr's address and key are not set under Connections.")
        impl = "Radarr" if kind == "film" else "Sonarr"
        headers = {"X-Api-Key": self.config.prowlarr_api_key}
        apps = _call("GET", self.config.prowlarr_url + "/api/v1/applications", headers) or []
        if any(a.get("implementation") == impl for a in apps):
            return "Already connected"
        schema = next(s for s in _call("GET", self.config.prowlarr_url + "/api/v1/applications/schema", headers) if s.get("implementation") == impl)
        arr = self.arr(kind)
        values = {"prowlarrUrl": self.config.prowlarr_url, "baseUrl": arr.url, "apiKey": arr.key}
        for f in schema.get("fields") or []:
            if f["name"] in values:
                f["value"] = values[f["name"]]
        _call("POST", self.config.prowlarr_url + "/api/v1/applications", headers, params={"forceSave": "true"},
              json=dict(schema, name=impl, syncLevel="fullSync"))
        return "Prowlarr now shares its indexers with " + impl

    def _ensure_prowlarr_client(self):
        """Books found on Discover are sent through Prowlarr, which needs qBittorrent as its own download client."""
        url, headers = self.config.prowlarr_url + "/api/v1/downloadclient", {"X-Api-Key": self._key("prowlarr")}
        host, port, ssl = self._qbit_address()
        values = {"host": host, "port": port, "useSsl": ssl, "username": self.config.qbit_user,
                  "password": self.config.qbit_pass, "category": self.config.qbit_category or "books"}
        existing = next((c for c in _call("GET", url, headers) or [] if c.get("implementation") == "QBittorrent"), None)
        if existing:
            for f in existing.get("fields") or []:
                if f.get("name") in ("host", "port", "useSsl", "username", "password"):
                    f["value"] = values[f["name"]]
            _call("PUT", "%s/%d" % (url, existing["id"]), headers, params={"forceSave": "true"}, json=existing)
            return "Already connected · sign-in brought up to date"
        schema = next(s for s in _call("GET", url + "/schema", headers) if s.get("implementation") == "QBittorrent")
        for f in schema.get("fields") or []:
            if f["name"] in values:
                f["value"] = values[f["name"]]
        _call("POST", url, headers, params={"forceSave": "true"}, json=dict(schema, name="qBittorrent", enable=True))
        return "Books from Discover go to qBittorrent, category %s" % values["category"]

    def _ensure_categories(self):
        from . import integrations
        s = integrations.qbit_session(self.config)
        base = self.config.qbit_url + "/api/v2/torrents/"
        wanted = dict(TORRENTS)
        wanted[self.config.qbit_category or "books"] = "/downloads"
        have = s.get(base + "categories", timeout=8).json() or {}
        for name, path in wanted.items():
            s.post(base + ("editCategory" if name in have else "createCategory"), data={"category": name, "savePath": path}, timeout=8)
        # Category folders only count when qBittorrent manages where torrents go. Without that, films and TV land in
        # its default folder (the books one), which Radarr and Sonarr cannot see, so nothing is ever filed.
        prefs = dict(QUEUE_PREFS, auto_tmm_enabled=True, **FINISHED_PREFS)
        current = s.get(self.config.qbit_url + "/api/v2/app/preferences", timeout=8).json() or {}
        # qBittorrent's own slow mode is 10 KB/s, which all but stops downloads; give it a gentler start.
        if current.get("alt_dl_limit") == 10240 and current.get("alt_up_limit") == 10240:
            prefs.update(alt_dl_limit=2 * 1048576, alt_up_limit=512 * 1024)
        s.post(self.config.qbit_url + "/api/v2/app/setPreferences", data={"json": json.dumps(prefs)}, timeout=8)
        # Films and TV that went to the wrong folder before are moved into theirs, so they can be filed after all.
        stray = [t["hash"] for t in s.get(base + "info", timeout=8).json() or []
                 if t.get("category") in TORRENTS and not t.get("auto_tmm") and t.get("hash")]
        if stray:
            s.post(base + "setAutoManagement", data={"hashes": "|".join(stray), "enable": "true"}, timeout=8)
        return "Films → torrents/movies, TV → torrents/tv, books → the finished-downloads folder" + \
            (" · moved %d earlier download%s into %s folder" % (len(stray), "" if len(stray) == 1 else "s", "its" if len(stray) == 1 else "their")
             if stray else "")

    def _ensure_bazarr(self):
        """Point Bazarr at Radarr and Sonarr and ask for English subtitles, forced ones too. Untested against a live Bazarr."""
        form = [("settings-general-use_radarr", "true"), ("settings-general-use_sonarr", "true")]
        for service, port in (("radarr", 7878), ("sonarr", 8989)):
            u = urlparse(getattr(self.config, service + "_url"))
            form += [("settings-%s-ip" % service, u.hostname or service), ("settings-%s-port" % service, str(u.port or port)),
                     ("settings-%s-base_url" % service, u.path or ""), ("settings-%s-ssl" % service, "false"),
                     ("settings-%s-apikey" % service, self._key(service))]
        langs = [x.strip() for x in (self.config.subtitle_langs or "en").split(",") if x.strip()]
        items, n = [], 0
        for code in langs:
            # Normal subtitles (hearing-impaired ones are accepted when nothing else exists) and the
            # "forced" ones that only cover foreign-language lines.
            for forced in ("False", "True"):
                n += 1
                items.append({"id": n, "language": code, "audio_exclude": "False", "hi": "False", "forced": forced})
        # Bazarr replaces its whole list of language profiles with what is sent, so keep the admin's own.
        others = [p for p in (self.bazarr("GET", "system/languages/profiles") or []) if p.get("name") != PROFILE_NAME]
        ours = max([p.get("profileId") or 0 for p in others] + [0]) + 1
        profile = others + [{"profileId": ours, "name": PROFILE_NAME, "items": items, "cutoff": None,
                             "mustContain": [], "mustNotContain": [], "originalFormat": False}]
        form += [("languages-enabled", code) for code in langs]
        form += [("languages-profiles", json.dumps(profile)),
                 ("settings-general-serie_default_enabled", "true"), ("settings-general-serie_default_profile", str(ours)),
                 ("settings-general-movie_default_enabled", "true"), ("settings-general-movie_default_profile", str(ours))]
        sources = list(FREE_SUBTITLE_SOURCES)
        if self.config.opensubtitles_user and self.config.opensubtitles_pass:
            sources.append("opensubtitlescom")
            form += [("settings-opensubtitlescom-username", self.config.opensubtitles_user),
                     ("settings-opensubtitlescom-password", self.config.opensubtitles_pass)]
        form += [("settings-general-enabled_providers", p) for p in sources]
        self.bazarr("POST", "system/settings", data=form)
        return "Languages: %s · sources: %s" % (", ".join(langs), ", ".join(sources))

    def _ensure_jellyfin(self):
        host = urlparse(self.config.jellyfin_url).hostname
        if host != "jellyfin":
            return "Using your own Jellyfin: its libraries are left as they are"
        folders = self.jellyfin("GET", "/Library/VirtualFolders") or []
        made = []
        for name, kind, path in (("Films", "movies", "/media/movies"), ("TV shows", "tvshows", "/media/tv")):
            if any(f.get("CollectionType") == kind for f in folders):
                continue
            self.jellyfin("POST", "/Library/VirtualFolders", params={"name": name, "collectionType": kind, "paths": path, "refreshLibrary": "true"},
                          json={"LibraryOptions": {}})
            made.append(name)
        return "Added the %s libraries" % " and ".join(made) if made else "Libraries already there"
