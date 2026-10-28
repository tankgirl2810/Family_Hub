"""Films, TV and downloads: bugs found poking at media.py, watchlist.py and their routes."""
from datetime import date

from test_app import app, client, login, media_servers  # noqa: F401

from bookhub import media, watchlist


class FakeArr:
    """Just enough Radarr/Sonarr for one call: answers GETs from a dict keyed by path."""
    name = "sonarr"

    def __init__(self, answers):
        self.answers = answers

    def __call__(self, method, path, **kw):
        return self.answers[path]

    def get(self, path, **params):
        return self.answers[path]


def test_an_old_film_with_only_a_cinema_date_is_out_not_in_cinemas():
    today = date.today().isoformat()
    # what Radarr really sends for a 1968 film: a cinema date, no home release dates, status released
    old = {"title": "Symbiopsychotaxiplasm: Take One", "year": 1968, "status": "released", "inCinemas": "1968-10-28T00:00:00Z"}
    assert watchlist._film(old, today) == ("Out now", False)
    # no dates at all, but decades old: not "announced" either
    assert watchlist._film({"title": "Metropolis", "year": 1998, "status": "announced"}, today) == ("Out now", False)
    # still really in cinemas, or still to come
    words, coming = watchlist._film({"status": "inCinemas", "inCinemas": today}, today)
    assert coming and words.startswith("In cinemas now")
    assert watchlist._film({"status": "announced"}, today) == ("Announced", True)
    assert watchlist._film({"status": "announced", "year": int(today[:4]) + 1}, today) == ("Announced", True)


def test_a_season_pack_is_not_counted_once_per_episode(app, monkeypatch):
    # Sonarr: a 10-episode pack (each record carrying the whole 100 GB, 50 left) plus one single episode (10, all left)
    pack = [{"seriesId": 1, "downloadId": "PACK", "size": 100, "sizeleft": 50, "episodeId": n,
             "episode": {"seasonNumber": 1, "episodeNumber": n}, "series": {"title": "Long Show"}} for n in range(1, 11)]
    single = [{"seriesId": 1, "downloadId": "ONE", "size": 10, "sizeleft": 10, "episodeId": 99,
               "episode": {"seasonNumber": 1, "episodeNumber": 11}, "series": {"title": "Long Show"}}]
    fake = FakeArr({"queue": {"records": pack + single}})
    with app.app_context():
        m = media.Media(app.hub)
        monkeypatch.setattr(m, "arr", lambda kind: fake)
        monkeypatch.setattr(m, "configured", lambda kind: kind == "show")
        incoming = m.downloading("show")[1]
        assert incoming["progress"] == int(100 * (110 - 60) / 110) and incoming["episodes"] == 11
        line = m.queue()["items"][0]
        assert line["size"] == 110 and line["left"] == 60 and line["items"] == 11
        assert line["title"] == "Long Show · Season 1"


def test_a_queue_line_without_episode_details_does_not_say_season_none(app, monkeypatch):
    fake = FakeArr({"queue": {"records": [{"seriesId": 1, "downloadId": "X", "size": 10, "sizeleft": 5, "series": {"title": "Long Show"}}]}})
    with app.app_context():
        m = media.Media(app.hub)
        monkeypatch.setattr(m, "arr", lambda kind: fake)
        monkeypatch.setattr(m, "configured", lambda kind: kind == "show")
        assert m.queue()["items"][0]["title"] == "Long Show"


def test_a_pack_of_several_seasons_says_so(client, media_servers):
    login(client, "You")
    torrent_hash = "a" * 40
    media_servers.arr["sonarr"]["queue"] = [
        {"seriesId": 1, "downloadId": torrent_hash.upper(), "size": 100, "sizeleft": 50, "series": {"title": "Long Show"},
         "episode": {"seasonNumber": season, "episodeNumber": number}} for season in (1, 2, 3) for number in (1, 2)]
    media_servers.torrents = [{"hash": torrent_hash, "name": "Long.Show.S01-S03", "category": "tv", "state": "downloading", "progress": 0.5}]
    rows = client.get("/api/media/torrents").get_json()["torrents"]
    assert rows[0]["title"] == "Long Show · Seasons 1–3"
    assert media._episodes_label([{"seasonNumber": 2, "episodeNumber": 5}]) == "S02E05"
    assert media._episodes_label([{"seasonNumber": 2, "episodeNumber": 5}, {"seasonNumber": 2, "episodeNumber": 6}]) == "Season 2"
    assert media._episodes_label([{}]) == ""


def test_versions_checked_by_radarr_come_2160p_first(app, monkeypatch):
    found = [
        {"guid": "a", "title": "Film.720p", "quality": {"quality": {"name": "HDTV-720p"}}, "seeders": 900},
        {"guid": "b", "title": "Film.2160p", "quality": {"quality": {"name": "WEBDL-2160p"}}, "seeders": 4},
        {"guid": "c", "title": "Film.1080p", "quality": {"quality": {"name": "Bluray-1080p"}}, "seeders": 50},
        {"guid": "d", "title": "Film.2160p.Remux", "quality": {"quality": {"name": "Remux-2160p"}}, "seeders": 80},
        {"guid": "e", "title": "Film.2160p.big", "quality": {"quality": {"name": "Bluray-2160p"}}, "seeders": 9},
        {"guid": "f", "title": "Film.CAM", "quality": {"quality": {"name": "CAM"}}, "seeders": 1000, "rejected": True},
    ]
    fake = FakeArr({"release": found})
    with app.app_context():
        m = media.Media(app.hub)
        monkeypatch.setattr(m, "arr", lambda kind: fake)
        order = [release["guid"] for release in m.releases("film", 1)]
    assert order == ["e", "b", "c", "a", "d", "f"]


def test_the_speed_panel_survives_an_odd_answer_from_qbittorrent(client, media_servers):
    login(client, "You")
    media_servers.torrents = {"error": "not a list"}
    reply = client.get("/api/media/speed")
    assert reply.status_code == 200 and reply.get_json()["counts"]["downloading"] == 0


def test_grabbing_a_version_with_a_strange_guid_is_refused(client, media_servers):
    login(client, "You")
    assert client.post("/api/media/versions/nojob/grab", json={"guid": ["a"]}).status_code == 400
    assert client.post("/api/media/versions/nojob/grab", json={"guid": {"a": 1}}).status_code == 400
