"""The duplicates queue: one set of copies at a time, side by side, keep one and put it right, or tell them apart."""
import sqlite3
from pathlib import Path

from test_app import _twin, app, client, login  # noqa: F401
from test_review_queue_area import FakeManager
from bookhub import db


def _ready(app, client):
    """Sign in as an admin with the library manager set up (the test one) and a needs-attention folder."""
    hub = app.hub
    login(client, "You")
    client.post("/api/admin/connections", json={"values": {"cwa_api_url": "http://manager:8083", "cwa_user": "admin", "cwa_pass": "pw-1"}})
    attention = Path(hub.config.attention_dir)
    attention.mkdir(parents=True, exist_ok=True)
    fake = FakeManager(hub.config.library_dir)
    hub.manager = fake
    return fake, attention


def _count_backups(app, monkeypatch):
    calls = []
    real = app.hub.duplicates.backup
    monkeypatch.setattr(app.hub.duplicates, "backup", lambda: calls.append(1) or real())
    return calls


def _series(library, book_id, name, number):
    con = sqlite3.connect(Path(library) / "metadata.db")
    series = con.execute("INSERT INTO series(name, sort) VALUES(?,?)", (name, name)).lastrowid
    con.execute("INSERT INTO books_series_link(book, series) VALUES(?,?)", (book_id, series))
    con.execute("UPDATE books SET series_index=? WHERE id=?", (number, book_id))
    con.commit()
    con.close()


def test_the_queue_shows_copies_side_by_side_with_what_keeping_each_would_do(app, client):
    hub, library = app.hub, app.hub.config.library_dir
    same = _twin(library, "Dracula", same_bytes=True)
    far = _twin(library, "Moby-Dick", size=300)
    _series(library, far[1], "Whaling Tales", 2)
    hub.library.refresh(force=True)
    login(client, "You")
    data = client.get("/api/admin/duplicates/queue").get_json()
    assert "sign-in is not set up" in data["blocked"]
    # the ones only a person can decide come first
    assert [entry["title"] for entry in data["sets"]] == ["Moby-Dick", "Dracula"] and data["set"]["title"] == "Moby-Dick"
    copies = {copy["id"]: copy for copy in data["set"]["copies"]}
    assert set(copies) == set(far) and copies[far[1]]["series"] == "Whaling Tales" and copies[far[0]]["series"] == ""
    assert copies[far[0]]["files"][0]["format"] == "EPUB" and copies[far[0]]["pages"]
    assert copies[far[0]]["outcome"]["words"] == "the other copy will be set aside in needs-attention"
    # the series comes from the copy that has one, whichever is kept
    start = data["set"]["start"]
    assert start["series"] == "Whaling Tales" and start["series_index"] == 2 and start["from"]["series"] == far[1]

    one = client.get("/api/admin/duplicates/queue?key=" + data["sets"][1]["key"]).get_json()["set"]
    assert one["title"] == "Dracula" and one["cls"] == "identical"
    assert all(copy["outcome"]["words"] == "the other copy is identical and will be deleted" for copy in one["copies"])
    assert client.get("/api/admin/duplicates/cover/%d" % same[0]).status_code in (200, 404)
    client.post("/api/logout")
    login(client, "Nan")
    assert client.get("/api/admin/duplicates/queue").status_code == 403
    assert client.post("/api/admin/duplicates/queue", json={"action": "apart"}).status_code == 403


def test_keeping_an_identical_copy_deletes_the_other_and_writes_the_new_details(app, client, monkeypatch):
    hub, library = app.hub, app.hub.config.library_dir
    same = _twin(library, "Dracula", same_bytes=True)
    hub.library.refresh(force=True)
    fake, attention = _ready(app, client)
    backups = _count_backups(app, monkeypatch)
    old_key = hub.library.get(same[0])["key"]
    hub.add_tag("family", old_key, "Gothic", None)
    item = client.get("/api/admin/duplicates/queue").get_json()["set"]
    fields = dict(item["start"], title="Dracula (Annotated)", series="Classics", series_index="3", tags=["Horror", "Vampires"])
    answer = client.post("/api/admin/duplicates/queue", json={"key": item["key"], "action": "keep", "keep": same[1], "fields": fields})
    assert answer.status_code == 200, answer.get_json()
    assert answer.get_json()["cls"] == "identical" and not answer.get_json()["problem"]
    assert len(backups) == 1  # one copy of the library's database for the lot
    assert hub.library.get(same[0]) is None and not (attention / "_duplicates").exists()
    kept = hub.library.get(same[1])
    assert kept["title"] == "Dracula (Annotated)" and kept["own_series"] == "Classics" and kept["series_index"] == 3
    assert sorted(kept["raw_tags"]) == ["Horror", "Vampires"]  # the whole list, so the old ones came off
    # Family Hub's own records followed the book to its new name
    tags = [row["tag"] for row in hub.con().execute("SELECT tag FROM family_tags WHERE book_key=?", (kept["key"],))]
    assert "Gothic" in tags and kept["key"] != old_key
    assert client.get("/api/admin/duplicates/queue").get_json()["set"] is None


def test_keeping_a_different_file_sets_the_other_aside(app, client):
    hub, library = app.hub, app.hub.config.library_dir
    far = _twin(library, "Moby-Dick", size=300)
    hub.library.refresh(force=True)
    fake, attention = _ready(app, client)
    item = client.get("/api/admin/duplicates/queue").get_json()["set"]
    answer = client.post("/api/admin/duplicates/queue", json={"key": item["key"], "action": "keep", "keep": far[1], "fields": item["start"]})
    assert answer.status_code == 200 and answer.get_json()["cls"] == "review"
    assert hub.library.get(far[0]) is None and len(list((attention / "_duplicates").rglob("*.epub"))) == 1
    assert not [call for call in fake.calls if call[0] == "set" and call[2] == "title"]  # nothing to change, nothing sent
    # a bad answer changes nothing
    assert client.post("/api/admin/duplicates/queue", json={"key": item["key"], "action": "keep", "keep": far[1], "fields": {}}).status_code == 400


def test_not_duplicates_gives_each_its_own_name_and_is_remembered(app, client, monkeypatch):
    hub, library = app.hub, app.hub.config.library_dir
    pair = _twin(library, "Emma", size=495)
    hub.library.refresh(force=True)
    fake, _ = _ready(app, client)
    backups = _count_backups(app, monkeypatch)
    old_key = hub.library.get(pair[0])["key"]
    hub.add_tag("family", old_key, "Regency", None)
    item = client.get("/api/admin/duplicates/queue").get_json()["set"]
    names = {str(pair[0]): {"title": "Emma (Volume 1)"}, str(pair[1]): {"title": "Emma (Volume 2)", "authors": "Jane Austen"}}
    answer = client.post("/api/admin/duplicates/queue", json={"key": item["key"], "action": "apart", "names": names})
    assert answer.status_code == 200 and answer.get_json()["renamed"] == 2 and len(backups) == 1
    first, second = hub.library.get(pair[0]), hub.library.get(pair[1])
    assert (first["title"], second["title"]) == ("Emma (Volume 1)", "Emma (Volume 2)") and first["key"] != second["key"]
    for book in (first, second):  # both had the tag before, so both keep it
        assert hub.con().execute("SELECT 1 FROM family_tags WHERE book_key=? AND tag='Regency'", (book["key"],)).fetchone()
    assert sorted(db.kv_get(hub.con(), "duplicates:apart").strip("[]").split(", ")) == sorted(str(i) for i in pair)
    # Named the same again later, they are still not suggested
    for book_id in pair:
        fake.set_field(book_id, "title", "Emma")
    hub.library.refresh(force=True)
    assert client.get("/api/admin/duplicates/queue").get_json()["sets"] == []
    assert client.get("/api/admin/duplicates").get_json()["sets"] == []


def test_not_duplicates_without_new_names_needs_no_library_manager(app, client):
    hub, library = app.hub, app.hub.config.library_dir
    pair = _twin(library, "Persuasion", size=277)
    hub.library.refresh(force=True)
    login(client, "You")
    item = client.get("/api/admin/duplicates/queue").get_json()["set"]
    answer = client.post("/api/admin/duplicates/queue", json={"key": item["key"], "action": "apart", "names": {}})
    assert answer.status_code == 200 and answer.get_json()["renamed"] == 0
    assert hub.copy_choices()[item["key"]] == "apart" and len(hub.library.all_books()) >= 2
    assert hub.library.get(pair[0]) and hub.library.get(pair[1])
    assert client.get("/api/admin/duplicates/queue").get_json()["set"] is None


def test_the_queue_waits_while_a_batch_is_running(app, client):
    hub, library = app.hub, app.hub.config.library_dir
    _twin(library, "Dracula", same_bytes=True)
    hub.library.refresh(force=True)
    _ready(app, client)
    item = client.get("/api/admin/duplicates/queue").get_json()["set"]
    hub.duplicates._lock.acquire()
    try:
        answer = client.post("/api/admin/duplicates/queue", json={"key": item["key"], "action": "keep", "keep": item["keep"], "fields": item["start"]})
        assert answer.status_code == 409
    finally:
        hub.duplicates._lock.release()
    assert len(hub.library.sets()) == 1


def test_different_books_keep_every_detail_written_under_them_and_the_strip_shows_each_set(app, client, monkeypatch):
    hub, library = app.hub, app.hub.config.library_dir
    pair = _twin(library, "Emma", size=495)
    hub.library.refresh(force=True)
    _ready(app, client)
    _count_backups(app, monkeypatch)
    data = client.get("/api/admin/duplicates/queue").get_json()
    strip = next(entry for entry in data["sets"] if entry["key"] == data["set"]["key"])
    assert strip["copies"] == 2 and strip["cover"] in pair  # the carousel's strip has a cover to show
    names = {str(pair[0]): {"title": "Emma (Volume 1)", "series": "Emma", "series_index": "1", "tags": "Regency, Classic"},
             str(pair[1]): {"title": "Emma (Volume 2)", "series": "Emma", "series_index": "2"}}
    answer = client.post("/api/admin/duplicates/queue", json={"key": data["set"]["key"], "action": "apart", "names": names})
    assert answer.status_code == 200 and answer.get_json()["renamed"] == 2
    first, second = hub.library.get(pair[0]), hub.library.get(pair[1])
    assert (first["own_series"], first["series_index"], second["series_index"]) == ("Emma", 1, 2)
    assert sorted(first["raw_tags"]) == ["Classic", "Regency"]
