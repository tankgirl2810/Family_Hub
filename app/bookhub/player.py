"""Watching inside Family Hub: the player's side of Jellyfin.

Jellyfin keeps the films and does any converting. Family Hub asks it how one film or episode can be played in the
browser that is asking, then passes the video through, so the browser only ever talks to Family Hub and the
Jellyfin key stays on the server:

  direct   the browser can play the file as it is: the hub passes the file on, a piece at a time
  hls      it cannot (most 2160p files, or sound a browser has no decoder for): Jellyfin converts it as it plays
           and the hub passes on the playlist and its pieces, with the key taken out of every address

Jellyfin is used through one account, its first administrator, so the place reached in a film is the household's,
not each person's. The hub keeps that place itself (table watch_places) and tells Jellyfin too, so Jellyfin's
own apps carry on from the same spot; when playing starts, whichever was touched last wins.
"""
import math
import re
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlsplit

import requests

from . import db
from .media import KINDS, MediaError, jellyfin_auth

ITEM = re.compile(r"[0-9a-fA-F]{32}")  # how Jellyfin names a film, an episode or a file
SESSION = re.compile(r"[0-9a-zA-Z-]{8,64}")
# the only addresses of a converted stream that are passed on: its two playlists and their numbered pieces
HLS_PATH = re.compile(r"(master|main)\.m3u8|hls1/[A-Za-z0-9_-]+/-?[0-9]+\.(ts|mp4|m4s)")
KEY_IN_ADDRESS = re.compile(r"(?i)(?<=[?&])api_?key=[^&\s\"#]*&?")
LOOSE_END = re.compile(r"[?&](?=[\"\s]|$)", re.M)

VIDEO = ("h264", "hevc", "av1", "vp9", "vp8")
AUDIO = ("aac", "mp3", "opus", "flac", "vorbis", "ac3", "eac3")
CONTAINERS = ("mp4", "webm")
TEXT_SUBTITLES = {"srt", "subrip", "ass", "ssa", "vtt", "webvtt", "mov_text", "text"}
TICKS = 10_000_000       # Jellyfin counts time in ten-millionths of a second
FINISHED = 0.92          # past this much of the running time, it has been watched
WORTH_RESUMING = 60      # seconds: less than this in, start from the beginning
LONGEST = 24 * 3600      # nothing on the server runs longer than a day
PIECE = 256 * 1024


class PlayError(MediaError):
    """Why something cannot be played, in words for the person who pressed Play."""


def device_profile(can):
    """What Jellyfin is told this browser plays. `can` comes from the page ({"video", "audio", "containers"});
    only names the hub knows are passed on."""
    can = can if isinstance(can, dict) else {}
    def known(name, allowed):
        said = can.get(name)
        return [codec for codec in allowed if isinstance(said, list) and codec in said]
    video, audio, containers = known("video", VIDEO), known("audio", AUDIO), known("containers", CONTAINERS)
    direct = []
    if "mp4" in containers:
        direct.append({"Container": "mp4,m4v,mov", "Type": "Video",
                       "VideoCodec": ",".join(codec for codec in video if codec != "vp8"),
                       "AudioCodec": ",".join(codec for codec in audio if codec != "vorbis")})
    if "webm" in containers:
        direct.append({"Container": "webm", "Type": "Video",
                       "VideoCodec": ",".join(codec for codec in video if codec in ("vp8", "vp9", "av1")),
                       "AudioCodec": ",".join(codec for codec in audio if codec in ("vorbis", "opus"))})
    # A browser that plays HEVC is sent the 2160p picture untouched (in MP4 pieces); the rest get H.264.
    hevc = "hevc" in video
    return {
        "Name": "Family Hub", "MaxStreamingBitrate": 120_000_000, "MaxStaticBitrate": 200_000_000,
        "MusicStreamingTranscodingBitrate": 384_000,
        "DirectPlayProfiles": [entry for entry in direct if entry["VideoCodec"]],
        "TranscodingProfiles": [{"Container": "mp4" if hevc else "ts", "Type": "Video", "Protocol": "hls", "Context": "Streaming",
                                 "VideoCodec": "hevc,h264" if hevc else "h264", "AudioCodec": "aac",
                                 "MaxAudioChannels": "2", "MinSegments": "1", "BreakOnNonKeyFrames": False}],
        "ContainerProfiles": [],
        "CodecProfiles": [
            {"Type": "Video", "Codec": "h264", "Conditions": [
                {"Condition": "LessThanEqual", "Property": "VideoBitDepth", "Value": "8", "IsRequired": False},
                {"Condition": "LessThanEqual", "Property": "VideoLevel", "Value": "52", "IsRequired": False}]},
            # Dolby Vision without an ordinary HDR picture underneath plays green and purple in a browser.
            {"Type": "Video", "Codec": "hevc", "Conditions": [
                {"Condition": "EqualsAny", "Property": "VideoRangeType", "IsRequired": False,
                 "Value": "SDR|HDR10|HLG|DOVIWithHDR10|DOVIWithHLG|DOVIWithSDR"}]}],
        "SubtitleProfiles": [{"Format": "vtt", "Method": "External"}],
    }


def _when(text):
    """A moment as Jellyfin or the hub wrote it ("2026-11-18T09:30:00.1234567Z"), or None."""
    if not isinstance(text, str):
        return None
    found = re.match(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)", text)
    return datetime.strptime(found.group(1), "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc) if found else None


def _seconds(value, most=LONGEST):
    """A number of seconds a page sent, or None when it is not one (a word, true, not-a-number, below zero)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        return None
    return min(float(value), most)


class Player:
    def __init__(self, hub):
        self.hub = hub
        self._account = None    # (Jellyfin address, key, the administrator's id)
        self._sessions = {}     # play session -> the device name Jellyfin filed its conversion under

    # ------------------------------------------------------------ asking Jellyfin

    def _server(self):
        config = self.hub.config
        if not (config.jellyfin_url and config.jellyfin_api_key):
            raise PlayError("Watching here is not set up yet. An admin can connect Jellyfin under Admin, Hub, Connections.")
        return config.jellyfin_url, config.jellyfin_api_key

    def _ask(self, method, path, **kw):
        """A question with an answer in JSON."""
        self._server()
        try:
            return self.hub.media.jellyfin(method, path, **kw)
        except PlayError:
            raise
        except MediaError as exc:
            raise PlayError("The family server could not do that (%s). Try again in a minute." % self._clean(str(exc)))

    def _fetch(self, path, query="", params=None, range_header=None, wait=60):
        """Something to pass on as it comes: the reply is left open for the caller to read and close."""
        url, key = self._server()
        headers = dict(jellyfin_auth(key), **{"Accept-Encoding": "identity"})  # byte counts must stay true
        if range_header:
            headers["Range"] = range_header
        try:
            # no redirects: the hub's sign-in to Jellyfin goes to Jellyfin's own address and nowhere it points to
            return requests.request("GET", url + path + ("?" + query if query else ""), headers=headers, params=params,
                                    stream=True, timeout=(10, wait), allow_redirects=False)
        except requests.RequestException:
            raise PlayError("The family server is not answering. Try again in a minute.")

    def _clean(self, text):
        """Text from Jellyfin with its key taken out, wherever it sits."""
        key = self.hub.config.jellyfin_api_key
        text = LOOSE_END.sub("", KEY_IN_ADDRESS.sub("", text))
        return text.replace(key, "") if key else text

    def forget_account(self):
        self._account = None

    def account(self):
        """The Jellyfin account everything is played as: its first administrator."""
        server = self._server()
        if self._account and self._account[:2] == server:
            return self._account[2]
        users = self._ask("GET", "/Users")
        for user in users if isinstance(users, list) else []:
            policy = user.get("Policy") or {} if isinstance(user, dict) else {}
            if policy.get("IsAdministrator") and not policy.get("IsDisabled") and ITEM.fullmatch(str(user.get("Id") or "")):
                self._account = server + (user["Id"],)
                return user["Id"]
        raise PlayError("Jellyfin has no administrator account to play as yet. An admin needs to finish its first-time setup.")

    # ------------------------------------------------------------ starting

    def start(self, item_id, can, audio=None):
        """How this browser can play one film or episode: where the video is, its subtitles and languages, and
        where to carry on from."""
        user = self.account()
        body = {"UserId": user, "DeviceProfile": device_profile(can), "MaxStreamingBitrate": 120_000_000,
                "SubtitleStreamIndex": -1,  # subtitles are laid over by the browser, never burnt into the picture
                "EnableDirectPlay": audio is None, "EnableDirectStream": True, "EnableTranscoding": True, "AutoOpenLiveStream": False}
        if audio is not None:
            body["AudioStreamIndex"] = audio  # a browser cannot switch language inside a file; Jellyfin picks it out
        info = self._ask("POST", "/Items/%s/PlaybackInfo" % item_id, params={"userId": user}, json=body)
        info = info if isinstance(info, dict) else {}
        sources = [source for source in info.get("MediaSources") or [] if isinstance(source, dict)]
        if info.get("ErrorCode") or not sources:
            raise PlayError("The family server has no file for this that it can play (%s)." % (info.get("ErrorCode") or "no file listed"))
        source = sources[0]
        source_id, session = str(source.get("Id") or ""), str(info.get("PlaySessionId") or "")
        if not ITEM.fullmatch(source_id):
            raise PlayError("The family server answered in a way Family Hub did not expect.")
        streams = [stream for stream in source.get("MediaStreams") or [] if isinstance(stream, dict)]
        if audio is None and source.get("SupportsDirectPlay"):
            kind, url = "direct", "/api/media/play/%s/file?source=%s" % (item_id, source_id)
        elif source.get("TranscodingUrl"):
            parts = urlsplit(str(source["TranscodingUrl"]))
            if not parts.path.lower().endswith("/master.m3u8"):
                raise PlayError("The family server offered a kind of stream Family Hub cannot pass on.")
            query = self._clean("?" + parts.query)[1:]
            kind, url = "hls", "/api/media/play/%s/hls/master.m3u8?%s" % (item_id, query)
            device = (parse_qs(parts.query).get("DeviceId") or parse_qs(parts.query).get("deviceId") or [""])[0]
            if SESSION.fullmatch(session) and device:
                if len(self._sessions) > 200:  # never stopped (a closed laptop): the oldest are let go
                    for old in list(self._sessions)[:100]:
                        self._sessions.pop(old, None)
                self._sessions[session] = device
        else:
            raise PlayError("This one cannot be played in a browser, and the family server cannot convert it.")
        length = (source.get("RunTimeTicks") or 0) / TICKS if isinstance(source.get("RunTimeTicks"), (int, float)) else 0
        chosen = audio if audio is not None else source.get("DefaultAudioStreamIndex")
        return {
            "item": item_id, "kind": kind, "url": url, "length": length, "session": session,
            "resume": self.resume(item_id, user, length),
            "subtitles": [{"index": stream.get("Index"), "label": stream.get("DisplayTitle") or stream.get("Language") or "Subtitles",
                           "lang": stream.get("Language") or "",
                           "url": "/api/media/play/%s/subs/%s/%d.vtt" % (item_id, source_id, stream["Index"])}
                          for stream in streams if stream.get("Type") == "Subtitle" and isinstance(stream.get("Index"), int)
                          and (stream.get("IsTextSubtitleStream") or str(stream.get("Codec") or "").lower() in TEXT_SUBTITLES)],
            "audio": [{"index": stream.get("Index"), "label": stream.get("DisplayTitle") or stream.get("Language") or "Sound",
                       "chosen": stream.get("Index") == chosen}
                      for stream in streams if stream.get("Type") == "Audio" and isinstance(stream.get("Index"), int)],
        }

    # ------------------------------------------------------------ passing it through

    def file(self, item_id, source_id, range_header=None):
        """The file as it is on the disk, or the part of it the browser asked for."""
        return self._fetch("/Videos/%s/stream" % item_id, params={"static": "true", "mediaSourceId": source_id},
                           range_header=range_header)

    def hls(self, item_id, path, query):
        """One playlist or piece of a converted stream. The first piece can take a while: the converter is starting."""
        return self._fetch("/Videos/%s/%s" % (item_id, path), query=query, wait=120)

    def playlist(self, reply):
        """A playlist's text with the key out of every address in it."""
        try:
            return self._clean(reply.content.decode("utf-8", "replace"))
        finally:
            reply.close()

    def subtitle(self, item_id, source_id, index):
        reply = self._fetch("/Videos/%s/%s/Subtitles/%d/0/Stream.vtt" % (item_id, source_id, index), wait=60)
        try:
            if reply.status_code != 200:
                raise PlayError("Those subtitles could not be fetched.")
            return self._clean(reply.content.decode("utf-8", "replace"))
        finally:
            reply.close()

    def stop(self, session):
        """Tell Jellyfin a viewer has gone, so it stops converting for them. Only a session started here, and only that one."""
        device = self._sessions.pop(session, None) if isinstance(session, str) else None
        if not device:
            return
        try:
            self._ask("DELETE", "/Videos/ActiveEncodings", params={"deviceId": device, "playSessionId": session})
        except MediaError:
            pass  # it stops by itself after a minute without a viewer

    # ------------------------------------------------------------ the place in a film

    def _row(self, row):
        return {"item": row["item_id"], "kind": row["kind"], "arr_id": row["arr_id"], "season": row["season"], "episode": row["episode"],
                "title": row["title"], "at": row["at"], "length": row["length"], "finished": bool(row["finished"]), "updated": row["updated"]}

    def places(self):
        """Every film and episode somebody has started, the latest first."""
        rows = self.hub.con().execute("SELECT * FROM watch_places ORDER BY updated DESC, item_id LIMIT 2000")
        return [self._row(row) for row in rows]

    def place(self, item_id):
        row = self.hub.con().execute("SELECT * FROM watch_places WHERE item_id=?", (item_id.lower(),)).fetchone()
        return self._row(row) if row else None

    def keep_place(self, item_id, at, length, about, profile_id):
        """Remember how far in a film or episode is. Raises ValueError for numbers that make no sense."""
        at, length = _seconds(at, most=float("inf")), _seconds(length, most=float("inf"))
        about = about if isinstance(about, dict) else {}
        if at is None or not length or length > LONGEST or about.get("kind") not in KINDS:
            raise ValueError("not a place in a film")
        at = min(at, length)
        finished = at >= FINISHED * length
        number = lambda value: value if isinstance(value, int) and not isinstance(value, bool) and 0 <= value < 10 ** 9 else None
        title = about.get("title")[:200] if isinstance(about.get("title"), str) else ""
        con = self.hub.con()
        con.execute(
            "INSERT INTO watch_places(item_id, kind, arr_id, season, episode, title, at, length, finished, updated, profile_id) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(item_id) DO UPDATE SET kind=excluded.kind, arr_id=excluded.arr_id, "
            "season=excluded.season, episode=excluded.episode, title=excluded.title, at=excluded.at, length=excluded.length, "
            "finished=excluded.finished, updated=excluded.updated, profile_id=excluded.profile_id",
            (item_id.lower(), about["kind"], number(about.get("arr_id")), number(about.get("season")), number(about.get("episode")),
             title, at, length, int(finished), db.now(), profile_id))
        con.commit()
        self._tell_jellyfin(item_id, at, finished)
        return self.place(item_id)

    def _tell_jellyfin(self, item_id, at, finished):
        """Jellyfin's own apps carry on from the same place. Its refusal (an older Jellyfin) loses nothing: the hub has it."""
        try:
            body = {"PlaybackPositionTicks": 0 if finished else int(at * TICKS), "LastPlayedDate": db.now()}
            if finished:
                body["Played"] = True
            self.hub.media.jellyfin("POST", "/UserItems/%s/UserData" % item_id, params={"userId": self.account()}, json=body, timeout=6)
        except MediaError:
            pass

    def resume(self, item_id, user, length):
        """Seconds in to carry on from: the hub's place or Jellyfin's, whichever was touched last. 0 = from the start."""
        mine = self.place(item_id)
        at, touched = (0.0 if mine["finished"] else mine["at"], _when(mine["updated"])) if mine else (0.0, None)
        try:
            found = self.hub.media.jellyfin("GET", "/Items", params={"ids": item_id, "userId": user, "enableUserData": "true"}, timeout=6)
            data = ((found or {}).get("Items") or [{}])[0].get("UserData") or {}
            theirs, ticks = _when(data.get("LastPlayedDate")), data.get("PlaybackPositionTicks")
            if theirs and isinstance(ticks, (int, float)) and not isinstance(ticks, bool) and (touched is None or theirs > touched):
                at = 0.0 if data.get("Played") and not ticks else ticks / TICKS
        except (MediaError, AttributeError, IndexError, TypeError):
            pass  # Jellyfin's side is a nicety; the hub's own place stands
        if length and at >= FINISHED * length:
            return 0
        return at if at >= WORTH_RESUMING else 0
