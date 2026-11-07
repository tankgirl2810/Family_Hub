"""Game books in the library: the Tabletop view, the grouped feed, nested tags, hiding per profile, and Komga."""
import json
import sqlite3
import types
import zipfile

from test_app import app, client, login  # noqa: F401
from test_tabletop_area import _retitle
from bookhub import db, kinds, komga, search, tabletop


def _game_book(app, title, role, tags, verdict=None, kind=kinds.TABLETOP, fmt="PDF"):
    """A library entry made into a game book: its kind and role, family tags as the sorter puts them, and a verdict."""
    hub = app.hub
    new = _retitle(app, "Dracula", title, fmt=fmt)
    hub.library.refresh(force=True)  # _retitle stamps the same time on the library each go
    key = hub.library.get(new)["key"]
    hub.set_kind(key, kind, source="auto", role=role)
    for tag in tags:
        hub.con().execute("INSERT OR IGNORE INTO family_tags(book_key, tag, added_by, created) VALUES(?,?,NULL,?)", (key, tag, db.now()))
    hub.con().execute("INSERT INTO sort_verdicts(book_key, verdict, origin, updated) VALUES(?,?,'',?)",
                      (key, json.dumps(dict({"series": "", "series_index": None, "product_line": [], "language": ""}, **(verdict or {}))), db.now()))
    hub.con().commit()
    return key


def _library(app):
    """A small Pathfinder collection: core books, a general player's guide, an adventure path with its guide and
    maps, a pile of GM aids, a novel set in the world and a book in Russian."""
    hub = app.hub
    pf2 = ["Pathfinder", "Pathfinder 2e"]
    ap = ["Adventure Paths", "Age of Ashes"]
    with app.app_context():
        hub.set_tag_parent("Pathfinder 2e", "Pathfinder")
        hub.set_tag_parent("Pathfinder 1e", "Pathfinder")
        keys = {
            "core": _game_book(app, "Core Rulebook", kinds.RULEBOOK, pf2, {"product_line": ["Core"]}),
            "bestiary": _game_book(app, "Bestiary", kinds.RULEBOOK, pf2, {"product_line": ["Core"]}),
            "apg": _game_book(app, "Advanced Player's Guide", kinds.RULEBOOK, pf2, {"product_line": ["Core"]}),
            "gamemastery": _game_book(app, "Gamemastery Guide", kinds.RULEBOOK, pf2, {"product_line": ["Core"]}),
            "part2": _game_book(app, "The Cult of Cinders", kinds.ADVENTURE, pf2, {"series": "Age of Ashes", "series_index": 2, "product_line": ap}),
            "part1": _game_book(app, "Hellknight Hill", kinds.ADVENTURE, pf2, {"series": "Age of Ashes", "series_index": 1, "product_line": ap}),
            "guide": _game_book(app, "Age of Ashes Player's Guide", kinds.PLAYER_AID, pf2, {"series": "Age of Ashes", "product_line": ap}),
            "maps": _game_book(app, "Hellknight Hill Maps", kinds.GM_AID, pf2, {"series": "Age of Ashes", "product_line": ap}),
            "pawns1": _game_book(app, "Bestiary Pawns", kinds.GM_AID, pf2),
            "pawns2": _game_book(app, "NPC Pawns", kinds.GM_AID, pf2),
            "old": _game_book(app, "Crown of the Kobold King", kinds.ADVENTURE, ["Pathfinder", "Pathfinder 1e"]),
            "novel": _game_book(app, "Lord of Runes", "", ["Pathfinder"], kind=kinds.FICTION, fmt="EPUB"),
            "russian": _game_book(app, "Вампиры Книга правил", kinds.RULEBOOK, pf2, {"language": "rus"}),
        }
    return keys


def _feed(client, **args):
    return client.get("/api/books", query_string=dict({"feed": "1"}, **args)).get_json()


def test_the_tabletop_view_groups_game_edition_and_product_line(app, client):
    keys = _library(app)
    login(client, "You")
    data = client.get("/api/books?view=tabletop").get_json()
    books = data["books"]
    assert {book["key"] for book in books} == set(keys.values())  # the novel too, with its game's tag; nothing else
    pathfinder = next(game for game in data["tree"] if game["game"] == "Pathfinder")
    assert [edition["label"] for edition in pathfinder["editions"]] == ["1e", "2e", "No edition given"]
    second = pathfinder["editions"][1]
    lines = {line["name"]: [books[at]["key"] for at in line["items"]] for line in second["lines"]}
    # the core books first (the rules, then the monsters), then the general player's guide, then the rest
    assert lines["Core"] == [keys["core"], keys["bestiary"], keys["apg"], keys["gamemastery"]]
    # an adventure path: its parts in order, then its Player's Guide and maps
    assert lines["Adventure Paths › Age of Ashes"] == [keys["part1"], keys["part2"], keys["guide"], keys["maps"]]
    assert lines["GM aids"] == [keys["pawns1"], keys["pawns2"]]
    assert [line["name"] for line in second["lines"]][0] == "Core"  # rulebook lines before adventures and aids
    novel = next(book for book in books if book["key"] == keys["novel"])
    assert (novel["game"], novel["edition"], novel["role"]) == ("Pathfinder", "", "")
    assert any(line["name"] == "Novels and stories" for edition in pathfinder["editions"] for line in edition["lines"])
    # what the feed needs to group game books, and the language badge
    part = next(book for book in books if book["key"] == keys["part2"])
    assert (part["game_series"], part["game_index"], part["edition"]) == ("Age of Ashes", 2, "Pathfinder 2e")
    assert next(book for book in books if book["key"] == keys["russian"])["language"] == "rus"
    assert part["language"] == ""


def test_hidden_tags_and_roles_leave_my_feed_only(app, client):
    keys = _library(app)
    login(client, "Nan")
    everything = _feed(client)["total"]
    assert client.put("/api/me/hidden", json={"hidden": ["GM aid", "Pathfinder 1e", "gm aid"]}).get_json()["hidden"] == ["GM aid", "Pathfinder 1e"]
    feed = _feed(client)
    shown = {book["key"] for book in feed["books"]}
    assert not shown & {keys["maps"], keys["pawns1"], keys["pawns2"], keys["old"]}
    assert keys["core"] in shown and feed["hidden"] == 4 and feed["total"] == everything - 4
    # a search still finds them, and so does the Tabletop view
    found = client.get("/api/books", query_string={"feed": "1", "terms": json.dumps([{"fields": ["title"], "value": "pawns"}])}).get_json()
    assert {book["key"] for book in found["books"]} == {keys["pawns1"], keys["pawns2"]}
    assert keys["old"] in {book["key"] for book in client.get("/api/books?view=tabletop").get_json()["books"]}
    # hiding a parent tag hides everything under it; it is this profile's choice alone
    client.put("/api/me/hidden", json={"hidden": ["Pathfinder"]})
    assert not {book["key"] for book in _feed(client)["books"]} & set(keys.values())
    client.post("/api/logout")
    login(client, "You")
    assert client.get("/api/me/hidden").get_json()["hidden"] == []
    assert _feed(client)["hidden"] == 0
    client.post("/api/logout")
    assert client.put("/api/me/hidden", json={"hidden": ["x"]}).status_code == 401


def test_a_parent_tag_finds_books_with_the_tags_under_it(app, client):
    keys = _library(app)
    with app.app_context():  # a book carrying only the edition, as a person might tag it
        app.hub.con().execute("DELETE FROM family_tags WHERE book_key=? AND tag='Pathfinder'", (keys["core"],))
        app.hub.con().commit()
    login(client, "You")
    term = [{"fields": ["tag"], "value": "Pathfinder", "sign": "+"}]
    found = {book["key"] for book in client.get("/api/books", query_string={"terms": json.dumps(term)}).get_json()["books"]}
    assert keys["core"] in found and keys["old"] in found
    assert keys["core"] in {book["key"] for book in client.get("/api/books?tag=pathfinder").get_json()["books"]}
    # the tag list says what sits under what, and a parent counts its children's books
    tags = {tag["tag"]: tag for tag in client.get("/api/tags?merged=1").get_json()["tags"]}
    assert tags["Pathfinder 2e"]["parent"] == "Pathfinder"
    assert tags["Pathfinder"]["count"] == len(keys)
    # matcher on its own: nested names come from tabletop.descendants
    nested = tabletop.descendants({"Pathfinder 2e": "Pathfinder", "Pathfinder": "Tabletop games"})
    matches = search.matcher(search.clean([{"fields": ["tag"], "value": "Tabletop games"}]), nested)
    assert matches({"title": "", "author": "", "tags": {"family": ["Pathfinder 2e"]}})
    assert not search.matcher(search.clean([{"fields": ["tag"], "value": "Tabletop games"}]))({"title": "", "author": "", "tags": {"family": ["Pathfinder 2e"]}})


def test_nested_names_read_short_under_their_parent():
    assert tabletop.short_name("Pathfinder 2e", "Pathfinder") == "2e"
    assert tabletop.short_name("Vampire: The Masquerade V5", "Vampire: The Masquerade") == "V5"
    assert tabletop.short_name("AD&D 2e", "D&D") == "AD&D 2e"
    assert tabletop.short_name("Pathfinder", "Pathfinder") == "Pathfinder"


def test_admins_nest_and_unnest_tags(app, client):
    login(client, "Nan")
    assert client.post("/api/admin/tag-parents", json={"tag": "Pathfinder 2e", "parent": "Pathfinder"}).status_code == 403
    client.post("/api/logout")
    login(client, "You")
    assert client.post("/api/admin/tag-parents", json={"tag": "Pathfinder 2e", "parent": "Pathfinder"}).status_code == 200
    assert client.get("/api/admin/tag-parents").get_json()["parents"] == [{"tag": "Pathfinder 2e", "parent": "Pathfinder"}]
    assert client.post("/api/admin/tag-parents", json={"tag": "Pathfinder", "parent": "Pathfinder 2e"}).status_code == 400  # a loop
    assert client.post("/api/admin/tag-parents", json={"tag": "Pathfinder 2e", "parent": ""}).status_code == 200
    assert client.get("/api/admin/tag-parents").get_json()["parents"] == []


def test_a_card_deck_sorted_to_tabletop_leaves_komga(tmp_path):
    library_dir = tmp_path / "library"
    library_dir.mkdir()
    files = {}
    for number in (1, 2):
        path = files[number] = library_dir / ("deck%d.cbz" % number)
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("1.jpg", b"x" * 2000)
    books = [{"id": number, "key": "k%d" % number, "title": "Spell Deck %d" % number, "series": "Spell Deck",
              "series_index": float(number), "formats": {"CBZ": "x"}} for number in (1, 2)]
    kind_of = {"k1": kinds.GRAPHIC, "k2": kinds.GRAPHIC}
    library = types.SimpleNamespace(books=lambda: books, file_path=lambda book, fmt: files.get(book["id"]) if fmt in book["formats"] else None)
    root = tmp_path / "komga"
    root.mkdir()
    hub = types.SimpleNamespace(library=library, kind_index=lambda: kind_of, config=types.SimpleNamespace(komga_comics_dir=root))
    assert komga.sync_files(hub) == (2, 0)
    kind_of.update(k1=kinds.TABLETOP, k2=kinds.TABLETOP)  # the re-sort: a card deck, not a comic
    assert komga.sync_files(hub) == (0, 2)
    assert not any(root.iterdir())
    assert all(path.is_file() for path in files.values())  # the library's own files are never touched


def test_reading_apps_get_game_books_one_by_one_with_their_role(app, client):
    with app.app_context():
        keys = [_game_book(app, "Spell Cards %d" % number, kinds.PLAYER_AID, ["Pathfinder"], fmt="CBZ") for number in (1, 2)]
        con = sqlite3.connect(app.hub.library.metadata_db)
        series = con.execute("INSERT INTO series(name, sort) VALUES('Spell Cards','Spell Cards')").lastrowid
        for book in app.hub.library.books():
            if book["key"] in keys:
                con.execute("INSERT INTO books_series_link(book, series) VALUES(?,?)", (book["id"], series))
        con.commit()
        con.close()
        app.hub.library.refresh(force=True)
    feed = client.get("/opds/all", headers={"Authorization": "Basic " + __import__("base64").b64encode(b"You:demo").decode()}).data.decode()
    assert feed.count("Spell Cards") >= 2 and 'term="Player aid"' in feed
