"""Tags written back into the library: what each entry should carry, writing it through a stand-in Calibre-Web,
taking off only what Family Hub put there, Undo, and the switch."""
import json
import sqlite3

import pytest

from test_app import app, client, login  # noqa: F401
from bookhub import db


@pytest.fixture()
def calibre_web(app, monkeypatch):  # noqa: F811
    """A stand-in for Calibre-Web's edit: replaces an entry's whole tag list in the library, like the real one."""
    hub = app.hub
    for name, value in (("cwa_api_url", "http://cwa"), ("cwa_user", "admin"), ("cwa_pass", "pw")):
        monkeypatch.setattr(hub.config, name, value, raising=False)
    backups, calls = [], []
    monkeypatch.setattr(hub.duplicates, "backup", lambda: backups.append(1))

    def set_field(book_id, field, value):
        assert field == "tags"
        calls.append((book_id, value))
        con = sqlite3.connect(str(hub.library.metadata_db))
        con.execute("DELETE FROM books_tags_link WHERE book=?", (book_id,))
        for name in [part.strip() for part in value.split(",") if part.strip()]:
            row = con.execute("SELECT id FROM tags WHERE name=?", (name,)).fetchone()
            tag_id = row[0] if row else con.execute("INSERT INTO tags(name) VALUES(?)", (name,)).lastrowid
            con.execute("INSERT INTO books_tags_link(book, tag) VALUES(?,?)", (book_id, tag_id))
        con.commit()
        con.close()

    monkeypatch.setattr(hub.manager, "set_field", set_field)
    return hub, calls, backups


def _book(hub, title):
    return next(book for book in hub.library.all_books() if book["title"] == title)


def _give(hub, book, community=(), family=(), raw=None):
    con = hub.con()
    for tag in community:
        con.execute("INSERT OR IGNORE INTO community_tags(book_key, tag, source) VALUES(?,?,?)", (book["key"], tag, "openlibrary"))
    for tag in family:
        hub.put_family_tag(book["key"], tag)
    con.commit()
    if raw is not None:
        lib = sqlite3.connect(str(hub.library.metadata_db))
        lib.execute("DELETE FROM books_tags_link WHERE book=?", (book["id"],))
        for name in raw:
            tag_id = lib.execute("INSERT INTO tags(name) VALUES(?)", (name,)).lastrowid
            lib.execute("INSERT INTO books_tags_link(book, tag) VALUES(?,?)", (book["id"], tag_id))
        lib.commit()
        lib.close()
    hub.library.refresh(force=True)
    return _book(hub, book["title"])


def test_an_entry_gets_its_tidied_tags_the_best_community_tags_and_family_tags(calibre_web):
    hub, calls, backups = calibre_web
    book = _give(hub, _book(hub, "Dracula"), community=["Vampires", "Gothic fiction", "Accessible book"], family=["Halloween read"],
                 raw=["Horror", "ISBN: 9780141439846", "http://example.com"])
    plan = next(plan for entry, plan in hub.tagsync.plans() if entry["id"] == book["id"])
    assert {"Vampires", "Gothic fiction", "Halloween read"} <= set(plan["add"]) and "Accessible book" not in plan["add"]
    assert plan["remove"] == ["ISBN: 9780141439846", "http://example.com"]  # clutter goes; "Accessible book" never arrives
    assert hub.tagsync.run() >= 1 and backups == [1]
    assert {"Horror", "Vampires", "Gothic fiction", "Halloween read"} <= set(_book(hub, "Dracula")["raw_tags"])
    assert len(set(plan["add"]) - {"Halloween read"}) <= 6  # only the best few community tags
    assert not any(entry["id"] == book["id"] for entry, _ in hub.tagsync.plans())  # done; not written again
    shown = hub.tag_index()[book["key"]]
    assert "Vampires" not in shown["epub"] and "Vampires" in shown["community"]  # shown once, where it came from


def test_only_tags_family_hub_wrote_are_taken_off_again(calibre_web):
    hub, calls, backups = calibre_web
    book = _give(hub, _book(hub, "Dracula"), family=["Halloween read"], raw=["Horror", "Typed in Calibre"])
    hub.tagsync.run(limit=100)
    hub.remove_tag("family", book["key"], "Halloween read", None)
    plan = next(plan for entry, plan in hub.tagsync.plans() if entry["id"] == book["id"])
    assert plan["remove"] == ["Halloween read"] and plan["add"] == []  # "Typed in Calibre" stays: somebody meant it


def test_personal_tags_and_hidden_tags(calibre_web):
    hub, calls, backups = calibre_web
    book = _book(hub, "Dracula")
    profile = hub.profiles()[0]
    hub.add_tag("mine", book["key"], "My secret shelf", profile["id"])
    hub.set_tag_rule("Horror", "")  # hidden by an admin
    book = _give(hub, book, raw=["Horror", "Classics"])
    plan = next(plan for entry, plan in hub.tagsync.plans() if entry["id"] == book["id"])
    assert "My secret shelf" not in plan["to"] and "Horror" in plan["remove"] and "Classics" in plan["to"]


def test_undo_puts_the_tags_back_and_leaves_the_book_alone(calibre_web):
    hub, calls, backups = calibre_web
    book = _give(hub, _book(hub, "Dracula"), community=["Vampires"], raw=["Horror"])
    hub.tagsync.run(limit=100)
    change = next(change for change in hub.tagsync.summary()["changes"] if change["book_id"] == book["id"])
    assert "Vampires" in change["added"]
    hub.tagsync.undo(change["id"])
    assert _book(hub, "Dracula")["raw_tags"] == ["Horror"]
    assert not any(entry["id"] == book["id"] for entry, _ in hub.tagsync.plans())  # held after an Undo
    hub.tagsync.release(book["id"])
    assert any(entry["id"] == book["id"] for entry, _ in hub.tagsync.plans())


def test_nothing_is_written_with_the_switch_off_or_without_a_sign_in(app, calibre_web, monkeypatch):  # noqa: F811
    hub, calls, backups = calibre_web
    _give(hub, _book(hub, "Dracula"), community=["Vampires"])
    hub.tagsync.set_auto(False)
    assert hub.tagsync.run() == 0 and calls == []
    hub.tagsync.set_auto(True)
    monkeypatch.setattr(hub.config, "cwa_pass", "", raising=False)
    assert hub.tagsync.run() == 0 and calls == [] and backups == []


def test_when_calibre_web_ignores_a_whole_batch_writing_stops(calibre_web, monkeypatch):
    hub, calls, backups = calibre_web
    _give(hub, _book(hub, "Dracula"), community=["Vampires"])
    monkeypatch.setattr(hub.manager, "set_field", lambda *args: None)  # answers, changes nothing
    assert hub.tagsync.run(limit=100) == 0
    summary = hub.tagsync.summary()
    assert summary["auto"] is False and "stopped by itself" in summary["blocked"]
    assert summary["waiting"] > 0  # the books are not blamed; they wait for the switch
    hub.tagsync.set_auto(True)
    assert "stopped" not in (hub.tagsync.summary()["blocked"] or "")


def test_a_change_calibre_web_ignored_is_not_tried_every_minute(calibre_web, monkeypatch):
    hub, calls, backups = calibre_web
    dracula = _give(hub, _book(hub, "Dracula"), community=["Vampires"])
    real = hub.manager.set_field
    # Calibre-Web takes every change but Dracula's
    monkeypatch.setattr(hub.manager, "set_field", lambda book_id, field, value: None if book_id == dracula["id"] else real(book_id, field, value))
    hub.tagsync.run(limit=100)
    assert hub.tagsync.auto and not any(entry["id"] == dracula["id"] for entry, _ in hub.tagsync.plans())
    row = hub.con().execute("SELECT note FROM tag_sync_log WHERE book_id=?", (dracula["id"],)).fetchone()
    assert "did not make the change" in row["note"]


def test_the_panel_is_for_admins(app, client):  # noqa: F811
    login(client, "Nan")
    assert client.get("/api/admin/tagsync").status_code == 403
    client.post("/api/logout")
    login(client, "You")
    data = client.get("/api/admin/tagsync").get_json()
    assert data["auto"] is True and data["blocked"] and isinstance(data["next"], list)
    assert client.post("/api/admin/tagsync", json={"auto": 1}).status_code == 400
    assert client.post("/api/admin/tagsync", json={"auto": False}).get_json()["auto"] is False
    assert db.kv_get(app.hub.con(), "tagsync:auto") == "0"
    assert client.post("/api/admin/tagsync/undo/12345").status_code == 404
    assert json.loads(json.dumps(data))  # plain JSON all the way down
