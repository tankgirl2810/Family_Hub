"""More tests for the modules behind the routes: background chores, outside services that answer oddly."""
import datetime
import time
from datetime import date

from test_app import FakeResponse, app, client, login, media_servers  # noqa: F401

from bookhub import chapterfile, hub as hub_module, kosync, series


# ------------------------------------------------------------------ background chores

def test_one_failing_chore_does_not_skip_the_rest_of_the_minute(app, monkeypatch):
    hub = app.hub
    ran = []

    def boom():
        # a write that dies half way: it must not be kept, nor hold the write lock for the chores after it
        hub.con().execute("INSERT INTO kv(key, value) VALUES('half-done', 'x')")
        raise RuntimeError("the downloads folder went away")

    def after():
        ran.append(hub.con().in_transaction)

    monkeypatch.setattr(hub_module, "chores_due", lambda hub, app, tick: [("boom", boom), ("after", after)])
    with app.app_context():
        hub_module._housekeeping(hub, app, 1)
        assert ran == [False]
        assert hub.con().execute("SELECT 1 FROM kv WHERE key='half-done'").fetchone() is None


def test_chores_run_on_their_own_minutes(app):
    hub = app.hub
    with app.app_context():
        first = [what for what, _ in hub_module.chores_due(hub, app, 0)]
        later = [what for what, _ in hub_module.chores_due(hub, app, 1)]
    assert "could not read the tag packs" in first and "could not read the tag packs" not in later
    for every_minute in ("could not import downloads", "could not back up the book data", "could not send books to devices"):
        assert every_minute in first and every_minute in later
    # the backup is written before the second-disk copy looks for it
    assert later.index("could not back up the book data") < later.index("could not start the copy to the second disk")


def test_stats_survive_a_finished_date_with_no_month(app):
    hub = app.hub
    with app.app_context():
        profile_id = hub.profiles()[0]["id"]
        before = hub.personal_stats(profile_id)["read_year"]
        hub.save_reading(profile_id, "ta:odd-import|someone", status="finished", finished=str(date.today().year), title="Odd Import")
        stats = hub.personal_stats(profile_id)  # used to crash on int("")
    assert stats["read_year"] == before + 1


# ------------------------------------------------------------------ reading apps

def test_a_nonsense_percentage_does_not_finish_a_book(app, client):
    import hashlib
    with app.app_context():
        book = next(b for b in app.hub.library.books() if b["title"] == "Emma")
        document = kosync.partial_md5(app.hub.library.file_path(book, "EPUB"))
    headers = {"x-auth-user": "You", "x-auth-key": hashlib.md5(b"demo").hexdigest()}
    assert client.put("/kosync/syncs/progress", headers=headers, json={"document": document, "percentage": "nan"}).status_code == 200
    login(client, "You")
    emma = next(b for b in client.get("/api/books").get_json()["books"] if b["title"] == "Emma")
    assert emma["status"] == "reading" and emma["percent"] == 0


# ------------------------------------------------------------------ films and TV

def _answer_oddly(monkeypatch, media_servers, odd):  # noqa: F811
    """Wrap the fake services: odd(method, url, params) returns a reply to use instead, or None."""
    from bookhub import media
    real = media_servers.request

    def request(method, url, headers=None, params=None, json=None, data=None, timeout=0):
        return odd(method, url, params) or real(method, url, headers=headers, params=params, json=json, data=data, timeout=timeout)
    monkeypatch.setattr(media.requests, "request", request)


def test_a_version_search_finishes_even_when_a_site_answers_with_junk(client, media_servers, monkeypatch):  # noqa: F811
    login(client, "You")
    client.post("/api/admin/media/connect")
    client.post("/api/media/request", json={"kind": "film", "id": 19})

    def odd(method, url, params):
        if url.endswith("/api/v1/search") and params and params.get("indexerIds") == 5:
            return FakeResponse(200, b"<html>Cloudflare says no</html>", "text/html")
        if url.endswith("/api/v3/release") and method == "GET":
            return FakeResponse(200, {"message": "indexers are busy"})
    _answer_oddly(monkeypatch, media_servers, odd)
    job = client.post("/api/media/versions/film/1").get_json()["job"]
    for _ in range(100):
        progress = client.get("/api/media/versions/" + job).get_json()
        if progress["done"]:
            break
        time.sleep(0.05)
    assert progress["done"] and progress["answered"] == 2 and progress["checked"] == []
    assert [found["title"] for found in progress["found"]][0] == "Metropolis.1927.2160p.WEB"  # the good site still counts


def test_connect_everything_carries_on_when_a_service_lacks_a_form(client, media_servers, monkeypatch):  # noqa: F811
    login(client, "You")
    _answer_oddly(monkeypatch, media_servers,
                  lambda method, url, params: FakeResponse(200, []) if ":7878/api/v3/downloadclient/schema" in url else None)
    reply = client.post("/api/admin/media/connect")
    assert reply.status_code == 200
    steps = {step["step"]: step for step in reply.get_json()["steps"]}
    assert not steps["Radarr → qBittorrent"]["ok"] and "does not offer QBittorrent" in steps["Radarr → qBittorrent"]["message"]
    assert steps["Sonarr → qBittorrent"]["ok"]


def test_service_status_survives_an_answer_that_is_not_json(app, media_servers, monkeypatch):  # noqa: F811
    _answer_oddly(monkeypatch, media_servers,
                  lambda method, url, params: FakeResponse(200, b"It works!", "text/html") if url.endswith(":7878/api/v3/system/status") else None)
    with app.app_context():
        found = app.hub.media.status()
    assert found["radarr"]["ok"] is False and "Unexpected answer" in found["radarr"]["error"]
    assert found["sonarr"]["ok"] is True


def test_damaged_notes_about_waiting_downloads_start_again_empty(app):
    from bookhub import db
    with app.app_context():
        db.kv_set(app.hub.con(), "media:parked", "{not json")
        assert app.hub.media.parked() == {}


# ------------------------------------------------------------------ series by chapter, Komga

def test_one_odd_series_does_not_stop_the_comic_files_of_the_rest(app, monkeypatch):
    hub = app.hub
    monkeypatch.setattr(series.Series, "configured", property(lambda self: True))
    monkeypatch.setattr(hub.series, "shelf", lambda: {"queued": 0, "series": [{"id": 1, "title": "Odd", "downloaded": 2},
                                                                             {"id": 2, "title": "Fine", "downloaded": 2}]})

    def detail(series_id):
        if series_id == 1:
            raise KeyError("chapters")  # the series server answered, just not as expected
        return {"id": 2, "title": "Fine", "doubles": 0}
    monkeypatch.setattr(hub.series, "detail", detail)
    kept = []
    monkeypatch.setattr(chapterfile, "keep_series", lambda hub, info, busy=False: kept.append(info["id"]))
    with app.app_context():
        chapterfile.keep_all(hub)
    assert kept == [2]


def test_komga_history_still_syncs_when_copying_files_fails(app, monkeypatch):
    from bookhub import komga
    monkeypatch.setattr(komga, "configured", lambda hub: True)

    def full(hub):
        raise chapterfile.NoRoom("Only 1.0 GB is free on the disk")
    monkeypatch.setattr(komga, "sync_files", full)
    synced = []
    monkeypatch.setattr(komga, "sync_history", lambda hub: synced.append(True))
    with app.app_context():
        komga.run(app.hub)
    assert synced == [True]


def test_komga_graphic_novel_history_syncs_while_the_series_server_is_away(app, monkeypatch):
    from bookhub import komga
    monkeypatch.setattr(series.Series, "configured", property(lambda self: True))
    monkeypatch.setattr(komga, "_libraries", lambda hub: [{"id": "g", "root": "/comics"}, {"id": "c", "root": "/data/series"}])
    people = [({"id": 1, "name": "Ann"}, ("ann@x", "pw")), ({"id": 2, "name": "Bo"}, ("bo@x", "pw"))]
    monkeypatch.setattr(komga, "accounts", lambda hub: people)
    graphic = []

    def graphic_history(hub, library, profile, auth):
        graphic.append(profile["name"])
        return 0, 0

    def chapter_history(hub, library, profile, auth):
        raise series.SeriesError("Could not reach the series server")
    monkeypatch.setattr(komga, "sync_graphic_history", graphic_history)
    monkeypatch.setattr(komga, "sync_chapter_history", chapter_history)
    with app.app_context():
        komga.sync_history(app.hub)
    assert graphic == ["Ann", "Bo"]


def test_something_else_answering_as_the_series_server_is_reported_not_crashed(app, client, monkeypatch):
    login(client, "You")
    client.post("/api/admin/connections", json={"values": {"series_url": "http://suwayomi:4567"}})

    class Reply:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return ["not", "graphql"]
    monkeypatch.setattr(series.requests, "post", lambda *args, **kwargs: Reply())
    with app.app_context():
        found = app.hub.series.status()
    assert found["configured"] and found["ok"] is False and found["error"]
    monkeypatch.setattr(series.Series, "gql", lambda self, query, variables=None, config=None, timeout=0: {"hello": "world"})
    with app.app_context():
        found = app.hub.series.status()
    assert found["ok"] is False and "not a series server" in found["error"]


def test_installed_series_extensions_are_kept_up_to_date(app, monkeypatch):
    asked = []

    def fake_gql(self, query, variables=None, config=None, timeout=0):
        asked.append((query, variables))
        if "fetchExtensions" in query:
            return {"fetchExtensions": {"extensions": [
                {"pkgName": "eu.kanade.mangafire", "name": "MangaFire", "isInstalled": True, "hasUpdate": True},
                {"pkgName": "eu.kanade.webtoons", "name": "Webtoons.com", "isInstalled": True, "hasUpdate": False},
                {"pkgName": "eu.kanade.other", "name": "Not installed", "isInstalled": False, "hasUpdate": True}]}}
        return {"updateExtensions": {"extensions": []}}

    monkeypatch.setattr(series.Series, "gql", fake_gql)
    with app.app_context():
        assert app.hub.series.update_extensions() == ["MangaFire"]
        # only what is installed and out of date; nothing is installed or removed
        assert asked[1][1] == {"ids": ["eu.kanade.mangafire"]} and "update:true" in asked[1][0]
        asked.clear()
        monkeypatch.setattr(series.Series, "gql", lambda self, query, variables=None, config=None, timeout=0: (
            asked.append(query) or {"fetchExtensions": {"extensions": []}}))
        assert app.hub.series.update_extensions() == [] and len(asked) == 1  # nothing out of date: no update asked for
        due = [what for what, _ in hub_module.chores_due(app.hub, app, 13)]
        assert "could not update the series extensions" in due
        assert "could not update the series extensions" not in [what for what, _ in hub_module.chores_due(app.hub, app, 14)]


def test_a_series_a_site_lists_with_no_chapters_still_opens(app, monkeypatch):
    asked = []

    def fake_gql(self, query, variables=None, config=None, timeout=0):
        asked.append(query.split("{")[1].split("(")[0])
        if "fetchMangaAndChapters" in query:
            raise series.SeriesError("The series server said: Exception while fetching data (/fetchMangaAndChapters) : No chapters found")
        if "fetchManga(" in query:
            return {"fetchManga": {"manga": {"id": 9}}}
        return {"manga": {"id": 9, "title": "Solo Leveling: Ragnarok", "inLibrary": False, "chapters": {"totalCount": 0}},
                "chapters": {"nodes": []}}

    monkeypatch.setattr(series.Series, "gql", fake_gql)
    with app.app_context():
        info = app.hub.series.detail(9)
    assert info["title"] == "Solo Leveling: Ragnarok" and info["chapters"] == 0 and info["chapters_list"] == []
    assert "fetchManga" in asked  # its details were still fetched


def test_other_series_server_errors_still_say_so(app, monkeypatch):
    def fake_gql(self, query, variables=None, config=None, timeout=0):
        if "fetchMangaAndChapters" in query:
            raise series.SeriesError("The series server said: HTTP error 522")
        return {"manga": {"id": 9, "title": "x", "inLibrary": False}, "chapters": {"nodes": []}}

    monkeypatch.setattr(series.Series, "gql", fake_gql)
    with app.app_context():
        try:
            app.hub.series.detail(9)
            raise AssertionError("a site that is down should not look like an empty series")
        except series.SeriesError as exc:
            assert "522" in str(exc)


def _ms(day):
    return int(datetime.datetime(day.year, day.month, day.day, 12).timestamp() * 1000)


def _chapters(*days):
    return [{"uploaded": _ms(day)} for day in days]


def test_how_often_a_series_comes_out_is_read_from_its_upload_dates():
    from datetime import date
    # Fridays, with the odd extra chapter on a Tuesday (seen on MangaFire)
    weekly = _chapters(date(2026, 10, 2), date(2026, 10, 6), date(2026, 10, 9), date(2026, 10, 16), date(2026, 10, 20),
                       date(2026, 10, 23), date(2026, 10, 30), date(2026, 11, 6))
    found = series.cadence(weekly, today=date(2026, 11, 7))
    assert found["weekday"] == "Fridays" and found["every"] == 7 and found["next"] == date(2026, 11, 13)
    assert not found["late"] and not series.due_for_a_look(found, date(2026, 11, 12))
    assert series.due_for_a_look(found, date(2026, 11, 13)) and series.due_for_a_look(found, date(2026, 11, 16))
    assert series.cadence(weekly, today=date(2026, 11, 20))["late"]
    assert series.cadence(weekly, today=date(2027, 3, 1))["paused"]  # months of nothing: on a break, no guess
    # every chapter given the same date (the day the site added the series): no schedule at all
    batch = series.cadence(_chapters(*[date(2026, 2, 4)] * 30), today=date(2026, 11, 7))
    assert batch["last"] == date(2026, 2, 4) and batch["every"] is None and batch["next"] is None
    # every three days, any weekday
    steady = series.cadence(_chapters(*[date(2026, 10, 1) + datetime.timedelta(days=3 * n) for n in range(8)]), today=date(2026, 10, 23))
    assert steady["weekday"] is None and steady["every"] == 3 and steady["next"] == date(2026, 10, 25)
    assert series.cadence([], today=date(2026, 11, 7))["last"] is None


def test_a_followed_series_is_looked_at_when_its_chapter_is_due(app, monkeypatch):
    from datetime import date
    mondays = [{"uploadDate": str(_ms(date(2026, 10, 5) + datetime.timedelta(weeks=n)))} for n in range(6)]  # last: 9 Nov
    asked = []

    def fake_gql(self, query, variables=None, config=None, timeout=0):
        return {"mangas": {"nodes": [{"id": 1, "status": "ONGOING", "chapters": {"nodes": mondays}},
                                     {"id": 2, "status": "COMPLETED", "chapters": {"nodes": mondays}}]}}

    monkeypatch.setattr(series.Series, "gql", fake_gql)
    monkeypatch.setattr(series.Series, "download", lambda self, series_id, refresh=False: asked.append((series_id, refresh)) or 0)
    monday = datetime.datetime(2026, 11, 16, 9).timestamp()
    with app.app_context():
        assert app.hub.series.look_when_due(now=monday - 86400) == []  # Sunday: not due yet
        assert app.hub.series.look_when_due(now=monday) == [1] and asked == [(1, True)]  # finished series left alone
        assert app.hub.series.look_when_due(now=monday + 3600) == []  # looked an hour ago
        assert app.hub.series.look_when_due(now=monday + 4 * 3600) == [1]
        assert "could not look for chapters that are due" in [what for what, _ in hub_module.chores_due(app.hub, app, 17)]
