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


def _seconds_between(first, second):
    from datetime import datetime
    try:
        parse = lambda stamp: datetime.fromisoformat(stamp.replace("Z", "+00:00")[:25])  # noqa: E731
        return abs((parse(second) - parse(first)).total_seconds())
    except ValueError:
        return 1e9  # can't read the dates? call them "ages apart"


def _dicts(answer):
    """The objects in a list a service sent back, or none: an error page or a lone object isn't a list of releases."""
    return [entry for entry in answer if isinstance(entry, dict)] if isinstance(answer, list) else []


def jellyfin_auth(key):
    """Jellyfin 12 no longer accepts the old X-Emby-Token header; this form works on 10.x too."""
    return {"Authorization": 'MediaBrowser Token="%s"' % key}


def _call(method, url, headers, **kw):
    try:
        reply = requests.request(method, url, headers=headers, timeout=kw.pop("timeout", TIMEOUT), **kw)
        if reply.status_code >= 400:
            detail = ""
            try:
                # the *arrs complain as [{"errorMessage": ...}], everyone else as {"message": ...}. dig out whichever
                body = reply.json()
                if isinstance(body, list) and body and isinstance(body[0], dict):
                    detail = body[0].get("errorMessage") or ""
                elif isinstance(body, dict):
                    detail = body.get("message") or body.get("error") or ""
            except ValueError:
                pass
            raise MediaError("%s %s" % (reply.status_code, detail or reply.reason))
        if not reply.content:
            return None
        try:
            return reply.json()
        except ValueError:
            return reply.text
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
            # poor man's YAML: grab the "auth:" section (up to the next unindented line), then the apikey inside it
            section = re.search(r"^auth:\s*$(.*?)(?=^\S)", text + "\nend:", re.M | re.S)
            found = re.search(r"apikey:\s*'?([0-9a-fA-F]{20,})", section.group(1) if section else text)
        else:
            text = (stack_dir / (service + "-config") / "config.xml").read_text(encoding="utf-8")
            found = re.search(r"<ApiKey>([^<]+)</ApiKey>", text)
        return found.group(1).strip() if found else ""
    except OSError:
        return ""


def _fill(schema, values, only=None):
    """Set the values of a Radarr/Sonarr/Prowlarr settings form (its "fields" list), the ones we know, or just ``only``."""
    for field in schema.get("fields") or []:
        if field.get("name") in values and (only is None or field.get("name") in only):
            field["value"] = values[field["name"]]


def _item_path(kind, arr_id):
    """Radarr's or Sonarr's address for one film or show."""
    return ("movie/%d" if kind == "film" else "series/%d") % arr_id


def _queue_details(kind):
    """What to ask a queue or history list to include, so each entry says which film, show or episode it's for."""
    return {"includeMovie": "true"} if kind == "film" else {"includeSeries": "true", "includeEpisode": "true"}


def _command_is_for(body, arr_id, film):
    """Whether a Radarr or Sonarr command (a search, say) is about this film or show."""
    return arr_id in (body.get("movieIds") or []) if film else body.get("seriesId") == arr_id


def _latest_season(show):
    """The newest season of a show (not the specials), or 1 when Sonarr doesn't list any."""
    numbers = [season["seasonNumber"] for season in (show or {}).get("seasons") or [] if season.get("seasonNumber")]
    return max(numbers) if numbers else 1


def _episodes_label(episodes):
    """Which episodes one download holds: "S01E02", "Season 1", or "Seasons 1–3" for a pack of several."""
    seasons = sorted({episode["seasonNumber"] for episode in episodes if episode.get("seasonNumber") is not None})
    if not seasons:
        return ""
    if len(episodes) == 1:
        return "S%02dE%02d" % (seasons[0], episodes[0].get("episodeNumber") or 0)
    return "Season %d" % seasons[0] if len(seasons) == 1 else "Seasons %d–%d" % (seasons[0], seasons[-1])


def _is_torrent_hash(text):
    # a torrent hash is 40 hex chars (v1) or 64 (v2). anything else is someone poking at us
    return bool(re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", text or ""))


def _schema_for(schemas, implementation, offered_by):
    """The settings form for one kind of thing ("QBittorrent", "Radarr") from a service's list of them."""
    found = next((schema for schema in _dicts(schemas) if schema.get("implementation") == implementation), None)
    if found is None:
        # used to be a bare next(), whose StopIteration sailed past every except and took the whole page down
        raise MediaError("%s does not offer %s here. Is it an older version?" % (offered_by, implementation))
    return found


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
            ip = self.gluetun("/v1/publicip/ip")
            ip = ip if isinstance(ip, dict) else {}
        except MediaError as exc:
            return {"configured": True, "ok": False, "message": "VPN not connected · %s" % exc}
        port = self.forwarded_port()
        where = ", ".join(place for place in (ip.get("city"), ip.get("country")) if place)
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
                answer = self.gluetun(path)
            except MediaError:
                continue
            if not isinstance(answer, dict):
                continue
            port = answer.get("port") or (answer.get("ports") or [0])[0]
            try:
                if port and int(port) > 0:
                    return int(port)
            except (TypeError, ValueError):
                continue
        return 0

    @staticmethod
    def _check(label, configured, version):
        """One service's line on the Admin page: asks version() for its version when it is set up."""
        found = {"label": label, "configured": configured, "ok": False}
        if configured:
            try:
                found["version"] = version()
                found["ok"] = True
            except MediaError as exc:
                found["error"] = str(exc)
            except (AttributeError, KeyError, TypeError, ValueError) as exc:  # answered, but not with what we asked for
                found["error"] = "Unexpected answer: %s" % exc
        return found

    def status(self):
        services = {}
        for kind, label in (("film", "Films (Radarr)"), ("show", "TV shows (Sonarr)")):
            services[KINDS[kind]] = self._check(label, self.configured(kind),
                                                lambda kind=kind: self.arr(kind).get("system/status").get("version"))
        services["bazarr"] = self._check("Subtitles (Bazarr)", bool(self.config.bazarr_url), lambda: (
            (self.bazarr("GET", "system/status") or {}).get("data") or {}).get("bazarr_version"))
        services["jellyfin"] = self._check("Watching (Jellyfin)", bool(self.config.jellyfin_url), lambda: (
            self.jellyfin("GET", "/System/Info/Public") or {}).get("Version"))
        services["vpn"] = self.vpn()
        return services

    # ------------------------------------------------------------ library

    def poster(self, item, kind):
        img = next((image for image in item.get("images") or [] if image.get("coverType") == "poster"), None)
        if item.get("id") and img:
            return "/api/media/poster/%s/%s" % (kind, item["id"])
        if img and img.get("remoteUrl"):
            return "/api/media/poster-remote?url=" + requests.utils.quote(img["remoteUrl"], safe="")
        return ""

    def summary(self, item, kind, requested=None, coming=None):
        stats = item.get("statistics") or {}
        summary = {
            "kind": kind, "id": item.get("id"), "title": item.get("title") or "", "year": item.get("year"),
            "overview": item.get("overview") or "", "runtime": item.get("runtime"),
            "tmdb": item.get("tmdbId"), "tvdb": item.get("tvdbId"), "poster": self.poster(item, kind),
            "added": (item.get("added") or "")[:10], "monitored": bool(item.get("monitored")),
            "genres": (item.get("genres") or [])[:3], "network": item.get("network") or item.get("studio") or "",
            "requested_by": (requested or {}).get(item.get("id")),
        }
        incoming = (coming or {}).get(item.get("id"))
        summary["downloading"] = ({"progress": incoming["progress"], "episodes": incoming["episodes"], "stuck": incoming.get("stuck", False)}
                                  if incoming else None)
        if kind == "film":
            summary["downloaded"] = bool(item.get("hasFile"))
            summary["size"] = item.get("sizeOnDisk") or (item.get("movieFile") or {}).get("size") or 0
            summary["quality"] = (((item.get("movieFile") or {}).get("quality") or {}).get("quality") or {}).get("name", "")
        else:
            have, want = stats.get("episodeFileCount", 0), stats.get("episodeCount", 0)
            summary["downloaded"] = bool(want) and have >= want
            summary["episodes"] = {"have": have, "want": want, "total": stats.get("totalEpisodeCount", 0)}
            summary["size"] = stats.get("sizeOnDisk", 0)
            summary["seasons"] = [{"number": season["seasonNumber"], "monitored": season.get("monitored", False),
                                   "have": (season.get("statistics") or {}).get("episodeFileCount", 0),
                                   "want": (season.get("statistics") or {}).get("episodeCount", 0)}
                                  for season in item.get("seasons") or [] if season.get("seasonNumber", 0) > 0]  # 0 = specials
            summary["ended"] = item.get("status") == "ended"
        return summary

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
        incoming, counted = {}, set()
        for record in records:
            owner = record.get("movieId") if kind == "film" else record.get("seriesId")
            if owner is None:
                continue
            totals = incoming.setdefault(owner, {"size": 0, "left": 0, "episodes": 0, "ids": {}, "stuck": False})
            # Sonarr lists a season pack once per episode, each with the WHOLE download's size: add it up once
            download = (owner, record.get("downloadId") or id(record))
            if download not in counted:
                counted.add(download)
                totals["size"] += record.get("size") or 0
                totals["left"] += record.get("sizeleft") or 0
            totals["episodes"] += 1
            # Downloaded, but Radarr or Sonarr report a problem filing it: it will not arrive by itself.
            if not record.get("sizeleft") and (record.get("trackedDownloadStatus") or "").lower() in ("warning", "error"):
                totals["stuck"] = True
            if record.get("episodeId"):
                size = record.get("size") or 0
                totals["ids"][record["episodeId"]] = int(100 * (size - (record.get("sizeleft") or 0)) / size) if size else 0
        for totals in incoming.values():
            totals["progress"] = int(100 * (totals["size"] - totals["left"]) / totals["size"]) if totals["size"] else 0
        return incoming

    def library(self, kind):
        arr = self.arr(kind)
        items = arr.get("movie" if kind == "film" else "series")
        requested, coming = self.requested_by(kind), self.downloading(kind)
        summaries = [self.summary(item, kind, requested, coming) for item in items]
        summaries.sort(key=lambda summary: summary["added"], reverse=True)
        return summaries

    def item(self, kind, arr_id):
        arr = self.arr(kind)
        return self.summary(arr.get(_item_path(kind, arr_id)), kind, self.requested_by(kind),
                            self.downloading(kind))

    def activity(self, kind, arr_id):
        """What has happened so far for one film or show, oldest first, in plain words.
        Each step: {"at": ISO time or "", "state": "done" | "active" | "bad" | "wait", "text": ...}."""
        arr = self.arr(kind)
        film = kind == "film"
        item = arr.get(_item_path(kind, arr_id))
        title = item.get("title") or ""
        whose = {"movieId": arr_id} if film else {"seriesId": arr_id}

        since, steps = self._request_step(kind, arr_id, item)
        steps += self._search_steps(arr, arr_id, film)
        lines, grabbed_titles = self._indexer_lines(item, title, film, since)
        steps += [self._indexer_step(line) for line in lines]
        history = arr.get("history/movie" if film else "history/series", **whose) or []
        took = [entry for entry in history if entry.get("eventType") == "grabbed"]
        steps += self._history_steps(history, took, grabbed_titles, film)
        queue = arr.get("queue/details", **(whose if film else dict(whose, includeEpisode="true"))) or []
        steps += self._queue_steps(arr, queue, film)

        steps.sort(key=lambda step: step["at"] or "")
        done = item.get("hasFile") if film else (item.get("statistics") or {}).get("percentOfEpisodes", 0) >= 100
        searching = any(step["state"] == "active" for step in steps)
        if done:
            steps.append({"at": "", "state": "done", "stage": 100, "text": "On the server, ready to watch"})
        elif not item.get("monitored") and not searching:
            steps.append({"at": "", "state": "wait", "text": "Stopped looking. Press Start looking again to carry on."})
        elif not searching and not took and not any(step["state"] == "bad" for step in steps):
            steps.append({"at": "", "state": "wait",
                          "text": "No watchable version found yet. %s keeps watching for new releases." % arr.name.title()})
        # the headline for the top of the panel. pecking order: something happening now > something broken > the last step
        current = (next((step for step in reversed(steps) if step["state"] == "active"), None)
                   or (None if done else next((step for step in reversed(steps) if step["state"] == "bad"), None)) or steps[-1])
        now = {"text": current["text"], "state": current["state"],
               "progress": max((step.get("stage", 0) for step in steps if step["state"] != "bad"), default=0)}
        if lines:
            asked = sorted({line["where"] for line in lines})
            had = sorted({line["where"] for line in lines if line["n"] > 0})
            now["sites"] = "Asked %d site%s · %s" % (len(asked), "" if len(asked) == 1 else "s",
                                                    "%d had something (%s)" % (len(had), ", ".join(had)) if had else "none had it")
        # Downloaded but not filed yet: every download for it has finished, and Radarr or Sonarr still has to move
        # it into the library. The corner pop-up shows this as done, with a note, so nothing looks still under way.
        fetched = [entry for entry in queue
                   if (entry.get("status") or "").lower() == "completed" or ((entry.get("size") or 0) and not entry.get("sizeleft"))]
        pending = [entry for entry in queue if entry not in fetched]
        downloaded = bool(not done and fetched and not pending)
        stuck = downloaded and any((entry.get("trackedDownloadStatus") or "").lower() in ("warning", "error") for entry in fetched)
        return {"title": title, "steps": steps, "finished": bool(done), "now": now,
                "downloaded": downloaded, "filing": ("stuck" if stuck else "filing") if downloaded else None}

    def _request_step(self, kind, arr_id, item):
        """(when it was asked for, [the "Requested by" step])."""
        row = self.hub.con().execute("SELECT p.name, r.created FROM media_requests r LEFT JOIN profiles p ON p.id = r.profile_id "
                                     "WHERE r.kind=? AND r.arr_id=? ORDER BY r.id DESC LIMIT 1", (kind, arr_id)).fetchone()
        since = (row[1] if row else "") or item.get("added") or ""
        return since, [{"at": since, "state": "done", "stage": 5,
                        "text": "Requested by %s" % row[0] if row and row[0] else "Added to the list"}]

    def _search_steps(self, arr, arr_id, film):
        """The searches Radarr or Sonarr ran for it."""
        steps = []
        for command in arr.get("command") or []:
            body = command.get("body") or {}
            mine = _command_is_for(body, arr_id, film)
            if not mine or not command.get("name", "").endswith("Search"):
                continue
            running = command.get("status") in ("queued", "started")
            steps.append({"at": command.get("started") or command.get("queued") or "", "state": "active" if running else "done",
                          "stage": 20 if running else 25,
                          "text": ("Searching every indexer…" if running else
                                   "Search finished: %s" % (command.get("message") or "done"))})
        return steps

    def _indexer_lines(self, item, title, film, since):
        """Each indexer Prowlarr asked on this film's or show's behalf, and the releases it grabbed:
        ([{"at", "seen", "n", "where"}], [(when, release, indexer)]). n is the number of results, -1 for no answer."""
        if not (self.config.prowlarr_url and self._key("prowlarr")):
            return [], []
        prowlarr_key = {"X-Api-Key": self._key("prowlarr")}
        try:
            names = {indexer["id"]: indexer["name"] for indexer in _call("GET", self.config.prowlarr_url + "/api/v1/indexer", prowlarr_key) or []}
            records = (_call("GET", self.config.prowlarr_url + "/api/v1/history", prowlarr_key,
                             params={"pageSize": 200, "sortKey": "date", "sortDirection": "descending"}) or {}).get("records") or []
        except MediaError:
            names, records = {}, []
        words = _plain(title)
        ext = str((item.get("tmdbId") if film else item.get("tvdbId")) or "")
        looked, lines, grabbed_titles = {}, [], []
        for record in reversed(records):
            data = record.get("data") or {}
            if data.get("source") not in ("Radarr", "Sonarr") or (since and record.get("date", "") < since[:19]):
                continue
            query = _plain(data.get("query"))
            if record.get("eventType") == "indexerQuery" and (words and words in query or ext and ext in json.dumps(data)):
                where = names.get(record.get("indexerId"), "an indexer")
                results = int(data.get("queryResults") or 0) if record.get("successful") else -1
                # Prowlarr logs every PAGE of results as its own query. squish ones within 15s into one line and add
                # them up (-1 = didn't answer, and it stays -1 only if every page failed)
                last = looked.get(where)
                if last and _seconds_between(last["seen"], record.get("date", "")) < 15:
                    last["n"] = max(last["n"], 0) + max(results, 0) if results >= 0 or last["n"] >= 0 else -1
                    last["seen"] = record.get("date", "")
                    continue
                looked[where] = {"at": record.get("date", ""), "seen": record.get("date", ""), "n": results, "where": where}
                lines.append(looked[where])
            elif record.get("eventType") == "releaseGrabbed" and words and words in _plain(data.get("grabTitle")):
                grabbed_titles.append((record.get("date", ""), data.get("grabTitle"), names.get(record.get("indexerId"), "an indexer")))
        return lines, grabbed_titles

    @staticmethod
    def _indexer_step(line):
        results = line["n"]
        if results < 0:
            return {"at": line["at"], "state": "bad", "stage": 30, "text": "%s did not answer" % line["where"]}
        found = "%d result%s" % (results, "" if results == 1 else "s") if results else "nothing there"
        return {"at": line["at"], "state": "done", "stage": 30, "text": "Looked at %s · %s" % (line["where"], found)}

    def _history_steps(self, history, took, grabbed_titles, film):
        """What it picked, whether the download client took it, and what failed or got filed."""
        steps = []
        for entry in took:
            quality = ((entry.get("quality") or {}).get("quality") or {}).get("name", "")
            steps.append({"at": entry.get("date", ""), "state": "done", "stage": 50,
                          "text": "Best version found: %s%s · from %s" % (entry.get("sourceTitle", ""), " (%s)" % quality if quality else "",
                                                                          (entry.get("data") or {}).get("indexer") or "an indexer")})
        if grabbed_titles and not took:
            # Prowlarr says it grabbed something, Radarr/Sonarr never heard back: nine times out of ten that's
            # qBittorrent refusing the login
            at, name, where = grabbed_titles[-1]
            steps.append({"at": at, "state": "bad",
                          "text": "Picked %s from %s, but qBittorrent refused it. Check its user name and password under "
                                  "Admin, Hub, Connections, then press Connect everything." % (name, where)})
        imported = ("downloadFolderImported", "movieFileImported" if film else "episodeFileImported")
        for entry in history:
            event = entry.get("eventType")
            if event in ("downloadFailed", "importFailed"):
                steps.append({"at": entry.get("date", ""), "state": "bad",
                              "text": "%s: %s" % ("Download failed" if event == "downloadFailed" else "Could not file it",
                                                  (entry.get("data") or {}).get("message") or entry.get("sourceTitle", ""))})
            elif event in imported:
                steps.append({"at": entry.get("date", ""), "state": "done", "stage": 98, "text": "Filed on the server: %s" % entry.get("sourceTitle", "")})
        return steps

    def _queue_steps(self, arr, queue, film):
        """Downloading now. Sonarr lists a whole season in one download once per episode: it is one line here."""
        parked = {torrent_hash.lower() for torrent_hash in self.parked()}
        shared = {}
        for entry in queue:
            shared.setdefault(entry.get("downloadId") or id(entry), []).append(entry.get("episode") or {})
        steps = []
        for key, episodes in shared.items():
            entry = next(queued for queued in queue if (queued.get("downloadId") or id(queued)) == key)
            size, left = entry.get("size") or 0, entry.get("sizeleft") or 0
            percent = int(100 * (size - left) / size) if size else 0
            status = (entry.get("status") or "").lower()
            added = entry.get("added", "") or ""
            which = "" if film else _episodes_label(episodes)
            which = which + ": " if which else ""
            problem = next((message for status_message in entry.get("statusMessages") or [] for message in status_message.get("messages") or []), "")
            if (entry.get("downloadId") or "").lower() in parked:
                steps.append({"at": added, "state": "wait", "progress": percent, "stage": 50,
                              "text": which + "Waiting for seed · nobody is sharing it right now, so it is paused at the back "
                                      "of the line and checked every half hour · %d%%" % percent})
            elif status == "downloading":
                time_left = entry.get("timeleft") if entry.get("timeleft") and entry.get("timeleft") != "00:00:00" else ""
                steps.append({"at": added, "state": "active", "progress": percent, "stage": 50 + percent * 45 // 100,
                              "text": which + "Downloading through the VPN · %d%%%s" % (percent, " · %s left" % time_left if time_left else "")})
            elif status == "completed" and problem and (entry.get("trackedDownloadStatus") or "").lower() in ("warning", "error"):
                # downloaded but can't be filed. usual culprit: qBittorrent saved it somewhere Radarr/Sonarr can't
                # see. Connect everything fixes that, so say so
                hint = (" An admin can press Connect everything under Admin, Hub, Films and TV, which moves it where it can be filed."
                        if "eligible for import" in problem else "")
                steps.append({"at": added, "state": "bad",
                              "text": which + "Downloaded, but %s could not file it: %s.%s" % (arr.name.title(), problem.rstrip("."), hint)})
            else:
                steps.append({"at": added, "state": "bad" if status in QUEUE_TROUBLE else "active", "stage": 50,
                              "text": which + QUEUE_WORDS.get(status, "Waiting in qBittorrent (%s)" % (entry.get("status") or "queued"))})
        return steps

    def lookup(self, kind, term):
        arr = self.arr(kind)
        found = arr.get("movie/lookup" if kind == "film" else "series/lookup", term=term) or []
        external_id = lambda item: item.get("tmdbId") if kind == "film" else item.get("tvdbId")  # noqa: E731
        have = {external_id(owned) for owned in arr.get("movie" if kind == "film" else "series")}
        results = []
        for item in found[:20]:
            summary = self.summary(dict(item, id=None), kind)
            summary["in_library"] = external_id(item) in have
            summary["seasons"] = [{"number": season["seasonNumber"]} for season in item.get("seasons") or [] if season.get("seasonNumber", 0) > 0]
            results.append(summary)
        return results

    # ----------------------------------------------------------- requests

    def _profile_id(self, arr):
        profiles = arr.get("qualityprofile")
        mine = next((profile for profile in profiles if profile["name"] == PROFILE_NAME), None)
        if mine:
            return mine["id"]
        if not profiles:
            raise MediaError("%s has no quality profiles yet." % arr.name.title())
        return profiles[0]["id"]

    def _root(self, arr, wanted):
        roots = arr.get("rootfolder")
        if not roots:
            raise MediaError("%s has no media folder yet. An admin can press Connect everything under Admin, Hub, Films and TV." % arr.name.title())
        return next((root["path"] for root in roots if root["path"].rstrip("/") == wanted), roots[0]["path"])

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
            numbers = sorted(season["seasonNumber"] for season in show.get("seasons") or [] if season.get("seasonNumber", 0) > 0)
            if seasons == "latest":
                keep = set(numbers[-1:])
            elif seasons == "pick":
                keep = {int(number) for number in picked} & set(numbers)
                if not keep:
                    raise MediaError("Choose at least one season.")
            else:
                keep = set(numbers)
            for season in show.get("seasons") or []:
                season["monitored"] = season.get("seasonNumber") in keep
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
            have = next((listed for listed in arr.get(path) or [] if str(listed.get(field)) == str(external_id)), None)
            if not have:
                raise
        return dict(self.summary(have, kind, self.requested_by(kind)), already=True)

    def remove(self, kind, arr_id):
        """Take a film or show off the server, files and all. Returns what was removed."""
        arr = self.arr(kind)
        item = self.item(kind, arr_id)
        arr("DELETE", _item_path(kind, arr_id),
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
        path = _item_path(kind, arr_id)
        item = arr.get(path)
        item["monitored"] = bool(on)
        arr("PUT", path, json=item)
        title = item.get("title") or ""
        if on:
            arr("POST", "command", json={"name": "MoviesSearch", "movieIds": [arr_id]} if film else
                {"name": "SeriesSearch", "seriesId": arr_id})
            return "Looking for %s again." % title
        for command in arr.get("command") or []:
            body = command.get("body") or {}
            mine = _command_is_for(body, arr_id, film)
            if mine and command.get("status") == "queued":
                try:
                    arr("DELETE", "command/%d" % command["id"])
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
                season = _latest_season(arr.get("series/%d" % arr_id))
            query = {"seriesId": arr_id, "seasonNumber": season}
        found = _dicts(arr("GET", "release", params=query, timeout=SEARCH_TIMEOUT))
        offered = [{
            "guid": release.get("guid"), "indexer_id": release.get("indexerId"), "title": release.get("title"),
            "quality": ((release.get("quality") or {}).get("quality") or {}).get("name", ""), "size": release.get("size") or 0,
            "indexer": release.get("indexer"), "seeders": release.get("seeders"), "rejected": bool(release.get("rejected")),
            "why": (release.get("rejections") or [""])[0],
        } for release in found]
        # best first means the household order: 2160p, then 1080p, ... and the most seeders within each
        offered.sort(key=lambda release: (release["rejected"], -QUALITY_RANK.get(_quality_from_name(release["quality"]), 0),
                                          -(release["seeders"] or 0)))
        return offered[:60]

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
        item = arr.get(_item_path(kind, arr_id))
        if not film and season is None:
            season = _latest_season(item)
        query = "%s %s" % (item.get("title") or "", item.get("year") or "") if film else "%s S%02d" % (item.get("title") or "", season)
        prowlarr_key = {"X-Api-Key": self._key("prowlarr")} if self.config.prowlarr_url and self._key("prowlarr") else None
        sites = []
        if prowlarr_key:
            try:
                sites = [(indexer["id"], indexer["name"]) for indexer in _call("GET", self.config.prowlarr_url + "/api/v1/indexer", prowlarr_key) or []
                         if indexer.get("enable")]
            except MediaError:
                sites = []
        job = {"id": uuid.uuid4().hex, "kind": kind, "arr_id": arr_id, "season": season, "started": _time.time(),
               "sites": len(sites), "answered": 0, "found": {}, "checked": None, "checked_error": "", "lock": threading.Lock()}
        _clear_old_jobs()
        _JOBS[job["id"]] = job

        def ask(site):
            # one thread per site, all writing into the same job dict, hence the lock. 2000 = movies, 5000 = TV (newznab)
            try:
                rows = _call("GET", self.config.prowlarr_url + "/api/v1/search", prowlarr_key, timeout=SEARCH_TIMEOUT, params={
                    "query": query.strip(), "type": "search", "indexerIds": site[0], "categories": 2000 if film else 5000, "limit": 100})
            except MediaError:
                rows = []
            with job["lock"]:
                job["answered"] += 1
                for release in _dicts(rows):
                    link = release.get("magnetUrl") or release.get("downloadUrl")
                    if link and release.get("guid") and release["guid"] not in job["found"]:
                        job["found"][release["guid"]] = {
                            "guid": release["guid"], "title": release.get("title") or "", "size": release.get("size") or 0,
                            "seeders": release.get("seeders"), "indexer": release.get("indexer") or site[1],
                            "quality": _quality_from_name(release.get("title") or ""),
                            "link": link, "published": release.get("publishDate") or ""}

        def checked():
            try:
                found = self.releases(kind, arr_id, season)
            except Exception as exc:  # anything at all, or the search never says it's done
                job["checked_error"] = str(exc) if isinstance(exc, MediaError) else "Unexpected answer: %s" % exc
                found = []
            job["checked"] = found

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
            found = sorted(job["found"].values(), key=lambda release: (-QUALITY_RANK.get(release["quality"], 0), -(release["seeders"] or 0)))
            progress = {"sites": job["sites"], "answered": job["answered"], "seconds": int(_time.time() - job["started"]),
                        # the download link stays on the server: the page only ever gets a guid to point at
                        "found": [{field: value for field, value in release.items() if field != "link"} for release in found[:150]],
                        "checked": job["checked"], "checked_error": job["checked_error"]}
        progress["done"] = job["checked"] is not None and job["answered"] >= job["sites"]
        return progress

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
        session = integrations.qbit_session(self.config)
        category = "movies" if kind == "film" else "tv"
        link = found["link"]
        add = self.config.qbit_url + "/api/v2/torrents/add"
        try:
            if link.startswith("magnet:"):
                reply = session.post(add, data={"urls": link, "category": category}, timeout=30)
            else:
                # we fetch the .torrent ourselves: qBittorrent lives inside the VPN box and can't reach the indexer's
                # link directly. some indexers "download" by redirecting to a magnet, so catch that too
                body = requests.get(link, timeout=60, allow_redirects=False)
                if body.is_redirect and (body.headers.get("Location") or "").startswith("magnet:"):
                    reply = session.post(add, data={"urls": body.headers["Location"], "category": category}, timeout=30)
                else:
                    body.raise_for_status()
                    reply = session.post(add, data={"category": category},
                                         files={"torrents": ("release.torrent", body.content, "application/x-bittorrent")}, timeout=30)
        except (integrations.IntegrationError, requests.RequestException) as exc:
            raise MediaError("Could not send it to qBittorrent: %s" % exc)
        if reply.status_code >= 400 or "fail" in (reply.text or "").lower():
            raise MediaError("qBittorrent would not take it (%s)." % reply.status_code)
        return {"ok": True, "direct": True}

    def queue(self):
        """What is downloading now for films and TV, with who asked for it."""
        items, problems = [], []
        for kind in ("film", "show"):
            if not self.configured(kind):
                continue
            try:
                arr = self.arr(kind)
                extra = _queue_details(kind)
                records = (arr.get("queue", pageSize=200, **extra) or {}).get("records", [])
            except MediaError as exc:
                problems.append("%s: %s" % ("Films" if kind == "film" else "TV shows", exc))
                continue
            requested = self.requested_by(kind)
            groups, counted = {}, set()
            for record in records:
                # a film is one line; a show gets one line per season (Sonarr lists every episode separately)
                if kind == "film":
                    group_key, title = ("film", record.get("movieId")), (record.get("movie") or {}).get("title") or record.get("title")
                    owner = record.get("movieId")
                else:
                    season = (record.get("episode") or {}).get("seasonNumber")
                    group_key = ("show", record.get("seriesId"), season)
                    show_title = (record.get("series") or {}).get("title") or record.get("title")
                    title = "%s · Season %s" % (show_title, season) if season is not None else show_title
                    owner = record.get("seriesId")
                group = groups.setdefault(group_key, {"kind": kind, "title": title, "size": 0, "left": 0, "items": 0,
                                                      "status": record.get("trackedDownloadState") or record.get("status") or "",
                                                      "requested_by": requested.get(owner), "eta": record.get("estimatedCompletionTime")})
                # every episode of a season pack carries the whole download's size: count each download once
                download = (group_key, record.get("downloadId") or id(record))
                if download not in counted:
                    counted.add(download)
                    group["size"] += record.get("size") or 0
                    group["left"] += record.get("sizeleft") or 0
                group["items"] += 1
                messages = [message for status in record.get("statusMessages") or [] for message in status.get("messages") or []]
                if messages:
                    group["problem"] = messages[0]
            for group in groups.values():
                group["progress"] = round(100 * (1 - group["left"] / group["size"])) if group["size"] else 0
                items.append(group)
        return {"items": items, "problems": problems}

    def _need_qbit(self):
        if not self.config.qbit_url:
            raise MediaError("qBittorrent is not set up yet. An admin can add it under Admin, Hub, Connections.")

    def torrents(self):
        """Everything in qBittorrent (films, TV and books), unfinished ones first in queue order, for the Downloads page."""
        from . import integrations
        self._need_qbit()
        try:
            session = integrations.qbit_session(self.config)
            found = _dicts(session.get(self.config.qbit_url + "/api/v2/torrents/info", timeout=8).json())
        except (integrations.IntegrationError, requests.RequestException, ValueError) as exc:
            raise MediaError("Could not reach qBittorrent: %s" % exc)
        # Radarr and Sonarr know which film or show each download is for, and who asked for it.
        names = {}
        for kind in ("film", "show"):
            if not self.configured(kind):
                continue
            try:
                extra = _queue_details(kind)
                records = (self.arr(kind).get("queue", pageSize=200, **extra) or {}).get("records", [])
            except MediaError:
                continue
            requested = self.requested_by(kind)
            episodes = {}
            for record in records:
                episodes.setdefault((record.get("downloadId") or "").lower(), []).append(record.get("episode") or {})
            for record in records:
                key = (record.get("downloadId") or "").lower()  # the downloadId IS qBittorrent's hash, in caps
                owner = record.get("movieId") if kind == "film" else record.get("seriesId")
                title = ((record.get("movie") if kind == "film" else record.get("series")) or {}).get("title")
                which = _episodes_label(episodes[key]) if kind == "show" else ""
                if title and which:
                    title += " · " + which  # one episode, a whole season, or several in one download
                names[key] = {"title": title, "kind": kind, "id": owner, "requested_by": requested.get(owner)}
        books = self.config.qbit_category or "books"
        asked = {}
        for row in self.hub.con().execute("SELECT r.title, p.name FROM requests r LEFT JOIN profiles p ON p.id = r.profile_id ORDER BY r.id"):
            asked[_plain(row[0])] = row[1]
        from . import bookswap
        parked, given_up = self.parked(), bookswap.given_up(self.hub)
        listed = []
        for torrent in found:
            torrent_hash = torrent.get("hash") or ""
            known = names.get(torrent_hash.lower(), {})
            if torrent.get("category") == books:
                known = {"requested_by": asked.get(_plain(torrent.get("name")))}
            state = torrent.get("state") or ""
            done = (torrent.get("progress") or 0) >= 1
            waiting = parked.get(torrent_hash) if not done else None
            words = TORRENT_WORDS.get(state, state)
            if waiting:
                words = "Waiting for seed · checking for someone sharing it" if waiting.get("checking") else "Waiting for seed"
            if torrent_hash in given_up and not done:
                words = "Paused · no shared copy found"
            listed.append({
                "hash": torrent_hash, "name": torrent.get("name") or "", "title": known.get("title") or torrent.get("name") or "",
                "kind": known.get("kind"), "id": known.get("id"), "requested_by": known.get("requested_by"),
                "type": "Book" if torrent.get("category") == books else TORRENT_KINDS.get(torrent.get("category") or "", "Other"),
                "progress": int((torrent.get("progress") or 0) * 100), "done": done, "state": words,
                "paused": state.startswith(("paused", "stopped")), "trouble": state in ("error", "missingFiles"),
                "missing": state == "missingFiles", "gave_up": torrent_hash in given_up and not done,
                "size": torrent.get("size") or 0, "speed": torrent.get("dlspeed") or 0, "up_speed": torrent.get("upspeed") or 0,
                # qBittorrent's "no idea" ETA is 8640000 (100 days). we'd rather say nothing
                "eta": torrent.get("eta") if 0 < (torrent.get("eta") or 0) < 8640000 else None,
                "seeds": torrent.get("num_seeds") or 0, "position": torrent.get("priority") if (torrent.get("priority") or 0) > 0 else None,
                # Waiting for seed: when it was last checked, and when the next check is.
                "waiting_seed": bool(waiting), "checked": waiting and waiting.get("checked"),
                "next_check": waiting and not waiting.get("checking") and waiting.get("checked", 0) + CHECK_EVERY_MINUTES * 60,
            })
        # Waiting for seed goes after everything else still to download, as it is in qBittorrent's line.
        listed.sort(key=lambda entry: (entry["done"], entry["waiting_seed"], entry["position"] is None, entry["position"] or 0))
        return listed

    def parked(self):
        """The downloads waiting for seed: {hash: {"since", "checked", "checking", "progress"}}."""
        return self._kept("media:parked")

    def _kept(self, key):
        """A {hash: ...} note kept between minutes; a damaged one starts again empty rather than stopping the Downloads page."""
        try:
            kept = json.loads(db.kv_get(self.hub.con(), key) or "{}")
        except ValueError:
            return {}
        return kept if isinstance(kept, dict) else {}

    def _start_stop(self, session, hashes, start):
        """qBittorrent 5 calls these start and stop; older versions resume and pause."""
        base, form = self.config.qbit_url + "/api/v2/torrents/", {"hashes": "|".join(hashes)}
        reply = session.post(base + ("start" if start else "stop"), data=form, timeout=8)
        if reply.status_code == 404:  # old qBittorrent, old words
            reply = session.post(base + ("resume" if start else "pause"), data=form, timeout=8)
        return reply

    def _queue_move(self, session, hashes, where):
        """Move downloads in qBittorrent's line: where is topPrio, increasePrio, decreasePrio or bottomPrio."""
        path, form = self.config.qbit_url + "/api/v2/torrents/" + where, {"hashes": "|".join(hashes)}
        reply = session.post(path, data=form, timeout=8)
        if reply.status_code == 409:  # the queue is switched off: switch it on, then try again
            session.post(self.config.qbit_url + "/api/v2/app/setPreferences", data={"json": json.dumps(QUEUE_PREFS)}, timeout=8)
            reply = session.post(path, data=form, timeout=8)
        return reply

    def park_stalled(self, now=None):
        """Run every minute by the background jobs. Books and graphic novels only: films and TV are left to Radarr
        and Sonarr, which give up on a stuck download and look for another copy themselves. A book download nobody
        has shared for STALL_MINUTES goes to the back of the line and is paused, so it does not hold up the rest. Every CHECK_EVERY_MINUTES it is started
        for CHECK_FOR_MINUTES to look for someone sharing it: if it finds someone it carries on from the back of
        the line, otherwise it is paused again. Pressing Continue on one takes it out of this."""
        from . import integrations
        if not self.config.qbit_url:
            return
        now = now or _time.time()
        con = self.hub.con()
        stalled = self._kept("media:stalled")  # hash -> when it was first seen with nobody sharing
        parked = self.parked()
        try:
            session = integrations.qbit_session(self.config)
            found = {torrent.get("hash"): torrent for torrent in _dicts(session.get(self.config.qbit_url + "/api/v2/torrents/info", timeout=8).json())}
        except (integrations.IntegrationError, requests.RequestException, ValueError):
            return
        from . import bookswap
        books = self.config.qbit_category or "books"
        given_up = bookswap.given_up(self.hub)  # paused for good after no other copy was found: not woken
        # five to-do lists, filled first and acted on at the end, so a qBittorrent hiccup half way changes nothing
        park, wake, sleep, free, handed_back = [], [], [], [], []
        # part 1: the ones already parked. still nobody? time for their half-hourly peek?
        for torrent_hash in list(parked):
            torrent = found.get(torrent_hash)
            if not torrent or (torrent.get("progress") or 0) >= 1 or torrent_hash in given_up:
                parked.pop(torrent_hash)
                continue
            if torrent.get("category") != books:  # parked before films and TV were left to Radarr and Sonarr
                parked.pop(torrent_hash)
                handed_back.append(torrent_hash)
                continue
            watch, state = parked[torrent_hash], torrent.get("state") or ""
            if watch.get("checking"):
                found_someone = ((torrent.get("num_seeds") or 0) > 0 or (torrent.get("dlspeed") or 0) > 0
                                 or (torrent.get("progress") or 0) > watch.get("progress", 0))
                if found_someone:
                    free.append(torrent_hash)  # someone is sharing it again
                elif now - watch["checking"] >= CHECK_FOR_MINUTES * 60:
                    sleep.append(torrent_hash)
                    watch.update(checking=None, checked=now)
            elif not state.startswith(("paused", "stopped")):
                parked.pop(torrent_hash)  # started again by hand, or by qBittorrent: it is back to normal
                stalled[torrent_hash] = now
            elif now - watch.get("checked", 0) >= CHECK_EVERY_MINUTES * 60:
                wake.append(torrent_hash)
                watch.update(checking=now, progress=torrent.get("progress") or 0)
        for torrent_hash in free:
            parked.pop(torrent_hash)
        # part 2: everyone else. been stuck with nobody sharing for STALL_MINUTES? off to the back of the line
        for torrent_hash, torrent in found.items():
            if torrent_hash in parked or (torrent.get("progress") or 0) >= 1 or torrent.get("category") != books or torrent_hash in given_up:
                stalled.pop(torrent_hash, None)
                continue
            state = torrent.get("state") or ""
            nobody = state == "stalledDL" or (state == "metaDL" and not (torrent.get("num_seeds") or 0) and not (torrent.get("num_leechs") or 0))
            if not nobody:
                stalled.pop(torrent_hash, None)
            elif now - stalled.setdefault(torrent_hash, now) >= STALL_MINUTES * 60:
                park.append(torrent_hash)
                parked[torrent_hash] = {"since": now, "checked": now, "checking": None, "progress": torrent.get("progress") or 0}
                stalled.pop(torrent_hash)
        stalled = {torrent_hash: at for torrent_hash, at in stalled.items() if torrent_hash in found}
        force_start = self.config.qbit_url + "/api/v2/torrents/setForceStart"
        try:
            if park:
                self._queue_move(session, park, "bottomPrio")
                self._start_stop(session, park, False)
            if wake:  # FORCE started, otherwise qBittorrent's queue leaves it sitting at the back doing nothing
                session.post(force_start, data={"hashes": "|".join(wake), "value": "true"}, timeout=8)
            if sleep or free or handed_back:
                session.post(force_start, data={"hashes": "|".join(sleep + free + handed_back), "value": "false"}, timeout=8)
            if handed_back:
                self._start_stop(session, handed_back, True)
            if sleep:
                self._start_stop(session, sleep, False)
        except requests.RequestException:
            return  # nothing is saved, so the same is tried next minute
        db.kv_set(con, "media:stalled", json.dumps(stalled))
        db.kv_set(con, "media:parked", json.dumps(parked))

    def unpark(self, torrent_hash):
        """Someone pressed Continue or moved a download waiting for seed: it is theirs to manage again."""
        from . import bookswap
        bookswap.start_over(self.hub, torrent_hash)
        con = self.hub.con()
        parked = self.parked()
        if parked.pop(torrent_hash, None) is not None:
            db.kv_set(con, "media:parked", json.dumps(parked))
            stalled = self._kept("media:stalled")
            stalled[torrent_hash] = _time.time()  # the ten minutes start again
            db.kv_set(con, "media:stalled", json.dumps(stalled))

    def forget(self, hashes):
        """Take downloads off qBittorrent's list, leaving their files where they are: for downloads whose files were
        moved by hand (qBittorrent says "Files are missing"). Returns how many went."""
        from . import integrations
        self._need_qbit()
        hashes = [torrent_hash for torrent_hash in hashes if _is_torrent_hash(torrent_hash)]
        if not hashes:
            return 0
        try:
            session = integrations.qbit_session(self.config)
            reply = session.post(self.config.qbit_url + "/api/v2/torrents/delete", data={"hashes": "|".join(hashes), "deleteFiles": "false"}, timeout=8)
        except (integrations.IntegrationError, requests.RequestException) as exc:
            raise MediaError("Could not reach qBittorrent: %s" % exc)
        if reply.status_code >= 400:
            raise MediaError("qBittorrent said no (%s)." % reply.status_code)
        return len(hashes)

    def speed(self):
        """qBittorrent's speeds now and its limits, all in bytes per second (0 means no limit). "Slow mode" is
        qBittorrent's second set of limits, switched on and off with one press."""
        from . import integrations
        self._need_qbit()
        try:
            session = integrations.qbit_session(self.config)
            prefs = session.get(self.config.qbit_url + "/api/v2/app/preferences", timeout=8).json()
            info = session.get(self.config.qbit_url + "/api/v2/transfer/info", timeout=8).json()
            slow = session.get(self.config.qbit_url + "/api/v2/transfer/speedLimitsMode", timeout=8).text.strip() == "1"
            found = _dicts(session.get(self.config.qbit_url + "/api/v2/torrents/info", timeout=8).json())
        except (integrations.IntegrationError, requests.RequestException, ValueError) as exc:
            raise MediaError("Could not reach qBittorrent: %s" % exc)
        if not (isinstance(prefs, dict) and isinstance(info, dict)):
            raise MediaError("Could not reach qBittorrent: it sent back something unexpected.")
        # How many downloads are doing what, so the Speed panel can say why the speed is what it is.
        counts = {"downloading": 0, "waiting_turn": 0, "waiting_seed": 0, "paused": 0, "done": 0}
        parked = self.parked()
        for torrent in found:
            state = torrent.get("state") or ""
            if (torrent.get("progress") or 0) >= 1:
                counts["done"] += 1
            elif torrent.get("hash") in parked or state == "stalledDL":
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
            session = integrations.qbit_session(self.config)
            if prefs:
                session.post(self.config.qbit_url + "/api/v2/app/setPreferences", data={"json": json.dumps(prefs)}, timeout=8)
            if "slow_mode" in values:
                # qBittorrent only has "toggle", no "set". so look first, and only flip it if it's the wrong way round
                now = session.get(self.config.qbit_url + "/api/v2/transfer/speedLimitsMode", timeout=8).text.strip() == "1"
                if now != bool(values["slow_mode"]):
                    session.post(self.config.qbit_url + "/api/v2/transfer/toggleSpeedLimitsMode", timeout=8)
        except (integrations.IntegrationError, requests.RequestException) as exc:
            raise MediaError("Could not reach qBittorrent: %s" % exc)
        return self.speed()

    def torrent_action(self, torrent_hash, action):
        """Pause, resume or move a download in qBittorrent's queue."""
        from . import integrations
        self._need_qbit()
        if action not in TORRENT_ACTIONS or not _is_torrent_hash(torrent_hash):
            raise MediaError("That is not something a download can do.")
        try:
            session = integrations.qbit_session(self.config)
            if action in ("pause", "resume"):
                reply = self._start_stop(session, [torrent_hash], action == "resume")
            else:
                moves = {"top": "topPrio", "up": "increasePrio", "down": "decreasePrio", "bottom": "bottomPrio"}
                reply = self._queue_move(session, [torrent_hash], moves[action])
        except (integrations.IntegrationError, requests.RequestException) as exc:
            raise MediaError("Could not reach qBittorrent: %s" % exc)
        if reply.status_code >= 400:
            raise MediaError("qBittorrent said no (%s)." % reply.status_code)
        if action == "resume":
            self.unpark(torrent_hash)
        return True

    def book_status(self, title, torrents=None):
        """Where a book sent from Discover has got to, in the same shape as "What's happening" for films:
        waiting for qBittorrent, downloading, then added to the library by Family Hub's intake."""
        torrents = self.torrents() if torrents is None else torrents
        want = _plain(title)
        download = next((torrent for torrent in torrents
                         if torrent["type"] == "Book" and want and (_plain(torrent["name"]) == want or want in _plain(torrent["name"]))), None)
        if download and not download["done"]:
            text = "Downloading through the VPN · %d%%" % download["progress"] if download["state"] == "Downloading" else download["state"]
            # the bar runs 10 -> 90 while downloading; the last 10% is the library filing it
            return {"title": title, "finished": False,
                    "now": {"text": text, "state": "bad" if download["trouble"] else "active", "progress": 10 + download["progress"] * 80 // 100}}
        if self._book_filed(title):
            return {"title": title, "finished": True, "now": {"text": "In the library", "state": "done", "progress": 100}}
        if download:
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
            # integer maths for "at least 60% of the words, rounded up": (n*6 + 9) // 10
            if len(words & set(name.split())) >= max(1, (len(words) * 6 + 9) // 10):
                return True
        return False

    def search_progress(self, query, seconds_ago):
        """How far a search that started seconds_ago has got: Prowlarr logs each site as it answers.
        Works for Discover book searches and for picking a version of a film or show."""
        from datetime import datetime, timedelta, timezone
        if not (self.config.prowlarr_url and self._key("prowlarr")):
            return {"sites": 0, "answered": 0, "names": []}
        prowlarr_key = {"X-Api-Key": self._key("prowlarr")}
        names = {indexer["id"]: indexer["name"] for indexer in _call("GET", self.config.prowlarr_url + "/api/v1/indexer", prowlarr_key) or []
                 if indexer.get("enable")}
        records = (_call("GET", self.config.prowlarr_url + "/api/v1/history", prowlarr_key,
                         params={"pageSize": 100, "sortKey": "date", "sortDirection": "descending",
                                 "eventType": 2}) or {}).get("records") or []  # 2 = a site was asked
        # A little slack for the two computers' clocks and for the search reaching Prowlarr.
        since = datetime.now(timezone.utc) - timedelta(seconds=max(0, seconds_ago) + 5)
        words = _plain(query)
        answered = []
        for record in records:
            try:
                # strip the fractional seconds first: older pythons choke on 7-digit fractions, which .NET loves
                at = datetime.fromisoformat(re.sub(r"\.\d+", "", record.get("date", "")).replace("Z", "+00:00"))
            except ValueError:
                continue
            if at.tzinfo is None:
                at = at.replace(tzinfo=timezone.utc)
            if at < since or record.get("eventType") not in ("indexerQuery", None) or record.get("indexerId") not in names:
                continue
            asked = _plain((record.get("data") or {}).get("query"))
            if words and (words in asked or asked in words) and names[record["indexerId"]] not in answered:
                answered.append(names[record["indexerId"]])
        return {"sites": len(names), "answered": min(len(answered), len(names)), "names": answered[:3]}

    def delete_download(self, torrent_hash):
        """Delete a download everywhere: from qBittorrent with its files and, when it is for a film or show,
        that film or show from the server too (Radarr or Sonarr, its files, and so Jellyfin). Returns what went."""
        from . import integrations
        found = next((torrent for torrent in self.torrents() if torrent["hash"].lower() == (torrent_hash or "").lower()), None)
        if not found:
            raise MediaError("That download is not in qBittorrent any more.")
        try:
            session = integrations.qbit_session(self.config)
            reply = session.post(self.config.qbit_url + "/api/v2/torrents/delete",
                                 data={"hashes": found["hash"], "deleteFiles": "true"}, timeout=8)
        except (integrations.IntegrationError, requests.RequestException) as exc:
            raise MediaError("Could not reach qBittorrent: %s" % exc)
        if reply.status_code >= 400:
            raise MediaError("qBittorrent said no (%s)." % reply.status_code)
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
        filed = []
        for kind in ("film", "show"):
            if not self.configured(kind):
                continue
            extra = _queue_details(kind)
            try:
                arr = self.arr(kind)
                # eventType 3 = "downloadFolderImported", i.e. it landed in the library
                records = (arr.get("history", pageSize=limit, sortKey="date", sortDirection="descending",
                                   eventType=3, **extra) or {}).get("records", [])
            except MediaError:
                continue
            requested = self.requested_by(kind)
            for record in records:
                if kind == "film":
                    title, owner = (record.get("movie") or {}).get("title") or record.get("sourceTitle"), record.get("movieId")
                else:
                    episode = record.get("episode") or {}
                    title = "%s · S%02dE%02d" % ((record.get("series") or {}).get("title") or "", episode.get("seasonNumber") or 0,
                                                 episode.get("episodeNumber") or 0)
                    owner = record.get("seriesId")
                filed.append({"kind": kind, "title": title, "at": record.get("date"), "requested_by": requested.get(owner)})
        filed.sort(key=lambda entry: entry["at"] or "", reverse=True)
        return filed[:limit]

    # ------------------------------------------------------------ Jellyfin

    def latest(self, limit=12):
        """The newest films and shows on the family server, as Jellyfin sees them."""
        items = (self.jellyfin("GET", "/Items", params={
            "Recursive": "true", "IncludeItemTypes": "Movie,Series", "SortBy": "DateCreated", "SortOrder": "Descending",
            "Limit": limit, "Fields": "ProductionYear,DateCreated,Overview"}) or {}).get("Items", [])
        link = self.config.jellyfin_link
        return [{
            "id": item["Id"], "title": item.get("Name") or "", "year": item.get("ProductionYear"),
            "kind": "film" if item.get("Type") == "Movie" else "show",
            "added": (item.get("DateCreated") or "")[:10], "poster": "/api/media/jellyfin-image/" + item["Id"],
            "watch": (link + "/web/#/details?id=" + item["Id"]) if link else "",
        } for item in items]

    def _jellyfin_match(self, kind, item):
        """Jellyfin's id for a film or show, matched by its TMDB or TVDB number, or None."""
        provider, number = ("Tmdb", item.get("tmdbId")) if kind == "film" else ("Tvdb", item.get("tvdbId"))
        found = (self.jellyfin("GET", "/Items", params={
            "Recursive": "true", "IncludeItemTypes": "Movie" if kind == "film" else "Series",
            "searchTerm": item.get("title") or "", "Fields": "ProviderIds", "Limit": 20}) or {}).get("Items", [])
        for candidate in found:
            # str(number or "-"): with no number we compare against "-", which no real id ever is
            if str((candidate.get("ProviderIds") or {}).get(provider) or "") == str(number or "-"):
                return candidate["Id"]
        # A different spelling of the name: fall back to the only result, if there is just one.
        return found[0]["Id"] if len(found) == 1 else None

    def watch(self, kind, arr_id):
        """Where to watch a film, or each episode of a show, in Jellyfin. Episodes come from Sonarr, so ones not
        downloaded yet are listed too. {"jellyfin": {"link", "port"}, "watch_id"} or {..., "seasons": [...]}."""
        arr = self.arr(kind)
        item = arr.get(_item_path(kind, arr_id))
        where = {"jellyfin": {"link": self.config.jellyfin_link, "port": self.config.service_ports["jellyfin"]}}
        try:
            jellyfin_id = self._jellyfin_match(kind, item)
        except MediaError:
            jellyfin_id = None  # Jellyfin not set up or not answering: still list the episodes
        if kind == "film":
            where["watch_id"] = jellyfin_id
            return where
        ids = {}
        if jellyfin_id:
            try:
                for episode in (self.jellyfin("GET", "/Shows/%s/Episodes" % jellyfin_id) or {}).get("Items", []):
                    ids[(episode.get("ParentIndexNumber"), episode.get("IndexNumber"))] = episode["Id"]
            except MediaError:
                pass
        seasons = {}
        coming = (self.downloading("show").get(arr_id) or {}).get("ids", {})
        for episode in arr.get("episode", seriesId=arr_id) or []:
            if not episode.get("seasonNumber"):
                continue  # specials
            seasons.setdefault(episode["seasonNumber"], []).append({
                "number": episode.get("episodeNumber"), "title": episode.get("title") or "", "aired": (episode.get("airDate") or "")[:10],
                "on_server": bool(episode.get("hasFile")), "wanted": bool(episode.get("monitored")), "downloading": coming.get(episode.get("id")),
                "watch_id": ids.get((episode["seasonNumber"], episode.get("episodeNumber"))),
            })
        where["seasons"] = [{"number": number, "episodes": sorted(episodes, key=lambda episode: episode["number"] or 0)}
                            for number, episodes in sorted(seasons.items())]
        return where

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
            except (KeyError, TypeError, ValueError, AttributeError, requests.RequestException) as exc:
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
            steps.append(("API keys", True, "Read from the services' own settings: " + ", ".join(name.split("_")[0].title() for name in found)))

        # First, so the download-client steps below already use qBittorrent's new sign-in.
        if self.config.services_user and self.config.services_pass:
            step("Sign-in for each program's page", self._ensure_logins)
        for kind in ("film", "show"):
            if not self.configured(kind):
                steps.append((("Films" if kind == "film" else "TV shows"), False, "Not set up: no address under Connections."))
                continue
            label = "Radarr" if kind == "film" else "Sonarr"
            # kind=kind freezes each lambda's kind; without it all four would see whatever kind the loop ended on
            step(label + " → media folder", lambda kind=kind: self._ensure_root(kind))
            step(label + " → quality (2160p first)", lambda kind=kind: self._ensure_profile(kind))
            step(label + " → qBittorrent", lambda kind=kind: self._ensure_client(kind))
            step("Prowlarr → " + label, lambda kind=kind: self._ensure_prowlarr_app(kind))
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
            session = integrations.qbit_session(self.config)  # signed in with the old sign-in
            reply = session.post(self.config.qbit_url + "/api/v2/app/setPreferences",
                                 data={"json": json.dumps({"web_ui_username": user, "web_ui_password": password})}, timeout=8)
            if reply.status_code >= 400:
                raise MediaError("qBittorrent would not change its sign-in (%s)." % reply.status_code)
            settings.save(self.hub, {"qbit_user": user, "qbit_pass": password})  # so Family Hub keeps getting in
            done.append("qBittorrent")
        elif self.config.qbit_url:
            done.append("qBittorrent")
        return "User name %s on %s" % (user, ", ".join(done)) if done else "No programs set up yet"

    def _ensure_root(self, kind):
        arr, path = self.arr(kind), FILMS_ROOT if kind == "film" else TV_ROOT
        if any(root["path"].rstrip("/") == path for root in arr.get("rootfolder")):
            return "Already set: " + path
        arr("POST", "rootfolder", json={"path": path})
        return "Added " + path

    def _ensure_profile(self, kind):
        arr = self.arr(kind)
        schema = arr.get("qualityprofile/schema")
        items = schema["items"]

        def quality_of(item):
            """An item is either one quality or a group of them; either way, a list."""
            quality = item.get("quality") or {}
            return [quality] if quality else [inner["quality"] for inner in item.get("items") or [] if inner.get("quality")]

        def skipped(name):
            return any(skip in name.lower() for skip in SKIP_QUALITIES)

        def wanted(item):
            qualities = quality_of(item)
            name = item.get("name") or (qualities[0]["name"] if qualities else "")
            return bool(qualities) and not skipped(name) and all(
                (quality.get("resolution") in RESOLUTIONS or quality["name"] == "DVD") and not skipped(quality["name"])
                for quality in qualities)

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
        existing = next((profile for profile in arr.get("qualityprofile") if profile["name"] == PROFILE_NAME), None)
        if existing:
            arr("PUT", "qualityprofile/%d" % existing["id"], json=dict(body, id=existing["id"]))
            return "Updated the Family Hub profile"
        arr("POST", "qualityprofile", json=body)
        return "Made the Family Hub profile: 2160p, then 1080p, then 720p, then the best there is, no remux"

    def _qbit_address(self):
        address = urlparse(self.config.qbit_url)
        return address.hostname, address.port or (443 if address.scheme == "https" else 80), address.scheme == "https"

    def _ensure_client(self, kind):
        arr = self.arr(kind)
        if not self.config.qbit_url:
            raise MediaError("qBittorrent's address is not set under Connections.")
        host, port, ssl = self._qbit_address()
        values = {"host": host, "port": port, "useSsl": ssl, "username": self.config.qbit_user, "password": self.config.qbit_pass,
                  "movieCategory": "movies", "tvCategory": "tv"}
        existing = next((client for client in arr.get("downloadclient") if client.get("implementation") == "QBittorrent"), None)
        if existing:
            # Keep its address and sign-in in step with Connections, so a changed qBittorrent password reaches it too.
            _fill(existing, values, only=("host", "port", "useSsl", "username", "password"))
            arr("PUT", "downloadclient/%d" % existing["id"], params={"forceSave": "true"}, json=existing)
            return "Already connected · sign-in brought up to date"
        schema = _schema_for(arr.get("downloadclient/schema"), "QBittorrent", arr.name.title())
        _fill(schema, values)
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
        if any(app.get("implementation") == impl for app in apps):
            return "Already connected"
        schema = _schema_for(_call("GET", self.config.prowlarr_url + "/api/v1/applications/schema", headers), impl, "Prowlarr")
        arr = self.arr(kind)
        _fill(schema, {"prowlarrUrl": self.config.prowlarr_url, "baseUrl": arr.url, "apiKey": arr.key})
        _call("POST", self.config.prowlarr_url + "/api/v1/applications", headers, params={"forceSave": "true"},
              json=dict(schema, name=impl, syncLevel="fullSync"))
        return "Prowlarr now shares its indexers with " + impl

    def _ensure_prowlarr_client(self):
        """Books found on Discover are sent through Prowlarr, which needs qBittorrent as its own download client."""
        url, headers = self.config.prowlarr_url + "/api/v1/downloadclient", {"X-Api-Key": self._key("prowlarr")}
        host, port, ssl = self._qbit_address()
        values = {"host": host, "port": port, "useSsl": ssl, "username": self.config.qbit_user,
                  "password": self.config.qbit_pass, "category": self.config.qbit_category or "books"}
        existing = next((client for client in _call("GET", url, headers) or [] if client.get("implementation") == "QBittorrent"), None)
        if existing:
            _fill(existing, values, only=("host", "port", "useSsl", "username", "password"))
            _call("PUT", "%s/%d" % (url, existing["id"]), headers, params={"forceSave": "true"}, json=existing)
            return "Already connected · sign-in brought up to date"
        schema = _schema_for(_call("GET", url + "/schema", headers), "QBittorrent", "Prowlarr")
        _fill(schema, values)
        _call("POST", url, headers, params={"forceSave": "true"}, json=dict(schema, name="qBittorrent", enable=True))
        return "Books from Discover go to qBittorrent, category %s" % values["category"]

    def _ensure_categories(self):
        from . import integrations
        session = integrations.qbit_session(self.config)
        base = self.config.qbit_url + "/api/v2/torrents/"
        wanted = dict(TORRENTS)
        wanted[self.config.qbit_category or "books"] = "/downloads"
        have = session.get(base + "categories", timeout=8).json() or {}
        for name, path in wanted.items():
            session.post(base + ("editCategory" if name in have else "createCategory"), data={"category": name, "savePath": path}, timeout=8)
        # Category folders only count when qBittorrent manages where torrents go. Without that, films and TV land in
        # its default folder (the books one), which Radarr and Sonarr cannot see, so nothing is ever filed.
        prefs = dict(QUEUE_PREFS, auto_tmm_enabled=True, **FINISHED_PREFS)
        current = session.get(self.config.qbit_url + "/api/v2/app/preferences", timeout=8).json() or {}
        # qBittorrent's out-of-the-box slow mode is 10 KB/s, i.e. basically "off". only nudge it if it's still on that default
        if current.get("alt_dl_limit") == 10240 and current.get("alt_up_limit") == 10240:
            prefs.update(alt_dl_limit=2 * 1048576, alt_up_limit=512 * 1024)
        session.post(self.config.qbit_url + "/api/v2/app/setPreferences", data={"json": json.dumps(prefs)}, timeout=8)
        # Films and TV that went to the wrong folder before are moved into theirs, so they can be filed after all.
        stray = [torrent["hash"] for torrent in session.get(base + "info", timeout=8).json() or []
                 if torrent.get("category") in TORRENTS and not torrent.get("auto_tmm") and torrent.get("hash")]
        if stray:
            session.post(base + "setAutoManagement", data={"hashes": "|".join(stray), "enable": "true"}, timeout=8)
        return "Films → torrents/movies, TV → torrents/tv, books → the finished-downloads folder" + \
            (" · moved %d earlier download%s into %s folder" % (len(stray), "" if len(stray) == 1 else "s", "its" if len(stray) == 1 else "their")
             if stray else "")

    def _ensure_bazarr(self):
        """Point Bazarr at Radarr and Sonarr and ask for English subtitles, forced ones too. Untested against a live Bazarr."""
        form = [("settings-general-use_radarr", "true"), ("settings-general-use_sonarr", "true")]
        for service, port in (("radarr", 7878), ("sonarr", 8989)):
            address = urlparse(getattr(self.config, service + "_url"))
            form += [("settings-%s-ip" % service, address.hostname or service), ("settings-%s-port" % service, str(address.port or port)),
                     ("settings-%s-base_url" % service, address.path or ""), ("settings-%s-ssl" % service, "false"),
                     ("settings-%s-apikey" % service, self._key(service))]
        langs = [lang.strip() for lang in (self.config.subtitle_langs or "en").split(",") if lang.strip()]
        # Normal subtitles (hearing-impaired ones are accepted when nothing else exists) and the
        # "forced" ones that only cover foreign-language lines.
        pairs = [(code, forced) for code in langs for forced in ("False", "True")]
        items = [{"id": number, "language": code, "audio_exclude": "False", "hi": "False", "forced": forced}
                 for number, (code, forced) in enumerate(pairs, 1)]
        # heads up: Bazarr REPLACES the whole profile list with whatever we send, so the admin's own go back in too
        others = [profile for profile in (self.bazarr("GET", "system/languages/profiles") or []) if profile.get("name") != PROFILE_NAME]
        ours = max([profile.get("profileId") or 0 for profile in others] + [0]) + 1
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
        form += [("settings-general-enabled_providers", source) for source in sources]
        self.bazarr("POST", "system/settings", data=form)
        return "Languages: %s · sources: %s" % (", ".join(langs), ", ".join(sources))

    def _ensure_jellyfin(self):
        host = urlparse(self.config.jellyfin_url).hostname
        if host != "jellyfin":
            return "Using your own Jellyfin: its libraries are left as they are"
        folders = self.jellyfin("GET", "/Library/VirtualFolders") or []
        made = []
        for name, kind, path in (("Films", "movies", "/media/movies"), ("TV shows", "tvshows", "/media/tv")):
            if any(folder.get("CollectionType") == kind for folder in folders):
                continue
            self.jellyfin("POST", "/Library/VirtualFolders", params={"name": name, "collectionType": kind, "paths": path, "refreshLibrary": "true"},
                          json={"LibraryOptions": {}})
            made.append(name)
        return "Added the %s libraries" % " and ".join(made) if made else "Libraries already there"
