"""End-to-end tests against the demo library. Run with:  pytest"""
import hashlib
import io
import json
import os
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from bookhub import catalogues, create_app, demo, devices, duplicates, goodreads, integrations, kinds, kosync, readers, secondcopy, series, tags  # noqa: E402
from bookhub.config import Config  # noqa: E402


@pytest.fixture()
def app(tmp_path, monkeypatch):
    folders = demo.build(tmp_path / "demo")
    monkeypatch.setenv("CALIBRE_LIBRARY", str(folders["library"]))
    monkeypatch.setenv("BOOKHUB_DATA_DIR", str(folders["data"]))
    monkeypatch.setenv("BACKGROUND_JOBS", "false")
    monkeypatch.setenv("DOWNLOADS_DIR", str(tmp_path / "downloads"))
    monkeypatch.setenv("INGEST_DIR", str(tmp_path / "ingest"))
    monkeypatch.setenv("INBOX_DIR", str(tmp_path / "inbox"))
    monkeypatch.setenv("ATTENTION_DIR", str(tmp_path / "attention"))
    for name in ("PROWLARR_URL", "PROWLARR_API_KEY", "QBITTORRENT_URL"):
        monkeypatch.delenv(name, raising=False)
    app = create_app(Config())
    with app.app_context():
        demo.seed(app.hub)
    return app


@pytest.fixture()
def client(app):
    return app.test_client()


def login(client, name, passcode="1234"):
    profiles = client.get("/api/state").get_json()["profiles"]
    pid = next(p["id"] for p in profiles if p["name"] == name)
    return client.post("/api/login", json={"profile_id": pid, "passcode": passcode})


def book_id(client, title):
    return next(b["id"] for b in client.get("/api/books").get_json()["books"] if b["title"] == title)


def test_passcode_and_admin_override(client):
    assert client.get("/api/books").status_code == 401
    assert login(client, "Nan", "0000").status_code == 403
    assert login(client, "Nan").status_code == 200
    assert client.get("/api/stats/admin").status_code == 403
    client.post("/api/logout")
    state = client.get("/api/state").get_json()
    ids = {p["name"]: p["id"] for p in state["profiles"]}
    r = client.post("/api/login", json={"profile_id": ids["Nan"], "admin_id": ids["You"], "admin_passcode": "1234"})
    assert r.status_code == 200
    me = client.get("/api/state").get_json()["me"]
    assert me["name"] == "Nan" and me["via_admin"] is True
    # a member cannot be used for an override
    client.post("/api/logout")
    r = client.post("/api/login", json={"profile_id": ids["You"], "admin_id": ids["Nan"], "admin_passcode": "1234"})
    assert r.status_code == 403


def test_lockout_after_wrong_tries(client):
    for _ in range(5):
        assert login(client, "Partner", "9999").status_code == 403
    assert login(client, "Partner", "1234").status_code == 429


def test_deleting_a_profile_leaves_nothing_behind(app, client):
    from bookhub import db
    login(client, "You")
    kid = client.post("/api/profiles", json={"name": "Kid"}).get_json()["id"]
    client.patch("/api/profiles/%d" % kid, json={"can_request_media": True})
    assert kid in json.loads(db.kv_get(app.hub.con(), "media:allowed"))
    assert client.delete("/api/profiles/%d" % kid).status_code == 200
    assert kid not in json.loads(db.kv_get(app.hub.con(), "media:allowed"))
    # A profile that is already gone says so, rather than pretending to delete it.
    assert client.delete("/api/profiles/%d" % kid).status_code == 404


def test_guest_can_download_but_not_track(client):
    assert client.post("/api/login", json={"guest": True}).status_code == 200
    books = client.get("/api/books").get_json()["books"]
    assert len(books) == 12 and all(b["status"] == "none" for b in books)
    bid = next(b["id"] for b in books if b["title"] == "Dracula")
    r = client.get("/api/books/%d/download/EPUB" % bid)
    assert r.status_code == 200 and r.data[:2] == b"PK"
    assert client.post("/api/books/%d/rating" % bid, json={"rating": 5}).status_code == 403
    assert client.get("/api/stats/me").status_code == 403


def test_tags_ratings_and_search(client):
    login(client, "You")
    bid = book_id(client, "Dracula")
    assert client.post("/api/books/%d/tags" % bid, json={"kind": "family", "tag": "Spooky season"}).status_code == 200
    assert client.post("/api/books/%d/tags" % bid, json={"kind": "mine", "tag": "Reread soon"}).status_code == 200
    client.post("/api/books/%d/rating" % bid, json={"rating": 4})
    detail = client.get("/api/books/%d" % bid).get_json()
    assert "Spooky season" in detail["tags"]["family"]
    assert "Reread soon" in detail["tags"]["mine"]
    assert "Found family" in detail["tags"]["community"]
    assert "Gothic" in detail["tags"]["epub"]
    assert detail["rating"] == 4
    assert {"name": "Partner", "rating": 5} in detail["family_ratings"]

    tags = client.get("/api/tags?q=spook").get_json()["tags"]
    assert tags and tags[0]["source"] == "family"
    assert client.get("/api/tags?q=spook&scopes=epub,community").get_json()["tags"] == []
    hits = client.get("/api/books?tag=Slow%20burn&scopes=community").get_json()["books"]
    assert {"Pride and Prejudice", "Emma", "Persuasion"} <= {b["title"] for b in hits}

    # personal tags stay private to the profile
    client.post("/api/logout")
    login(client, "Partner")
    other = client.get("/api/books/%d" % bid).get_json()
    assert other["tags"]["mine"] == [] and "Spooky season" in other["tags"]["family"]


def test_restricted_profile(client):
    login(client, "Nan")
    assert client.get("/api/discover/search?q=austen").status_code == 403
    assert client.post("/api/profiles", json={"name": "X"}).status_code == 403


def test_personal_and_admin_stats(client):
    login(client, "You")
    s = client.get("/api/stats/me").get_json()
    assert s["read_year"] == 9
    assert s["read_library"] == 5
    assert sum(m["library"] + m["outside"] for m in s["months"]) == 9
    assert s["pages_year"] > 2000
    assert len(s["reading_now"]) == 3
    filtered = client.get("/api/stats/me?kind=community&value=Slow%20burn").get_json()
    assert 0 < filtered["filtered_total"] < 9
    by_author = client.get("/api/stats/me?kind=author&value=Charlotte%20Bronte").get_json()
    assert by_author["filtered_total"] == 2
    a = client.get("/api/stats/admin").get_json()
    assert a["total"] == 12 and a["added_year"] == 12
    assert a["read_year"] == 9 + 1 + 3
    assert {f["name"] for f in a["family"]} == {"You", "Partner", "Nan"}


def test_recommendations(client):
    login(client, "You")
    bid = book_id(client, "Persuasion")
    books = client.get("/api/similar/%d" % bid).get_json()["books"]
    titles = [b["title"] for b in books]
    assert "Emma" in titles
    assert any(not b["in_library"] for b in books)  # tag packs suggest books to look for
    unread_first = [b["already_read"] for b in books]
    assert unread_first == sorted(unread_first)


def test_device_sync(app, client):
    with app.app_context():
        book = next(b for b in app.hub.library.books() if b["title"] == "Emma")
        document = kosync.partial_md5(app.hub.library.file_path(book, "EPUB"))
    key = hashlib.md5(b"demo").hexdigest()
    headers = {"x-auth-user": "You", "x-auth-key": key}
    assert client.get("/kosync/users/auth", headers={"x-auth-user": "You", "x-auth-key": "bad"}).status_code == 401
    assert client.get("/kosync/users/auth", headers=headers).status_code == 200
    r = client.put("/kosync/syncs/progress", headers=headers,
                   json={"document": document, "percentage": 0.42, "progress": "/body/x[12]", "device": "Kobo"})
    assert r.status_code == 200
    back = client.get("/kosync/syncs/progress/" + document, headers=headers).get_json()
    assert abs(back["percentage"] - 0.42) < 1e-6 and back["progress"] == "/body/x[12]"
    login(client, "You")
    emma = next(b for b in client.get("/api/books").get_json()["books"] if b["title"] == "Emma")
    assert emma["status"] == "reading" and emma["percent"] == 42 and emma["device"] == "Kobo"
    client.put("/kosync/syncs/progress", headers=headers, json={"document": document, "percentage": 1.0, "progress": "end"})
    emma = next(b for b in client.get("/api/books").get_json()["books"] if b["title"] == "Emma")
    assert emma["status"] == "finished"


def test_partial_md5_matches_koreader_sampling(tmp_path):
    data = bytes(range(256)) * 64  # 16 KB
    f = tmp_path / "sample.bin"
    f.write_bytes(data)
    expected = hashlib.md5(data[0:1024] + data[1024:2048] + data[4096:5120]).hexdigest()
    assert kosync.partial_md5(f) == expected


def test_export_import_roundtrip(app, client, tmp_path, monkeypatch):
    login(client, "You")
    export = client.get("/api/admin/export")
    data = json.loads(export.data)
    assert data["format"] == "bookhub-bookdata"
    pride = next(b for b in data["books"].values() if b["title"] == "Pride and Prejudice")
    assert pride["family_tags"] == ["Class difference"]
    assert pride["ratings"] == {"Partner": 5}
    assert pride["personal_tags"] == {"You": ["Comfort re-read"]}
    assert any(t["tag"] == "Slow burn" for t in pride["community_tags"])
    assert "Romance" in pride["epub_tags"]

    # restore into a brand-new, empty Family Hub pointing at the same library
    monkeypatch.setenv("BOOKHUB_DATA_DIR", str(tmp_path / "fresh"))
    fresh = create_app(Config())
    c = fresh.test_client()
    c.post("/api/setup", json={"name": "You", "passcode": "1234"})
    assert c.post("/api/admin/import", json=data).get_json()["books"] == len(data["books"])
    again = json.loads(c.get("/api/admin/export").data)
    pride2 = next(b for b in again["books"].values() if b["title"] == "Pride and Prejudice")
    assert pride2["ratings"] == pride["ratings"] and pride2["family_tags"] == pride["family_tags"]
    assert c.get("/api/stats/me").get_json()["read_year"] == 9


def test_backup_file_written(app, client):
    login(client, "You")
    r = client.post("/api/admin/backup").get_json()
    assert (app.hub.config.backups_dir / r["file"]).exists()


def test_downloads_are_copied_to_ingest(app, tmp_path):
    downloads = tmp_path / "downloads" / "Some.Book"
    downloads.mkdir(parents=True)
    (downloads / "book.epub").write_bytes(b"PK demo")
    (downloads / "notes.txt").write_text("ignore me")
    (downloads / "partial.epub.!qB").write_bytes(b"x")
    unfinished = tmp_path / "downloads" / "incomplete" / "Other.Book"
    unfinished.mkdir(parents=True)
    (unfinished / "half.epub").write_bytes(b"PK half")
    with app.app_context():
        assert app.hub.import_downloads(min_age=0) == ["book.epub"]
        assert app.hub.import_downloads(min_age=0) == []
    assert (tmp_path / "ingest" / "book.epub").read_bytes() == b"PK demo"
    assert (downloads / "book.epub").exists()  # original left in place for seeding


def test_outside_book_and_setup_guard(client):
    assert client.post("/api/setup", json={"name": "Z", "passcode": "1234"}).status_code == 409
    login(client, "You")
    r = client.post("/api/me/outside", json={"title": "North and South", "author": "Elizabeth Gaskell", "pages": 521})
    assert r.status_code == 200
    assert client.get("/api/stats/me").get_json()["read_year"] == 10
    outside = client.get("/api/books?filter=outside").get_json()["books"]
    assert "North and South" in {b["title"] for b in outside}


def test_inbox_is_tidied(app, tmp_path):
    inbox = tmp_path / "inbox"
    (inbox / "Shirley Jackson Collection").mkdir(parents=True)
    (inbox / "Shirley Jackson Collection" / "The Lottery.epub").write_bytes(b"PK lottery")
    (inbox / "Shirley Jackson Collection" / "cover.jpg").write_bytes(b"jpg")
    (inbox / "slides.ppt").write_bytes(b"ppt")
    with zipfile.ZipFile(inbox / "bundle.zip", "w") as z:
        z.writestr("inner/Emma.epub", b"PK emma")
        z.writestr("inner/readme.nfo", b"hello")
    with zipfile.ZipFile(inbox / "Star Trek 01.zip", "w") as z:
        for n in range(4):
            z.writestr("page%02d.jpg" % n, b"jpg")
    with zipfile.ZipFile(inbox / "junk.zip", "w") as z:  # a saved web page is not a comic
        for n in range(4):
            z.writestr("img%d.jpg" % n, b"jpg")
            z.writestr("page%d.html" % n, b"<p>")
    (inbox / "broken.zip").write_bytes(b"not a zip")
    pages = inbox / "Loose Comic 07"
    pages.mkdir()
    for n in range(3):
        (pages / ("p%d.png" % n)).write_bytes(b"png")

    with app.app_context():
        result = app.hub.intake.tidy_inbox(min_age=0)
        waiting = app.hub.intake.attention_items()
    ingest = {p.name for p in (tmp_path / "ingest").iterdir()}
    assert ingest == {"The Lottery.epub", "Emma.epub", "Star Trek 01.cbz", "Loose Comic 07.cbz"}
    assert sorted(result["delivered"]) == sorted(ingest) and result["attention"] == 3
    assert zipfile.ZipFile(tmp_path / "ingest" / "Loose Comic 07.cbz").namelist() == ["p0.png", "p1.png", "p2.png"]
    reasons = {i["path"]: i["reason"] for i in waiting["items"]}
    assert set(reasons) == {"slides.ppt", "junk.zip", "broken.zip"}
    assert "Could not open" in reasons["broken.zip"]
    # nothing is deleted: originals that went into the library are kept out of the way
    kept = tmp_path / "attention" / "_processed"
    assert (kept / "bundle.zip").exists() and (kept / "Shirley Jackson Collection" / "cover.jpg").exists()
    assert (kept / "Loose Comic 07" / "p0.png").exists() and waiting["processed"] == 5
    assert list(inbox.iterdir()) == []

    # A Kindle file beside an EPUB of the same name would become a second copy, so it is held back.
    for name in ("Persuasion.epub", "Persuasion.mobi", "Lonely.mobi"):
        (inbox / name).write_bytes(b"book")
    with app.app_context():
        assert sorted(app.hub.intake.tidy_inbox(min_age=0)["delivered"]) == ["Lonely.mobi", "Persuasion.epub"]
    assert (kept / "Persuasion.mobi").exists()

    with app.app_context():  # "try again" sends the same files round once more
        assert app.hub.intake.retry_attention() == 3
        assert app.hub.intake.tidy_inbox(min_age=0)["attention"] == 3


def test_archives_in_downloads_are_unpacked_not_moved(app, tmp_path):
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    with zipfile.ZipFile(downloads / "release.zip", "w") as z:
        z.writestr("Persuasion.epub", b"PK p")
        z.writestr("notes.txt", b"scene notes")
    with app.app_context():
        assert app.hub.import_downloads(min_age=0) == ["Persuasion.epub"]
        assert app.hub.import_downloads(min_age=0) == []
    assert (downloads / "release.zip").exists()
    assert not (tmp_path / "attention").exists()


def test_kind_guesses():
    guess = kinds.guess
    assert guess("Emma", "Jane Austen", tags=["Romance", "Classics"], formats=["EPUB"]) == ("Fiction", [])
    assert guess("Dune", tags=["Science Fiction"])[0] == "Fiction"
    assert guess("A Brief History", tags=["Science", "History"])[0] == "Non-fiction"
    assert guess("Masters of Doom", formats=["PDF"])[0] == "Unsorted"
    assert guess("Star Trek - Deep Space Nine (Malibu) 05", formats=["CBR"]) == ("Graphic novel", ["Star Trek"])
    assert guess("Clanbook Lasombra - Revised", formats=["PDF"]) == ("Tabletop", ["VTM", "Revised"])
    assert guess("Corebook", formats=["PDF"], origin="VampireTheMasqueradeV5/Corebook.pdf") == ("Tabletop", ["VTM", "V5"])
    assert guess("AD&D - Complete Set of 26 Books")[0] == "Tabletop"
    assert guess("Revised Economics", tags=["Economics"]) == ("Non-fiction", [])  # edition tags are for tabletop only


def test_kinds_filter_and_sorting(client):
    login(client, "You")
    books = client.get("/api/books?filter=library").get_json()["books"]
    assert {b["kind"] for b in books} == {"Fiction"}  # the demo library is all classic novels
    dracula, emma = book_id(client, "Dracula"), book_id(client, "Emma")
    r = client.post("/api/books/kind", json={"ids": [dracula, emma], "kind": "Tabletop", "tag": "VTM"})
    assert r.get_json()["updated"] == 2
    assert client.post("/api/books/kind", json={"ids": [dracula], "kind": "Nonsense"}).status_code == 400
    tabletop = client.get("/api/books?kinds=Tabletop").get_json()["books"]
    assert {b["title"] for b in tabletop} == {"Dracula", "Emma"}
    assert all("VTM" in b["tags"]["family"] for b in tabletop)
    assert len(client.get("/api/books?filter=library&kinds=Fiction").get_json()["books"]) == 10
    admin = client.get("/api/stats/admin").get_json()
    assert dict(admin["kinds"])["Tabletop"] == 2
    # a chosen kind is kept in the book-data file; guesses are not
    data = json.loads(client.get("/api/admin/export").data)
    assert sorted(b["title"] for b in data["books"].values() if b["kind"]) == ["Dracula", "Emma"]
    # a profile that cannot edit family tags cannot re-sort books
    client.post("/api/logout")
    login(client, "Partner")
    nan = next(p["id"] for p in client.get("/api/state").get_json()["profiles"] if p["name"] == "Nan")
    client.patch("/api/profiles/%d" % nan, json={"can_tag": False})
    client.post("/api/logout")
    login(client, "Nan")
    assert client.post("/api/books/kind", json={"ids": [dracula], "kind": "Fiction"}).status_code == 403


def test_forgotten_import_files_are_nudged(app, tmp_path):
    ingest = tmp_path / "ingest"
    ingest.mkdir()
    stuck = ingest / "Forgotten.epub"
    stuck.write_bytes(b"PK forgotten")
    (ingest / "arriving.epub.part").write_bytes(b"PK")
    os.utime(stuck, (1_000_000_000, 1_000_000_000))
    with app.app_context():
        assert app.hub.intake.nudge_stuck() == 0  # only just noticed
        assert stuck.stat().st_mtime == 1_000_000_000
        assert app.hub.intake.nudge_stuck(wait=0) == 1
    assert stuck.stat().st_mtime > 1_000_000_000 and stuck.read_bytes() == b"PK forgotten"


def test_connections_are_set_from_the_admin_page(app, client, monkeypatch):
    login(client, "Nan")
    assert client.get("/api/admin/connections").status_code == 403
    assert client.post("/api/admin/connections", json={"values": {"prowlarr_url": "http://x"}}).status_code == 403
    client.post("/api/logout")
    login(client, "You")
    assert client.get("/api/state").get_json()["discover_configured"] is False

    seen = {}

    def fake_get(url, headers=None, timeout=None, **kwargs):
        seen["url"], seen["key"] = url, headers["X-Api-Key"]

        class Reply:
            def raise_for_status(self):
                if seen["key"] != "s3cret":
                    raise integrations.requests.HTTPError("401 Client Error: Unauthorized")

            def json(self):
                return {"version": "2.6.5"}
        return Reply()

    monkeypatch.setattr(integrations.requests, "get", fake_get)
    # test before saving: nothing is stored yet
    trial = {"prowlarr_url": "http://prowlarr:9696/", "prowlarr_api_key": "wrong"}
    r = client.post("/api/admin/connections/test", json={"group": "prowlarr", "values": trial}).get_json()
    assert r["ok"] is False and "refused the API key" in r["message"]
    trial["prowlarr_api_key"] = "s3cret"
    r = client.post("/api/admin/connections/test", json={"group": "prowlarr", "values": trial}).get_json()
    assert r["ok"] is True and "2.6.5" in r["message"] and seen["url"] == "http://prowlarr:9696/api/v1/system/status"
    assert client.get("/api/state").get_json()["discover_configured"] is False

    assert client.post("/api/admin/connections", json={"values": {"prowlarr_url": "prowlarr:9696"}}).status_code == 400
    saved = client.post("/api/admin/connections", json={"values": trial})
    assert saved.status_code == 200 and b"s3cret" not in saved.data  # the key never comes back
    fields = {f["name"]: f for g in saved.get_json()["groups"] for f in g["fields"]}
    assert fields["prowlarr_url"]["value"] == "http://prowlarr:9696" and fields["prowlarr_url"]["source"] == "here"
    assert fields["prowlarr_api_key"]["value"] == "" and fields["prowlarr_api_key"]["set"] is True
    assert client.get("/api/state").get_json()["discover_configured"] is True
    assert client.get("/api/stats/admin").get_json()["services"]["search"]["ok"] is True
    assert b"s3cret" not in client.get("/api/admin/export").data

    # saving the address again without retyping the key keeps the key
    client.post("/api/admin/connections", json={"values": {"prowlarr_url": "http://prowlarr:9696"}})
    assert app.hub.config.prowlarr_api_key == "s3cret"
    # a saved value survives a restart and beats the environment; removing it falls back
    monkeypatch.setenv("PROWLARR_URL", "http://from-env:9696")
    again = create_app(Config())
    assert again.hub.config.prowlarr_url == "http://prowlarr:9696" and again.hub.config.prowlarr_api_key == "s3cret"
    c = again.test_client()
    login(c, "You")
    back = c.post("/api/admin/connections", json={"values": {"prowlarr_url": ""}}).get_json()
    fields = {f["name"]: f for g in back["groups"] for f in g["fields"]}
    assert fields["prowlarr_url"]["value"] == "http://from-env:9696" and fields["prowlarr_url"]["source"] == "env"


class FakeMail:
    """Stands in for the mail server: remembers what it was asked to send."""
    sent, refuse_login = [], False

    def __init__(self, host, port, timeout=None, context=None):
        self.host, self.port = host, port

    def starttls(self, context=None):
        pass

    def login(self, user, password):
        if FakeMail.refuse_login:
            raise devices.smtplib.SMTPAuthenticationError(535, b"bad credentials")

    def send_message(self, msg):
        FakeMail.sent.append(msg)

    def quit(self):
        pass


@pytest.fixture()
def mail(monkeypatch):
    FakeMail.sent, FakeMail.refuse_login = [], False
    monkeypatch.setattr(devices.smtplib, "SMTP", FakeMail)
    monkeypatch.setattr(devices.smtplib, "SMTP_SSL", FakeMail)
    monkeypatch.setattr(devices.Devices, "kick", lambda self, app: None)  # the test sends when it chooses
    return FakeMail


def ids_of(client):
    return {p["name"]: p["id"] for p in client.get("/api/state").get_json()["profiles"]}


def test_devices_belong_to_a_profile(client):
    ids = ids_of(client)
    login(client, "Nan")
    url = "/api/profiles/%d/devices" % ids["Nan"]
    assert client.post(url, json={"kind": "kindle", "name": "My Kindle", "address": "nope"}).status_code == 400
    assert client.post(url, json={"kind": "readest", "name": "Tablet", "link": "ftp://x"}).status_code == 400
    kid = client.post(url, json={"kind": "kindle", "name": "My Kindle", "address": "nan@kindle.com", "auto_send": True}).get_json()["id"]
    client.post(url, json={"kind": "readest", "name": "Tablet", "auto_send": True})
    listed = client.get(url).get_json()
    by_name = {d["name"]: d for d in listed["devices"]}
    assert by_name["My Kindle"]["auto_send"] is True and by_name["My Kindle"]["link"].startswith("https://read.amazon")
    assert by_name["Tablet"]["auto_send"] is False and by_name["Tablet"]["address"] == ""  # only a Kindle is emailed
    assert listed["mail_ready"] is False and listed["sender"] == ""
    assert client.patch("/api/devices/%d" % kid, json={"link": "https://example.org/mine"}).status_code == 200
    assert client.get(url).get_json()["devices"][0]["link"] == "https://example.org/mine"
    # another member cannot see or change them; an admin can
    assert client.get("/api/profiles/%d/devices" % ids["You"]).status_code == 403
    client.post("/api/logout")
    login(client, "Partner")
    assert len(client.get(url).get_json()["devices"]) == 2
    assert client.delete("/api/devices/%d" % kid).status_code == 200
    assert len(client.get(url).get_json()["devices"]) == 1
    client.post("/api/logout")
    client.post("/api/login", json={"guest": True})
    assert client.get(url).status_code == 403


def test_my_books_and_sending_to_a_kindle(app, client, mail):
    ids = ids_of(client)
    login(client, "You")
    nan = ids["Nan"]
    kid = client.post("/api/profiles/%d/devices" % nan, json={"kind": "kindle", "name": "Nan's Kindle", "address": "nan@kindle.com", "auto_send": True}).get_json()["id"]
    emma, island = book_id(client, "Emma"), book_id(client, "Treasure Island")

    # before mail is set up the book still goes on her shelf, and the page says why it was not sent
    r = client.post("/api/books/%d/shelf" % emma, json={"profile_id": nan, "on": True}).get_json()
    assert r == {"on": True, "sending": [], "still_on": []}
    theirs = next(s for s in client.get("/api/books/%d" % emma).get_json()["family_shelves"] if s["name"] == "Nan")
    assert theirs["on"] is True and theirs["devices"][0]["state"] == "failed" and "not set up" in theirs["devices"][0]["reason"]
    assert client.post("/api/books/%d/send" % emma, json={"device_id": kid}).status_code == 400

    saved = client.post("/api/admin/connections", json={"values": {"smtp_user": "books@example.org", "smtp_pass": "s3cr3t word"}})
    assert saved.status_code == 200 and b"s3cr3t word" not in saved.data
    fields = {f["name"]: f for g in saved.get_json()["groups"] for f in g["fields"]}
    assert fields["smtp_host"]["value"] == "smtp.gmail.com" and fields["smtp_host"]["source"] == "default"
    assert client.post("/api/admin/connections/test", json={"group": "mail"}).get_json()["ok"] is True
    mail.refuse_login = True
    assert "app password" in client.post("/api/admin/connections/test", json={"group": "mail"}).get_json()["message"]
    mail.refuse_login = False

    assert client.post("/api/books/%d/send" % emma, json={"device_id": kid}).status_code == 200
    r = client.post("/api/books/%d/shelf" % island, json={"profile_id": nan, "on": True}).get_json()
    assert r["sending"] == ["Nan's Kindle"]
    app.hub.devices.send_queued()
    assert len(mail.sent) == 2
    by_subject = {m["Subject"]: m for m in mail.sent}
    msg = by_subject["Emma"]
    assert msg["To"] == "nan@kindle.com" and msg["From"] == "books@example.org"
    part = next(msg.iter_attachments())
    assert part.get_filename() == "Emma - Jane Austen.epub" and part.get_content()[:2] == b"PK"
    assert next(by_subject["Treasure Island"].iter_attachments()).get_filename().endswith(".pdf")
    theirs = next(s for s in client.get("/api/books/%d" % emma).get_json()["family_shelves"] if s["name"] == "Nan")
    assert theirs["devices"][0]["state"] == "sent"
    # the admin records how she is getting on
    assert client.post("/api/books/%d/reading" % emma, json={"profile_id": nan, "status": "none"}).status_code == 200
    assert client.post("/api/books/%d/reading" % island, json={"profile_id": nan, "status": "finished"}).status_code == 200

    # a book over the limit is refused with the reason
    client.post("/api/admin/connections", json={"values": {"smtp_max_mb": "1"}})
    big = app.hub.library.file_path(app.hub.library.get(book_id(client, "Dracula")), "EPUB")
    with open(big, "ab") as f:
        f.write(b"\0" * (2 * 1048576))
    r = client.post("/api/books/%d/send" % book_id(client, "Dracula"), json={"device_id": kid})
    assert r.status_code == 400 and "Too big" in r.get_json()["error"]

    # Nan sees her shelf, and only hers; members cannot touch other shelves
    client.post("/api/logout")
    login(client, "Nan")
    mine = client.get("/api/books?filter=shelf").get_json()["books"]
    assert {b["title"] for b in mine} == {"Emma", "Treasure Island"} and all(b["on_shelf"] for b in mine)
    assert next(b for b in mine if b["title"] == "Treasure Island")["status"] == "finished"
    assert "family_shelves" not in client.get("/api/books/%d" % emma).get_json()
    assert client.post("/api/books/%d/shelf" % emma, json={"profile_id": ids["You"], "on": True}).status_code == 403
    assert client.post("/api/books/%d/reading" % emma, json={"profile_id": ids["You"], "status": "finished"}).status_code == 403
    # taking a book off keeps the history and says which device still has a copy
    r = client.post("/api/books/%d/shelf" % island, json={"on": False}).get_json()
    assert r["on"] is False and r["still_on"] == ["Nan's Kindle"]
    after = client.get("/api/books/%d" % island).get_json()
    assert after["on_shelf"] is False and after["status"] == "finished" and after["shelf"]["devices"][0]["state"] == "none"

    # shelves travel with the book-data export; the mail password and devices do not
    client.post("/api/logout")
    login(client, "You")
    export = client.get("/api/admin/export")
    assert b"s3cr3t word" not in export.data and b"nan@kindle.com" not in export.data
    books = json.loads(export.data)["books"]
    assert next(b for b in books.values() if b["title"] == "Emma")["shelves"] == ["Nan"]


def test_private_catalogue_for_reading_apps(app, client):
    import base64
    import xml.etree.ElementTree as ET
    ids = ids_of(client)
    login(client, "You")
    client.post("/api/books/%d/shelf" % book_id(client, "Persuasion"), json={"on": True})
    client.post("/api/logout")

    def auth(name, password):
        return {"Authorization": "Basic " + base64.b64encode(("%s:%s" % (name, password)).encode()).decode()}

    r = client.get("/opds")
    assert r.status_code == 401 and "Basic" in r.headers["WWW-Authenticate"]
    assert client.get("/opds", headers=auth("You", "1234")).status_code == 401  # the passcode is not the sync password
    you = auth("You", "demo")
    root = client.get("/opds", headers=you)
    assert root.status_code == 200 and b"/opds/mine" in root.data and "kind=navigation" in root.headers["Content-Type"]
    mine = client.get("/opds/mine", headers=you)
    assert b"Persuasion" in mine.data and b"Dracula" not in mine.data
    assert b"application/epub+zip" in mine.data and b"Jane Austen" in mine.data
    bid = app.hub.library.by_key(next(iter(app.hub.devices.shelf(ids["You"]))))["id"]
    book = client.get("/opds/book/%d/epub" % bid, headers=you)
    assert book.status_code == 200 and book.data[:2] == b"PK"
    assert client.get("/opds/book/%d/epub" % bid).status_code == 401
    assert b"Persuasion" not in client.get("/opds/mine", headers=auth("Nan", "demo")).data  # shelves are per person
    everything = client.get("/opds/all", headers=you)
    assert everything.data.count(b"<entry>") == 12
    found = client.get("/opds/search?q=austen", headers=you)
    assert found.data.count(b"<entry>") == 4 and b"Dracula" not in found.data
    ET.fromstring(everything.data)  # well-formed


# ------------------------------------------------- tidy tags and catalogues

def test_clutter_is_removed_from_book_file_tags():
    raw = ["Fiction", "ISBN-13:", "# Publisher: Routledge", "9780240810706", "2008-10-17", "Team DDU",
           "Charles River Media", "Fiction.Horror", "Mystery; Thriller & Suspense", "Thrillers", "Short Fiction",
           "ABC Amber CHM Converter http://www.processtext.com/abcchm.html", "CS", "game",
           "This document gives an introduction to programming games in C++ for Series 60.", "UNREGISTERD VERSION"]
    kept = tags.tidy(raw)
    assert kept == ["Fiction", "Horror", "Mystery", "Thriller", "Suspense", "Short stories", "Game"]
    # the book's own publisher, title and author are not tags either
    assert tags.tidy(["Tor", "Jane Austen", "Romance"], skip=["Tor", "Jane Austen"]) == ["Romance"]
    # catalogue shelving notes go, and genres are kept first when there are too many
    found = tags.tidy(["Widows", "nyt:hardcover-fiction=2020-07-12", "New York Times bestseller", "Accessible book",
                       "Fiction, psychological", "FICTION / Literary"], limit=3)
    assert found == ["Fiction", "Psychological fiction", "Literary fiction"]
    assert tags.dropped(raw, kept)[:2] == ["ISBN-13:", "# Publisher: Routledge"]
    # catalogues write names backwards and file subjects under broad headings
    assert tags.tidy(["Solo, Han (Fictitious character)", "Solo, Han, fiction", "Fiction, thrillers, suspense",
                      "Great Britain, history", "Comic books, strips, etc.", "Self realization", "Self-realization",
                      "collectionID:swNRa", "Antilles, wedge",
                      "Jeux vidéo"], plain=True) == [
        "Han Solo", "Fiction", "Thriller", "Suspense", "Great Britain", "History", "Comics", "Self realization",
        "Wedge Antilles"]


class FakeCatalogues:
    """Stands in for Open Library and Wikidata; records what was asked."""

    def __init__(self):
        self.asked = []
        self.down = False

    def get(self, url, params=None, headers=None, timeout=None):
        self.asked.append((url, dict(params or {})))
        if self.down:
            raise catalogues.requests.ConnectionError("no route")
        data = {}
        if "openlibrary.org" in url:
            data = {"docs": [
                {"title": "Dracula", "author_name": ["Somebody Else"], "subject": ["Wrong book"]},
                {"title": "Dracula", "author_name": ["Bram Stoker"],
                 "subject": ["Vampires", "Fiction, horror", "Accessible book", "nyt:x=1", "Transylvania (Romania)"]},
            ]} if (params.get("title") == "Dracula") else {"docs": []}
        elif params.get("action") == "wbsearchentities":
            data = {"search": [{"id": "Q1", "label": "Dracula"}, {"id": "Q2", "label": "Dracula"}]
                    if params["search"] == "Dracula" else []}
        elif params.get("action") == "wbgetentities" and params.get("props") == "claims":
            def ids(*qs):
                return [{"mainsnak": {"datavalue": {"value": {"id": q}}}} for q in qs]
            data = {"entities": {"Q1": {"claims": {"P50": ids("Q9"), "P136": ids("Q30")}},      # a film
                                 "Q2": {"claims": {"P50": ids("Q10"), "P136": ids("Q20", "Q21"), "P921": ids("Q22")}}}}
        elif params.get("action") == "wbgetentities":
            names = {"Q9": "Tod Browning", "Q10": "Bram Stoker", "Q20": "Gothic fiction", "Q21": "epistolary novel",
                     "Q22": "vampire", "Q30": "horror film"}
            data = {"entities": {q: {"labels": {"en": {"value": names[q]}}} for q in params["ids"].split("|")}}

        elif "comicvine" in url and "/search/" in url:
            if params["api_key"] != "cv-key-1":
                data = {"status_code": 100, "error": "Invalid API Key"}
            else:
                data = {"status_code": 1, "results": [
                    {"id": 1, "name": "Star Trek", "publisher": {"name": "Marvel"}, "start_year": "1980", "count_of_issues": 18},
                    {"id": 2, "name": "Star Trek", "publisher": {"name": "Gold Key"}, "start_year": "1967", "count_of_issues": 61},
                    {"id": 3, "name": "Star Trek: Voyager", "publisher": {"name": "Gold Key"}, "count_of_issues": 99},
                ] if params["query"] == "Star Trek" else []}
        elif "comicvine" in url:
            data = {"status_code": 1, "results": {
                "publisher": {"name": "Gold Key"},
                "concepts": [{"name": "Space exploration", "count": "40"}, {"name": "Variant cover", "count": "2"}],
                "characters": [{"name": "Spock", "count": "60"}, {"name": "James T. Kirk", "count": "61"},
                               {"name": "Uhura", "count": "30"}, {"name": "Scotty", "count": "29"},
                               {"name": "One-off alien", "count": "1"}]}} if url.endswith("/4050-2/") else {"status_code": 101}

        class Reply:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return data
        return Reply()


@pytest.fixture()
def outside(monkeypatch):
    fake = FakeCatalogues()
    monkeypatch.setattr(catalogues.requests, "get", fake.get)
    monkeypatch.setattr(catalogues.time, "sleep", lambda s: None)
    return fake


def test_community_tags_come_from_catalogues(app, client, outside):
    login(client, "You")
    bid = book_id(client, "Dracula")
    r = client.post("/api/books/%d/lookup" % bid).get_json()
    assert r["added"] >= 5 and r["sources"] == ["Open Library", "Wikidata"] and r["failed"] == []
    detail = client.get("/api/books/%d" % bid).get_json()
    community = detail["tags"]["community"]
    # the entry by the right author was used, clutter left out, and Wikidata's genres added
    assert {"Vampires", "Horror", "Transylvania", "Gothic fiction", "Epistolary novel"} <= set(community)
    assert not {"Wrong book", "Accessible book", "Horror film"} & set(community)
    assert "Open Library" in detail["community_sources"] and "Wikidata" in detail["community_sources"]
    # only the title and author went out
    assert all(set(p) <= {"title", "author", "q", "fields", "limit", "action", "search", "language", "type",
                          "format", "ids", "props", "languages"} for _, p in outside.asked)
    # a book the catalogues do not know is remembered and not asked about again
    emma = book_id(client, "Emma")
    assert client.post("/api/books/%d/lookup" % emma).get_json()["added"] == 0
    with app.app_context():
        before = len(outside.asked)
        assert app.hub.catalogues.run() == len(app.hub.library.books()) - 2
        assert app.hub.catalogues.run() == 0 and len(outside.asked) > before
        progress = app.hub.catalogues.progress()
    assert progress["asked"] == progress["books"] and progress["matched"] == 1 and progress["waiting"] == 0
    # a catalogue being down is a failure to retry, and keeps the tags already found
    outside.down = True
    r = client.post("/api/books/%d/lookup" % bid).get_json()
    assert r["added"] == 0 and r["failed"] == ["Open Library", "Wikidata"]
    assert "Vampires" in client.get("/api/books/%d" % bid).get_json()["tags"]["community"]
    # switched off, nothing is asked
    client.post("/api/admin/connections", json={"values": {"tag_lookups": "off"}})
    assert client.post("/api/books/%d/lookup" % bid).status_code == 400
    with app.app_context():
        assert app.hub.catalogues.pending() == []
    # members who cannot tag cannot trigger lookups, and the admin page is for admins
    client.post("/api/logout")
    login(client, "Nan")
    assert client.get("/api/admin/tags").status_code == 403
    assert client.post("/api/admin/tags/rule", json={"tag": "Vampires", "hide": True}).status_code == 403


def test_matching_refuses_the_wrong_book():
    same = catalogues.same_book
    assert same("Death in Her Hands", "Ottessa Moshfegh", "Death in Her Hands", ["Ottessa Moshfegh"])
    assert same("Beginning Illustration and Storyboarding for Games", "Pardew, Les.", "Beginning illustration and storyboarding for games", ["Les Pardew"])
    assert same("The Reformatory: A Novel", "Tananarive Due", "The Reformatory", ["Tananarive Due"])
    assert not same("Dracula", "Bram Stoker", "Dracula", ["Tod Browning"])
    assert not same("Emma", "Jane Austen", "Emma Brown", ["Jane Austen"])
    # no usable author: only a long exact title, or a title that carries the author, is trusted
    assert not same("Exiles", "Unknown", "Exiles", ["Jane Harper"])
    assert same("Game Design Theory & Practice, Richard Rouse", "Unknown", "Game Design: Theory & Practice", ["Richard Rouse III"])
    assert same("Writing for Animation, Comics, and Games", "admin", "Writing for animation, comics, and games", ["Christy Marx"])
    assert catalogues.query_title("Art_of_C.G_design.pdf") == "Art of C.G design"
    assert not catalogues.author_known("MR2") and catalogues.author_known("Lau Tzu")


def test_admin_can_hide_and_rename_machine_tags(app, client):
    login(client, "You")
    listed = client.get("/api/admin/tags").get_json()
    gothic = next(t for t in listed["tags"] if t["tag"] == "Gothic")
    assert gothic["count"] >= 1 and "Book file" in gothic["sources"] and gothic["becomes"] is None
    assert client.post("/api/admin/tags/rule", json={"tag": "Gothic", "hide": True}).status_code == 200
    assert client.post("/api/admin/tags/rule", json={"tag": "Slow burn", "becomes": "Slow-burn romance"}).status_code == 200
    assert client.post("/api/admin/tags/rule", json={"tag": "Slow burn", "becomes": " "}).status_code == 400
    bid = book_id(client, "Dracula")
    assert "Gothic" not in client.get("/api/books/%d" % bid).get_json()["tags"]["epub"]
    assert client.get("/api/tags?q=gothic").get_json()["tags"] == []
    hits = client.get("/api/books?tag=Slow-burn%20romance&scopes=community").get_json()["books"]
    assert {"Pride and Prejudice", "Emma", "Persuasion"} <= {b["title"] for b in hits}
    # the choices travel with the book data, and can be undone
    with app.app_context():
        exported = app.hub.export_data()
    assert {"tag": "Gothic", "becomes": ""} in exported["tag_rules"]
    assert client.post("/api/admin/tags/rule", json={"tag": "Gothic", "restore": True}).status_code == 200
    assert "Gothic" in client.get("/api/books/%d" % bid).get_json()["tags"]["epub"]
    stats = client.get("/api/stats/admin").get_json()["tags"]
    assert stats["hidden"] == 1 and stats["community"] >= 1 and stats["lookups"]["on"] is True
    assert "catalogues" in [g["id"] for g in client.get("/api/admin/connections").get_json()["groups"]]


def test_volumes_named_in_the_file_name_are_one_series(app, client):
    import sqlite3
    library = app.hub.library
    con = sqlite3.connect(library.metadata_db)
    for n in (2, 1, 10):  # added out of order
        new = demo.add_copy(library.dir, "Dracula", size=40 + n)
        con.execute("UPDATE books SET title=? WHERE id=?", ("Cherry Magic! v%02d (2020) (Digital) (1r0n)" % n, new))
        con.commit()
    con.close()
    os.utime(library.metadata_db, (2, 2))
    login(client, "You")
    books = client.get("/api/books").get_json()["books"]
    volumes = [b for b in books if b["series"] == "Cherry Magic!"]
    assert sorted(b["title"] for b in volumes) == ["Cherry Magic! · Volume 1", "Cherry Magic! · Volume 10", "Cherry Magic! · Volume 2"]
    shelf = client.get("/api/books", query_string={"series": "cherry magic!"}).get_json()["books"]
    assert [b["series_index"] for b in shelf] == [1, 2, 10]  # one series, in reading order
    # The library itself still has the names the files came with.
    assert any(b["title"].startswith("Cherry Magic! v01 (2020)") for b in library.books())


def test_comic_issues_annuals_and_story_titles_are_read_from_names():
    from bookhub import calibre
    v = calibre.volume_from_title
    assert v("Star Trek - Deep Space Nine (Malibu) 05", comic=True) == {"series": "Star Trek - Deep Space Nine (Malibu)", "index": 5, "label": "Issue 5", "subtitle": ""}
    assert v("Star Trek - Deep Space Nine (Marvel) 13 - Telepathy War 03", comic=True)["subtitle"] == "Telepathy War 03"
    annual = v("Star Trek - Deep Space Nine (Malibu) - Annual 01", comic=True)
    assert annual["label"] == "Annual 1" and annual["index"] > 1000  # after the numbered issues
    assert v("Fahrenheit 451") is None  # a bare number only counts for comics
    # Each issue's story landed in the author field: shown as the issue's title instead.
    books = [{"part": "Issue %d" % n, "series": "DS9", "own_formats": {"CBR": "x"}, "author": story, "subtitle": ""}
             for n, story in ((1, "Judgment Day"), (8, "Public Enemies"), (10, "Wedding of Doom"))]
    calibre._issue_titles_as_authors(books)
    assert [b["subtitle"] for b in books] == ["Judgment Day", "Public Enemies", "Wedding of Doom"] and books[0]["shown_author"] == ""


def test_comics_gather_by_franchise_and_same_titles_make_a_run():
    from bookhub import calibre
    assert calibre.collection_of("Star Trek - Deep Space Nine (Malibu)") == "Star Trek"
    assert calibre.collection_of("Star Trek (Gold Key)") == "Star Trek"
    assert calibre.collection_of("Cherry Magic! Thirty Years of Virginity Can Make You a Wizard") == "Cherry Magic! Thirty Years of Virginity Can Make You a Wizard"
    assert calibre.volume_from_title("Star Trek (Joe 90 - Top Secret)", comic=True) is None  # the 90 is inside the brackets
    books = [{"id": i, "title": "Star Trek (Book & Record Set)", "series": "", "own_formats": {"CBR": "x"}, "author": story, "part": "", "subtitle": ""}
             for i, story in ((43, "Passage to Moauv"), (21, "A Mirror for Futility"))]
    calibre._same_title_runs(books)
    calibre._issue_titles_as_authors(books)
    assert [(b["part"], b["subtitle"]) for b in sorted(books, key=lambda b: b["series_index"])] == [("Part 1", "A Mirror for Futility"), ("Part 2", "Passage to Moauv")]


def test_editions_mangled_titles_and_lost_apostrophes_are_shown_tidily():
    from bookhub import calibre
    v = calibre.volume_from_title
    assert v("Vampire The Masquerade V5") is None and v("Vampire The Wild West V20") is None  # editions, not volumes
    assert v("Cherry Magic! v10 (2024) (Digital)")["label"] == "Volume 10"
    assert v("Some Manga V01")["label"] == "Volume 1" and v("Some Comic V2", comic=True)["label"] == "Volume 2"
    assert calibre.unmangle("The Birdâ€™s Nest") == "The Bird’s Nest" and calibre.unmangle("JOHN LE CARRÃ‰") == "JOHN LE CARRÉ"
    assert calibre.unmangle("Ça va, Ãngel") == "Ça va, Ãngel" and calibre.unmangle("Dracula") == "Dracula"
    books = [{"part": "Issue %d" % n, "series": "Gold Key", "own_formats": {"CBR": "x"}, "author": story, "subtitle": ""}
             for n, story in ((2, "The Devil s Isle of Space"), (14, "Nobody Knows the Tribbles I ve Seen"), (23, "Gods of War"))]
    calibre._issue_titles_as_authors(books)
    assert [b["subtitle"] for b in books] == ["The Devil's Isle of Space", "Nobody Knows the Tribbles I've Seen", "Gods of War"]


def test_book_downloads_are_named_safely_and_shown_titles_are_tidied(app, client):
    import sqlite3
    library = app.hub.library
    con = sqlite3.connect(library.metadata_db)
    for title in ("Field Notes/Drafts", "The Birdâ€™s Nest"):
        new = demo.add_copy(library.dir, "Dracula", size=60 + len(title))
        con.execute("UPDATE books SET title=? WHERE id=?", (title, new))
        con.commit()
    con.close()
    os.utime(library.metadata_db, (3, 3))
    login(client, "You")
    titles = [b["title"] for b in client.get("/api/books").get_json()["books"]]
    assert "The Bird’s Nest" in titles and not any("â€" in t for t in titles)
    r = client.get("/api/books/%d/download/EPUB" % book_id(client, "Field Notes/Drafts"))
    disposition = r.headers["Content-Disposition"]
    assert r.status_code == 200 and "Field Notes Drafts - " in disposition and "/" not in disposition.split("filename=")[1]
    r.close()


def test_a_comic_series_downloads_as_one_joined_file(tmp_path):
    import zipfile
    from bookhub import seriesfile
    files = {}
    for n in (1, 2):
        f = tmp_path / ("v%d.cbz" % n)
        with zipfile.ZipFile(f, "w") as z:
            for page in ("page10.jpg", "page2.jpg", "notes.txt"):
                z.writestr(page, "v%d %s" % (n, page))
        files[n] = f

    class Lib:
        def file_path(self, b, fmt):
            return files[b["id"]]
    volumes = [{"id": n, "title": "Vol %d" % n, "formats": {"CBZ": "x"}} for n in (1, 2)]
    path, name = seriesfile.build(Lib(), volumes, "Test: Series")
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        assert names == ["001-0001.jpg", "001-0002.jpg", "002-0001.jpg", "002-0002.jpg"]  # page 2 before page 10
        assert z.read("001-0002.jpg") == b"v1 page10.jpg"
    assert name == "Test Series (whole series).cbz"
    path.unlink()


def test_copies_of_a_book_are_shown_as_one(app, client, tmp_path):
    library = app.hub.library
    before = len(library.books())
    first = next(b for b in library.books() if b["title"] == "Dracula")
    copy = demo.add_copy(library.dir, "Dracula", size=80)
    os.utime(library.metadata_db, (1, 1))  # the library manager changed the library
    assert len(library.books()) == before and len(library.all_books()) == before + 1

    login(client, "You")
    listed = [b for b in client.get("/api/books").get_json()["books"] if b["title"] == "Dracula"]
    assert len(listed) == 1
    shown = listed[0]["id"]
    other = ({first["id"], copy} - {shown}).pop()
    detail = client.get("/api/books/%d" % shown).get_json()
    assert [c["id"] for c in detail["copies"]] == [other] and detail["copies"][0]["files"][0]["format"] == "EPUB"
    # an old link to the copy shows the book, and the copy's own file can still be fetched
    assert client.get("/api/books/%d" % other).get_json()["id"] == shown
    assert client.get("/api/books/%d/download/EPUB" % other).status_code == 200
    stats = client.get("/api/stats/admin").get_json()
    assert stats["total"] == before and stats["copies"]["sets"] == 1 and stats["copies"]["hidden"] == 1
    assert stats["copies"]["spare"] > 0

    sets = client.get("/api/admin/copies").get_json()["sets"]
    assert len(sets) == 1 and sets[0]["together"] and len(sets[0]["books"]) == 2
    key = sets[0]["key"]
    assert client.post("/api/admin/copies", json={"key": key, "choice": "apart"}).status_code == 200
    assert len([b for b in client.get("/api/books").get_json()["books"] if b["title"] == "Dracula"]) == 2
    assert "copy_choices" in client.get("/api/admin/export").get_json()
    assert client.post("/api/admin/copies", json={"key": key, "choice": None}).status_code == 200
    assert len(library.books()) == before
    assert client.post("/api/admin/copies", json={"key": "ta:nothing|nobody", "choice": "apart"}).status_code == 404

    login(client, "Nan")
    assert client.get("/api/admin/copies").status_code == 403
    assert "copies" not in client.get("/api/books/%d" % shown).get_json()


def test_same_file_name_is_not_proof_of_the_same_book():
    def entry(n, title, author, size, key="ta:x|y"):
        return {"id": n, "title": title, "author": author, "key": key, "has_cover": True,
                "own_formats": {"PDF": "p/%d.pdf" % n}, "sizes": {"PDF": size}}

    from bookhub import calibre
    assert calibre.real_name(entry(1, "A Nameless Witch", "A. Lee Martinez", 5))
    assert not calibre.real_name(entry(1, "00 3634_FM_Intro", "uschugl", 5))
    assert not calibre.real_name(entry(1, "1592005934.pdf", "Unknown", 5, key="isbn:1592005934"))
    assert not calibre.real_name(entry(1, "Chapter One", "admin", 5))
    assert calibre.real_name(entry(1, "1984", "George Orwell", 5)) and not calibre.real_name(entry(1, "1592005934", "MR2", 5))

    lib = calibre.Library("nowhere")
    lib._by_id = {b["id"]: b for b in (entry(1, "scan_01", "admin", 100), entry(2, "scan_01", "admin", 100),
                                       entry(3, "scan_01", "admin", 999))}
    lib._group()
    assert sorted(b["id"] for b in lib._books) == [1, 3] and lib._by_id[2]["copy_of"] == 1
    lib._choices = {"ta:x|y": "together"}
    lib._group()
    assert len(lib._books) == 1 and len(lib._books[0]["copies"]) == 2


def test_comics_are_looked_up_by_series_on_comic_vine(app, client, outside):
    series = catalogues.comic_series
    assert series("Star Trek (Gold Key) 07") == ("Star Trek", "Gold Key")
    assert series("Star Trek - Deep Space Nine (Malibu) 23 - Secret of the Lost Orb 01") == ("Star Trek: Deep Space Nine", "Malibu")
    assert series("Young Avengers 005 (2013) (Variant)") == ("Young Avengers", "2013")
    assert series("Batman v2 014.cbz") == ("Batman", "") and series("Saga 012 - Chapter Twelve") == ("Saga", "")

    def comic(title):
        return {"title": title, "author": "Unknown", "authors": ["Unknown"], "isbn": "", "series": "", "formats": {"CBR": "x"}}

    catalogues._volumes.clear()
    config = app.hub.config
    assert "comicvine" not in catalogues.active(config)  # no key, not used
    login(client, "You")
    client.post("/api/admin/connections", json={"values": {"comic_vine_key": "cv-key-1"}})
    assert "comicvine" in catalogues.active(config)
    # the publisher in brackets picks the right series; the most-seen characters and themes become tags
    found = catalogues.comic_vine(config, comic("Star Trek (Gold Key) 07"))
    assert found == ["Gold Key", "Space exploration", "Variant cover", "James T. Kirk", "Spock", "Uhura", "Scotty"]
    asked = len(outside.asked)
    assert catalogues.comic_vine(config, comic("Star Trek (Gold Key) 09")) == found and len(outside.asked) == asked
    assert catalogues.comic_vine(config, comic("Star Trek (Malibu) 01")) is None  # no such series from that publisher
    assert catalogues.comic_vine(config, comic("Nobody Knows This 01")) is None
    # only comics are sent to it
    novel = {"formats": {"EPUB": "x"}}
    assert not catalogues.suits("comicvine", novel, "Fiction") and catalogues.suits("openlibrary", novel, "Fiction")
    assert catalogues.suits("comicvine", novel, "Graphic novel") and catalogues.suits("comicvine", comic("x"), None)
    # a wrong key is reported by the Test button, and the key itself is never sent back
    r = client.post("/api/admin/connections/test", json={"group": "catalogues", "values": {"comic_vine_key": "bad"}}).get_json()
    assert not r["ok"] and "Comic Vine: could not be reached (it refused the API key)" in r["message"]
    assert "cv-key-1" not in json.dumps(client.get("/api/admin/connections").get_json())
    assert "cv-key-1" not in json.dumps(client.get("/api/admin/export").get_json())



def shelf_feed(books):
    items = "".join("""
  <item>
    <title>%s</title>
    <book_id>%s</book_id>
    <book id="%s"><num_pages>%s</num_pages></book>
    <author_name>%s</author_name>
    <isbn>%s</isbn>
    <user_rating>%s</user_rating>
    <user_read_at><![CDATA[%s]]></user_read_at>
    <user_date_added><![CDATA[Fri, 11 Sep 2026 10:07:58 -0700]]></user_date_added>
  </item>""" % (title, gid, gid, pages, author, isbn, rating, read_at) for title, gid, pages, author, isbn, rating, read_at in books)
    return ('<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>shelf</title>%s</channel></rss>' % items).encode()


class FakeGoodreads:
    def __init__(self):
        self.shelves = {
            "read": [("Dracula", "17245", 418, "Bram Stoker", "", 5, "Tue, 18 Aug 2026 00:00:00 +0000"),
                     ("The Long Ships (The Long Ships, #1-2)", "10081041", 528, "Frans G. Bengtsson", "159017416X", 4,
                      "Sat, 01 Aug 2026 00:00:00 +0000"),
                     ("Persuasion", "2156", 249, "Jane Austen", "", 0, "")],
            "currently-reading": [("Emma", "6969", 474, "Jane Austen", "", 0, "")],
        }
        self.asked = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.asked.append((url, dict(params or {})))
        fake = self

        class Reply:
            status_code = 200 if url.endswith("/4242") else 404
            content = shelf_feed(fake.shelves[params["shelf"]] if params["page"] == 1 else [])
        return Reply()


@pytest.fixture()
def shelves(monkeypatch):
    from bookhub import api, goodreads
    fake = FakeGoodreads()
    monkeypatch.setattr(goodreads.requests, "get", fake.get)

    def at_once(profile_id, full):  # the real one reads the shelves on another thread
        try:
            goodreads.sync(api.hub(), profile_id, full=full)
        except goodreads.GoodreadsError:
            pass
        return True
    monkeypatch.setattr(api, "_goodreads_sync_later", at_once)
    return fake


def test_goodreads_links_and_files_are_understood():
    from bookhub import goodreads
    assert goodreads.user_id("https://www.goodreads.com/user/show/4242-nan-reads") == "4242"
    assert goodreads.user_id("goodreads.com/review/list/4242?shelf=read") == "4242" and goodreads.user_id(" 4242 ") == "4242"
    assert goodreads.user_id("https://example.com/user/show/4242") == "" and goodreads.user_id("nan") == ""
    assert goodreads.plain_title("The Pale Horseman (The Saxon Stories, #2)") == "The Pale Horseman"
    assert goodreads.plain_title("Frankenstein (or the Modern Prometheus)") == "Frankenstein (or the Modern Prometheus)"
    export = ('Book Id,Title,Author,ISBN,ISBN13,My Rating,Number of Pages,Date Read,Date Added,Exclusive Shelf\n'
              '5,"Dune (Dune, #1)",Frank Herbert,"=""0441172717""","=""9780441172719""",5,604,2026/03/04,2026/01/02,read\n'
              '6,Wanted Some Day,Some One,,,0,,,2026/01/02,to-read\n'
              '7,Emma,Jane Austen,,,0,474,,2026/01/02,currently-reading\n')
    rows = goodreads.parse_csv(export)
    assert [(r["title"], r["status"]) for r in rows] == [("Dune", "finished"), ("Emma", "reading")]
    assert rows[0]["isbn"] == "9780441172719" and rows[0]["finished"] == "2026-03-04" and rows[0]["rating"] == 5
    with pytest.raises(goodreads.GoodreadsError):
        goodreads.parse_csv("a,b\n1,2\n")


def test_goodreads_shelves_and_books_only_on_kindle(app, client, shelves):
    ids = {p["name"]: p["id"] for p in client.get("/api/state").get_json()["profiles"]}
    nan, you = ids["Nan"], ids["You"]

    def book(title, flt=None):
        url = "/api/books" + ("?filter=" + flt if flt else "")
        return next((b for b in client.get(url).get_json()["books"] if b["title"] == title), None)

    login(client, "Nan")
    # progress typed in by hand is kept; a hand-set "reading" does become finished
    client.post("/api/books/%d/reading" % book_id(client, "Emma"), json={"percent": 40})
    client.post("/api/books/%d/reading" % book_id(client, "Persuasion"), json={"status": "reading"})
    assert client.post("/api/profiles/%d/goodreads" % nan, json={"link": "my page"}).status_code == 400
    r = client.post("/api/profiles/%d/goodreads" % nan, json={"link": "https://www.goodreads.com/user/show/4242-nan"}).get_json()
    assert r["linked"] and r["mine"] and r["url"].endswith("4242-nan") and r["synced"] and r["books"] == 4 and not r["note"]
    assert {p["shelf"] for _, p in shelves.asked} == {"read", "currently-reading"}
    dracula = book("Dracula")
    assert dracula["in_library"] and dracula["status"] == "finished" and dracula["finished"] == "2026-08-18"
    assert dracula["rating"] == 5 and dracula["goodreads_id"] == "17245" and not dracula["kindle"]
    assert book("Emma")["percent"] == 40 and book("Persuasion")["status"] == "finished"
    ships = book("The Long Ships")
    assert ships and not ships["in_library"] and ships["kindle"] and ships["status"] == "finished" and ships["rating"] == 4
    assert book("The Long Ships", "kindle") and book("The Long Ships", "outside")
    assert client.get("/api/books/%d" % dracula["id"]).get_json()["goodreads_id"] == "17245"
    assert client.get("/api/admin/elsewhere").status_code == 403
    assert client.get("/api/profiles/%d/goodreads" % you).status_code == 403
    # reading the shelves again changes nothing
    again = client.post("/api/profiles/%d/goodreads" % nan, json={"sync": True}).get_json()
    assert again["books"] == 4 and book("Emma")["percent"] == 40

    # someone else in the family sees the book, but not whose it is, nor Nan's rating or progress
    client.post("/api/logout")
    login(client, "You")
    theirs = book("The Long Ships")
    assert theirs["kindle"] and theirs["status"] == "none" and theirs["rating"] == 0 and theirs["finished"] is None
    assert "Nan" not in json.dumps(theirs) and book("The Long Ships", "outside") is None and book("The Long Ships", "kindle")
    assert book("Dracula")["goodreads_id"] == ""
    # an admin helping Nan is told a link is saved, not what it is; the link is in no list, export or backup
    seen = client.get("/api/profiles/%d/goodreads" % nan).get_json()
    assert seen["linked"] and not seen["mine"] and seen["url"] == "" and seen["user_id"] == ""
    for url in ("/api/state", "/api/admin/export", "/api/stats/admin"):
        assert "4242" not in json.dumps(client.get(url).get_json())
    assert client.get("/api/stats/admin").get_json()["elsewhere"] == 1
    # only the Admin page says whose it is
    assert client.get("/api/admin/elsewhere").get_json()["books"] == [
        {"key": "ta:the-long-ships|frans-g-bengtsson", "title": "The Long Ships", "author": "Frans G. Bengtsson",
         "goodreads_id": "10081041", "people": ["Nan"]}]
    # an export file does the same job for a private profile, and the same book is one entry
    export = ("Book Id,Title,Author,ISBN,ISBN13,My Rating,Number of Pages,Date Read,Date Added,Exclusive Shelf\n"
              "10081041,The Long Ships,Frans G. Bengtsson,,,3,528,2026/02/03,2026/01/02,read\n")
    r = client.post("/api/profiles/%d/goodreads/import" % you, data={"file": (io.BytesIO(export.encode()), "goodreads_library_export.csv")})
    assert r.get_json() == {"books": 1, "changed": 1}
    assert client.get("/api/admin/elsewhere").get_json()["books"][0]["people"] == ["You", "Nan"]
    assert book("The Long Ships")["rating"] == 3 and book("The Long Ships", "outside")
    bad = client.post("/api/profiles/%d/goodreads/import" % you, data={"file": (io.BytesIO(b"x,y\n1,2\n"), "x.csv")})
    assert bad.status_code == 400

    # a subtitle on one side does not hide a match with the library
    from bookhub import goodreads
    with app.app_context():
        emma = app.hub.library.by_key(goodreads.key_for(app.hub, "Emma: A Novel", "Jane Austen"))
        assert emma and emma["title"] == "Emma"
        assert goodreads.key_for(app.hub, "Emma", "Somebody Else") == "ta:emma|somebody-else"

    # guests see the library only
    client.post("/api/logout")
    client.post("/api/login", json={"guest": True})
    assert book("The Long Ships") is None and book("Dracula")
    assert client.get("/api/profiles/%d/goodreads" % nan).status_code == 403

    # a profile number Goodreads does not know is reported, and removing the link takes the books off the family list
    client.post("/api/logout")
    login(client, "Nan")
    r = client.post("/api/profiles/%d/goodreads" % nan, json={"link": "https://www.goodreads.com/user/show/777"}).get_json()
    assert r["linked"] and not r["synced"] and "does not know that profile" in r["note"]
    r = client.post("/api/profiles/%d/goodreads" % nan, json={"link": ""}).get_json()
    assert not r["linked"] and r["books"] == 0
    assert book("The Long Ships")["status"] == "finished"  # her history stays
    client.post("/api/logout")
    login(client, "You")
    assert client.get("/api/admin/elsewhere").get_json()["books"][0]["people"] == ["You"]


def test_a_kindle_only_book_that_arrives_in_the_library_is_joined_up(app, client, shelves):
    from bookhub import goodreads
    shelves.shelves = {"read": [("Dracula: A Mystery Story", "17245", 418, "Bram Stoker", "", 5, "Tue, 18 Aug 2026 00:00:00 +0000")],
                       "currently-reading": []}
    login(client, "Nan")
    nan = next(p["id"] for p in client.get("/api/state").get_json()["profiles"] if p["name"] == "Nan")
    with app.app_context():
        hub = app.hub
        # as if the shelf had been read before the library held the book
        hub.save_reading(nan, "ta:dracula-old-edition|bram-stoker", status="finished", source="goodreads",
                         title="Dracula", author="Bram Stoker")
        hub.set_rating("ta:dracula-old-edition|bram-stoker", nan, 4)
        hub.con().execute("INSERT INTO goodreads_books(profile_id, book_key, gr_id, status, at) VALUES(?,?,?,?,?)",
                          (nan, "ta:dracula-old-edition|bram-stoker", "17245", "finished", "2026-01-01T00:00:00Z"))
        hub.con().commit()
        assert len(goodreads.elsewhere(hub)) == 1
        assert goodreads.reconcile(hub) == 1 and goodreads.reconcile(hub) == 0
        assert goodreads.elsewhere(hub) == {}
    dracula = next(b for b in client.get("/api/books").get_json()["books"] if b["title"] == "Dracula")
    assert dracula["in_library"] and dracula["status"] == "finished" and dracula["rating"] == 4 and dracula["goodreads_id"] == "17245"
    assert not [b for b in client.get("/api/books").get_json()["books"] if b["kindle"]]


def test_covers_can_be_changed_without_touching_the_library(app, client, monkeypatch):
    from PIL import Image
    from bookhub import covers

    def picture(size=(300, 450), kind="PNG"):
        out = io.BytesIO()
        Image.new("RGB", size, "#336699").save(out, kind)
        return out.getvalue()

    asked = []

    def fake_get(url, params=None, headers=None, timeout=None, stream=False):
        asked.append(url)

        class Reply:
            status_code = 200 if url.endswith("/b/id/55-L.jpg") or url.endswith("/b/id/55-M.jpg") else 404
            raw = type("Raw", (), {"read": staticmethod(lambda n, decode_content=True: picture())})
        return Reply()
    monkeypatch.setattr(covers.requests, "get", fake_get)
    monkeypatch.setattr(covers.catalogues, "_get", lambda url, params, headers=None: (
        {"docs": [{"key": "/works/W1", "title": "Dracula", "author_name": ["Bram Stoker"], "cover_i": 55, "first_publish_year": 1897},
                  {"key": "/works/W2", "title": "Dracula", "author_name": ["Someone Else"], "cover_i": 66}]}
        if "search.json" in url else {"entries": [{"covers": [55, 77, -1], "publishers": ["Penguin"], "publish_date": "2003"}]}))

    login(client, "You")
    bid = book_id(client, "Dracula")
    with app.app_context():
        library_files = sorted(str(p) for p in app.hub.library.dir.rglob("*"))
    assert client.get("/api/books/%d/cover" % bid).status_code == 404  # the sample library has no covers
    assert not client.get("/api/books/%d" % bid).get_json()["has_cover"]

    # a title cover
    assert client.post("/api/books/%d/cover" % bid, json={"generated": True}).get_json()["source"] == "generated"
    detail = client.get("/api/books/%d" % bid).get_json()
    assert detail["has_cover"] and detail["own_cover"] == "generated" and detail["cover_v"]
    made = client.get("/api/books/%d/cover" % bid)
    assert made.status_code == 200 and Image.open(io.BytesIO(made.data)).size == (600, 900)
    listed = next(b for b in client.get("/api/books").get_json()["books"] if b["id"] == bid)
    assert listed["has_cover"] and listed["cover_v"] == detail["cover_v"]

    # an uploaded picture replaces it, shrunk to a sensible size; rubbish is refused
    r = client.post("/api/books/%d/cover" % bid, data={"file": (io.BytesIO(picture((2000, 3000))), "big.png")})
    assert r.get_json()["source"] == "upload"
    assert Image.open(io.BytesIO(client.get("/api/books/%d/cover" % bid).data)).size == (900, 1350)
    assert client.post("/api/books/%d/cover" % bid, data={"file": (io.BytesIO(b"not a picture"), "x.jpg")}).status_code == 400
    assert client.post("/api/books/%d/cover" % bid, data={"file": (io.BytesIO(picture((20, 20))), "tiny.png")}).status_code == 400
    assert client.post("/api/books/%d/cover" % bid, json={}).status_code == 400

    # Open Library: only the right author's covers are offered, other editions included
    found = client.get("/api/books/%d/cover/options" % bid).get_json()["options"]
    assert [o["url"] for o in found] == ["https://covers.openlibrary.org/b/id/55-L.jpg", "https://covers.openlibrary.org/b/id/77-L.jpg"]
    assert found[0]["label"] == "Open Library, 1897" and found[1]["label"] == "Penguin, 2003"
    assert client.post("/api/books/%d/cover" % bid, json={"url": found[0]["url"]}).get_json()["source"] == "openlibrary"
    assert client.get("/api/covers/preview?url=https://covers.openlibrary.org/b/id/55-M.jpg").status_code == 200
    # pictures are fetched from Open Library's cover store and nowhere else
    for bad in ("http://covers.openlibrary.org/b/id/55-L.jpg", "https://evil.example/b/id/55-L.jpg",
                "https://covers.openlibrary.org.evil.example/b/id/55-L.jpg", "http://127.0.0.1:8090/api/admin/export"):
        assert client.post("/api/books/%d/cover" % bid, json={"url": bad}).status_code == 400
        assert client.get("/api/covers/preview?url=" + bad).status_code == 404
    assert all(u.startswith("https://covers.openlibrary.org/b/") for u in asked)

    # reading apps get the chosen cover too
    with app.app_context():
        assert app.hub.covers.path(app.hub.library.get(bid)["key"]).is_file()
        # nothing in the library changed
        assert sorted(str(p) for p in app.hub.library.dir.rglob("*")) == library_files

    # back to the library's cover
    assert client.delete("/api/books/%d/cover" % bid).status_code == 200
    assert client.get("/api/books/%d/cover" % bid).status_code == 404
    assert client.get("/api/books/%d" % bid).get_json()["own_cover"] == ""

    # someone who cannot edit family tags cannot change covers; guests cannot either
    client.patch("/api/profiles/%d" % next(p["id"] for p in client.get("/api/state").get_json()["profiles"] if p["name"] == "Nan"),
                 json={"can_tag": False})
    client.post("/api/logout")
    login(client, "Nan")
    assert client.post("/api/books/%d/cover" % bid, json={"generated": True}).status_code == 403
    assert client.get("/api/books/%d/cover/options" % bid).status_code == 403
    client.post("/api/logout")
    client.post("/api/login", json={"guest": True})
    assert client.post("/api/books/%d/cover" % bid, json={"generated": True}).status_code == 403


def test_suggestions_come_from_what_someone_has_enjoyed(app, client, monkeypatch):
    from bookhub import recommend
    asked = []

    def fake(url, params, headers=None):
        asked.append(dict(params))
        if params.get("author") == "Jane Austen":
            return {"docs": [
                {"title": "Emma", "author_name": ["Jane Austen"], "first_publish_year": 1815, "cover_i": 1},        # owned
                {"title": "Lady Susan", "author_name": ["Jane Austen"], "first_publish_year": 1871, "cover_i": 2},
                {"title": "Sanditon", "author_name": ["Jane Austen"], "first_publish_year": 1817},
                {"title": "Orgullo y prejuicio", "author_name": ["Jane Austen"], "language": ["spa"]},             # a translation
                {"title": "Best Love Stories", "author_name": ["A", "B", "Jane Austen"]},                           # an anthology
                {"title": "Austen Country", "author_name": ["Jane Smith"]}]}                                        # somebody else
        if "subject:" in params.get("q", ""):
            return {"docs": [{"title": "A Modern Romance", "author_name": ["New Writer"], "first_publish_year": 2019, "cover_i": 9}]}
        return {"docs": []}
    monkeypatch.setattr(recommend.catalogues, "_get", fake)

    client.post("/api/logout")
    login(client, "Partner")
    start = client.get("/api/recommendations").get_json()
    assert not start["ready"] and start["books"] == [] and start["need"] == 5
    view = client.get("/api/books?filter=foryou").get_json()
    assert view["books"] == [] and view["taste"]["ready"] is False
    assert client.post("/api/recommendations/refresh").get_json()["books"] == [] and asked == []  # too little to go on

    titles = ["Persuasion", "Emma", "Little Women", "Wuthering Heights"]  # on top of the two the sample data gives her
    unread_austen = [b["title"] for b in client.get("/api/books").get_json()["books"]
                     if b["author"] == "Jane Austen" and b["title"] not in titles and b["status"] == "none" and not b["rating"]]
    for title in titles:
        bid = book_id(client, title)
        client.post("/api/books/%d/rating" % bid, json={"rating": 5 if title != "Wuthering Heights" else 1})
    view = client.get("/api/books?filter=foryou").get_json()
    assert view["taste"]["ready"] and view["total"] >= 1
    listed = [b["title"] for b in view["books"]]
    # her other Austens come first, each with a reason; nothing she has rated or started is offered
    assert unread_austen and set(listed[:len(unread_austen)]) == set(unread_austen) and not set(listed) & set(titles)
    assert view["books"][0]["why"][0] == "More by Jane Austen"
    assert all(b["status"] == "none" and b["in_library"] for b in view["books"])

    got = client.post("/api/recommendations/refresh").get_json()
    found = {b["title"]: b for b in got["books"]}
    assert set(found) == {"Lady Susan", "Sanditon", "A Modern Romance"} and got["updated"]
    assert found["Lady Susan"]["reason"] == "More by Jane Austen" and found["Lady Susan"]["cover_id"] == 2
    assert found["A Modern Romance"]["reason"].startswith("Because you like ")
    # what went out: authors and subjects, nothing about who asked
    assert all(set(p) <= {"author", "q", "fields", "sort", "limit"} for p in asked) and "Partner" not in json.dumps(asked)
    assert not any("Bronte" in json.dumps(p) for p in asked)  # an author she rated 1 star is not asked about

    # "Not for me" removes a book for good; the list is private to its owner
    client.post("/api/recommendations/dismiss", json={"key": found["Sanditon"]["key"]})
    assert "Sanditon" not in [b["title"] for b in client.post("/api/recommendations/refresh").get_json()["books"]]
    client.post("/api/logout")
    login(client, "Nan")
    assert "Lady Susan" not in [b["title"] for b in client.get("/api/recommendations").get_json()["books"]]
    # switched off under Connections, nothing is asked
    client.post("/api/logout")
    login(client, "You")
    client.post("/api/admin/connections", json={"values": {"tag_lookups": "off"}})
    before = len(asked)
    with app.app_context():
        recommend.refresh_due(app.hub)
    assert len(asked) == before and client.get("/api/recommendations").get_json()["on"] is False
    client.post("/api/logout")
    client.post("/api/login", json={"guest": True})
    assert client.get("/api/recommendations").status_code == 403


class FakeSeriesServer:
    """Stands in for Suwayomi: answers the handful of questions Family Hub asks it."""

    def __init__(self):
        self.settings = {}
        self.asked = []
        self.queued = []
        self.following = {7}
        self.chapters = {7: [{"id": 70 + n, "name": "Chapter %d" % n, "chapterNumber": float(n), "sourceOrder": n,
                              "isRead": n == 1, "isDownloaded": n < 3, "uploadDate": "0", "scanlator": "", "lastPageRead": 0}
                             for n in (1, 2, 3, 4)],
                         8: []}
        self.titles = {7: "Tower Climber", 8: "Quiet Tea Shop"}

    def manga(self, mid):
        chapters = self.chapters[mid]
        unread = [c for c in chapters if not c["isRead"]]
        return {"id": mid, "title": self.titles[mid], "author": "Someone", "status": "ONGOING", "genre": ["Action"],
                "unreadCount": len(unread), "downloadCount": sum(c["isDownloaded"] for c in chapters), "sourceId": "55",
                "source": {"displayName": "Reading Site (EN)"}, "chapters": {"totalCount": len(chapters)},
                "firstUnreadChapter": unread[0] if unread else None, "description": "About a tower.",
                "inLibrary": mid in self.following, "realUrl": "https://example.org/tower"}

    def gql(self, query, variables=None, config=None, timeout=0):
        v = variables or {}
        self.asked.append(query)
        if "setSettings" in query:
            self.settings.update(v["s"])
            return {"setSettings": {"settings": {"downloadAsCbz": True}}}
        if "aboutServer" in query:
            return {"aboutServer": {"version": "v2.4.2366"}, "settings": dict(series.KEEP_EVERYTHING, **self.settings),
                    "sources": {"nodes": [{"id": "0", "lang": "localsourcelang"}, {"id": "55", "lang": "en"}]}}
        if "fetchSourceManga" in query:
            if v["s"] == "66":
                raise series.SeriesError("The series server said: that site is down")
            return {"fetchSourceManga": {"mangas": [{"id": m, "title": t, "inLibrary": m in self.following}
                                                    for m, t in self.titles.items() if v["q"].lower() in t.lower()]}}
        if "sources{nodes{id displayName" in query:
            return {"sources": {"nodes": [{"id": "55", "displayName": "Reading Site (EN)", "lang": "en", "isNsfw": False},
                                          {"id": "66", "displayName": "Broken Site (EN)", "lang": "en", "isNsfw": False},
                                          {"id": "77", "displayName": "Reading Site (FR)", "lang": "fr", "isNsfw": False}]}}
        if "mangas(condition:{inLibrary:true})" in query:
            return {"mangas": {"nodes": [self.manga(m) for m in sorted(self.following)]},
                    "downloadStatus": {"queue": [{"state": "QUEUED"}] * len(self.queued)}}
        if "fetchMangaAndChapters" in query:
            if v["id"] == 8 and not self.chapters[8]:
                self.chapters[8] = [{"id": 81, "name": "Chapter 1", "chapterNumber": 1.0, "sourceOrder": 1, "isRead": False,
                                     "isDownloaded": False, "uploadDate": "0", "scanlator": "", "lastPageRead": 0}]
            return {"fetchMangaAndChapters": {"manga": {"id": v["id"]}}}
        if "updateManga" in query:
            self.following.add(v["id"]) if "inLibrary:true" in query else self.following.discard(v["id"])
            return {"updateManga": {"manga": {"id": v["id"]}}}
        if "enqueueChapterDownloads" in query:
            self.queued += v["ids"]
            return {"enqueueChapterDownloads": {"downloadStatus": {"state": "STARTED"}}}
        if "startDownloader" in query:
            return {"startDownloader": {"downloadStatus": {"state": "STARTED"}}}
        if "updateChapters" in query:
            for chapters in self.chapters.values():
                for c in chapters:
                    if c["id"] in v["ids"]:
                        c["isRead"] = v["r"]
            return {"updateChapters": {"chapters": []}}
        if "fetchChapterPages" in query:
            return {"fetchChapterPages": {"pages": ["/a", "/b", "/c"]}}
        if "manga(id:$id)" in query:
            return {"manga": self.manga(v["id"]) if v["id"] in self.titles else None, "chapters": {"nodes": self.chapters.get(v["id"], [])}}
        raise AssertionError("unexpected question: " + query[:80])


@pytest.fixture()
def series_server(app, client, monkeypatch):
    fake = FakeSeriesServer()
    monkeypatch.setattr(series.Series, "gql", lambda self, query, variables=None, config=None, timeout=0: fake.gql(query, variables))
    login(client, "You")
    client.post("/api/admin/connections", json={"values": {"series_url": "http://suwayomi:4567", "series_pass": "s3cret"}})
    return fake


def test_series_are_for_everyone_signed_in_unless_switched_off(client, series_server):
    assert client.get("/api/state").get_json()["series"] is True
    assert client.get("/api/series").status_code == 200
    saved = client.get("/api/admin/connections").get_json()
    field = next(f for g in saved["groups"] if g["id"] == "series" for f in g["fields"] if f["name"] == "series_pass")
    assert field["set"] and field["value"] == "" and "s3cret" not in json.dumps(saved)
    assert "s3cret" not in client.get("/api/admin/export").get_data(as_text=True)
    nan = next(p for p in client.get("/api/state").get_json()["profiles"] if p["name"] == "Nan")
    assert nan["can_series"] is True and nan["can_request"] is False

    # Nan may read what others follow, but following downloads a whole series, so that needs "Can find new books".
    client.post("/api/logout")
    login(client, "Nan")
    assert client.get("/api/state").get_json()["series"] is True
    assert client.get("/api/series").status_code == 200 and client.get("/api/series/7").status_code == 200
    assert client.get("/api/series/chapters/72/pages").status_code == 200
    assert client.post("/api/series/chapters/read", json={"ids": [72], "read": True}).status_code == 200
    assert client.get("/api/series/search?q=tower").status_code == 403
    assert client.post("/api/series/8/follow", json={"on": True}).status_code == 403
    assert client.post("/api/series/7/follow", json={"on": False}).status_code == 403
    assert client.post("/api/series/7/download").status_code == 403
    assert 8 not in series_server.following and 7 in series_server.following and not series_server.queued

    # An admin can switch the shelf off for one profile; the profile cannot switch it back on.
    client.post("/api/logout")
    login(client, "You")
    client.patch("/api/profiles/%d" % nan["id"], json={"can_series": False})
    client.post("/api/logout")
    login(client, "Nan")
    assert client.get("/api/state").get_json()["series"] is False
    for url in ("/api/series", "/api/series/7", "/api/series/search?q=tower", "/api/series/7/cover",
                "/api/series/7/chapter/1/page/0", "/api/series/chapters/71/file", "/api/series/chapters/71/pages"):
        assert client.get(url).status_code == 403, url
    assert client.patch("/api/profiles/%d" % nan["id"], json={"can_series": True}).status_code == 200  # ignored: not an admin
    assert client.get("/api/series").status_code == 403

    client.post("/api/logout")
    client.post("/api/login", json={"guest": True})
    assert client.get("/api/state").get_json()["series"] is False
    assert client.get("/api/series").status_code == 403 and client.get("/api/series/7/cover").status_code == 403


def test_series_shelf_search_follow_and_reading(client, series_server):
    fake = series_server
    shelf = client.get("/api/series").get_json()
    assert shelf["configured"] and [s["title"] for s in shelf["series"]] == ["Tower Climber"]
    tower = shelf["series"][0]
    assert (tower["chapters"], tower["read"], tower["unread"], tower["downloaded"]) == (4, 1, 3, 2)
    assert tower["next"] == {"id": 72, "name": "Chapter 2", "order": 2} and tower["status"] == "Ongoing"
    # Looking at the shelf sets the series server to keep every chapter, once.
    assert fake.settings == series.KEEP_EVERYTHING and sum("setSettings" in q for q in fake.asked) == 1
    client.get("/api/series")
    assert sum("setSettings" in q for q in fake.asked) == 1

    # Only sources in the chosen languages are asked, and one broken site does not spoil the search.
    found = client.get("/api/series/search?q=tea").get_json()
    assert [g["source"] for g in found["groups"]] == ["Reading Site (EN)", "Broken Site (EN)"]
    assert found["groups"][0]["results"] == [{"id": 8, "title": "Quiet Tea Shop", "following": False}]
    assert "that site is down" in found["groups"][1]["error"]
    assert client.get("/api/series/search?q=t").status_code == 400

    # Following fetches the chapter list and queues every chapter.
    assert client.post("/api/series/8/follow", json={"on": True}).get_json()["queued"] == 1
    assert 8 in fake.following and fake.queued == [81]
    # Asking for the rest of a series queues only what is missing.
    assert client.post("/api/series/7/download").get_json()["queued"] == 2 and fake.queued == [81, 73, 74]

    detail = client.get("/api/series/7").get_json()
    assert [c["name"] for c in detail["chapters_list"]] == ["Chapter 1", "Chapter 2", "Chapter 3", "Chapter 4"]
    assert detail["following"] and detail["description"] == "About a tower."
    assert client.get("/api/series/chapters/72/pages").get_json() == {"pages": 3}
    assert client.post("/api/series/chapters/read", json={"ids": [72, 73], "read": True}).status_code == 200
    assert client.get("/api/series/7").get_json()["read"] == 3
    assert client.post("/api/series/chapters/read", json={"ids": []}).status_code == 400

    # Stopping keeps what was downloaded: nothing is asked to be deleted.
    client.post("/api/series/7/follow", json={"on": False})
    assert 7 not in fake.following and not any("delete" in q.lower() for q in fake.asked)
    assert client.get("/api/series/999").status_code == 502

    r = client.post("/api/admin/connections/test", json={"group": "series", "values": {}}).get_json()
    assert r["ok"] and "Connected to Suwayomi v2.4.2366" in r["message"] and "keep every new chapter" in r["message"]


def test_library_can_be_asked_for_books_or_graphic_novels_only(client):
    login(client, "You")
    everything = client.get("/api/books").get_json()["books"]
    target = next(b for b in everything if b["title"] == "Dracula")
    assert client.post("/api/books/kind", json={"ids": [target["id"]], "kind": kinds.GRAPHIC}).status_code == 200
    graphic = client.get("/api/books?kinds=Graphic novel").get_json()["books"]
    books = client.get("/api/books?kinds=Fiction,Non-fiction,Tabletop,Unsorted").get_json()["books"]
    assert [b["title"] for b in graphic] == ["Dracula"]
    assert "Dracula" not in [b["title"] for b in books] and len(books) == len(everything) - 1


def test_books_show_what_readers_at_large_rate_them(app, client, monkeypatch):
    asked = []

    def doc(book, fields):
        asked.append(book["title"])
        assert "ratings_average" in fields
        return {"Dracula": {"ratings_average": 3.9919, "ratings_count": 247}, "Emma": {"title": "Emma"}}.get(book["title"])

    monkeypatch.setattr(catalogues, "open_library_doc", doc)
    monkeypatch.setattr(readers.time, "sleep", lambda s: None)
    login(client, "You")
    assert all(b["readers"] is None for b in client.get("/api/books").get_json()["books"])
    with app.app_context():
        hub = app.hub
        total = len(hub.library.books())
        assert readers.run(hub, limit=3) == 3 and readers.run(hub) == total - 3
        assert readers.run(hub) == 0 and len(asked) == total  # a book with no rating is not asked about again straight away
        # Goodreads gives an average with every book on a shelf, and it is preferred.
        feed = goodreads.parse_feed(
            "<rss><channel><item><title>Emma</title><book_id>6969</book_id><author_name>Jane Austen</author_name>"
            "<user_rating>0</user_rating><average_rating>4.06</average_rating></item>"
            "<item><title>Dracula</title><book_id>17245</book_id><author_name>Bram Stoker</author_name>"
            "<average_rating>4.02</average_rating></item>"
            "<item><title>Nobody Rated This</title><book_id>1</book_id><author_name>A. Writer</author_name>"
            "<average_rating>0.00</average_rating></item></channel></rss>", "read")
        assert [i["average"] for i in feed] == [4.06, 4.02, None]
        me = next(p["id"] for p in hub.profiles() if p["name"] == "You")
        goodreads.apply(hub, me, feed)
        # Open Library does not overwrite a Goodreads figure afterwards.
        con = hub.con()
        readers.save(con, db_key(hub, "Dracula"), 2.0, 9, readers.OPEN_LIBRARY)
        con.commit()
    books = {b["title"]: b for b in client.get("/api/books").get_json()["books"]}
    assert books["Dracula"]["readers"] == {"rating": 4.02, "count": 0, "source": "Goodreads"}
    assert books["Emma"]["readers"] == {"rating": 4.06, "count": 0, "source": "Goodreads"}
    assert books["Persuasion"]["readers"] is None
    detail = client.get("/api/books/%d" % books["Dracula"]["id"]).get_json()
    assert detail["readers"]["source"] == "Goodreads"
    # An average from a handful of people is kept but not shown.
    with app.app_context():
        con = app.hub.con()
        readers.save(con, db_key(app.hub, "Persuasion"), 4.8, 9, readers.OPEN_LIBRARY)
        readers.save(con, db_key(app.hub, "Little Women"), 4.1, 10, readers.GOOGLE)
        con.commit()
    again = {b["title"]: b for b in client.get("/api/books").get_json()["books"]}
    assert again["Persuasion"]["readers"] is None
    assert again["Little Women"]["readers"] == {"rating": 4.1, "count": 10, "source": "Google Books"}
    # The family's own ratings are a separate thing and stay as they were.
    assert books["Emma"]["rating"] == 0

    # Switching outside lookups off stops Open Library being asked.
    client.post("/api/admin/connections", json={"values": {"tag_lookups": "off"}})
    with app.app_context():
        app.hub.con().execute("DELETE FROM public_ratings")
        app.hub.con().commit()
        assert readers.run(app.hub) == 0


def db_key(hub, title):
    return next(b["key"] for b in hub.library.books() if b["title"] == title)


def test_nightly_copy_to_a_second_disk_keeps_everything(app, client, tmp_path):
    login(client, "You")
    hub, config = app.hub, app.hub.config
    # Not set up: nothing is copied and the Admin page says so.
    assert client.get("/api/stats/admin").get_json()["services"]["second_copy"]["configured"] is False
    assert client.post("/api/admin/second-copy").status_code == 400
    with app.app_context():
        assert not secondcopy.due(hub) and secondcopy.run(hub)["ok"] is False

    disk = tmp_path / "disk2"
    disk.mkdir()
    stack = tmp_path / "stack"
    (stack / "cwa-config" / "processed_books").mkdir(parents=True)
    (stack / "cwa-config" / "app.db").write_bytes(b"not really a database")
    (stack / "cwa-config" / "processed_books" / "huge.epub").write_bytes(b"x" * 100)
    (stack / "suwayomi" / "cache").mkdir(parents=True)
    (stack / "suwayomi" / "server.conf").write_text("port = 4567")
    (stack / "suwayomi" / "database.mv.db").write_bytes(b"H:2,another kind of database")  # ends in .db but is not SQLite
    (stack / "cwa-config" / ".cache").mkdir()
    (stack / "cwa-config" / ".cache" / "fonts").write_bytes(b"x")
    (stack / "suwayomi" / "cache" / "thumb.jpg").write_bytes(b"x")
    chapters = tmp_path / "chapters" / "Site" / "Tower Climber"
    chapters.mkdir(parents=True)
    (chapters / "Chapter 1.cbz").write_bytes(b"one")
    (chapters / "Chapter 2.cbz").write_bytes(b"two")
    config.second_disk_dir, config.stack_dir, config.series_dir = disk, stack, tmp_path / "chapters"

    with app.app_context():
        first = secondcopy.run(hub)
    assert first["ok"], first["errors"]
    assert first["folders"] == ["bookhub", "cwa-config", "suwayomi", "library", "series"] and first["set_aside"] == 0
    day = disk / "settings" / first["day"]
    assert (day / "bookhub" / "bookhub.db").is_file() and (day / "cwa-config" / "app.db").read_bytes() == b"not really a database"
    assert (day / "suwayomi" / "server.conf").is_file() and not (day / "cwa-config" / ".cache").exists()
    assert (day / "suwayomi" / "database.mv.db").read_bytes() == b"H:2,another kind of database"
    assert not (day / "cwa-config" / "processed_books").exists() and not (day / "suwayomi" / "cache").exists()  # caches are left out
    import sqlite3

    def count(path, table):  # the copy is a whole database that opens on its own
        con = sqlite3.connect(str(path))
        try:
            return con.execute("SELECT COUNT(*) FROM %s" % table).fetchone()[0]
        finally:
            con.close()

    assert count(day / "bookhub" / "bookhub.db", "profiles") >= 3
    assert (disk / "library" / "metadata.db").is_file()
    assert count(disk / "library" / "metadata.db", "books") > 5
    assert (disk / "series" / "Site" / "Tower Climber" / "Chapter 1.cbz").read_bytes() == b"one"
    books = [f for f in (disk / "library").rglob("*") if f.suffix in (".epub", ".pdf")]
    assert len(books) >= 5

    # A second run copies nothing that has not changed.
    with app.app_context():
        again = secondcopy.run(hub)
    assert again["ok"] and again["set_aside"] == 0 and again["unchanged"] >= len(books) + 2, again
    assert again["copied"] < first["copied"]

    # A chapter that changes and one that goes are both still on the second disk afterwards.
    import os
    (chapters / "Chapter 1.cbz").write_bytes(b"one, redone")
    os.utime(chapters / "Chapter 1.cbz", (2_000_000_000, 2_000_000_000))
    (chapters / "Chapter 2.cbz").unlink()
    with app.app_context():
        third = secondcopy.run(hub)
    attic = disk / "removed" / third["day"] / "series" / "Site" / "Tower Climber"
    assert third["ok"] and third["set_aside"] == 2
    assert (disk / "series" / "Site" / "Tower Climber" / "Chapter 1.cbz").read_bytes() == b"one, redone"
    assert not (disk / "series" / "Site" / "Tower Climber" / "Chapter 2.cbz").exists()
    assert (attic / "Chapter 1.cbz").read_bytes() == b"one" and (attic / "Chapter 2.cbz").read_bytes() == b"two"

    seen = client.get("/api/stats/admin").get_json()["services"]["second_copy"]
    assert seen["configured"] and seen["on"] and seen["last"]["day"] == third["day"] and seen["free"] > 0
    r = client.post("/api/admin/connections/test", json={"group": "copy", "values": {}}).get_json()
    assert r["ok"] and "GB free" in r["message"]
    # It runs once a day, and not at all when switched off.
    with app.app_context():
        assert not secondcopy.due(hub)
        hub.con().execute("DELETE FROM kv WHERE key=?", (secondcopy.KEY,))
        hub.con().commit()
        assert secondcopy.due(hub) == (__import__("datetime").datetime.now().hour >= secondcopy.START_HOUR)
    client.post("/api/admin/connections", json={"values": {"second_copy": "off"}})
    with app.app_context():
        assert not secondcopy.due(hub)


def test_ratings_take_the_answer_most_people_stand_behind(app, client, monkeypatch):
    monkeypatch.setattr(catalogues, "open_library_doc", lambda book, fields: {"ratings_average": 3.5, "ratings_count": 12})
    monkeypatch.setattr(catalogues, "google_books_info", lambda config, book, full=True: {"averageRating": 4.0, "ratingsCount": 300})

    def hardcover(config, book):
        raise catalogues.LookupError_("it refused the API key")

    monkeypatch.setattr(catalogues, "hardcover_info", hardcover)
    login(client, "You")
    with app.app_context():
        hub = app.hub
        book = next(b for b in hub.library.books() if b["title"] == "Dracula")
        assert readers.look_up(hub, book) == 3.5  # no keys: only Open Library is asked
        client.post("/api/admin/connections", json={"values": {"google_books_key": "g-key", "hardcover_key": "h-key"}})
        assert readers.look_up(hub, book) == 4.0  # a refused Hardcover key does not spoil the others
    saved = client.get("/api/admin/connections").get_json()
    assert "h-key" not in json.dumps(saved) and "g-key" not in json.dumps(saved)
    dracula = next(b for b in client.get("/api/books").get_json()["books"] if b["title"] == "Dracula")
    assert dracula["readers"] == {"rating": 4.0, "count": 300, "source": "Google Books"}


def test_my_books_lists_where_each_book_stands_on_my_devices(client):
    login(client, "You")
    me = client.get("/api/state").get_json()["me"]["id"]
    assert client.get("/api/me/shelf").get_json() == {"devices": [], "books": {}}
    client.post("/api/profiles/%d/devices" % me, json={"name": "My Kindle", "kind": "kindle", "address": "me@kindle.com", "auto_send": False})
    client.post("/api/profiles/%d/devices" % me, json={"name": "iPad", "kind": "readest"})
    emma = book_id(client, "Emma")
    client.post("/api/books/%d/shelf" % emma, json={"on": True})
    shelf = client.get("/api/me/shelf").get_json()
    assert [(d["name"], d["kind"]) for d in shelf["devices"]] == [("My Kindle", "kindle"), ("iPad", "readest")]
    assert list(shelf["books"]) == [str(emma)]
    kindle = shelf["books"][str(emma)]["devices"]
    assert [d["name"] for d in kindle] == ["My Kindle"] and kindle[0]["state"] == "none"
    assert [b["title"] for b in client.get("/api/books?filter=shelf").get_json()["books"]] == ["Emma"]
    client.post("/api/logout")
    client.post("/api/login", json={"guest": True})
    assert client.get("/api/me/shelf").status_code == 403


class FakeLibraryManager:
    """Stands in for the library manager: changes the sample library the way its merge and edit pages do."""

    def __init__(self, library):
        self.library = Path(library)
        self.calls = []

    def _con(self):
        import sqlite3
        return sqlite3.connect(self.library / "metadata.db")

    def set_field(self, book_id, field, value):
        self.calls.append(("set", book_id, field))
        con = self._con()
        if field == "synopsis":
            con.execute("DELETE FROM comments WHERE book=?", (book_id,))
            if value:
                con.execute("INSERT INTO comments(book, text) VALUES(?,?)", (book_id, value))
        elif field == "tags":
            con.execute("DELETE FROM books_tags_link WHERE book=?", (book_id,))
            for name in [t.strip() for t in value.split(",") if t.strip()]:
                row = con.execute("SELECT id FROM tags WHERE name=?", (name,)).fetchone()
                tag = row[0] if row else con.execute("INSERT INTO tags(name) VALUES(?)", (name,)).lastrowid
                con.execute("INSERT INTO books_tags_link(book, tag) VALUES(?,?)", (book_id, tag))
        elif field == "title":
            con.execute("UPDATE books SET title=? WHERE id=?", (value, book_id))
        elif field == "authors":
            con.execute("DELETE FROM books_authors_link WHERE book=?", (book_id,))
            for name in [a.strip() for a in value.split("&") if a.strip()]:
                row = con.execute("SELECT id FROM authors WHERE name=?", (name,)).fetchone()
                author = row[0] if row else con.execute("INSERT INTO authors(name, sort) VALUES(?, ?)", (name, name)).lastrowid
                con.execute("INSERT INTO books_authors_link(book, author) VALUES(?,?)", (book_id, author))
        con.commit()
        con.close()

    def merge(self, keep_id, spare_ids):
        import shutil
        self.calls.append(("merge", keep_id, tuple(spare_ids)))
        con = self._con()
        for spare in spare_ids:
            (path,) = con.execute("SELECT path FROM books WHERE id=?", (spare,)).fetchone()
            for table, col in (("books", "id"), ("data", "book"), ("comments", "book"), ("books_tags_link", "book"),
                               ("books_authors_link", "book"), ("identifiers", "book"), ("books_series_link", "book")):
                con.execute("DELETE FROM %s WHERE %s=?" % (table, col), (spare,))
            shutil.rmtree(self.library / path)
        con.commit()
        con.close()


def _twin(library, title, size=None, same_bytes=False, details=True):
    """A second entry for a sample book: with or without the first one's details, the same file or another."""
    import sqlite3
    library = Path(library)
    con = sqlite3.connect(library / "metadata.db")
    first, path, name0 = con.execute("SELECT b.id, b.path, d.name FROM books b JOIN data d ON d.book=b.id WHERE b.title=?", (title,)).fetchone()
    original = library / path / (name0 + ".epub")
    con.close()
    repeats = {"Dracula": 520, "Emma": 500, "Persuasion": 280, "Moby-Dick": 700, "Little Women": 560}[title]
    new = demo.add_copy(library, title, size=size or repeats)
    con = sqlite3.connect(library / "metadata.db")
    folder, name = con.execute("SELECT b.path, d.name FROM books b JOIN data d ON d.book=b.id WHERE b.id=?", (new,)).fetchone()
    if same_bytes:
        (library / folder / (name + ".epub")).write_bytes(original.read_bytes())
        con.execute("UPDATE data SET uncompressed_size=? WHERE book=?", (original.stat().st_size, new))
    if details:
        con.execute("UPDATE books SET has_cover=(SELECT has_cover FROM books WHERE id=?) WHERE id=?", (first, new))
        for text, in con.execute("SELECT text FROM comments WHERE book=?", (first,)).fetchall():
            con.execute("INSERT INTO comments(book, text) VALUES(?,?)", (new, text))
        for tag, in con.execute("SELECT tag FROM books_tags_link WHERE book=?", (first,)).fetchall():
            con.execute("INSERT INTO books_tags_link(book, tag) VALUES(?,?)", (new, tag))
        for kind, val in con.execute("SELECT type, val FROM identifiers WHERE book=?", (first,)).fetchall():
            con.execute("INSERT INTO identifiers(book, type, val) VALUES(?,?,?)", (new, kind, val))
    con.commit()
    con.close()
    return first, new


def test_spare_copies_are_judged_and_removed_through_the_library_manager(app, client, tmp_path):
    hub, library = app.hub, Path(app.hub.config.library_dir)
    same = _twin(library, "Dracula", same_bytes=True)          # the very same file twice
    alike = _twin(library, "Emma", size=495)                   # a slightly different file, the same details
    richer = _twin(library, "Persuasion", size=277)            # the first entry is about to lose its description
    far = _twin(library, "Moby-Dick", size=300)                # much smaller: a person decides
    lone = _twin(library, "Little Women", size=565)            # an admin will say these are different books
    import sqlite3
    con = sqlite3.connect(library / "metadata.db")
    con.execute("DELETE FROM comments WHERE book=?", (richer[0],))
    con.execute("DELETE FROM books_tags_link WHERE book=?", (richer[0],))
    con.commit()
    con.close()
    hub.library.refresh(force=True)
    login(client, "You")
    entries = len(hub.library.all_books())
    key = {p["title"]: p for p in client.get("/api/admin/duplicates").get_json()["sets"]}
    assert {t: p["cls"] for t, p in key.items()} == {"Dracula": "identical", "Emma": "duplicate", "Persuasion": "merge",
                                                     "Moby-Dick": "review", "Little Women": "duplicate"}
    assert [c["label"] for c in key["Persuasion"]["copy"]] == ["description", "tags"] and key["Persuasion"]["keep"] == richer[0]
    assert "more than 10%" in key["Moby-Dick"]["why"]

    # Nothing can be done until the library manager's sign-in and the set-aside folder are there.
    blocked = client.get("/api/admin/duplicates").get_json()["blocked"]
    assert "sign-in is not set up" in blocked
    assert client.post("/api/admin/duplicates", json={"cls": "identical"}).status_code == 400
    client.post("/api/admin/connections", json={"values": {"cwa_api_url": "http://manager:8083", "cwa_user": "admin", "cwa_pass": "pw-1"}})
    assert "pw-1" not in client.get("/api/admin/connections").get_data(as_text=True)
    attention = Path(hub.config.attention_dir)
    attention.mkdir(parents=True, exist_ok=True)
    assert client.get("/api/admin/duplicates").get_json()["blocked"] is None
    fake = FakeLibraryManager(library)
    hub.manager = fake

    def run(body):
        assert client.post("/api/admin/duplicates", json=body).status_code == 200, body
        for _ in range(200):
            job = client.get("/api/admin/duplicates").get_json()["job"]
            if not job["running"]:
                return job
            __import__("time").sleep(0.05)
        raise AssertionError("the batch did not finish")

    # "They are different books" takes a set off the list altogether.
    client.post("/api/admin/copies", json={"key": key["Little Women"]["key"], "choice": "apart"})
    assert "Little Women" not in [p["title"] for p in client.get("/api/admin/duplicates").get_json()["sets"]]

    # A set that needs a person cannot be done in bulk or without a choice.
    assert client.post("/api/admin/duplicates", json={"cls": "review"}).status_code == 400
    assert client.post("/api/admin/duplicates", json={"key": key["Moby-Dick"]["key"]}).status_code == 400
    assert client.post("/api/admin/duplicates", json={"key": key["Moby-Dick"]["key"], "keep": 99999}).status_code == 400

    job = run({"cls": "identical"})
    assert job["done"] == 1 and not job["problems"] and job["backup"].startswith("metadata-")
    assert (Path(hub.config.data_dir) / "library-backups" / job["backup"]).is_file()
    assert hub.library.get(same[1]) is None and hub.library.get(same[0]) is not None
    assert not (attention / "_duplicates").exists()  # an identical file is not set aside

    job = run({"cls": "duplicate"})
    assert job["done"] == 1 and not job["problems"] and hub.library.get(alike[1]) is None
    aside = [f.name for f in (attention / "_duplicates").rglob("*.epub")]
    assert len(aside) == 1 and "Emma" in aside[0]

    job = run({"key": key["Persuasion"]["key"]})
    assert job["done"] == 1 and not job["problems"]
    kept = hub.library.get(richer[0])
    assert hub.library.get(richer[1]) is None and kept["synopsis"] and kept["raw_tags"]
    assert [c[:3] for c in fake.calls if c[0] == "set" and c[1] == richer[0]] == [("set", richer[0], "synopsis"), ("set", richer[0], "tags")]
    assert len(list((attention / "_duplicates").rglob("*.epub"))) == 2

    # The person keeps the smaller Moby-Dick: the bigger one is set aside, not lost.
    job = run({"key": key["Moby-Dick"]["key"], "keep": far[1]})
    assert job["done"] == 1 and not job["problems"]
    assert hub.library.get(far[0]) is None and hub.library.get(far[1]) is not None
    assert len(list((attention / "_duplicates").rglob("*.epub"))) == 3

    after = client.get("/api/admin/duplicates").get_json()
    assert after["sets"] == [] and [l["action"] for l in after["log"]] == ["review", "merge", "duplicate", "identical"]
    assert len(hub.library.all_books()) == entries - 4
    # Everyone's reading history is keyed by the book, not the entry, so none of it went with the spares.
    assert {b["title"] for b in client.get("/api/books").get_json()["books"]} >= {"Dracula", "Emma", "Persuasion", "Moby-Dick", "Little Women"}

    # If the library manager says yes but leaves the spare, that is reported and nothing is logged as done.
    again = _twin(library, "Dracula", same_bytes=True)
    hub.library.refresh(force=True)
    fake.merge = lambda keep, spares: None
    job = run({"cls": "identical"})
    assert job["problems"] and "still there" in job["problems"][0] and hub.library.get(again[1]) is not None
    client.post("/api/logout")
    login(client, "Nan")
    assert client.get("/api/admin/duplicates").status_code == 403 and client.post("/api/admin/duplicates", json={"cls": "identical"}).status_code == 403


def test_household_look_is_chosen_by_an_admin(app, client):
    login(client, "You")
    look = client.get("/api/state").get_json()["look"]
    assert (look["design"], look["theme"], look["scene"], look["transition"], look["accent"]) == ("minimalist", "dark", "daily", True, None)
    catalogue = client.get("/api/admin/look").get_json()["designs"]
    assert len(catalogue) == 1 and [s["id"] for s in catalogue[0]["schemes"]] == ["dark", "light", "paper", "blueprint"]
    assert all(all(s["swatch"]) for s in catalogue[0]["schemes"])
    assert client.post("/api/admin/look", json={"theme": "blueprint", "scene": "live-sky", "accent": 150, "transition": False}).status_code == 200
    look = client.get("/api/state").get_json()["look"]
    assert (look["theme"], look["scene"], look["accent"], look["transition"]) == ("blueprint", "live-sky", 150, False)
    page = client.get("/").get_data(as_text=True)
    assert 'data-design="minimalist" data-theme="blueprint" data-accent="custom" style="--accent-h:150"' in page
    assert "/static/looks/minimalist/bundle.css" in page and "family=Archivo" in page
    # A bad value changes nothing, not even the good values sent with it.
    for bad in ({"theme": "vesper"}, {"scene": "../etc"}, {"scene": 5}, {"accent": 360}, {"accent": "teal"}, {"accent": True}):
        assert client.post("/api/admin/look", json=dict(bad, transition=True)).status_code == 400, bad
    assert client.get("/api/state").get_json()["look"]["transition"] is False
    # The design's own purple is no accent at all, as is a reset.
    client.post("/api/admin/look", json={"accent": 295})
    assert client.get("/api/state").get_json()["look"]["accent"] is None
    client.post("/api/admin/look", json={"accent": 25})
    client.post("/api/admin/look", json={"accent": None})
    assert client.get("/api/state").get_json()["look"]["accent"] is None and "data-accent" not in client.get("/").get_data(as_text=True)
    # A household that chose one of the old designs gets the new one with its first scheme.
    from bookhub import db
    db.kv_set(app.hub.con(), "look:design", "medieval")
    db.kv_set(app.hub.con(), "look:theme", "ash")
    look = client.get("/api/state").get_json()["look"]
    assert (look["design"], look["theme"]) == ("minimalist", "dark")
    assert client.get("/static/looks/minimalist/tokens.css").status_code == 200
    assert client.get("/static/looks/medieval/tokens.css").status_code == 404
    login(client, "Nan")
    assert client.post("/api/admin/look", json={"theme": "light"}).status_code == 403


def test_home_lists_the_newest_books(client):
    assert client.get("/api/home").status_code == 401
    login(client, "Nan")
    home = client.get("/api/home").get_json()
    added = [b["added"] for b in home["books"]]
    assert added == sorted(added, reverse=True) and 0 < len(added) <= 12


class FakeResponse:
    def __init__(self, status, body=None, content_type="application/json"):
        self.status_code, self._body = status, body
        self.headers = {"Content-Type": content_type}
        self.reason = "error" if status >= 400 else "OK"
        self.content = b"" if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode())
        self.text = self.content.decode("latin-1")

    def json(self):
        return json.loads(self.content)


class FakeMediaServers:
    """Stands in for Radarr, Sonarr, Prowlarr and qBittorrent: just the parts Family Hub uses."""

    def __init__(self):
        self.calls = []
        self.arr = {name: {"rootfolder": [], "qualityprofile": [{"id": 1, "name": "Any"}], "downloadclient": [], "items": [], "queue": []}
                    for name in ("radarr", "sonarr")}
        self.prowlarr_apps = []
        self.categories = {}
        self.deleted = []
        self.down = set()  # services not answering
        self.release_queries = []
        self.prowlarr_history = []
        self.prowlarr_clients = []
        self.hosts = {}
        self.qbit_password_ok = True
        self.torrents, self.qbit_posts, self.queueing = [], [], False
        self.site_searches, self.pushed = [], []
        self.prefs, self.slow = {"dl_limit": 0, "up_limit": 0, "alt_dl_limit": 10240, "alt_up_limit": 10240}, False

    def schema(self):
        q = lambda i, name, res: {"quality": {"id": i, "name": name, "resolution": res}, "items": [], "allowed": False}
        return {"name": "", "upgradeAllowed": False, "cutoff": 0, "items": [
            q(0, "Unknown", 0), q(6, "CAM", 480), q(1, "SDTV", 480), q(2, "DVD", 0), q(3, "DVD-R", 480), q(4, "HDTV-720p", 720), q(9, "HDTV-1080p", 1080), q(7, "Bluray-1080p", 1080),
            q(30, "Remux-1080p", 1080), q(31, "Bluray-2160p", 2160),
            {"id": 1001, "name": "WEB 2160p", "allowed": False, "items": [q(18, "WEBDL-2160p", 2160), q(17, "WEBRip-2160p", 2160)]},
            q(32, "Remux-2160p", 2160)]}

    def request(self, method, url, headers=None, params=None, json=None, data=None, timeout=0):
        self.calls.append((method, url))
        if ":8096" in url:
            if url.endswith("/Items"):
                return FakeResponse(200, {"Items": [{"Id": "jf-show", "Name": "Long Show", "ProviderIds": {"Tvdb": "99"}},
                                                    {"Id": "jf-other", "Name": "Long Show (1990)", "ProviderIds": {"Tvdb": "5"}}]})
            if url.endswith("/Shows/jf-show/Episodes"):
                return FakeResponse(200, {"Items": [{"Id": "jf-s1e1", "ParentIndexNumber": 1, "IndexNumber": 1}]})
            return FakeResponse(404, {"message": "not here"})
        name = "radarr" if ":7878" in url else "sonarr" if ":8989" in url else "prowlarr" if ":9696" in url else None
        if name and name in self.down:
            return FakeResponse(503, {"message": "Service Unavailable"})
        if name == "prowlarr":
            path = url.split("/api/v1/")[1]
            if path == "applications" and method == "GET":
                return FakeResponse(200, self.prowlarr_apps)
            if path == "applications/schema":
                return FakeResponse(200, [{"implementation": impl, "fields": [{"name": "prowlarrUrl"}, {"name": "baseUrl"}, {"name": "apiKey"}]}
                                          for impl in ("Radarr", "Sonarr")])
            if path == "applications":
                self.prowlarr_apps.append(json)
                return FakeResponse(201, json)
            if path == "search" and method == "GET" and params and params.get("indexerIds"):
                # One site at a time, as the live versions list asks.
                by_site = {4: [{"guid": "a", "title": "Metropolis.1927.2160p.WEB", "size": 9, "seeders": 3, "magnetUrl": "magnet:?xt=a", "indexer": "Example Tracker"},
                               {"guid": "b", "title": "Metropolis.1927.HDCAM", "size": 1, "seeders": 50, "downloadUrl": "http://prowlarr:9696/1/download?x", "indexer": "Example Tracker"}],
                           5: [{"guid": "c", "title": "Metropolis.1927.1080p.BluRay", "size": 5, "seeders": 20, "magnetUrl": "magnet:?xt=c", "indexer": "Quiet Site"}]}
                self.site_searches.append(params)
                return FakeResponse(200, by_site.get(params["indexerIds"], []))
            if path == "indexer":
                return FakeResponse(200, [{"id": 4, "name": "Example Tracker", "enable": True}, {"id": 5, "name": "Quiet Site", "enable": True},
                                          {"id": 6, "name": "Switched Off", "enable": False}])
            if path == "downloadclient/schema":
                return FakeResponse(200, [{"implementation": "QBittorrent", "fields": [{"name": n} for n in ("host", "port", "username", "password", "category")]}])
            if path == "downloadclient" and method == "GET":
                return FakeResponse(200, self.prowlarr_clients)
            if path == "downloadclient":
                self.prowlarr_clients.append(dict(json, id=len(self.prowlarr_clients) + 1))
                return FakeResponse(201, json)
            if path.startswith("downloadclient/") and method == "PUT":
                self.prowlarr_clients[int(path.split("/")[1]) - 1] = json
                return FakeResponse(202, json)
            if path == "history":
                return FakeResponse(200, {"records": self.prowlarr_history})
            if path == "config/host":
                if method == "PUT":
                    self.hosts["prowlarr"] = json
                return FakeResponse(200, self.hosts.get("prowlarr", {"username": "", "apiKey": "p"}))
        if name:
            a, path = self.arr[name], url.split("/api/v3/")[1]
            film = name == "radarr"
            if path == "system/status":
                return FakeResponse(200, {"version": "5.0"})
            if path == "config/host":
                if method == "PUT":
                    self.hosts[name] = json
                return FakeResponse(200, self.hosts.get(name, {"username": "", "apiKey": "k"}))
            if path in ("rootfolder", "downloadclient") and method == "GET":
                return FakeResponse(200, a[path])
            if path in ("rootfolder", "downloadclient"):
                a[path].append(dict(json, id=len(a[path]) + 1))
                return FakeResponse(201, json)
            if path.startswith("downloadclient/") and method == "PUT":
                a["downloadclient"][int(path.split("/")[1]) - 1] = json
                return FakeResponse(202, json)
            if path == "command" and method == "POST":
                a.setdefault("sent", []).append(json)
                return FakeResponse(201, dict(json, status="queued", id=len(a["sent"])))
            if path.startswith("command/") and method == "GET":
                return FakeResponse(200, {"status": "completed"})
            if path == "manualimport":
                a.setdefault("scans", []).append(params)
                return FakeResponse(200, [i for i in a.get("files", []) if i["path"].startswith(params["folder"] + "/")]
                                    if not params.get("movieId") else [dict(i, movie={"id": params["movieId"], "title": "Picked Film", "movieFileId": 0}, rejections=[])
                                                                       for i in a.get("files", []) if i["path"].startswith(params["folder"] + "/")])
            if path == "parse":
                return FakeResponse(200, {"parsedMovieInfo": {"movieTitles": ["Some Odd Film"], "year": 2003}})
            if path == "command":
                return FakeResponse(200, a.get("commands", []))
            if path == "episode":
                return FakeResponse(200, [
                    {"seasonNumber": 0, "episodeNumber": 1, "title": "A special"},
                    {"seasonNumber": 1, "episodeNumber": 2, "title": "Second", "hasFile": False, "monitored": True, "airDate": "2020-01-08"},
                    {"seasonNumber": 1, "episodeNumber": 1, "title": "Pilot", "hasFile": True, "monitored": True, "airDate": "2020-01-01"}])
            if path in ("history/movie", "history/series"):
                return FakeResponse(200, a.get("item_history", []))
            if path == "queue/details":
                return FakeResponse(200, a["queue"])
            if path == "qualityprofile/schema":
                return FakeResponse(200, self.schema())
            if path == "qualityprofile" and method == "GET":
                return FakeResponse(200, a["qualityprofile"])
            if path == "qualityprofile":
                a["qualityprofile"].append(dict(json, id=7))
                return FakeResponse(201, json)
            if path == "downloadclient/schema":
                return FakeResponse(200, [{"implementation": "QBittorrent", "fields": [{"name": n} for n in ("host", "port", "username", "password", "movieCategory", "tvCategory")]}])
            if path in ("movie", "series") and method == "GET":
                return FakeResponse(200, a["items"])
            if path == "movie/lookup/tmdb":
                return FakeResponse(200, {"title": "Metropolis", "year": 1927, "tmdbId": params["tmdbId"], "images": []})
            if path == "series/lookup":
                return FakeResponse(200, [{"title": "Long Show", "tvdbId": 99, "images": [{"coverType": "poster", "remoteUrl": "https://artworks.thetvdb.com/p.jpg"}],
                                           "seasons": [{"seasonNumber": n} for n in (0, 1, 2, 3)]}])
            if path == "movie/lookup":
                return FakeResponse(200, [{"title": "Metropolis", "year": 1927, "tmdbId": 19}, {"title": "Metropolis 2", "tmdbId": 20}])
            if path in ("movie", "series"):
                ext = "tmdbId" if film else "tvdbId"
                if any(i.get(ext) == json.get(ext) for i in a["items"]):
                    return FakeResponse(400, [{"errorMessage": "This %s has already been added" % path}])
                item = dict(json, id=len(a["items"]) + 1, added="2026-10-23T00:00:00Z")
                a["items"].append(item)
                return FakeResponse(201, item)
            if path.startswith(("movie/", "series/")) and method == "GET":
                n = int(path.split("/")[1])
                return FakeResponse(200, a["items"][n - 1]) if n <= len(a["items"]) else FakeResponse(404, {"message": "NotFound"})
            if path.startswith(("movie/", "series/")) and method == "PUT":
                a["items"][int(path.split("/")[1]) - 1] = json
                return FakeResponse(202, json)
            if path.startswith("command/") and method == "DELETE":
                a.setdefault("cancelled", []).append(int(path.split("/")[1]))
                return FakeResponse(200)
            if path.startswith(("movie/", "series/")) and method == "DELETE":
                self.deleted.append((name, path, params))
                return FakeResponse(200)
            if path == "queue":
                return FakeResponse(200, {"records": a["queue"]})
            if path == "history":
                return FakeResponse(200, {"records": []})
            if path == "release/push":
                self.pushed.append((name, json))
                cam = "CAM" in json["title"]
                return FakeResponse(200, [{"title": json["title"], "rejected": cam, "rejections": ["Cam copies are not wanted"] if cam else []}])
            if path == "release" and method == "GET":
                self.release_queries.append((name, params, timeout))
                return FakeResponse(200, [{"guid": "g1", "indexerId": 2, "title": "Long.Show.S02", "size": 5, "seeders": 9, "quality": {"quality": {"name": "WEBDL-2160p"}}}])
        return FakeResponse(404, {"message": "not here"})

    # qBittorrent, through requests.Session
    def session(self):
        fake = self

        class Session:
            headers = {}

            def post(self, url, data=None, timeout=0, files=None):
                if url.endswith("/auth/login"):
                    # Like qBittorrent 5: an empty 204 when it worked, 401 when it did not.
                    return FakeResponse(204, b"", "text/plain") if fake.qbit_password_ok else FakeResponse(401, b"Unauthorized", "text/plain")
                if "Category" in url:
                    fake.categories[data["category"]] = data["savePath"]
                fake.qbit_posts.append((url.rsplit("/", 1)[1], data))
                if url.endswith("/setPreferences"):
                    fake.prefs.update(json.loads(data["json"]))
                    fake.queueing = fake.queueing or bool(fake.prefs.get("queueing_enabled"))
                if url.endswith("/toggleSpeedLimitsMode"):
                    fake.slow = not fake.slow
                if url.endswith("Prio") and not fake.queueing:
                    return FakeResponse(409, b"Torrent queueing must be enabled", "text/plain")
                return FakeResponse(200, b"", "text/plain")

            def get(self, url, params=None, timeout=0):
                if url.endswith("/torrents/info"):
                    return FakeResponse(200, fake.torrents)
                if url.endswith("/app/preferences"):
                    return FakeResponse(200, fake.prefs)
                if url.endswith("/transfer/info"):
                    return FakeResponse(200, {"dl_info_speed": 3145728, "up_info_speed": 2048})
                if url.endswith("/transfer/speedLimitsMode"):
                    return FakeResponse(200, b"1" if fake.slow else b"0", "text/plain")
                return FakeResponse(200, {k: {"savePath": v} for k, v in fake.categories.items()} if url.endswith("categories") else [])
        return Session()


@pytest.fixture()
def media_servers(app, monkeypatch):
    from bookhub import media, settings
    fake = FakeMediaServers()
    monkeypatch.setattr(media.requests, "request", fake.request)
    monkeypatch.setattr(integrations.requests, "Session", fake.session)
    with app.app_context():
        settings.save(app.hub, {"radarr_url": "http://radarr:7878", "radarr_api_key": "r" * 32,
                                "sonarr_url": "http://sonarr:8989", "sonarr_api_key": "s" * 32,
                                "prowlarr_url": "http://prowlarr:9696", "prowlarr_api_key": "p" * 32,
                                "qbit_url": "http://gluetun:8080", "qbit_user": "admin", "qbit_pass": "pw"})
    return fake


def test_connect_everything_wires_the_services_once(client, media_servers):
    login(client, "You")
    steps = client.post("/api/admin/media/connect").get_json()["steps"]
    by_step = {s["step"]: s for s in steps}
    for name in ("Radarr → media folder", "Sonarr → qBittorrent", "Prowlarr → Radarr", "Prowlarr → Sonarr", "qBittorrent → download folders"):
        assert by_step[name]["ok"], (name, by_step[name])
    assert by_step["VPN"]["ok"] is False and "not set up" in by_step["VPN"]["message"]
    radarr = media_servers.arr["radarr"]
    assert [r["path"] for r in radarr["rootfolder"]] == ["/data/media/movies"]
    client_fields = {f["name"]: f.get("value") for f in radarr["downloadclient"][0]["fields"]}
    assert client_fields["host"] == "gluetun" and client_fields["port"] == 8080
    profile = radarr["qualityprofile"][-1]
    allowed = [i.get("name") or i["quality"]["name"] for i in profile["items"] if i["allowed"]]
    # SD as a last resort; no remux, disc images or cam copies
    assert allowed == ["SDTV", "DVD", "HDTV-720p", "HDTV-1080p", "Bluray-1080p", "Bluray-2160p", "WEB 2160p"]
    assert profile["cutoff"] == 1001 and profile["upgradeAllowed"]
    assert media_servers.categories == {"movies": "/data/torrents/movies", "tv": "/data/torrents/tv", "books": "/downloads"}
    prefs = media_servers.prefs  # finished downloads are checked, then stopped: no sharing
    assert prefs["recheck_completed_torrents"] and prefs["max_ratio"] == 0 and prefs["max_seeding_time"] == 0 and prefs["max_ratio_act"] == 0
    assert by_step["Prowlarr → qBittorrent (books)"]["ok"]  # books from Discover are sent through Prowlarr
    book_client = {f["name"]: f.get("value") for f in media_servers.prowlarr_clients[0]["fields"]}
    assert book_client["category"] == "books" and book_client["host"] == "gluetun"
    # Pressing it again changes nothing that is already in place.
    client.post("/api/admin/media/connect")
    assert len(radarr["rootfolder"]) == 1 and len(radarr["downloadclient"]) == 1 and len(media_servers.prowlarr_apps) == 2 and len(media_servers.prowlarr_clients) == 1


def test_films_and_shows_are_requested_by_permitted_profiles(client, media_servers):
    login(client, "Nan")
    assert client.get("/api/media/search?kind=film&q=metro").status_code == 403  # members need the switch
    login(client, "You")
    film_before = client.post("/api/media/request", json={"kind": "film", "id": 19})
    assert film_before.status_code == 502 and "Connect everything" in film_before.get_json()["error"]  # no media folder yet
    client.post("/api/admin/media/connect")
    nan = next(p["id"] for p in client.get("/api/state").get_json()["profiles"] if p["name"] == "Nan")
    client.patch("/api/profiles/%d" % nan, json={"can_request_media": True})
    login(client, "Nan")
    assert client.get("/api/state").get_json()["me"]["can_request_media"] is True
    results = client.get("/api/media/search?kind=film&q=metro").get_json()["results"]
    assert [r["title"] for r in results] == ["Metropolis", "Metropolis 2"]
    film = client.post("/api/media/request", json={"kind": "film", "id": 19}).get_json()
    sent = media_servers.arr["radarr"]["items"][0]
    assert sent["rootFolderPath"] == "/data/media/movies" and sent["addOptions"] == {"searchForMovie": True}
    library = client.get("/api/media/library?kind=film").get_json()["items"]
    assert library[0]["requested_by"] == "Nan" and film["title"] == "Metropolis"
    client.post("/api/media/request", json={"kind": "show", "id": 99, "seasons": "pick", "picked": [2]})
    show = media_servers.arr["sonarr"]["items"][0]
    assert [s["seasonNumber"] for s in show["seasons"] if s["monitored"]] == [2]
    assert show["monitorNewItems"] == "all" and "monitor" not in show["addOptions"]
    # Only admins remove, and the files go too.
    assert client.delete("/api/media/film/1").status_code == 403
    login(client, "You")
    removed = client.delete("/api/media/film/1").get_json()["removed"]
    assert removed["title"] == "Metropolis"
    assert media_servers.deleted == [("radarr", "movie/1", {"deleteFiles": "true", "addImportListExclusion": "false"})]


def test_downloads_say_who_asked_and_the_vpn_state(client, media_servers):
    login(client, "You")
    client.post("/api/admin/media/connect")
    client.post("/api/media/request", json={"kind": "show", "id": 99, "seasons": "all"})
    media_servers.arr["sonarr"]["queue"] = [
        {"seriesId": 1, "series": {"title": "Long Show"}, "episode": {"seasonNumber": 1}, "size": 100, "sizeleft": 60, "status": "downloading"},
        {"seriesId": 1, "series": {"title": "Long Show"}, "episode": {"seasonNumber": 1}, "size": 100, "sizeleft": 20, "status": "downloading"}]
    q = client.get("/api/media/queue").get_json()
    assert q["items"] == [dict(q["items"][0], title="Long Show · Season 1", items=2, progress=60, requested_by="You")]
    assert q["vpn"] == {"configured": False, "ok": False, "message": "Waiting for VPN · not set up"}
    assert client.get("/api/media/poster-remote?url=https://evil.example/x.jpg").status_code == 404


def test_admins_delete_a_book_through_the_library_manager(app, client):
    hub = app.hub
    login(client, "Nan")
    emma = book_id(client, "Emma")
    assert client.delete("/api/books/%d" % emma).status_code == 403
    login(client, "You")
    assert "sign-in is not set up" in client.delete("/api/books/%d" % emma).get_json()["error"]
    client.post("/api/admin/connections", json={"values": {"cwa_api_url": "http://manager:8083", "cwa_user": "admin", "cwa_pass": "pw"}})
    fake = FakeLibraryManager(hub.config.library_dir)
    fake.delete = lambda entry: fake.merge(0, [entry])  # the library manager removes the entry and its folder
    hub.manager = fake
    r = client.delete("/api/books/%d" % emma).get_json()
    assert r["removed"] == "Emma" and r["entries"] == 1 and r["backup"].startswith("metadata-")
    assert (Path(hub.config.data_dir) / "library-backups" / r["backup"]).is_file()
    assert "Emma" not in [b["title"] for b in client.get("/api/books").get_json()["books"]]


def test_connect_everything_reports_a_wrong_qbittorrent_password(client, media_servers):
    media_servers.qbit_password_ok = False  # still the temporary password from the first start
    login(client, "You")
    r = client.post("/api/admin/media/connect")
    assert r.status_code == 200
    by_step = {s["step"]: s for s in r.get_json()["steps"]}
    assert by_step["qBittorrent → download folders"]["ok"] is False
    assert by_step["Radarr → media folder"]["ok"]


def test_picking_a_release_for_a_show_searches_one_season(client, media_servers):
    login(client, "You")
    client.post("/api/admin/media/connect")
    client.post("/api/media/request", json={"kind": "show", "id": 99, "seasons": "all"})
    releases = client.get("/api/media/releases/show/1").get_json()["releases"]
    assert releases[0]["title"] == "Long.Show.S02"
    name, params, timeout = media_servers.release_queries[-1]
    assert name == "sonarr" and params == {"seriesId": 1, "seasonNumber": 3} and timeout >= 60  # newest season by default
    client.get("/api/media/releases/show/1?season=2")
    assert media_servers.release_queries[-1][1]["seasonNumber"] == 2
    assert client.post("/api/media/request", json={"kind": "show", "id": "abc"}).status_code == 400
    login(client, "Nan")
    assert client.get("/api/media/releases/show/1").status_code == 403


def test_look_again_searches_for_a_better_version(client, media_servers):
    login(client, "You")
    client.post("/api/admin/media/connect")
    client.post("/api/media/request", json={"kind": "film", "id": 19})
    client.post("/api/media/request", json={"kind": "show", "id": 99, "seasons": "all"})
    radarr, sonarr = media_servers.arr["radarr"], media_servers.arr["sonarr"]
    radarr["items"][0].update(hasFile=True, movieFile={"size": 9, "quality": {"quality": {"name": "Bluray-1080p"}}})
    r = client.post("/api/media/look-again/film/1")
    assert r.status_code == 200 and "Bluray-1080p" in r.get_json()["message"]
    assert radarr["sent"][-1] == {"name": "MoviesSearch", "movieIds": [1]}
    assert client.get("/api/media/item/film/1").get_json()["quality"] == "Bluray-1080p"
    radarr["items"][0]["movieFile"]["quality"]["quality"]["name"] = "WEBDL-2160p"
    assert "already in 2160p" in client.post("/api/media/look-again/film/1").get_json()["message"]
    client.post("/api/media/look-again/show/1")
    assert sonarr["sent"][-1] == {"name": "SeriesSearch", "seriesId": 1}
    login(client, "Nan")  # members need the request switch
    assert client.post("/api/media/look-again/film/1").status_code == 403


def test_stopping_and_starting_the_search_and_picking_a_version(client, media_servers):
    login(client, "You")
    client.post("/api/admin/media/connect")
    client.post("/api/media/request", json={"kind": "film", "id": 19})
    radarr = media_servers.arr["radarr"]
    radarr["commands"] = [{"id": 5, "name": "MoviesSearch", "status": "queued", "body": {"movieIds": [1]}},
                          {"id": 6, "name": "MoviesSearch", "status": "queued", "body": {"movieIds": [2]}}]
    r = client.post("/api/media/looking/film/1", json={"on": False})
    assert r.status_code == 200 and "Stopped looking" in r.get_json()["message"]
    assert radarr["items"][0]["monitored"] is False and radarr["cancelled"] == [5]  # only this film's search
    radarr["commands"] = []  # Radarr forgets a cancelled search
    steps = client.get("/api/media/activity/film/1").get_json()["steps"]
    assert steps[-1]["text"].startswith("Stopped looking")
    client.post("/api/media/looking/film/1", json={"on": True})
    assert radarr["items"][0]["monitored"] is True and radarr["sent"][-1] == {"name": "MoviesSearch", "movieIds": [1]}
    # Anyone allowed to request can choose a version themselves.
    nan = next(p["id"] for p in client.get("/api/state").get_json()["profiles"] if p["name"] == "Nan")
    login(client, "Nan")
    assert client.post("/api/media/looking/film/1", json={"on": False}).status_code == 403
    assert client.post("/api/media/grab", json={"kind": "film", "guid": "g1", "indexer_id": 2}).status_code == 403
    login(client, "You")
    client.patch("/api/profiles/%d" % nan, json={"can_request_media": True})
    login(client, "Nan")
    assert client.get("/api/media/releases/film/1").status_code == 200
    assert client.delete("/api/media/film/1").status_code == 403  # removing stays with admins


def test_every_download_can_be_paused_and_put_in_order(client, media_servers):
    login(client, "You")
    client.post("/api/media/request", json={"kind": "film", "id": 19})  # fails (not connected yet), which is fine here
    media_servers.arr["radarr"]["items"].append({"id": 1, "title": "Metropolis"})
    a, b = "a" * 40, "B" * 40
    media_servers.arr["radarr"]["queue"] = [{"downloadId": b, "movieId": 1, "movie": {"title": "Metropolis"}}]
    media_servers.torrents = [
        {"hash": a, "name": "Some.Book.epub", "category": "books", "state": "pausedDL", "progress": 0.2, "priority": 2, "size": 5, "eta": 8640000},
        {"hash": b.lower(), "name": "Metropolis.1927.2160p", "category": "movies", "state": "downloading", "progress": 0.5,
         "priority": 1, "size": 9, "dlspeed": 2097152, "eta": 600, "num_seeds": 4},
        {"hash": "c" * 40, "name": "Old.Show.S01", "category": "tv", "state": "stalledUP", "progress": 1, "priority": 0}]
    d = client.get("/api/media/torrents").get_json()
    rows = d["torrents"]
    assert d["can_change"] is True
    assert [r["title"] for r in rows] == ["Metropolis", "Some.Book.epub", "Old.Show.S01"]  # in line order, finished last
    assert rows[0]["kind"] == "film" and rows[0]["id"] == 1 and rows[0]["type"] == "Film" and rows[0]["eta"] == 600
    assert rows[1]["type"] == "Book" and rows[1]["paused"] is True and rows[1]["state"] == "Paused" and rows[1]["eta"] is None
    assert rows[2]["done"] is True and rows[2]["state"] == "Done · sharing" and rows[2]["position"] is None
    assert client.post("/api/media/torrents/%s/resume" % a).status_code == 200
    assert media_servers.qbit_posts[-1] == ("start", {"hashes": a})  # qBittorrent 5 calls it start
    # Moving one up switches qBittorrent's queue on the first time.
    assert client.post("/api/media/torrents/%s/top" % a).status_code == 200
    assert media_servers.queueing and media_servers.qbit_posts[-1] == ("topPrio", {"hashes": a})
    assert client.post("/api/media/torrents/%s/explode" % a).status_code == 404
    assert client.post("/api/media/torrents/not-a-hash/pause").status_code == 404
    login(client, "Nan")  # members can see the list but need the request switch to change films and TV
    assert client.get("/api/media/torrents").get_json()["can_change"] is False
    assert client.post("/api/media/torrents/%s/pause" % b.lower()).status_code == 403


def test_a_download_can_be_deleted_from_the_whole_hub(client, media_servers):
    login(client, "You")
    client.post("/api/admin/media/connect")
    client.post("/api/media/request", json={"kind": "film", "id": 19})
    film, book = "f" * 40, "e" * 40
    media_servers.arr["radarr"]["queue"] = [{"downloadId": film.upper(), "movieId": 1, "movie": {"title": "Metropolis"}}]
    media_servers.torrents = [{"hash": film, "name": "Metropolis.2160p", "category": "movies", "state": "downloading", "progress": 0.4},
                              {"hash": book, "name": "Some.Book.epub", "category": "books", "state": "downloading", "progress": 0.1}]
    assert client.get("/api/media/torrents").get_json()["can_delete"] is True
    r = client.delete("/api/media/torrents/" + film)
    assert r.status_code == 200 and r.get_json()["message"] == "Metropolis was deleted everywhere."
    assert ("delete", {"hashes": film, "deleteFiles": "true"}) in media_servers.qbit_posts
    assert media_servers.deleted[-1][:2] == ("radarr", "movie/1")  # the film and its files, off the server
    assert "and its files were deleted" in client.delete("/api/media/torrents/" + book).get_json()["message"]
    media_servers.torrents = []
    assert client.delete("/api/media/torrents/" + book).status_code == 502  # already gone
    nan = next(p["id"] for p in client.get("/api/state").get_json()["profiles"] if p["name"] == "Nan")
    client.patch("/api/profiles/%d" % nan, json={"can_request_media": True})
    login(client, "Nan")  # deleting stays with admins, even for members who can request
    assert client.delete("/api/media/torrents/" + book).status_code == 403


def test_admin_links_to_each_program(client, monkeypatch):
    import requests

    def fake_get(url, **kw):
        if "radarr" not in url:
            raise requests.ConnectionError("not running")
        return None
    monkeypatch.setattr(requests, "get", fake_get)
    login(client, "You")
    services = {x["id"]: x for x in client.get("/api/admin/services").get_json()["services"]}
    assert services["radarr"]["running"] is True and services["radarr"]["port"] == 7878
    assert services["bazarr"]["running"] is False
    login(client, "Nan")
    assert client.get("/api/admin/services").status_code == 403


def test_book_downloads_work_like_films(app, client, media_servers):
    from bookhub import db as bdb
    login(client, "You")
    nan = next(p["id"] for p in client.get("/api/state").get_json()["profiles"] if p["name"] == "Nan")
    client.patch("/api/profiles/%d" % nan, json={"can_request": True, "can_request_media": False})
    with app.app_context():
        con = app.hub.con()
        con.execute("INSERT INTO requests(profile_id, title, indexer, guid, status, created) VALUES(?,?,?,?,?,?)",
                    (nan, "Ursula K. Le Guin - The Dispossessed (1974) epub", "Example", "g", "sent", bdb.now()))
        con.commit()
    book, film = "d" * 40, "9" * 40
    media_servers.torrents = [
        {"hash": book, "name": "Ursula K. Le Guin - The Dispossessed (1974) epub", "category": "books", "state": "downloading", "progress": 0.5, "priority": 1},
        {"hash": film, "name": "Some.Film.2160p", "category": "movies", "state": "downloading", "progress": 0.1, "priority": 2}]
    rows = client.get("/api/media/torrents").get_json()["torrents"]
    assert rows[0]["type"] == "Book" and rows[0]["requested_by"] == "Nan"
    # The corner card follows a book by its release name: downloading, then in the library once intake has added it.
    title = "Ursula K. Le Guin - The Dispossessed (1974) epub"
    item = client.get("/api/media/tracking", query_string={"book": title}).get_json()["items"][0]
    assert item["kind"] == "book" and item["now"]["progress"] == 50 and item["finished"] is False
    media_servers.torrents[0].update(progress=1, state="stalledUP")
    assert client.get("/api/media/tracking", query_string={"book": title}).get_json()["items"][0]["now"]["text"].startswith("Downloaded")
    with app.app_context():
        con = app.hub.con()
        con.execute("INSERT INTO imported_files(path, at) VALUES(?, ?)", ("/downloads/The Dispossessed - Ursula K. Le Guin.epub", bdb.now()))
        con.commit()
    done = client.get("/api/media/tracking", query_string={"book": title}).get_json()["items"][0]
    assert done["finished"] is True and done["now"]["text"] == "In the library"
    unknown = client.get("/api/media/tracking", query_string={"book": "Nothing Like It"}).get_json()["items"][0]
    assert unknown["now"]["text"].startswith("Sent to qBittorrent")
    # Nan may find books but not request films: she can pause her book, not the film.
    login(client, "Nan")
    d = client.get("/api/media/torrents").get_json()
    assert d["can_change_books"] is True and d["can_change"] is False and d["can_delete"] is False
    assert client.post("/api/media/torrents/%s/pause" % book).status_code == 200
    assert client.post("/api/media/torrents/%s/pause" % film).status_code == 403


def test_a_running_search_says_how_many_sites_have_answered(client, media_servers):
    from datetime import datetime, timedelta, timezone
    login(client, "You")
    now = datetime.now(timezone.utc)
    stamp = lambda secs: (now - timedelta(seconds=secs)).strftime("%Y-%m-%dT%H:%M:%S.1234567Z")
    media_servers.prowlarr_history = [
        {"eventType": "indexerQuery", "indexerId": 4, "successful": True, "date": stamp(2), "data": {"query": "the dispossessed"}},
        {"eventType": "indexerQuery", "indexerId": 4, "successful": True, "date": stamp(1), "data": {"query": "the dispossessed"}},  # a second page
        {"eventType": "indexerQuery", "indexerId": 5, "successful": True, "date": stamp(600), "data": {"query": "the dispossessed"}},  # an older search
        {"eventType": "indexerQuery", "indexerId": 5, "successful": True, "date": stamp(1), "data": {"query": "something else"}}]
    d = client.get("/api/search-progress?q=The Dispossessed&ago=10").get_json()
    assert d == {"sites": 2, "answered": 1, "names": ["Example Tracker"]}  # switched-off sites are not counted
    assert client.get("/api/search-progress?ago=10").status_code == 400


def test_a_show_lists_its_episodes_with_where_to_watch_each(app, client, media_servers):
    from bookhub import settings
    login(client, "You")
    client.post("/api/admin/media/connect")
    client.post("/api/media/request", json={"kind": "show", "id": 99, "seasons": "all"})
    with app.app_context():
        settings.save(app.hub, {"jellyfin_url": "http://jellyfin:8096", "jellyfin_api_key": "j" * 32})
    d = client.get("/api/media/watch/show/1").get_json()
    assert d["jellyfin"]["port"] == 8096
    assert [x["number"] for x in d["seasons"]] == [1]  # specials left out
    pilot, second = d["seasons"][0]["episodes"]
    assert pilot["title"] == "Pilot" and pilot["on_server"] and pilot["watch_id"] == "jf-s1e1"  # matched by TVDB number
    assert second["on_server"] is False and second["watch_id"] is None and second["wanted"]


def test_versions_appear_as_sites_answer_and_one_can_be_picked_early(client, media_servers):
    import time
    login(client, "You")
    client.post("/api/admin/media/connect")
    client.post("/api/media/request", json={"kind": "film", "id": 19})
    job = client.post("/api/media/versions/film/1").get_json()["job"]
    for _ in range(100):
        d = client.get("/api/media/versions/" + job).get_json()
        if d["done"]:
            break
        time.sleep(0.05)
    assert d["sites"] == 2 and d["answered"] == 2
    assert [r["title"] for r in d["found"]] == ["Metropolis.1927.2160p.WEB", "Metropolis.1927.1080p.BluRay", "Metropolis.1927.HDCAM"]  # best first
    assert d["found"][2]["quality"] == "Cam" and "link" not in d["found"][0]  # links stay on the server
    assert {q["query"] for q in media_servers.site_searches} == {"Metropolis 1927"}
    assert d["checked"][0]["title"] == "Long.Show.S02"  # Radarr's own checked list, for when it finishes
    # Picked before the full search finished: handed to Radarr, which checks it and downloads it.
    ok = client.post("/api/media/versions/%s/grab" % job, json={"guid": "a"}).get_json()
    assert ok == {"ok": True} and media_servers.pushed[-1][1]["downloadUrl"] == "magnet:?xt=a"
    no = client.post("/api/media/versions/%s/grab" % job, json={"guid": "b"}).get_json()
    assert no["ok"] is False and "Cam copies" in no["why"]
    forced = client.post("/api/media/versions/%s/grab" % job, json={"guid": "c", "force": True}).get_json()
    assert forced["direct"] is True and media_servers.qbit_posts[-1] == ("add", {"urls": "magnet:?xt=c", "category": "movies"})
    assert client.get("/api/media/versions/nope").status_code == 502


def test_download_speed_can_be_limited_and_slowed(client, media_servers):
    login(client, "You")
    d = client.get("/api/media/speed").get_json()
    assert d["down_now"] == 3145728 and d["down_limit"] == 0 and d["slow_mode"] is False and d["can_change"] is True
    d = client.post("/api/media/speed", json={"down_limit": 5 * 1048576, "up_limit": 512 * 1024, "slow_down": 1048576}).get_json()
    assert media_servers.prefs["dl_limit"] == 5242880 and media_servers.prefs["up_limit"] == 524288 and d["slow_down"] == 1048576
    assert client.post("/api/media/speed", json={"slow_mode": True}).get_json()["slow_mode"] is True
    assert client.post("/api/media/speed", json={"slow_mode": True}).get_json()["slow_mode"] is True  # pressing twice keeps it on
    assert client.post("/api/media/speed", json={"down_limit": "fast"}).status_code == 502
    nan = next(p["id"] for p in client.get("/api/state").get_json()["profiles"] if p["name"] == "Nan")
    client.patch("/api/profiles/%d" % nan, json={"can_request": False, "can_request_media": False})
    login(client, "Nan")
    assert client.get("/api/media/speed").get_json()["can_change"] is False
    assert client.post("/api/media/speed", json={"slow_mode": False}).status_code == 403


def test_a_film_being_downloaded_says_so(client, media_servers):
    login(client, "You")
    client.post("/api/admin/media/connect")
    client.post("/api/media/request", json={"kind": "film", "id": 19})
    media_servers.arr["radarr"]["queue"] = [{"movieId": 1, "size": 200, "sizeleft": 50}]
    film = client.get("/api/media/library?kind=film").get_json()["items"][0]
    assert film["downloading"] == {"progress": 75, "episodes": 1, "stuck": False}
    assert client.get("/api/media/item/film/1").get_json()["downloading"]["progress"] == 75
    media_servers.arr["radarr"]["queue"] = []
    assert client.get("/api/media/item/film/1").get_json()["downloading"] is None


def test_jellyfin_is_asked_with_the_current_sign_in_header():
    from bookhub import media
    assert media.jellyfin_auth("abc") == {"Authorization": 'MediaBrowser Token="abc"'}


def test_whats_happening_tells_the_story_of_a_search(client, media_servers):
    login(client, "You")
    client.post("/api/admin/media/connect")
    client.post("/api/media/request", json={"kind": "film", "id": 19})
    radarr = media_servers.arr["radarr"]
    radarr["items"][0]["title"] = "The Metropolis!"  # Radarr searches without "The" and the punctuation
    radarr["commands"] = [{"name": "MoviesSearch", "status": "completed", "message": "Completed search for 1 movies. 1 reports downloaded.",
                           "body": {"movieIds": [1]}, "started": "2099-01-01T10:00:01Z"}]
    media_servers.prowlarr_history = [
        {"eventType": "indexerQuery", "indexerId": 4, "successful": True, "date": "2099-01-01T10:00:02Z",
         "data": {"source": "Radarr", "query": "Metropolis 1927", "queryResults": "7"}},
        {"eventType": "indexerQuery", "indexerId": 4, "successful": True, "date": "2099-01-01T10:00:03Z",
         "data": {"source": "Radarr", "query": "Metropolis 1927", "queryResults": "3"}},
        {"eventType": "indexerQuery", "indexerId": 5, "successful": True, "date": "2099-01-01T10:00:03Z",
         "data": {"source": "Radarr", "query": "Metropolis 1927", "queryResults": "0"}}]
    radarr["item_history"] = [{"eventType": "grabbed", "date": "2099-01-01T10:00:05Z", "sourceTitle": "Metropolis.1927.2160p",
                               "quality": {"quality": {"name": "Bluray-2160p"}}, "data": {"indexer": "Example Tracker"}}]
    radarr["queue"] = [{"movieId": 1, "status": "downloading", "size": 100, "sizeleft": 25, "timeleft": "00:10:00", "added": "2099-01-01T10:00:06Z"}]
    activity = client.get("/api/media/activity/film/1").get_json()
    steps = activity["steps"]
    texts = [s["text"] for s in steps]
    assert texts[0].startswith("Requested by You")
    assert "Looked at Quiet Site · nothing there" in texts
    assert "Looked at Example Tracker · 10 results" in texts  # two pages of one search, added up
    assert any(t.startswith("Best version found: Metropolis.1927.2160p (Bluray-2160p) · from Example Tracker") for t in texts)
    assert steps[-1]["state"] == "active" and steps[-1]["progress"] == 75
    # The top line of the panel: the current step and how far along it is.
    now = activity["now"]
    assert now["text"].startswith("Downloading through the VPN · 75%") and now["progress"] == 50 + 75 * 45 // 100
    assert now["sites"] == "Asked 2 sites · 1 had something (Example Tracker)"
    # The corner pop-up asks for several at once; a removed one comes back as gone.
    tracked = client.get("/api/media/tracking?items=film:1,film:9,nonsense,show:x").get_json()["items"]
    assert tracked[0]["title"] == "The Metropolis!" and tracked[0]["finished"] is False and tracked[0]["now"]["progress"] == now["progress"]
    assert tracked[1] == {"kind": "film", "id": 9, "gone": True} and len(tracked) == 2
    login(client, "Nan")
    assert client.get("/api/media/activity/film/1").status_code == 200  # everyone signed in may watch it happen


def test_downloads_go_where_they_can_be_filed_and_say_when_they_cannot(client, media_servers):
    login(client, "You")
    # One that went to qBittorrent's default folder before: moved into its category's folder.
    media_servers.torrents = [{"hash": "a" * 40, "category": "movies", "auto_tmm": False},
                              {"hash": "b" * 40, "category": "books", "auto_tmm": False}]
    steps = {s["step"]: s for s in client.post("/api/admin/media/connect").get_json()["steps"]}
    assert media_servers.prefs["auto_tmm_enabled"] is True  # category folders count
    assert ("setAutoManagement", {"hashes": "a" * 40, "enable": "true"}) in media_servers.qbit_posts  # books stay put
    assert "moved 1 earlier download into its folder" in steps["qBittorrent → download folders"]["message"]
    client.post("/api/media/request", json={"kind": "show", "id": 99, "seasons": "all"})
    # A whole season in one download, finished but refused by Sonarr: one line, said plainly, not "filing it".
    stuck = {"seriesId": 1, "downloadId": "S1", "status": "completed", "trackedDownloadStatus": "warning", "size": 100, "sizeleft": 0,
             "statusMessages": [{"messages": ["No files found are eligible for import in /downloads/Long.Show.S01"]}]}
    media_servers.arr["sonarr"]["queue"] = [dict(stuck, episode={"seasonNumber": 1, "episodeNumber": n}) for n in (1, 2, 3)]
    activity = client.get("/api/media/activity/show/1").get_json()
    bad = [s for s in activity["steps"] if s["state"] == "bad"]
    assert len(bad) == 1 and bad[0]["text"].startswith("Season 1: Downloaded, but Sonarr could not file it: No files found")
    assert "Connect everything" in bad[0]["text"] and activity["now"]["state"] == "bad"
    assert client.get("/api/media/item/show/1").get_json()["downloading"]["stuck"] is True


def test_asking_twice_and_services_that_are_away(client, media_servers):
    login(client, "You")
    client.post("/api/admin/media/connect")
    first = client.post("/api/media/request", json={"kind": "film", "id": 19}).get_json()
    again = client.post("/api/media/request", json={"kind": "film", "id": 19})  # from a second screen, say
    assert again.status_code == 200 and again.get_json()["already"] is True and again.get_json()["id"] == first["id"]
    assert len(media_servers.arr["radarr"]["items"]) == 1
    # qBittorrent away: the books in the corner say so, and the films keep updating.
    media_servers.qbit_password_ok = False
    tracked = client.get("/api/media/tracking", query_string={"items": "film:1", "book": "Some Book"}).get_json()["items"]
    assert tracked[0]["kind"] == "book" and tracked[0]["now"]["state"] == "bad" and "qBittorrent" in tracked[0]["now"]["text"]
    assert tracked[1]["title"] == "Metropolis"
    # Radarr away: that film is still followed, with the reason.
    media_servers.down.add("radarr")
    tracked = client.get("/api/media/tracking?items=film:1").get_json()["items"]
    assert tracked == [dict(tracked[0], kind="film", id=1, unreachable=True)] and tracked[0]["now"]["text"].startswith("503")


def test_connect_everything_passes_a_changed_qbittorrent_password_on(app, client, media_servers):
    from bookhub import settings
    login(client, "You")
    client.post("/api/admin/media/connect")
    with app.app_context():
        settings.save(app.hub, {"qbit_pass": "a-new-password"})
    client.post("/api/admin/media/connect")
    fields = {f["name"]: f.get("value") for f in media_servers.arr["radarr"]["downloadclient"][0]["fields"]}
    assert fields["password"] == "a-new-password"


# ------------------------------------------------------------- robustness and security

def test_a_failed_write_does_not_keep_the_database_locked(app, client):
    import sqlite3
    import threading
    login(client, "You")
    ids = ids_of(client)
    # Renaming Nan to a name already taken fails part-way through a write.
    assert client.patch("/api/profiles/%d" % ids["Nan"], json={"name": "Partner"}).status_code == 409
    result = {}

    def other_thread():
        con = sqlite3.connect(str(app.hub.config.db_path), timeout=1)
        try:
            con.execute("INSERT INTO kv(key, value) VALUES('lock-test', 'x')")
            con.commit()
            result["ok"] = True
        except sqlite3.OperationalError as exc:
            result["ok"] = str(exc)
        finally:
            con.close()

    worker = threading.Thread(target=other_thread)
    worker.start()
    worker.join()
    assert result["ok"] is True


def test_passcode_tries_sent_side_by_side_are_all_counted(app):
    import threading
    ids = {p["name"]: p["id"] for p in app.test_client().get("/api/state").get_json()["profiles"]}
    start, codes = threading.Barrier(10), []

    def guess(n):
        c = app.test_client()
        start.wait()
        codes.append(c.post("/api/login", json={"profile_id": ids["Partner"], "passcode": "99%02d" % n}).status_code)

    workers = [threading.Thread(target=guess, args=(n,)) for n in range(10)]
    for w in workers:
        w.start()
    for w in workers:
        w.join()
    assert sorted(codes) == [403] * 5 + [429] * 5


def test_home_network_checks_believe_only_the_proxy(app, client):
    app.hub.config.trust_proxy = True
    # A browser outside can put a home address at the front; the proxy adds the real one at the end.
    spoofed = {"X-Forwarded-For": "192.168.1.5, 8.8.8.8"}  # (documentation ranges count as private to Python)
    assert client.post("/api/login", json={"guest": True}, headers=spoofed).status_code == 403
    home = {"X-Forwarded-For": "192.168.1.5"}
    assert client.post("/api/login", json={"guest": True}, headers=home).status_code == 200
    assert client.get("/api/books", headers=home).status_code == 200
    # The same guest cookie, taken out of the house, no longer works.
    assert client.get("/api/books", headers={"X-Forwarded-For": "8.8.8.8"}).status_code == 401


def test_sync_passwords_cannot_be_guessed_without_end(client, monkeypatch):
    monkeypatch.setattr(kosync, "_misses", {})
    login(client, "Nan")
    ids = ids_of(client)
    assert client.patch("/api/profiles/%d" % ids["Nan"], json={"sync_password": "reading"}).status_code == 200
    right = {"x-auth-user": "Nan", "x-auth-key": kosync.sync_key_for("reading")}
    assert client.get("/kosync/users/auth", headers=right).status_code == 200
    for n in range(kosync.MAX_TRIES):
        wrong = {"x-auth-user": "Nan", "x-auth-key": kosync.sync_key_for("guess%d" % n)}
        assert client.get("/kosync/users/auth", headers=wrong).status_code == 401
    assert client.get("/kosync/users/auth", headers=right).status_code == 401  # locked for a minute


def test_links_inside_archives_are_never_followed(app, tmp_path, monkeypatch):
    from bookhub import seriesfile
    real_is_symlink = Path.is_symlink
    # Stands in for a link inside the archive pointing somewhere else on the server (this PC cannot make real ones).
    monkeypatch.setattr(Path, "is_symlink", lambda p: p.name.startswith("link") or real_is_symlink(p))

    def fake_unpack(args, **kw):
        out = Path(args[args.index("-C") + 1])
        (out / "001.jpg").write_bytes(b"page")
        (out / "link.jpg").write_bytes(b"secret")
        (out / "book.epub").write_bytes(b"PK book")
        (out / "link.epub").write_bytes(b"secret")

    monkeypatch.setattr(seriesfile.subprocess, "run", fake_unpack)
    cbr = tmp_path / "volume.cbr"
    cbr.write_bytes(b"Rar!")
    scratch = tmp_path / "scratch"
    assert [name for name, _ in seriesfile._pages(cbr, scratch)] == ["001.jpg"]

    from bookhub import intake
    monkeypatch.setattr(intake.subprocess, "run", fake_unpack)
    monkeypatch.setattr(intake, "_archive_tool", lambda: "bsdtar")
    into = tmp_path / "unpacked"
    into.mkdir()
    found = app.hub.intake._extract_books(tmp_path / "books.rar", into, {".epub"})
    assert [f.name for f in found] == ["book.epub"]


def test_pictures_passed_on_from_other_servers_are_only_ever_pictures(client, monkeypatch):
    from bookhub import api
    assert api._safe_type("image/jpeg; charset=binary") == "image/jpeg"
    assert api._safe_type("image/svg+xml") == "application/octet-stream"
    assert api._safe_type("text/html") == "application/octet-stream"
    assert api._safe_type(None) == "application/octet-stream"
    login(client, "Nan")
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    monkeypatch.setattr(api.requests, "get", lambda *a, **kw: FakeResponse(200, svg, "image/svg+xml"))
    assert client.get("/api/media/poster-remote?url=https://image.tmdb.org/t/p/w500/x.svg").status_code == 404


def test_json_that_is_not_an_object_is_refused_plainly(client):
    assert client.post("/api/login", json=[1, 2]).status_code == 400
    login(client, "You")
    r = client.post("/api/books/kind", json=["Dracula"])
    assert r.status_code == 400 and r.get_json()["error"]


def test_catalogue_errors_never_show_the_api_key(monkeypatch):
    import requests

    def refuse(url, params=None, **kw):
        raise requests.ConnectionError("Max retries exceeded with url: /books/v1/volumes?q=x&key=%s" % params["key"])

    monkeypatch.setattr(catalogues.requests, "get", refuse)
    with pytest.raises(catalogues.LookupError_) as caught:
        catalogues._get(catalogues.GOOGLE, {"q": "x", "key": "AIzaSecretKey123"})
    assert "AIzaSecretKey123" not in str(caught.value) and "(key)" in str(caught.value)


def test_a_download_nobody_shares_waits_at_the_back_until_someone_does(app, client, media_servers):
    stuck = {"hash": "a" * 40, "name": "Lost.Film.1950", "category": "movies", "progress": 0.1, "state": "stalledDL", "num_seeds": 0, "priority": 1}
    going = {"hash": "b" * 40, "name": "Busy.Film.2001", "category": "movies", "progress": 0.5, "state": "downloading", "num_seeds": 9, "priority": 2, "dlspeed": 5}
    media_servers.torrents = [stuck, going]
    with app.app_context():
        m = app.hub.media
        m.park_stalled(now=1000)
        assert m.parked() == {}  # not yet: ten minutes first
        m.park_stalled(now=1000 + 11 * 60)
        assert list(m.parked()) == [stuck["hash"]]
    sent = [name for name, _ in media_servers.qbit_posts]
    assert "bottomPrio" in sent and "stop" in sent
    stuck["state"] = "stoppedDL"
    login(client, "You")
    rows = client.get("/api/media/torrents").get_json()["torrents"]
    assert [r["name"] for r in rows] == ["Busy.Film.2001", "Lost.Film.1950"]  # it waits after the rest
    assert rows[1]["state"] == "Waiting for seed" and rows[1]["waiting_seed"] and rows[1]["next_check"]
    counts = client.get("/api/media/speed").get_json()["counts"]
    assert counts["waiting_seed"] == 1 and counts["downloading"] == 1
    with app.app_context():
        # Half an hour on it is started to look for someone sharing it...
        m.park_stalled(now=1000 + 42 * 60)
        assert media_servers.qbit_posts[-1] == ("setForceStart", {"hashes": stuck["hash"], "value": "true"})
        # ...nobody: after five minutes it is paused again.
        stuck["state"] = "stalledDL"
        m.park_stalled(now=1000 + 48 * 60)
        assert m.parked()[stuck["hash"]]["checking"] is None and media_servers.qbit_posts[-1][0] == "stop"
        stuck["state"] = "stoppedDL"
        # The next check finds someone, and it carries on from the back of the line.
        m.park_stalled(now=1000 + 79 * 60)
        stuck.update(state="forcedDL", num_seeds=2)
        m.park_stalled(now=1000 + 80 * 60)
        assert m.parked() == {} and media_servers.qbit_posts[-1] == ("setForceStart", {"hashes": stuck["hash"], "value": "false"})
        # Pressing Continue on one takes it out of waiting for seed straight away.
        stuck.update(state="stoppedDL", num_seeds=0)
        from bookhub import db
        db.kv_set(app.hub.con(), "media:parked", json.dumps({stuck["hash"]: {"since": 1, "checked": 1, "checking": None}}))
    assert client.post("/api/media/torrents/%s/resume" % stuck["hash"]).status_code == 200
    with app.app_context():
        assert app.hub.media.parked() == {}


def test_films_on_the_disk_are_recognised_filed_and_spares_deleted(app, client, media_servers, tmp_path):
    root = tmp_path / "media"
    app.hub.config.media_root = root
    (root / "torrents" / "movies" / "Old.Film.1999").mkdir(parents=True)
    (root / "torrents" / "movies" / "Old.Film.1999" / "Old.Film.1999.mkv").write_bytes(b"x" * 10)
    (root / "torrents" / "movies" / "Old.Film.1999" / "Old.Film.1999.nfo").write_text("info")
    (root / "torrents" / "movies" / "Spare.Film.2001.mkv").write_bytes(b"x" * 10)
    q = {"quality": {"name": "Bluray-1080p"}}
    media_servers.arr["radarr"]["rootfolder"] = [{"id": 1, "path": "/data/media/movies"}]
    media_servers.arr["radarr"]["files"] = [
        {"path": "/data/torrents/movies/Old.Film.1999/Old.Film.1999.mkv", "relativePath": "Old.Film.1999/Old.Film.1999.mkv", "size": 10, "quality": q,
         "movie": {"id": 4, "title": "Old Film", "year": 1999, "movieFileId": 0}, "rejections": []},
        {"path": "/data/torrents/movies/Spare.Film.2001.mkv", "relativePath": "Spare.Film.2001.mkv", "size": 10, "quality": q,
         "movie": {"id": 5, "title": "Spare Film", "year": 2001, "movieFileId": 9}, "rejections": [{"reason": "Not an upgrade for existing movie file(s)", "type": "permanent"}]},
        {"path": "/data/unsorted/films/odd_film.mkv", "relativePath": "odd_film.mkv", "size": 10, "quality": q, "movie": None, "rejections": [{"reason": "Unknown Movie"}]},
        {"path": "/data/torrents/movies/notes.txt", "relativePath": "notes.txt", "size": 1, "quality": q, "movie": None, "rejections": []},
    ]
    login(client, "Nan")
    assert client.get("/api/admin/media/sort?kind=film").status_code == 403
    login(client, "You")
    d = client.get("/api/admin/media/sort?kind=film").get_json()
    assert (root / "unsorted" / "films").is_dir() and d["folders"]["unsorted"] == "stack/data/unsorted/films"
    status = {i["name"]: i["status"] for i in d["items"]}
    assert status == {"Old.Film.1999/Old.Film.1999.mkv": "ready", "odd_film.mkv": "unknown", "Spare.Film.2001.mkv": "have"}
    odd = next(i for i in d["items"] if i["status"] == "unknown")
    assert odd["guess"] == "Some Odd Film 2003"
    # Filing moves it through Radarr (the fake Radarr's scan does not look at the disk, so the move is done here
    # first), then tidies the folder it came from.
    (root / "torrents" / "movies" / "Old.Film.1999" / "Old.Film.1999.mkv").unlink()
    r = client.post("/api/admin/media/sort", json={"kind": "film", "path": "/data/torrents/movies/Old.Film.1999/Old.Film.1999.mkv"})
    assert r.status_code == 200 and r.get_json()["done"]
    sent = media_servers.arr["radarr"]["sent"][-1]
    assert sent["name"] == "ManualImport" and sent["importMode"] == "move" and sent["files"][0]["movieId"] == 4
    assert not (root / "torrents" / "movies" / "Old.Film.1999").exists()  # only the .nfo was left
    media_servers.arr["radarr"]["files"].pop(0)
    client.post("/api/admin/media/sort", json={"kind": "film", "path": "/data/torrents/movies/Spare.Film.2001.mkv"})  # refused: a spare
    assert len(media_servers.arr["radarr"]["sent"]) == 1
    # A file Radarr could not place is filed against the film picked from the search, added without a search.
    r = client.post("/api/admin/media/sort", json={"kind": "film", "path": "/data/unsorted/films/odd_film.mkv", "external_id": 19})
    assert r.status_code == 200, r.get_json()
    added = media_servers.arr["radarr"]["items"][-1]
    assert added["tmdbId"] == 19 and added["monitored"] is False and added["addOptions"] == {"searchForMovie": False}
    assert media_servers.arr["radarr"]["sent"][-1]["files"][0]["movieId"] == added["id"]
    # A spare copy is deleted; nothing outside the download and unsorted folders can be.
    assert client.post("/api/admin/media/sort/remove", json={"kind": "film", "path": "/data/media/movies/x.mkv"}).status_code == 502
    assert client.post("/api/admin/media/sort/remove", json={"kind": "film", "path": "/data/torrents/movies/../../media/x.mkv"}).status_code == 502
    r = client.post("/api/admin/media/sort/remove", json={"kind": "film", "path": "/data/torrents/movies/Spare.Film.2001.mkv"})
    assert r.status_code == 200 and not (root / "torrents" / "movies" / "Spare.Film.2001.mkv").exists()
    assert (root / "torrents" / "movies").is_dir()  # the download folder itself stays


def test_books_without_real_details_are_found_and_put_right(app, client, tmp_path):
    from bookhub import bookfix
    assert bookfix.valid_isbn("1592004938") == "1592004938" and bookfix.valid_isbn("1592004939") == ""
    assert bookfix.valid_isbn("978-0-14-143951-8") == "9780141439518"
    assert bookfix.username_like("elenak") and not bookfix.username_like("Homer")
    assert bookfix.tidy_title("Story Circle Digital Storytelling~tqw~ darksiderg") == "Story Circle Storytelling"
    hub, library = app.hub, Path(app.hub.config.library_dir)
    import sqlite3
    con = sqlite3.connect(library / "metadata.db")
    emma = con.execute("SELECT id FROM books WHERE title='Emma'").fetchone()[0]
    con.execute("UPDATE books SET title='0141439580.pdf' WHERE id=?", (emma,))
    con.execute("DELETE FROM identifiers WHERE book=?", (emma,))
    con.commit()
    con.close()
    hub.library.refresh(force=True)
    old_key = hub.library.get(emma)["key"]
    login(client, "You")
    with app.app_context():
        nan = next(p for p in hub.profiles() if p["name"] == "Nan")
        from bookhub import db
        hub.con().execute("DELETE FROM reading WHERE book_key=?", (db.make_key("Emma", "Jane Austen"),))  # the sample's own history
        hub.save_reading(nan["id"], old_key, percent=40)
    books = client.get("/api/admin/book-details").get_json()["books"]
    found = next(b for b in books if b["id"] == emma)
    assert found["problems"] == ["The title looks like a file name"]
    asked = []

    def fake_get(url, params, headers=None):
        asked.append(params)
        if params.get("q") == "isbn:0141439580":
            return {"docs": [{"title": "Emma", "author_name": ["Jane Austen"], "first_publish_year": 1815, "cover_i": 7}]}
        return {"docs": []}
    import bookhub.catalogues as cat
    real = cat._get
    cat._get = fake_get
    try:
        d = client.get("/api/admin/book-details/%d" % emma).get_json()
    finally:
        cat._get = real
    assert d["clues"]["isbns"][0] == {"isbn": "0141439580", "where": "in the title"}
    assert d["candidates"][0]["title"] == "Emma" and d["candidates"][0]["authors"] == ["Jane Austen"]
    # Saving needs the library manager; then the change is made there and Family Hub's records follow the book.
    assert client.post("/api/admin/book-details/%d" % emma, json={"title": "Emma", "authors": "Jane Austen"}).status_code == 400
    client.post("/api/admin/connections", json={"values": {"cwa_api_url": "http://manager:8083", "cwa_user": "admin", "cwa_pass": "pw-1"}})
    hub.manager = FakeLibraryManager(library)
    r = client.post("/api/admin/book-details/%d" % emma, json={"title": "Emma", "authors": "Jane Austen"})
    assert r.status_code == 200, r.get_json()
    assert ("set", emma, "title") in hub.manager.calls and list((Path(hub.config.data_dir) / "library-backups").glob("metadata-*.db"))
    new_key = hub.library.get(emma)["key"]
    assert new_key != old_key
    with app.app_context():
        assert hub.con().execute("SELECT percent FROM reading WHERE book_key=?", (new_key,)).fetchone()[0] == 40
        assert hub.con().execute("SELECT COUNT(*) FROM reading WHERE book_key=?", (old_key,)).fetchone()[0] == 0
    assert all(b["id"] != emma for b in client.get("/api/admin/book-details").get_json()["books"])
    # "It is right" leaves a book off the list.
    other = client.get("/api/admin/book-details").get_json()["books"]
    if other:
        client.post("/api/admin/book-details/%d" % other[0]["id"], json={"leave": True})
        assert all(b["id"] != other[0]["id"] for b in client.get("/api/admin/book-details").get_json()["books"])
    login(client, "Nan")
    assert client.get("/api/admin/book-details").status_code == 403


def test_settings_saved_on_the_admin_page_go_into_the_env_file(app, client, tmp_path):
    from bookhub import db, settings
    env = tmp_path / ".env"
    env.write_bytes(b"# my household\r\nTZ=Etc/UTC\r\nPROWLARR_URL=http://prowlarr:9696\r\nSMTP_PASS=\r\n")
    with app.app_context():
        db.kv_set(app.hub.con(), "setting:google_books_key", "g-key-saved-before")  # saved by an older version
    app.hub.config.config_file = str(env)
    with app.app_context():
        settings.apply(app.hub)  # as when Family Hub starts
    login(client, "You")
    d = client.get("/api/admin/connections").get_json()
    assert d["in_file"]
    # The older database value moved into the file, and out of the database.
    text = env.read_text()
    assert "GOOGLE_BOOKS_KEY=g-key-saved-before" in text and "# --- Saved from Family Hub's Admin page" in text
    with app.app_context():
        assert db.kv_get(app.hub.con(), "setting:google_books_key") is None
    tricky = "pa ss#$word'\"x"
    r = client.post("/api/admin/connections", json={"values": {"smtp_pass": tricky, "prowlarr_url": "http://other:9696/", "smtp_user": "family@example.com"}})
    assert r.status_code == 200
    raw = env.read_bytes()
    assert raw.startswith(b"# my household\r\nTZ=Etc/UTC\r\n") and b"\r\nPROWLARR_URL=http://other:9696\r\n" in raw  # comments and line endings kept
    assert settings.EnvFile(env).read()["SMTP_PASS"] == tricky
    assert app.hub.config.smtp_pass == tricky and app.hub.config.prowlarr_url == "http://other:9696"
    fields = {f["name"]: f for g in client.get("/api/admin/connections").get_json()["groups"] for f in g["fields"]}
    assert fields["smtp_pass"]["source"] == "file" and fields["smtp_pass"]["value"] == ""  # secrets never sent back
    # Clearing one leaves the line, empty, so the old value Docker started with is not used either.
    client.post("/api/admin/connections", json={"values": {"smtp_user": ""}})
    assert "SMTP_USER=\r\n" in env.read_text(encoding="utf-8").replace("\n", "\r\n") and app.hub.config.smtp_user == ""
    for value in ("plain", "with space", "dollar$sign", "quote'and$", "back\\slash'"):
        assert settings.EnvFile.parse_value(settings.EnvFile.format_value(value)) == value


def test_the_setup_file_carries_the_whole_setup_locked(app, client, tmp_path):
    from bookhub import setupfile
    env = tmp_path / "hub" / ".env"
    env.parent.mkdir()
    env.write_text("TZ=Etc/UTC\nCWA_PASS=library-pw\n")
    stack = tmp_path / "stack"
    (stack / "prowlarr-config" / "logs").mkdir(parents=True)
    (stack / "prowlarr-config" / "config.xml").write_text("<ApiKey>abc</ApiKey>")
    (stack / "prowlarr-config" / "logs" / "big.txt").write_text("noise")
    import sqlite3
    con = sqlite3.connect(stack / "prowlarr-config" / "prowlarr.db")
    con.execute("CREATE TABLE indexers(name)")
    con.execute("INSERT INTO indexers VALUES ('My tracker')")
    con.commit()
    con.close()
    app.hub.config.config_file, app.hub.config.stack_dir = str(env), stack
    login(client, "You")
    assert client.post("/api/admin/setup-file", json={"passphrase": "short"}).status_code == 400
    r = client.post("/api/admin/setup-file", json={"passphrase": "correct horse battery"})
    assert r.status_code == 200 and r.headers["Content-Disposition"].startswith("attachment; filename=family-hub-setup-")
    assert not list((Path(app.hub.config.data_dir) / "setup-files").glob("*.zip"))  # not left on the server
    setup = tmp_path / "setup.zip"
    setup.write_bytes(r.data)
    import zipfile
    names = zipfile.ZipFile(setup).namelist()
    assert {".env", "README.txt", "stack/bookhub/bookhub.db", "stack/prowlarr-config/config.xml", "stack/prowlarr-config/prowlarr.db"} <= set(names)
    assert not any("/logs/" in n for n in names)
    with zipfile.ZipFile(setup) as z:  # locked: nothing can be read without the passphrase
        with pytest.raises(RuntimeError):
            z.read(".env")
    target = tmp_path / "new-server"
    target.mkdir()
    (target / ".env").write_text("OLD=1\n")
    with pytest.raises(setupfile.SetupError):
        setupfile.restore(setup, target, "wrong passphrase!!")
    assert (target / ".env").read_text() == "OLD=1\n"  # nothing touched by a wrong passphrase
    setupfile.restore(setup, target, "correct horse battery")
    assert "CWA_PASS=library-pw" in (target / ".env").read_text() and (target / ".env.before-restore").read_text() == "OLD=1\n"
    con = sqlite3.connect(target / "stack" / "prowlarr-config" / "prowlarr.db")
    assert con.execute("SELECT name FROM indexers").fetchone()[0] == "My tracker"
    con.close()
    con = sqlite3.connect(target / "stack" / "bookhub" / "bookhub.db")
    assert con.execute("SELECT COUNT(*) FROM profiles").fetchone()[0] >= 2  # the profiles came too
    con.close()
    login(client, "Nan")
    assert client.post("/api/admin/setup-file", json={"passphrase": "correct horse battery"}).status_code == 403


def test_connect_everything_gives_each_program_the_same_sign_in(app, client, media_servers):
    app.hub.config.services_user, app.hub.config.services_pass = "admin", "Shared-pw-1!"
    login(client, "You")
    steps = {x["step"]: x for x in client.post("/api/admin/media/connect").get_json()["steps"]}
    assert steps["Sign-in for each program's page"]["ok"], steps["Sign-in for each program's page"]
    for name in ("radarr", "sonarr", "prowlarr"):
        host = media_servers.hosts[name]
        assert (host["username"], host["password"], host["authenticationMethod"]) == ("admin", "Shared-pw-1!", "forms")
    assert any('"web_ui_password": "Shared-pw-1!"' in d["json"] for n, d in media_servers.qbit_posts if n == "setPreferences")
    assert (app.hub.config.qbit_user, app.hub.config.qbit_pass) == ("admin", "Shared-pw-1!")  # Family Hub keeps getting in
    # Radarr and Sonarr are told qBittorrent's new sign-in in the same run.
    fields = {f["name"]: f.get("value") for f in media_servers.arr["radarr"]["downloadclient"][0]["fields"]}
    assert fields["password"] == "Shared-pw-1!"


def test_visuals_run_in_a_sealed_frame_with_their_libraries(app, client):
    page = client.get("/visuals/builtin/frame.html")
    assert page.status_code == 200 and "frame-ancestors 'self'" in page.headers["Content-Security-Policy"]
    html = page.get_data(as_text=True)
    for script in ("node_modules/d3/dist/d3.min.js", "visuals/weather.js", "visuals/live-sky.js", "daily.js", "frame.js"):
        assert '<script src="%s">' % script in html
    assert html.index("node_modules/d3") < html.index("visuals/cells.js") < html.index("frame.js")
    land = client.get("/visuals/builtin/node_modules/world-atlas/land-110m.json")
    assert land.status_code == 200 and land.headers["Access-Control-Allow-Origin"] == "*"
    weather = client.get("/visuals/builtin/visuals/weather.js").get_data(as_text=True)
    assert "HUB_PLACE" in weather and "Brisbane" not in weather  # no household's town in the shared files
    for bad in ("/visuals/builtin/../api/state", "/visuals/builtin/visuals/..%2f..%2fapi.py", "/visuals/builtin/api.py",
                "/visuals/0123456789ab/frame.html", "/visuals/builtin/node_modules/../../visuals.py"):
        assert client.get(bad).status_code == 404, bad
    # Signed in, the page is told the visuals' version and the home town (from .env) to hand to the frame.
    app.hub.config.home_place, app.hub.config.home_lat, app.hub.config.home_lon = "Home", "51.5", "-0.12"
    assert client.get("/api/state").get_json()["visuals"]["place"] is None  # nobody signed in yet
    login(client, "Nan")
    v = client.get("/api/state").get_json()["visuals"]
    assert v == {"version": "builtin", "place": {"name": "Home", "lat": 51.5, "lon": -0.12}}


def test_new_visuals_are_picked_up_from_github(app, client, monkeypatch):
    import io
    import zipfile
    from bookhub import settings, visuals
    sha = "abcdef0123456789abcdef0123456789abcdef01"
    files = {"visuals/sea.js": "VISUALS.push({id:'sea'})", "visuals/notes.txt": "x", "lib/extra.js": "var X=1",
             "daily.js": "function visualForDay(v){return v[0]}", "index.html": "<html>", "node_modules/big.js": "x",
             "visuals/../../escape.js": "bad"}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, text in files.items():
            z.writestr("repo-%s/%s" % (sha, name), text)
    asked = []

    class Answer:
        def __init__(self, status, body=None, raw=b""):
            self.status_code, self._body = status, body
            self.raw = type("Raw", (), {"read": lambda _self, n, decode_content=False: raw[:n]})()

        def json(self):
            return self._body

    def fake_get(url, headers=None, timeout=None, stream=False):
        asked.append(url)
        if url.endswith("/commits/HEAD"):
            return Answer(200, {"sha": sha}) if "family/visuals" in url else Answer(404, {})
        if "codeload.github.com" in url:
            return Answer(200, raw=buf.getvalue())
        return Answer(404)
    monkeypatch.setattr(visuals.requests, "get", fake_get)
    login(client, "You")
    assert client.get("/api/admin/visuals").get_json()["source"] == "builtin"
    with app.app_context():
        settings.save(app.hub, {"visuals_repo": "family/visuals", "home_lat": "-33.87", "home_lon": "151.21"})
        with pytest.raises(ValueError):
            settings.save(app.hub, {"home_lat": "95"})
    st = client.post("/api/admin/visuals/check").get_json()
    assert (st["source"], st["version"], st["count"], st["error"]) == ("github", sha[:12], 1, "")
    kept = sorted(p.relative_to(Path(app.hub.config.data_dir) / "visuals" / sha[:12]).as_posix()
                  for p in (Path(app.hub.config.data_dir) / "visuals" / sha[:12]).rglob("*") if p.is_file())
    assert kept == ["daily.js", "lib/extra.js", "visuals/sea.js"]  # nothing else from the repository
    html = client.get("/visuals/%s/frame.html" % sha[:12]).get_data(as_text=True)
    assert html.index('src="lib/extra.js"') < html.index('src="visuals/sea.js"')
    assert client.get("/api/state").get_json()["visuals"]["version"] == sha[:12]
    # Checked again within the hour: GitHub is not asked; pressing the button asks, and nothing changed.
    n = len(asked)
    with app.app_context():
        visuals.sync(app.hub)
    assert len(asked) == n
    client.post("/api/admin/visuals/check")
    assert not any("codeload" in u for u in asked[n:])
    # A repository that is not there keeps the visuals already in use, and says why.
    with app.app_context():
        settings.save(app.hub, {"visuals_repo": "family/gone"})
    st = client.post("/api/admin/visuals/check").get_json()
    assert st["version"] == sha[:12] and "no public repository family/gone" in st["error"]
    # No repository at all: back to the built-in visuals.
    with app.app_context():
        settings.save(app.hub, {"visuals_repo": ""})
    assert client.post("/api/admin/visuals/check").get_json()["source"] == "builtin"
    login(client, "Nan")
    assert client.post("/api/admin/visuals/check").status_code == 403
