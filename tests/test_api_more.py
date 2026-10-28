"""More API tests: odd input that used to 500, permission edges, half-saved changes."""

from test_app import app, book_id, client, login, media_servers, series_server  # noqa: F401

from bookhub import db, kinds, series


def test_numbers_that_are_not_numbers_are_refused_not_crashed(client):
    login(client, "You")
    bid = book_id(client, "Dracula")
    assert client.post("/api/books/%d/rating" % bid, json={"rating": "lots"}).status_code == 400
    assert client.post("/api/books/%d/rating" % bid, json={"rating": [5]}).status_code == 400
    assert client.post("/api/books/%d/rating" % bid, json={"rating": 4}).status_code == 200
    assert client.get("/api/books/%d" % bid).get_json()["rating"] == 4
    assert client.post("/api/books/%d/rating" % bid, json={"rating": 0}).status_code == 200  # 0 still takes it away
    assert client.get("/api/books/%d" % bid).get_json()["rating"] == 0

    assert client.post("/api/books/%d/reading" % bid, json={"status": "reading", "percent": "half"}).status_code == 400
    assert client.post("/api/books/%d/reading" % bid, json={"status": "reading", "percent": "NaN"}).status_code == 400
    assert client.post("/api/books/%d/reading" % bid, json={"status": "reading", "percent": "40"}).status_code == 200
    assert client.get("/api/books/%d" % bid).get_json()["percent"] == 40

    assert client.post("/api/me/outside", json={"title": "North and South", "percent": "x"}).status_code == 400
    assert client.post("/api/me/outside", json={"title": "North and South", "status": "banana"}).status_code == 400
    assert client.post("/api/me/outside", json={"title": "North and South", "finished": ["x"], "pages": "lots"}).status_code == 200

    named = [b for b in client.get("/api/books").get_json()["books"] if b.get("series")]
    if named:
        name = named[0]["series"]
        assert client.post("/api/series-info/rating", json={"name": name, "rating": "five"}).status_code == 400
        assert client.post("/api/series-info/rating", json={"name": name, "rating": 5}).status_code == 200


def test_text_fields_that_are_not_text_count_as_blank(client):
    login(client, "You")
    bid = book_id(client, "Dracula")
    assert client.post("/api/profiles", json={"name": 5}).status_code == 400
    assert client.post("/api/books/%d/tags" % bid, json={"kind": "mine", "tag": ["Spooky"]}).status_code == 400
    assert client.delete("/api/books/%d/tags" % bid, json={"kind": "mine", "tag": ["Spooky"]}).status_code == 200
    assert client.post("/api/watch", json={"kind": "author", "value": 5}).status_code == 400
    assert client.post("/api/watchlist", json={"kind": "author", "value": {"a": 1}}).status_code == 400
    assert client.post("/api/me/outside", json={"title": ["Emma"]}).status_code == 400
    assert client.post("/api/books/kind", json={"tag": 7}).status_code == 400
    assert client.post("/api/me/authors", json={"author": {"a": 1}, "how": "like"}).status_code == 400
    assert client.post("/api/recommendations/dismiss", json={"key": ["x"]}).status_code == 200
    assert client.post("/api/admin/connections", json={"values": ["x"]}).status_code == 400


def test_a_strange_profile_id_at_sign_in_is_just_not_a_profile(client):
    assert client.post("/api/login", json={"profile_id": {"a": 1}, "passcode": "1234"}).status_code == 404
    assert client.post("/api/login", json={"profile_id": [1], "passcode": "1234"}).status_code == 404
    pid = client.get("/api/state").get_json()["profiles"][0]["id"]
    r = client.post("/api/login", json={"profile_id": pid, "admin_id": ["x"], "admin_passcode": "1234"})
    assert r.status_code == 403
    assert client.post("/api/login", json={"profile_id": str(pid), "passcode": "1234"}).status_code == 200  # "3" still works


def test_lists_of_ids_that_are_not_lists(client, series_server):
    login(client, "You")
    r = client.post("/api/books/kind", json={"kind": kinds.KINDS[0], "ids": 5})
    assert r.status_code == 200 and r.get_json()["updated"] == 0
    assert client.post("/api/series/chapters/read", json={"ids": 5}).status_code == 400
    assert client.post("/api/series/chapters/read", json={"ids": [True]}).status_code == 400  # True is not chapter 1


def test_an_admin_asking_about_a_missing_profile_hears_it_is_missing(client):
    login(client, "You")
    bid = book_id(client, "Dracula")
    assert client.get("/api/profiles/9999/devices").status_code == 404
    assert client.get("/api/profiles/9999/goodreads").status_code == 404
    assert client.post("/api/books/%d/reading" % bid, json={"status": "reading", "profile_id": 9999}).status_code == 404
    assert client.post("/api/books/%d/shelf" % bid, json={"on": True, "profile_id": 9999}).status_code == 404
    client.post("/api/logout")
    login(client, "Nan")
    profiles = {p["name"]: p["id"] for p in client.get("/api/state").get_json()["profiles"]}
    # a member still gets "only your own", missing profile or not
    assert client.get("/api/profiles/%d/devices" % profiles["You"]).status_code == 403
    assert client.post("/api/books/%d/reading" % bid, json={"status": "reading", "profile_id": 9999}).status_code == 403


def test_a_clashing_name_leaves_the_other_changes_unsaved_too(app, client):
    login(client, "You")
    client.post("/api/profiles", json={"name": "Kid"})
    other = client.post("/api/profiles", json={"name": "Kid two"}).get_json()["id"]
    r = client.patch("/api/profiles/%d" % other, json={"name": "Kid", "can_request_media": True, "can_series": False})
    assert r.status_code == 409
    me = next(p for p in client.get("/api/state").get_json()["profiles"] if p["id"] == other)
    assert me["name"] == "Kid two" and not me["can_request_media"] and me["can_series"]
    with app.app_context():
        assert other not in series.blocked_ids(app.hub)


def test_a_broken_films_and_tv_permission_list_does_not_break_the_hub(app, client):
    with app.app_context():
        db.kv_set(app.hub.con(), "media:allowed", "5")
    assert client.get("/api/state").status_code == 200
    login(client, "Nan")
    assert client.get("/api/media/search?kind=film&q=metro").status_code == 403


def test_members_without_the_switch_cannot_touch_films_and_tv(client, media_servers):
    login(client, "Nan")
    for method, url in (("post", "/api/media/request"), ("post", "/api/media/grab"), ("post", "/api/media/look-again/film/1"),
                        ("post", "/api/media/looking/film/1"), ("get", "/api/media/releases/film/1"),
                        ("post", "/api/media/versions/film/1"), ("post", "/api/media/versions/abc/grab")):
        r = getattr(client, method)(url, json={})
        assert r.status_code == 403, url
        assert "films and TV" in r.get_json()["error"]


def test_forgetting_downloads_wants_a_list(client, media_servers):
    login(client, "You")
    media_servers.torrents = [{"hash": "d" * 40, "name": "Old.Film.1999", "category": "movies", "progress": 0,
                               "state": "missingFiles", "num_seeds": 0}]
    r = client.post("/api/media/torrents/forget", json={"hashes": 5})
    assert r.status_code == 200 and r.get_json()["forgotten"] == 0


def test_a_book_data_file_with_the_wrong_insides_is_refused(client):
    login(client, "You")
    r = client.post("/api/admin/import", json={"format": "bookhub-bookdata", "tag_rules": ["not a rule"]})
    assert r.status_code == 400


def test_a_chapter_file_name_with_quotes_makes_a_tidy_header(client, series_server, monkeypatch):
    class Reply:
        headers = {"Content-Disposition": 'attachment; filename="Bad"Name\\x.cbz"', "Content-Type": "application/zip"}

        def iter_content(self, size):
            return iter([b"PK"])

    monkeypatch.setattr(series.Series, "raw", lambda self, path, stream=False: Reply())
    r = client.get("/api/series/chapters/1/file")
    assert r.status_code == 200
    assert r.headers["Content-Disposition"] == 'attachment; filename="BadNamex.cbz"'
