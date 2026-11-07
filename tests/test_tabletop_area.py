"""Tabletop game books: telling the game, edition and role from the folder, the name and the first pages."""
import json
import os
import sqlite3

from test_app import app, client  # noqa: F401
from bookhub import db, demo, kinds

sort = kinds.sort_book


def test_the_folder_names_the_game_edition_role_and_series():
    v = sort("PF 2E", "Core Rulebook", formats=["PDF"], origin="Pathfinder/2nd Edition/Core/PF 2E - Core Rulebook {PZO2101}.pdf")
    assert (v["kind"], v["role"], v["game"], v["edition"]) == ("Tabletop", "Rulebook", "Pathfinder", "Pathfinder 2e")
    assert v["tags"] == ["Pathfinder", "Pathfinder 2e"] and v["parents"] == {"Pathfinder 2e": "Pathfinder"}
    assert v["title"] == "Core Rulebook" and v["sources"]["game"] == "folder"

    v = sort("PF 2E - Age of Ashes 5 - Against the Scarlet Triad", formats=["PDF"],
             origin="Pathfinder/2nd Edition/Adventure Paths/Age of Ashes/PF 2E - Age of Ashes 5 - Against the Scarlet Triad.pdf")
    assert (v["role"], v["series"], v["series_index"], v["title"]) == ("Adventure", "Age of Ashes", 5, "Against the Scarlet Triad")
    assert v["product_line"] == ["Adventure Paths", "Age of Ashes"]

    v = sort("S03-24 The Golden Serpent", formats=["PDF"], origin="Pathfinder/1st Edition/Society/Season 03/S03-24 The Golden Serpent.pdf")
    assert (v["edition"], v["role"], v["series"], v["series_index"]) == ("Pathfinder 1e", "Adventure", "Pathfinder Society Season 3", 24)


def test_roles_player_and_gm_aids():
    origin = "Pathfinder/2nd Edition/Adventure Paths/Age of Ashes/"
    assert sort("x", formats=["PDF"], origin=origin + "PF 2E - Age of Ashes - Player's Guide.pdf")["role"] == "Player aid"
    assert sort("x", formats=["PDF"], origin=origin + "PF 2E - Age of Ashes 3 - Tomorrow Must Burn - Interactive Maps.pdf")["role"] == "GM aid"
    # a general player's guide sits with the rulebooks
    assert sort("x", formats=["PDF"], origin="Pathfinder/1st Edition/Core Rules/Advanced Player's Guide.pdf")["role"] == "Rulebook"
    # pregens that came out of an archive: the archive's name says what they are
    v = sort("Merisiel", "Level 5", formats=["PDF"], origin="Pathfinder/2nd Edition/Core/PF 2E - Iconic Pregens.7z")
    assert v["role"] == "Player aid"
    assert sort("PF 2E", "Character Sheet Pack", formats=["PDF"], origin="Pathfinder/2nd Edition/Core/PF 2E - Advanced Players Guide Character Sheet Pack.pdf")["role"] == "Player aid"
    assert sort("Bestiary 2 Box", "Pawn Collection", formats=["PDF"], origin="Pathfinder/2nd Edition/Extra/Pawns/Bestiary 2 Box - Pawn Collection.pdf")["role"] == "GM aid"


def test_card_decks_and_map_packs_are_game_books_not_comics():
    v = sort("PF 2e", "Arcane Spell Deck", formats=["CB7"], origin="Pathfinder/2nd Edition/Extra/PF 2e - Arcane Spell Deck.7z")
    assert (v["kind"], v["role"]) == ("Tabletop", "Player aid")
    v = sort("Hoard of the Dragon Queen Maps", formats=["CBZ"])
    assert (v["kind"], v["role"], v["game"]) == ("Tabletop", "GM aid", "D&D")
    # a comic set in a game's world stays a comic, with the game's tag
    v = sort("Vampire the Masquerade", "Giovanni", formats=["CBR"], origin="Vampire the Masquerade/Vampire the Masquerade - Giovanni.cbr")
    assert (v["kind"], v["role"], v["tags"]) == ("Graphic novel", "", ["World of Darkness", "Vampire: The Masquerade"])


def test_fiction_set_in_a_game_world_keeps_its_kind():
    v = sort("Pathfinder Tales: Lord of Runes", "Dave Gross", formats=["EPUB"], catalogue_tags=["Fiction", "Fantasy"])
    assert (v["kind"], v["game"], v["role"]) == ("Fiction", "Pathfinder", "")
    # Hell's Pawns is a novel, not a box of pawns
    v = sort("Hell's Pawns", "Dave Gross", series="Pathfinder Tales", formats=["EPUB"], catalogue_tags=["Fiction"])
    assert v["kind"] == "Fiction"
    # a clanbook is a sourcebook; a clan novel is a novel
    assert sort("Clanbook Lasombra - Revised", formats=["PDF"])["kind"] == "Tabletop"
    v = sort("Clan Novel: Toreador", "Stewart Wieck", formats=["EPUB"])
    assert v["kind"] == "Fiction" and "Vampire: The Masquerade" in v["tags"]
    # fiction gets an edition only when the book itself names one
    v = sort("Some Novel", "A Writer", formats=["EPUB"], origin="Pathfinder/1st Edition/Tales/Some Novel.epub", catalogue_tags=["Fiction"])
    assert v["kind"] == "Fiction" and v["edition"] == "" and v["tags"] == ["Pathfinder"]
    # a catalogue saying "fiction" about a PDF in a game folder has mixed it up with a novel of the same name
    v = sort("Children of the Blood", formats=["PDF"], origin="World Of Darkness V5/Vampire The Masquerade 5ed/Children of the Blood.pdf",
             catalogue_tags=["Fiction"])
    assert (v["kind"], v["edition"]) == ("Tabletop", "Vampire: The Masquerade V5")


def test_a_book_called_pathfinder_is_not_a_game_book_on_its_name_alone():
    assert sort("Pathfinder", "Orson Scott Card", formats=["EPUB"], catalogue_tags=["Science Fiction"])["game"] == ""
    assert sort("The Call of Cthulhu and Other Weird Stories", "H. P. Lovecraft", formats=["EPUB"])["game"] == ""


def test_disagreeing_clues_and_open_questions_are_said():
    v = sort("Core Rulebook", formats=["PDF"], origin="Pathfinder/1st Edition/Core/Core Rulebook.pdf", pages="Pathfinder Second Edition")
    assert v["edition"] == "Pathfinder 1e"  # the folder wins
    assert v["conflicts"] == ["The folder says Pathfinder 1e, the pages says Pathfinder 2e."]
    v = sort("Maps", "DragonKing", formats=["PDF"], origin="Books/Some Campaign/Maps.zip")
    assert (v["kind"], v["role"], v["game"]) == ("Tabletop", "GM aid", "")
    assert "which game is not known" in v["unsure"][0]
    # a flip-mat suits every edition, so a missing one is not a question
    assert sort("Flip Mat - Swamp", formats=["PDF"], origin="Pathfinder/Extra/Flip Mats/Flip Mat - Swamp.pdf")["unsure"] == []
    v = sort("Core Rulebook", formats=["PDF"], pages="Pathfinder Roleplaying Game Core Rulebook, Paizo")
    assert v["game"] == "Pathfinder" and v["sources"]["game"] == "pages"


def test_learned_rules_win_and_rejections_stick():
    rule = {"id": 7, "folder": "Books/Some Campaign", "sets": {"game": "D&D", "edition": "D&D 5e", "series": "Some Campaign"},
            "rejects": {"title": "Maps DragonKing"}}
    v = sort("Maps", "DragonKing", formats=["PDF"], origin="Books/Some Campaign/Maps.zip", learned=[rule])
    assert (v["game"], v["edition"], v["series"]) == ("D&D", "D&D 5e", "Some Campaign")
    assert v["tags"] == ["D&D", "D&D 5e"] and v["learned"] == [7] and v["unsure"] == []
    assert v["title"] == ""
    other = sort("Maps", "DragonKing", formats=["PDF"], origin="Books/Other/Maps.zip", learned=[rule])
    assert other["learned"] == []


def test_new_game_lines_can_be_added():
    lines = kinds.load_game_lines([{"tag": "Shadowrun", "match": ["shadowrun"], "editions": [{"tag": "Shadowrun 6e", "match": ["6th edition|\\b6e\\b"]}]}])
    v = sort("Shadowrun 6e Core Rulebook", formats=["PDF"], game_lines=lines)
    assert (v["game"], v["edition"]) == ("Shadowrun", "Shadowrun 6e")


def test_language_from_the_alphabet():
    assert sort("Вампиры Маскарад. Пятая редакция. Книга правил", formats=["PDF"],
                origin="World Of Darkness V5/Vampire The Masquerade 5ed/Вампиры Маскарад.pdf")["language"] == "rus"
    assert sort("Core Rulebook", formats=["PDF"])["language"] == ""


def _retitle(app, title, new_title, author=None, fmt="PDF"):
    """Make a second library entry from a demo book, renamed, as a stand-in for a game book."""
    library = app.hub.library
    new = demo.add_copy(library.dir, title)
    con = sqlite3.connect(library.metadata_db)
    con.execute("UPDATE books SET title=? WHERE id=?", (new_title, new))
    if fmt != "EPUB":
        con.execute("UPDATE data SET format=? WHERE book=?", (fmt, new))
    if author:
        cur = con.execute("INSERT INTO authors(name, sort) VALUES(?,?)", (author, author))
        con.execute("UPDATE books_authors_link SET author=? WHERE book=?", (cur.lastrowid, new))
    con.commit()
    con.close()
    os.utime(library.metadata_db, (5, 5))
    return new


def test_resorting_puts_kind_role_nested_tags_and_the_verdict_on_books(app):
    hub = app.hub
    with app.app_context():
        new = _retitle(app, "Dracula", "PF 2E", author="Core Rulebook")
        book = hub.library.get(new)
        hub.con().execute("INSERT INTO intake_log(name, origin, size, action, at) VALUES(?,?,?,?,?)",
                          ("PF 2E.pdf", "Pathfinder/2nd Edition/Core/PF 2E - Core Rulebook.pdf", 0, "delivered", db.now()))
        # what the old sorter left on it, and a tag a person added
        hub.set_kind(book["key"], kinds.UNSORTED, source="auto")
        hub.con().execute("INSERT INTO family_tags(book_key, tag, added_by, created) VALUES(?,?,NULL,?)", (book["key"], "V5", db.now()))
        hub.con().execute("INSERT INTO family_tags(book_key, tag, added_by, created) VALUES(?,?,1,?)", (book["key"], "Favourite", db.now()))
        hub.con().commit()
        assert hub.resort_books() > 0
        assert hub.kind_index()[book["key"]] == "Tabletop"
        assert hub.role_index()[book["key"]] == "Rulebook"
        family = {row["tag"] for row in hub.con().execute("SELECT tag FROM family_tags WHERE book_key=?", (book["key"],))}
        assert family == {"Pathfinder", "Pathfinder 2e", "Favourite"}
        assert hub.tag_parents()["Pathfinder 2e"] == "Pathfinder"
        saved = hub.con().execute("SELECT verdict, origin FROM sort_verdicts WHERE book_key=?", (book["key"],)).fetchone()
        assert json.loads(saved["verdict"])["title"] == "Core Rulebook" and saved["origin"].startswith("Pathfinder/")
        # a person's choice of kind is never re-sorted
        emma = next(b for b in hub.library.books() if b["title"] == "Emma")
        hub.set_kind(emma["key"], kinds.TABLETOP, role=kinds.GM_AID)
        hub.con().commit()
        hub.resort_books()
        assert hub.kind_index()[emma["key"]] == "Tabletop" and hub.role_index()[emma["key"]] == "GM aid"
        # the next re-sort with a learned rule takes the edition tag it no longer thinks right back off
        hub.con().execute("INSERT INTO learned_rules(folder, sets, created, updated) VALUES(?,?,?,?)",
                          ("Pathfinder/2nd Edition/Core", json.dumps({"edition": "Pathfinder 1e"}), db.now(), db.now()))
        hub.con().commit()
        hub.resort_books()
        family = {row["tag"] for row in hub.con().execute("SELECT tag FROM family_tags WHERE book_key=?", (book["key"],))}
        assert family == {"Pathfinder", "Pathfinder 1e", "Favourite"}


def test_tag_nesting_refuses_loops(app):
    hub = app.hub
    with app.app_context():
        assert hub.set_tag_parent("Pathfinder 2e", "Pathfinder")
        assert hub.set_tag_parent("Pathfinder", "Tabletop games")
        assert not hub.set_tag_parent("Tabletop games", "Pathfinder 2e")
        assert not hub.set_tag_parent("Pathfinder", "Pathfinder")
        assert hub.set_tag_parent("Pathfinder", None) and "Pathfinder" not in hub.tag_parents()


def test_roles_and_nesting_travel_in_the_book_data_file(app, client):
    from test_app import login
    hub = app.hub
    with app.app_context():
        emma = next(b for b in hub.library.books() if b["title"] == "Emma")
        hub.set_kind(emma["key"], kinds.TABLETOP, role=kinds.ADVENTURE)
        hub.set_tag_parent("Pathfinder 2e", "Pathfinder")
        hub.con().commit()
    login(client, "You")
    data = json.loads(client.get("/api/admin/export").data)
    assert data["tag_parents"] == {"Pathfinder 2e": "Pathfinder"}
    assert data["books"][emma["key"]]["role"] == "Adventure"


def test_version_numbers_and_manga_volumes_are_not_editions():
    assert sort("The Ancient Magus' Bride v20 (2025) (Digital) (1r0n)", formats=["CBZ"])["game"] == ""
    assert sort("Guide to Pathfinder Society Organized Play v5.0", formats=["PDF"],
                origin="Pathfinder/1st Edition/Society/Guide to Pathfinder Society Organized Play v5.0.pdf")["conflicts"] == []
    # the deepest folder naming a game wins over a bare "V5" further up
    v = sort("H5 - Corebook", formats=["PDF"], origin="World Of Darkness V5/Hunter The Reckoning 5ed/H5 - Corebook.pdf")
    assert (v["game"], v["edition"], v["role"]) == ("Hunter: The Reckoning", "Hunter: The Reckoning H5", "Rulebook")


def test_a_folder_of_flip_mats_as_pictures_is_not_a_comic():
    v = sort("City Sites Multi-Pack JPGs", formats=["CBZ"], origin="Pathfinder/Extra/Flip-Tiles/City Sites Multi-Pack - JPGs.zip")
    assert (v["kind"], v["role"]) == ("Tabletop", "GM aid")


def test_what_each_person_hides_travels_in_the_book_data_file(app, client):
    from test_app import login
    from bookhub import tabletop
    hub = app.hub
    with app.app_context():
        you = next(p for p in hub.profiles() if p["name"] == "You")
        tabletop.set_hidden(hub, you["id"], ["GM aid", "Pathfinder 1e"])
    login(client, "You")
    data = json.loads(client.get("/api/admin/export").data)
    assert data["hidden_tags"] == {"You": ["GM aid", "Pathfinder 1e"]}
    with app.app_context():
        nan = next(p for p in hub.profiles() if p["name"] == "Nan")
        tabletop.set_hidden(hub, you["id"], [])
        tabletop.set_hidden(hub, nan["id"], ["Horror"])
        hub.import_data(dict(data, hidden_tags={"You": ["GM aid"], "Nan": ["Romance"]}))
        assert tabletop.hidden(hub, you["id"]) == ["GM aid"]
        assert tabletop.hidden(hub, nan["id"]) == ["Horror"]  # an existing choice is never overwritten


def test_not_duplicates_choices_travel_in_the_book_data_file(app, client):
    from test_app import login
    hub = app.hub
    with app.app_context():
        db.kv_set(hub.con(), "duplicates:apart", json.dumps([[3, 7]]))
    login(client, "You")
    data = json.loads(client.get("/api/admin/export").data)
    assert data["duplicates_apart"] == [[3, 7]]
    with app.app_context():
        hub.import_data(dict(data, duplicates_apart=[[7, 3], [8, 9]]))
        assert sorted(sorted(ids) for ids in hub.duplicates.told_apart()) == [[3, 7], [8, 9]]


def test_wayfinder_is_a_pathfinder_fanzine_with_issue_numbers():
    v = sort("Wayfinder #06", formats=["PDF"], origin="Pathfinder/Extra/Wayfinder/Wayfinder #06.pdf",
             pages="Dungeons & Dragons is mentioned in the Open Game License")
    assert (v["kind"], v["game"], v["series"], v["series_index"]) == ("Tabletop", "Pathfinder", "Wayfinder", 6)
    assert v["conflicts"] == [] and v["unsure"] == []  # the pages mentioning D&D is no disagreement
    assert sort("Wayfinder 18", formats=["PDF"])["game"] == "Pathfinder"
    assert sort("Wayfinder", "A Novelist", formats=["EPUB"], catalogue_tags=["Fiction"])["game"] == ""
