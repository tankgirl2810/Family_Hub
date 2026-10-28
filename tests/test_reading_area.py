"""Reading apps: the OPDS catalogue, KOReader sync and Komga."""
import base64
import hashlib
import xml.etree.ElementTree as ET

from test_app import FakeKomga, _komga_book, app, client, login, series_server  # noqa: F401

ATOM = "{http://www.w3.org/2005/Atom}"
YOU = {"Authorization": "Basic " + base64.b64encode(b"You:demo").decode()}


def test_a_komga_book_with_no_pages_is_left_alone(app, client, series_server, monkeypatch):
    from bookhub import komga
    # chapter 1 is read on the series server, but Komga couldn't open its file (0 pages, media status ERROR).
    # Komga refuses progress on such a book, and that refusal used to stop everyone's chapter sync.
    fake = FakeKomga(series=[{"id": "s7", "name": "Tower Climber", "url": "/data/mangas/Reading Site (EN)/Tower Climber"}],
                     books=[_komga_book("c1", "/data/x/Chapter 1.cbz", name="Chapter 1", pages=0),
                            _komga_book("c2", "/data/x/Chapter 2.cbz", name="Chapter 2", progress={"completed": True, "page": 10})])
    monkeypatch.setattr(komga, "_call", fake)
    with app.app_context():
        you = app.hub.con().execute("SELECT * FROM profiles WHERE name='You'").fetchone()
        assert komga.sync_chapter_history(app.hub, {"id": "lib"}, you, ("you@x", "pw")) == (0, 1)
    assert fake.written == {}


def test_a_graphic_novel_with_no_pages_is_not_pushed(app, monkeypatch):
    from pathlib import Path
    from bookhub import komga
    with app.app_context():
        hub = app.hub
        you = hub.con().execute("SELECT * FROM profiles WHERE name='You'").fetchone()
        read = {row["book_key"] for row in hub.con().execute("SELECT book_key FROM reading WHERE profile_id=?", (you["id"],))}
        book = next(b for b in hub.library.books() if b["key"] not in read)
        hub.save_reading(you["id"], book["key"], status="finished", source="device")
        monkeypatch.setattr(komga, "placed", lambda hub: {"Moon/Moon - Volume 001.cbz": (Path("x"), [book["key"]])})
        fake = FakeKomga(books=[_komga_book("v1", "/comics/Moon/Moon - Volume 001.cbz", pages=0)])
        monkeypatch.setattr(komga, "_call", fake)
        assert komga.sync_graphic_history(hub, {"id": "lib"}, you, ("you@x", "pw")) == (0, 0)
    assert fake.written == {}


def test_only_admins_are_told_to_edit_komga_accounts(app, client, monkeypatch):
    for name, value in (("komga_url", "http://komga:25600"), ("komga_user", "bookhub@x"), ("komga_pass", "pw"),
                        ("komga_accounts", ""), ("comics_app", "paperback")):
        monkeypatch.setattr(app.hub.config, name, value)
    login(client, "You")
    message = client.get("/api/me/comics-app").get_json()["message"]
    assert "no Komga login" in message and "KOMGA_ACCOUNTS" in message
    client.post("/api/logout")
    login(client, "Nan")
    message = client.get("/api/me/comics-app").get_json()["message"]
    assert "no Komga login" in message and "KOMGA_ACCOUNTS" not in message and "admin" in message


def test_two_library_books_sharing_a_key_get_their_own_entry_ids(app, client, monkeypatch):
    # the library can hold two different files that come out as the same book (same title and author, no ISBN).
    # Atom ids must be unique in a feed, or reading apps fold the two into one
    real_books = app.hub.library.books
    with app.app_context():
        emma = next(b for b in real_books() if b["title"] == "Emma")
    monkeypatch.setattr(app.hub.library, "books", lambda: real_books() + [dict(emma, id=9999)])
    feed = ET.fromstring(client.get("/opds/search?q=emma", headers=YOU).data)
    ids = [entry.find(ATOM + "id").text for entry in feed.findall(ATOM + "entry")]
    assert len(ids) == 2 and len(set(ids)) == 2
    # books with a key of their own keep the id they always had, so apps don't see them as new
    feed = ET.fromstring(client.get("/opds/search?q=persuasion", headers=YOU).data)
    with app.app_context():
        persuasion = next(b for b in real_books() if b["title"] == "Persuasion")
    assert [entry.find(ATOM + "id").text for entry in feed.findall(ATOM + "entry")] == ["urn:bookhub:book:" + persuasion["key"]]


def test_my_books_count_leaves_out_graphic_novels_read_elsewhere(app, client, monkeypatch):
    from bookhub import kinds
    with app.app_context():
        you = app.hub.con().execute("SELECT id FROM profiles WHERE name='You'").fetchone()["id"]
        emma, persuasion = [next(b for b in app.hub.library.books() if b["title"] == t) for t in ("Emma", "Persuasion")]
        for book in (emma, persuasion):
            app.hub.devices.set_shelf(you, book, True)
        shelved = len(app.hub.devices.shelf(you))
    real_index = app.hub.kind_index
    monkeypatch.setattr(app.hub, "kind_index", lambda: dict(real_index(), **{emma["key"]: kinds.GRAPHIC}))
    monkeypatch.setattr(app.hub.config, "comics_app", "paperback")
    root = client.get("/opds", headers=YOU).data.decode()
    assert "%d chosen for You" % (shelved - 1) in root


def test_kosync_shrugs_off_a_body_that_is_not_an_object(client):
    headers = {"x-auth-user": "You", "x-auth-key": hashlib.md5(b"demo").hexdigest()}
    r = client.put("/kosync/syncs/progress", headers=headers, json=["not", "a", "dict"])
    assert r.status_code == 403  # "document not provided", not a crash
