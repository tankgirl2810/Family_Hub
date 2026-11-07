"""Merging tags: one spelling into another, across family, book file and community tags."""
import json

from test_app import app, book_id, client, login  # noqa: F401
from bookhub import create_app, tagmerge
from bookhub.config import Config


def _key(app, title):
    return next(book["key"] for book in app.hub.library.books() if book["title"] == title)


def _family(app, title):
    rows = app.hub.con().execute("SELECT tag FROM family_tags WHERE book_key=? ORDER BY tag", (_key(app, title),))
    return [row["tag"] for row in rows]


def _tag(client, title, tag, kind="family"):
    assert client.post("/api/books/%d/tags" % book_id(client, title), json={"kind": kind, "tag": tag}).status_code == 200


def test_the_merge_key_ignores_case_plurals_and_punctuation():
    key = tagmerge.merge_key
    assert key("Boys' love") == key("Boy love") == key("boy-love")
    assert key("Sci-fi") == key("Sci fi") == key("SCI FI")
    assert key("Sword & sorcery") == key("Swords and Sorcery")
    assert key("Zombies") == key("Zombie") and key("Mysteries") == key("Mystery") and key("Witches") == key("Witch")
    assert key("Horses") == key("Horse") and key("Glasses") == key("Glass")
    assert key("Yaoi") != key("Boy love")  # synonyms are for a person to merge
    assert key("Pathfinder 2e") != key("Pathfinder 1e")


def test_merging_family_tags_rewrites_them_and_catches_later_ones(app, client):
    login(client, "You")
    _tag(client, "Dracula", "Yaoi")
    _tag(client, "Emma", "Yaoi")
    _tag(client, "Emma", "Boy love")
    _tag(client, "Emma", "Yaoi", kind="mine")
    app.hub.set_tag_parent("Yaoi", "Romance themes")
    app.hub.set_tag_parent("Yaoi in space", "Yaoi")

    preview = client.post("/api/admin/tags/merge/preview", json={"tags": ["Yaoi"], "keep": "Boy love"}).get_json()
    assert (preview["books"], preview["already"], preview["family"]) == (2, 1, 2)
    # a preview changes nothing
    assert _family(app, "Dracula") == ["Yaoi"]

    reply = client.post("/api/admin/tags/merge", json={"tags": ["Yaoi"], "keep": "Boy love"}).get_json()
    assert reply["family"] == 2
    assert _family(app, "Dracula") == ["Boy love"]
    assert _family(app, "Emma") == ["Boy love"]  # had both: one is left
    # who added it stays the same
    added_by = app.hub.con().execute("SELECT added_by FROM family_tags WHERE book_key=?", (_key(app, "Dracula"),)).fetchone()[0]
    assert added_by is not None
    # personal tags are never touched
    mine = app.hub.con().execute("SELECT tag FROM personal_tags WHERE book_key=?", (_key(app, "Emma"),)).fetchall()
    assert "Yaoi" in [row[0] for row in mine]
    # the nesting moves with it
    parents = app.hub.tag_parents()
    assert parents.get("Boy love") == "Romance themes" and parents.get("Yaoi in space") == "Boy love" and "Yaoi" not in parents

    # typed later, it goes on as the kept name
    _tag(client, "Persuasion", "yaoi")
    assert _family(app, "Persuasion") == ["Boy love"]
    merges = client.get("/api/admin/tags/merges").get_json()["merges"]
    assert [(m["tag"], m["becomes"], m["restorable"]) for m in merges] == [("Yaoi", "Boy love", 2)]


def test_the_sorter_and_book_file_tags_follow_merges(app, client):
    login(client, "You")
    assert client.post("/api/admin/tags/merge", json={"tags": ["Gothic"], "keep": "Gothic fiction"}).status_code == 200
    # a book file tag gets the ordinary rename rule: shown under the new name, the file untouched
    dracula = app.hub.tag_index()[_key(app, "Dracula")]
    assert "Gothic fiction" in dracula["epub"] and "Gothic" not in dracula["epub"]
    assert app.hub.tag_rules()["gothic"] == "Gothic fiction"

    client.post("/api/admin/tags/merge", json={"tags": ["PF2"], "keep": "Pathfinder 2e"})
    key = _key(app, "Moby-Dick")
    verdict = {"kind": "Tabletop", "role": "Rulebook", "tags": ["Pathfinder", "PF2"], "parents": {"PF2": "Pathfinder"}}
    app.hub.apply_verdict(key, verdict)
    assert _family(app, "Moby-Dick") == ["Pathfinder", "Pathfinder 2e"]
    assert app.hub.tag_parents()["Pathfinder 2e"] == "Pathfinder"
    # the same verdict again doesn't take the merged tag off as stale
    app.hub.apply_verdict(key, verdict)
    assert _family(app, "Moby-Dick") == ["Pathfinder", "Pathfinder 2e"]


def test_suggestions_offer_near_duplicates_and_remember_a_no(app, client):
    login(client, "You")
    _tag(client, "Dracula", "Science-Fiction")  # "The Time Machine" has the book file tag "Science fiction"
    _tag(client, "Emma", "Science-Fiction")
    found = client.get("/api/admin/tags/merges").get_json()["suggestions"]
    sci = next(s for s in found if {n["tag"] for n in s["names"]} == {"Science-Fiction", "Science fiction"})
    assert sci["keep"] == "Science-Fiction"  # on more books
    names = [n["tag"] for n in sci["names"]]
    assert client.post("/api/admin/tags/merge/dismiss", json={"tags": names}).status_code == 200
    found = client.get("/api/admin/tags/merges").get_json()["suggestions"]
    assert not any({n["tag"] for n in s["names"]} == {"Science-Fiction", "Science fiction"} for s in found)
    tags = client.get("/api/admin/tags/merges").get_json()["tags"]
    assert next(t for t in tags if t["tag"] == "Science-Fiction")["family"] == 2


def test_a_case_only_merge(app, client):
    login(client, "You")
    _tag(client, "Dracula", "spooky")
    client.post("/api/admin/tags/merge", json={"tags": ["spooky"], "keep": "Spooky"})
    assert _family(app, "Dracula") == ["Spooky"]
    _tag(client, "Emma", "SPOOKY")
    assert _family(app, "Emma") == ["Spooky"]
    client.post("/api/admin/tags/merge/undo", json={"tag": "spooky"})
    assert _family(app, "Dracula") == ["spooky"]


def test_undo_puts_the_old_name_back(app, client):
    login(client, "You")
    _tag(client, "Dracula", "Yaoi")
    _tag(client, "Emma", "Yaoi")
    _tag(client, "Emma", "Boy love")
    client.post("/api/admin/tags/merge", json={"tags": ["Yaoi"], "keep": "Boy love"})
    _tag(client, "Persuasion", "Yaoi")  # after the merge: stays Boy love after the undo too
    reply = client.post("/api/admin/tags/merge/undo", json={"tag": "Yaoi"}).get_json()
    assert reply["restored"] == 2
    assert _family(app, "Dracula") == ["Yaoi"]
    assert _family(app, "Emma") == ["Boy love", "Yaoi"]
    assert _family(app, "Persuasion") == ["Boy love"]
    assert "yaoi" not in app.hub.tag_rules()
    _tag(client, "Little Women", "Yaoi")
    assert "Yaoi" in _family(app, "Little Women")
    assert client.post("/api/admin/tags/merge/undo", json={"tag": "Yaoi"}).status_code == 404


def test_a_second_merge_folds_the_first_into_it(app, client):
    login(client, "You")
    _tag(client, "Dracula", "Yaoi")
    client.post("/api/admin/tags/merge", json={"tags": ["Yaoi"], "keep": "Boy love"})
    client.post("/api/admin/tags/merge", json={"tags": ["Boy love"], "keep": "BL"})
    assert _family(app, "Dracula") == ["BL"]
    assert tagmerge.rules(app.hub.con()) == {"yaoi": "BL", "boy love": "BL"}
    # and turning one round makes the kept name a name again
    client.post("/api/admin/tags/merge", json={"tags": ["BL"], "keep": "Boy love"})
    assert tagmerge.rules(app.hub.con()) == {"yaoi": "Boy love", "bl": "Boy love"}
    assert _family(app, "Dracula") == ["Boy love"]


def test_merges_travel_in_the_book_data_file(app, client, tmp_path, monkeypatch):
    login(client, "You")
    _tag(client, "Dracula", "Yaoi")
    client.post("/api/admin/tags/merge", json={"tags": ["Yaoi"], "keep": "Boy love"})
    client.post("/api/admin/tags/merge/dismiss", json={"tags": ["Sci fi", "Sci-fi"]})
    data = json.loads(client.get("/api/admin/export").data)
    assert data["tag_merges"][0]["tag"] == "Yaoi" and data["tag_merges"][0]["becomes"] == "Boy love"
    assert data["tag_merge_dismissed"] == ["sci fi|sci-fi"]

    # an old file still saying "Yaoi" on a book comes in as "Boy love" where the merge is known
    dracula = next(book for book in data["books"].values() if book["title"] == "Dracula")
    dracula["family_tags"] = ["Yaoi"]
    monkeypatch.setenv("BOOKHUB_DATA_DIR", str(tmp_path / "fresh"))
    fresh = create_app(Config())
    c = fresh.test_client()
    c.post("/api/setup", json={"name": "You", "passcode": "1234"})
    assert c.post("/api/admin/import", json=data).status_code == 200
    again = json.loads(c.get("/api/admin/export").data)
    assert next(book for book in again["books"].values() if book["title"] == "Dracula")["family_tags"] == ["Boy love"]
    assert [m["tag"] for m in again["tag_merges"]] == ["Yaoi"] and again["tag_merge_dismissed"] == ["sci fi|sci-fi"]
    # the undo history came along, so the merge can still be undone properly
    assert c.post("/api/admin/tags/merge/undo", json={"tag": "Yaoi"}).get_json()["restored"] == 1


def test_only_admins_merge(app, client):
    login(client, "Nan")
    assert client.get("/api/admin/tags/merges").status_code == 403
    assert client.post("/api/admin/tags/merge", json={"tags": ["Gothic"], "keep": "Goth"}).status_code == 403
    login(client, "You")
    assert client.post("/api/admin/tags/merge", json={"tags": [], "keep": "Goth"}).status_code == 400
    assert client.post("/api/admin/tags/merge", json={"tags": ["Goth"], "keep": "Goth"}).status_code == 400
    assert client.post("/api/admin/tags/merge", json={"tags": ["Gothic"]}).status_code == 400
