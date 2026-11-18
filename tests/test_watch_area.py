"""Watch mode: playing films and episodes inside Family Hub, with Jellyfin doing the work behind it (player.py)."""
import json
import re

import pytest

from test_app import app, client, login  # noqa: F401

from bookhub import player

KEY = "j" * 32
FILM = "a" * 32       # a film the browser can play as it is
BIG = "b" * 32        # a 2160p film that has to be converted
SOURCE = "c" * 32
ADMIN = "d" * 32
SESSION = "e" * 32
CAN = {"video": ["h264", "vp9"], "audio": ["aac", "mp3", "opus"], "containers": ["mp4", "webm"]}


class Reply:
    def __init__(self, status=200, body=None, headers=None):
        self.status_code = status
        self.reason = "OK" if status < 400 else "Not Found"
        self.content = b"" if body is None else body if isinstance(body, bytes) else json.dumps(body).encode()
        self.text = self.content.decode("utf-8", "replace")
        self.headers = dict(headers or {})
        self.closed = False

    def json(self):
        return json.loads(self.content)

    def iter_content(self, size):
        for start in range(0, len(self.content), size):
            yield self.content[start:start + size]

    def close(self):
        self.closed = True


def streams(*extra):
    return [{"Type": "Video", "Index": 0, "Codec": "h264"},
            {"Type": "Audio", "Index": 1, "Codec": "aac", "Language": "eng", "DisplayTitle": "English - AAC - Stereo"},
            {"Type": "Audio", "Index": 2, "Codec": "aac", "Language": "fre", "DisplayTitle": "French - AAC - Stereo"},
            {"Type": "Subtitle", "Index": 3, "Codec": "subrip", "Language": "eng", "DisplayTitle": "English - SRT", "IsTextSubtitleStream": True},
            {"Type": "Subtitle", "Index": 4, "Codec": "pgssub", "Language": "eng", "DisplayTitle": "English - PGS", "IsTextSubtitleStream": False},
            *extra]


class FakeJellyfin:
    """Just enough Jellyfin to play something: an administrator, PlaybackInfo, the file, a converted stream, user data."""

    def __init__(self):
        self.calls = []           # (method, path, params, json, headers)
        self.users = [{"Id": "f" * 32, "Name": "kid", "Policy": {"IsAdministrator": False}},
                      {"Id": ADMIN, "Name": "house", "Policy": {"IsAdministrator": True}}]
        self.sources = True
        self.user_data = {}       # item -> UserData as Jellyfin would send it
        self.refuse_user_data = False
        self.other_names = False  # a film in several versions: its files are not named after the film
        self.film_bytes = bytes(range(256)) * 40

    def asked(self, method, ending):
        return [call for call in self.calls if call[0] == method and call[1].endswith(ending)]

    def request(self, method, url, headers=None, params=None, json=None, data=None, timeout=0, stream=False, allow_redirects=True):
        assert url.startswith("http://jellyfin:8096/"), url
        assert not (stream and allow_redirects), "video is fetched from Jellyfin's own address only"
        assert (headers or {}).get("Authorization") == 'MediaBrowser Token="%s"' % KEY
        path, _, query = url[len("http://jellyfin:8096"):].partition("?")
        self.calls.append((method, path, dict(params or {}), json, dict(headers or {}), query))
        if path == "/Users":
            return Reply(200, self.users)
        if path.endswith("/PlaybackInfo"):
            item = path.split("/")[2]
            if not self.sources or (self.other_names and json.get("MediaSourceId")):
                return Reply(200, {"MediaSources": [], "PlaySessionId": SESSION})
            direct = item == FILM and json.get("EnableDirectPlay")
            source = {"Id": SOURCE, "Container": "mp4" if item == FILM else "mkv", "RunTimeTicks": 6000 * 10_000_000,
                      "SupportsDirectPlay": bool(direct), "DefaultAudioStreamIndex": 1, "MediaStreams": streams()}
            if not direct:
                source["TranscodingUrl"] = ("/videos/%s/master.m3u8?DeviceId=hub-device&MediaSourceId=%s&VideoCodec=h264&AudioCodec=aac"
                                            "&api_key=%s&PlaySessionId=%s&SegmentContainer=ts" % (item, SOURCE, KEY, SESSION))
            return Reply(200, {"MediaSources": [source], "PlaySessionId": SESSION})
        if path == "/Items":
            return Reply(200, {"Items": [{"Id": params["ids"], "UserData": self.user_data.get(params["ids"], {})}]})
        if path.endswith("/stream"):
            wanted = (headers or {}).get("Range")
            if wanted:
                first, last = (int(part) for part in wanted[len("bytes="):].split("-"))
                piece = self.film_bytes[first:last + 1]
                return Reply(206, piece, {"Content-Type": "video/mp4", "Content-Length": str(len(piece)), "Accept-Ranges": "bytes",
                                          "Content-Range": "bytes %d-%d/%d" % (first, last, len(self.film_bytes))})
            return Reply(200, self.film_bytes, {"Content-Type": "video/mp4", "Content-Length": str(len(self.film_bytes)), "Accept-Ranges": "bytes"})
        if path.endswith("/master.m3u8"):
            return Reply(200, ('#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=8000000\nmain.m3u8?DeviceId=hub-device&api_key=%s&PlaySessionId=%s\n'
                               % (KEY, SESSION)).encode(), {"Content-Type": "application/x-mpegURL"})
        if path.endswith("/main.m3u8"):
            return Reply(200, ('#EXTM3U\n#EXT-X-MAP:URI="hls1/main/-1.mp4?ApiKey=%s&PlaySessionId=%s"\n#EXTINF:6.0,\n'
                               'hls1/main/0.mp4?PlaySessionId=%s&ApiKey=%s\n#EXT-X-ENDLIST\n' % (KEY, SESSION, SESSION, KEY)).encode(),
                         {"Content-Type": "application/x-mpegURL"})
        if "/hls1/main/" in path:
            return Reply(200, b"\x00\x01piece", {"Content-Type": "video/mp4", "Content-Length": "7"})
        if path.endswith("/Stream.vtt"):
            return Reply(200, "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nG'day? Are you sure?\n".encode(), {"Content-Type": "text/vtt"})
        if path.endswith("/UserData"):
            return Reply(404, {"message": "no such thing"}) if self.refuse_user_data else Reply(200, {})
        if path == "/Videos/ActiveEncodings":
            return Reply(204)
        return Reply(404, {"message": "not here"})


@pytest.fixture()
def jellyfin(app, monkeypatch):
    from bookhub import settings
    fake = FakeJellyfin()
    monkeypatch.setattr(player.requests, "request", fake.request)
    with app.app_context():
        settings.save(app.hub, {"jellyfin_url": "http://jellyfin:8096", "jellyfin_api_key": KEY})
    return fake


def start(client, item=FILM, **more):
    return client.post("/api/media/play/%s/start" % item, json=dict({"can": CAN}, **more))


def addresses(playlist):
    """The addresses a playlist points at, in order."""
    return [line for line in playlist.splitlines() if line and not line.startswith("#")]


def first_piece(client, item=BIG):
    """Follow a converted stream the way a browser does: its offer, the two playlists, then the first piece's address."""
    base = "/api/media/play/%s/hls/" % item
    master = client.get(start(client, item).get_json()["url"]).get_data(as_text=True)
    main = client.get(base + addresses(master)[0]).get_data(as_text=True)
    return base + addresses(main)[0]


# ------------------------------------------------------------ choosing how it plays

def test_a_film_the_browser_can_play_is_passed_on_as_it_is(client, jellyfin):
    login(client, "Nan")
    reply = start(client)
    assert reply.status_code == 200, reply.get_json()
    play = reply.get_json()
    assert play["kind"] == "direct" and play["url"] == "/api/media/play/%s/file?source=%s" % (FILM, SOURCE)
    assert play["length"] == 6000 and play["session"] == SESSION and play["resume"] == 0
    asked = jellyfin.asked("POST", "/PlaybackInfo")[0]
    assert asked[2] == {"userId": ADMIN}  # the household account: the first administrator
    body = asked[3]
    assert body["SubtitleStreamIndex"] == -1 and body["EnableDirectPlay"] is True
    assert body["MediaSourceId"] == FILM  # Jellyfin only honours the language and "no subtitles" for a file it is told by name
    direct = body["DeviceProfile"]["DirectPlayProfiles"]
    assert {"Container": "mp4,m4v,mov", "Type": "Video", "VideoCodec": "h264,vp9", "AudioCodec": "aac,mp3,opus"} in direct
    assert body["DeviceProfile"]["TranscodingProfiles"][0]["Protocol"] == "hls"
    assert body["DeviceProfile"]["TranscodingProfiles"][0]["VideoCodec"] == "h264"  # this browser cannot play hevc


def test_a_film_the_browser_cannot_play_comes_converted_and_the_key_stays_home(client, jellyfin):
    login(client, "Nan")
    play = start(client, BIG).get_json()
    assert play["kind"] == "hls"
    assert play["url"].startswith("/api/media/play/%s/hls/master.m3u8?" % BIG)
    assert KEY not in json.dumps(play) and "api_key" not in play["url"].lower() and "apikey" not in play["url"].lower()
    assert "PlaySessionId=" + SESSION in play["url"] and "MediaSourceId=" + SOURCE in play["url"]


def test_a_browser_that_plays_hevc_is_not_made_to_wait_for_a_conversion(client, jellyfin):
    login(client, "Nan")
    client.post("/api/media/play/%s/start" % BIG, json={"can": dict(CAN, video=["h264", "hevc", "made-up"])})
    profile = jellyfin.asked("POST", "/PlaybackInfo")[0][3]["DeviceProfile"]
    assert profile["TranscodingProfiles"][0]["Container"] == "mp4" and profile["TranscodingProfiles"][0]["VideoCodec"] == "hevc,h264"
    assert "made-up" not in json.dumps(profile)  # only codecs the hub knows are passed on


def test_subtitles_in_words_are_offered_and_pictures_are_left_out(client, jellyfin):
    login(client, "Nan")
    play = start(client).get_json()
    assert play["subtitles"] == [{"index": 3, "label": "English - SRT", "lang": "eng",
                                  "url": "/api/media/play/%s/subs/%s/3.vtt" % (FILM, SOURCE)}]
    words = client.get(play["subtitles"][0]["url"])
    assert words.status_code == 200 and words.mimetype == "text/vtt"
    assert "G'day? Are you sure?\n" in words.get_data(as_text=True)  # the words exactly as they came, question marks and all
    assert jellyfin.asked("GET", "/Videos/%s/%s/Subtitles/3/0/Stream.vtt" % (FILM, SOURCE))


def test_another_language_is_played_as_a_converted_stream(client, jellyfin):
    login(client, "Nan")
    play = start(client).get_json()
    assert [(track["index"], track["chosen"]) for track in play["audio"]] == [(1, True), (2, False)]
    french = start(client, audio=2).get_json()
    body = jellyfin.asked("POST", "/PlaybackInfo")[-1][3]
    assert body["AudioStreamIndex"] == 2 and body["EnableDirectPlay"] is False
    assert french["kind"] == "hls" and [track["chosen"] for track in french["audio"]] == [False, True]
    assert start(client, audio="two").status_code == 400


def test_a_jellyfin_that_cannot_help_says_so_in_plain_words(app, client, jellyfin):
    login(client, "Nan")
    jellyfin.sources = False
    nothing = start(client)
    assert nothing.status_code == 502 and "no file" in nothing.get_json()["error"]
    jellyfin.sources, jellyfin.users = True, [{"Id": "f" * 32, "Policy": {"IsAdministrator": False}}]
    with app.app_context():
        app.hub.player.forget_account()
    nobody = start(client)
    assert nobody.status_code == 502 and "administrator" in nobody.get_json()["error"] and KEY not in nobody.get_json()["error"]


def test_without_jellyfin_playing_says_what_is_missing(client):
    login(client, "Nan")
    reply = start(client)
    assert reply.status_code == 502 and "not set up" in reply.get_json()["error"]


def test_only_people_with_a_profile_can_play(client, jellyfin):
    addresses = [("post", "/api/media/play/%s/start" % FILM), ("get", "/api/media/play/%s/file?source=%s" % (FILM, SOURCE)),
                 ("get", "/api/media/play/%s/hls/master.m3u8" % BIG), ("get", "/api/media/play/%s/subs/%s/3.vtt" % (FILM, SOURCE)),
                 ("post", "/api/media/play/%s/place" % FILM), ("post", "/api/media/play/%s/stop" % FILM), ("get", "/api/media/places")]
    for method, address in addresses:
        assert getattr(client, method)(address, **({"json": {}} if method == "post" else {})).status_code == 401, address
    assert client.post("/api/login", json={"guest": True}).status_code == 200
    for method, address in addresses:
        assert getattr(client, method)(address, **({"json": {}} if method == "post" else {})).status_code == 403, address
    assert jellyfin.calls == []


# ------------------------------------------------------------ passing the video through

def test_the_file_is_passed_through_a_piece_at_a_time(client, jellyfin):
    login(client, "Nan")
    whole = client.get("/api/media/play/%s/file?source=%s" % (FILM, SOURCE))
    assert whole.status_code == 200 and whole.data == jellyfin.film_bytes and whole.headers["Accept-Ranges"] == "bytes"
    piece = client.get("/api/media/play/%s/file?source=%s" % (FILM, SOURCE), headers={"Range": "bytes=100-199"})
    assert piece.status_code == 206 and piece.data == jellyfin.film_bytes[100:200]
    assert piece.headers["Content-Range"] == "bytes 100-199/%d" % len(jellyfin.film_bytes) and piece.headers["Content-Length"] == "100"
    asked = jellyfin.asked("GET", "/Videos/%s/stream" % FILM)[-1]
    assert asked[2] == {"static": "true", "mediaSourceId": SOURCE} and asked[4]["Range"] == "bytes=100-199"


def test_the_converted_stream_never_shows_the_key(client, jellyfin):
    login(client, "Nan")
    play = start(client, BIG).get_json()
    base = "/api/media/play/%s/hls/" % BIG
    first = client.get(play["url"])
    assert first.status_code == 200 and first.mimetype == "application/vnd.apple.mpegurl"
    text = first.get_data(as_text=True)
    assert KEY not in text and "api_key" not in text.lower()
    main = addresses(text)[0]
    assert main.startswith("main.m3u8?DeviceId=hub-device&PlaySessionId=%s&sig=" % SESSION)
    # Jellyfin is asked for exactly what it offered, with the hub's own sign-in in the header instead of the key
    assert jellyfin.asked("GET", "/Videos/%s/master.m3u8" % BIG)[-1][5] == play["url"].split("?", 1)[1].split("&sig=")[0]
    second = client.get(base + main).get_data(as_text=True)
    assert KEY not in second and "apikey" not in second.lower()
    opening, piece_address = re.search(r'URI="([^"]+)"', second).group(1), addresses(second)[0]
    assert opening.startswith("hls1/main/-1.mp4?PlaySessionId=%s&sig=" % SESSION)
    assert piece_address.startswith("hls1/main/0.mp4?PlaySessionId=%s&sig=" % SESSION)
    piece = client.get(base + piece_address)
    assert piece.status_code == 200 and piece.data == b"\x00\x01piece" and piece.mimetype == "video/mp4"
    assert jellyfin.asked("GET", "/hls1/main/0.mp4")[-1][5] == "PlaySessionId=" + SESSION


def test_the_browser_cannot_change_what_the_converter_is_told(client, jellyfin):
    # these settings end up on the command line of Jellyfin's converter: only Jellyfin's own, unchanged, are passed on
    login(client, "Nan")
    good = start(client, BIG).get_json()["url"]
    unsigned = good.split("&sig=")[0]
    for bad in (good.replace("VideoCodec=h264", "VideoCodec=h264%20-f%20null"),   # a setting changed
                good + "&VideoCodec=copy", good + "&static=true",               # a setting added
                good.replace("&PlaySessionId=" + SESSION, ""),                   # a setting taken away
                unsigned, unsigned + "&sig=" + "0" * 32, good + "&sig=" + "0" * 32,
                good.replace("/play/" + BIG, "/play/" + FILM),                  # signed for another film
                good + "&runtimeTicks=soon", good + "&actualSegmentLengthTicks=-1"):
        assert client.get(bad).status_code == 404, bad
    assert jellyfin.asked("GET", "/master.m3u8") == []
    # the two numbers that differ from piece to piece may ride along, as plain numbers
    assert client.get(good + "&runtimeTicks=60000000&actualSegmentLengthTicks=30000000").status_code == 200
    assert jellyfin.asked("GET", "/master.m3u8")[-1][5] == unsigned.split("?", 1)[1] + "&runtimeTicks=60000000&actualSegmentLengthTicks=30000000"


def test_a_film_whose_files_go_by_other_names_still_plays(client, jellyfin):
    login(client, "Nan")
    jellyfin.other_names = True
    assert start(client).get_json()["kind"] == "direct"
    named = [call[3].get("MediaSourceId") for call in jellyfin.asked("POST", "/PlaybackInfo")]
    assert named == [FILM, None]  # asked by name first, then without


def test_only_a_stream_address_is_passed_on(client, jellyfin):
    login(client, "Nan")
    for address in ("/api/media/play/%s/hls/../../Users" % BIG, "/api/media/play/%s/hls/main.m3u8/../../../Users" % BIG,
                    "/api/media/play/%s/hls/hls1/main/0.exe" % BIG, "/api/media/play/%s/hls/hls1/../../System/Info" % BIG,
                    "/api/media/play/%s/hls/master.m3u8" % ("a" * 31), "/api/media/play/%s/file?source=nope" % FILM,
                    "/api/media/play/%s/file" % FILM, "/api/media/play/%s/subs/%s/3.vtt" % (FILM, "x" * 32)):
        assert client.get(address).status_code == 404, address
    assert client.post("/api/media/play/not-an-id/start", json={"can": CAN}).status_code == 404
    assert jellyfin.calls == []


def test_stopping_ends_that_viewers_conversion_and_nobody_elses(client, jellyfin):
    login(client, "Nan")
    start(client, BIG)
    assert client.post("/api/media/play/%s/stop" % BIG, json={"session": "f" * 32}).status_code == 200  # not one we started
    assert jellyfin.asked("DELETE", "/Videos/ActiveEncodings") == []
    assert client.post("/api/media/play/%s/stop" % BIG, json={"session": SESSION}).status_code == 200
    ended = jellyfin.asked("DELETE", "/Videos/ActiveEncodings")
    assert len(ended) == 1 and ended[0][2] == {"deviceId": "hub-device", "playSessionId": SESSION}
    client.post("/api/media/play/%s/stop" % BIG, json={"session": SESSION})  # twice is harmless
    assert len(jellyfin.asked("DELETE", "/Videos/ActiveEncodings")) == 1


# ------------------------------------------------------------ the place in a film

ABOUT = {"kind": "show", "arr_id": 7, "season": 2, "episode": 5, "title": "Long Show"}


def keep(client, at, length=6000, item=FILM, **about):
    return client.post("/api/media/play/%s/place" % item, json=dict(ABOUT, at=at, length=length, **about))


def test_the_place_is_kept_and_playing_carries_on_from_it(client, jellyfin):
    login(client, "Nan")
    assert keep(client, 1234.5).status_code == 200
    places = client.get("/api/media/places").get_json()["places"]
    assert len(places) == 1
    place = places[0]
    assert (place["item"], place["at"], place["length"], place["finished"]) == (FILM, 1234.5, 6000, False)
    assert {name: place[name] for name in ABOUT} == ABOUT
    assert start(client).get_json()["resume"] == 1234.5
    told = jellyfin.asked("POST", "/UserItems/%s/UserData" % FILM)[-1]
    assert told[2] == {"userId": ADMIN} and told[3]["PlaybackPositionTicks"] == 12_345_000_000 and "Played" not in told[3]
    login(client, "You")  # the place is the household's
    assert start(client).get_json()["resume"] == 1234.5


def test_nearly_at_the_end_counts_as_finished(client, jellyfin):
    login(client, "Nan")
    assert keep(client, 5600).get_json()["finished"] is True  # over 92%
    assert client.get("/api/media/places").get_json()["places"][0]["finished"] is True
    assert start(client).get_json()["resume"] == 0
    told = jellyfin.asked("POST", "/UserData")[-1][3]
    assert told["Played"] is True and told["PlaybackPositionTicks"] == 0
    keep(client, 30)  # the first half minute is not worth coming back to
    assert start(client).get_json()["resume"] == 0


def test_a_jellyfin_that_refuses_the_place_does_not_lose_it(client, jellyfin):
    login(client, "Nan")
    jellyfin.refuse_user_data = True
    assert keep(client, 900).status_code == 200
    assert client.get("/api/media/places").get_json()["places"][0]["at"] == 900


def test_a_place_reached_in_jellyfins_own_app_wins_when_it_is_newer(client, jellyfin):
    login(client, "Nan")
    keep(client, 900)
    jellyfin.user_data[FILM] = {"PlaybackPositionTicks": 3000 * 10_000_000, "LastPlayedDate": "2099-01-01T00:00:00.0000000Z"}
    assert start(client).get_json()["resume"] == 3000
    jellyfin.user_data[FILM]["LastPlayedDate"] = "2001-01-01T00:00:00.0000000Z"
    assert start(client).get_json()["resume"] == 900
    jellyfin.user_data[FILM] = {"PlaybackPositionTicks": "soon", "LastPlayedDate": 5}  # nonsense is ignored
    assert start(client).get_json()["resume"] == 900


def test_a_nonsense_place_is_not_kept(client, jellyfin):
    login(client, "Nan")
    for at, length in ((-5, 6000), ("abc", 6000), (100, 0), (100, "long"), (float("nan"), 6000), (100, 10 ** 9), (None, 6000), (True, 6000)):
        assert client.post("/api/media/play/%s/place" % FILM, data=json.dumps(dict(ABOUT, at=at, length=length)),
                           content_type="application/json").status_code == 400, (at, length)
    assert client.get("/api/media/places").get_json()["places"] == []
    assert keep(client, 60000).get_json()["at"] == 6000  # past the end: the end
    assert keep(client, 100, kind="podcast").status_code == 400
    assert keep(client, 100, title="x" * 5000, season="two").status_code == 200
    place = client.get("/api/media/places").get_json()["places"][0]
    assert len(place["title"]) == 200 and place["season"] is None


# ------------------------------------------------------------ the page itself

def test_the_page_brings_watch_mode_and_its_player_library(client):
    page = client.get("/").get_data(as_text=True)
    assert "/static/watch.css" in page and page.index("/static/app.js") < page.index("/static/watch.js")
    library = client.get("/static/vendor/hls.min.js")
    assert library.status_code == 200 and b"Hls" in library.data
    assert client.get("/static/vendor/hls.LICENSE.txt").status_code == 200  # Apache 2.0: its licence travels with it


def test_the_wide_picture_is_for_profiles_and_real_kinds_only(client):
    assert client.get("/api/media/backdrop/film/1").status_code == 401
    login(client, "Nan")
    assert client.get("/api/media/backdrop/podcast/1").status_code == 404
    assert client.get("/api/media/backdrop/film/1").status_code == 404  # Radarr is not connected: no picture, no fuss


def test_what_is_passed_on_is_only_ever_video(client, jellyfin, monkeypatch):
    login(client, "Nan")
    real = jellyfin.request

    def a_page_instead(method, url, **kw):
        reply = real(method, url, **kw)
        if "/hls1/" in url:
            reply.headers["Content-Type"] = "text/html; charset=utf-8"
            reply.headers["Set-Cookie"] = "jellyfin=secret"
        return reply
    address = first_piece(client)
    monkeypatch.setattr(player.requests, "request", a_page_instead)
    piece = client.get(address)
    assert piece.mimetype == "application/octet-stream" and "jellyfin=secret" not in str(piece.headers)  # none of Jellyfin's cookies
    assert piece.headers["X-Content-Type-Options"] == "nosniff"
