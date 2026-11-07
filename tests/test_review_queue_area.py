"""The review queue: the sorter's suggestions, confirmed into the library, and the rules Family Hub learns."""
import json
import sqlite3
from pathlib import Path

from test_app import FakeLibraryManager, app, client, login  # noqa: F401
from bookhub import db, demo, kinds, review


class FakeManager(FakeLibraryManager):
    """The test library manager, also taking series, numbers, publishers and languages the way the real one does."""

    def set_field(self, book_id, field, value):
        if field not in ("series", "series_index", "publisher", "languages"):
            return super().set_field(book_id, field, value)
        self.calls.append(("set", book_id, field))
        con = self._con()
        con.execute("CREATE TABLE IF NOT EXISTS publishers (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
        con.execute("CREATE TABLE IF NOT EXISTS books_publishers_link (id INTEGER PRIMARY KEY, book INTEGER, publisher INTEGER)")
        con.execute("CREATE TABLE IF NOT EXISTS languages (id INTEGER PRIMARY KEY, lang_code TEXT NOT NULL)")
        con.execute("CREATE TABLE IF NOT EXISTS books_languages_link (id INTEGER PRIMARY KEY, book INTEGER, lang_code INTEGER)")
        if field == "series":
            con.execute("DELETE FROM books_series_link WHERE book=?", (book_id,))
            if value:
                row = con.execute("SELECT id FROM series WHERE name=?", (value,)).fetchone()
                series = row[0] if row else con.execute("INSERT INTO series(name, sort) VALUES(?,?)", (value, value)).lastrowid
                con.execute("INSERT INTO books_series_link(book, series) VALUES(?,?)", (book_id, series))
        elif field == "series_index":
            con.execute("UPDATE books SET series_index=? WHERE id=?", (float(value), book_id))
        elif field == "publisher":
            con.execute("DELETE FROM books_publishers_link WHERE book=?", (book_id,))
            publisher = con.execute("INSERT INTO publishers(name) VALUES(?)", (value,)).lastrowid
            con.execute("INSERT INTO books_publishers_link(book, publisher) VALUES(?,?)", (book_id, publisher))
        else:
            codes = {name: code for code, name in review.LANGUAGES.items()}
            language = con.execute("INSERT INTO languages(lang_code) VALUES(?)", (codes[value],)).lastrowid
            con.execute("INSERT INTO books_languages_link(book, lang_code) VALUES(?,?)", (book_id, language))
        con.commit()
        con.close()


def _game_book(app, title, author, origin, size):
    """A PDF in the sample library that arrived from ``origin``, as the library manager files one."""
    hub = app.hub
    library = Path(hub.config.library_dir)
    new = demo.add_copy(library, "Dracula", size=size)
    con = sqlite3.connect(library / "metadata.db")
    folder, name = con.execute("SELECT b.path, d.name FROM books b JOIN data d ON d.book=b.id WHERE b.id=?", (new,)).fetchone()
    pdf = library / folder / (name + ".pdf")
    (library / folder / (name + ".epub")).rename(pdf)
    con.execute("UPDATE data SET format='PDF' WHERE book=?", (new,))
    con.execute("UPDATE books SET title=? WHERE id=?", (title, new))
    row = con.execute("SELECT id FROM authors WHERE name=?", (author,)).fetchone()
    author_id = row[0] if row else con.execute("INSERT INTO authors(name, sort) VALUES(?,?)", (author, author)).lastrowid
    con.execute("UPDATE books_authors_link SET author=? WHERE book=?", (author_id, new))
    con.commit()
    con.close()
    hub.con().execute("INSERT INTO intake_log(name, origin, size, action, at) VALUES(?,?,?,?,?)",
                      (Path(origin).name, origin, pdf.stat().st_size, "delivered", db.now()))
    hub.con().commit()
    hub.library.refresh(force=True)
    return new


def _ready(app, client):
    login(client, "You")
    client.post("/api/admin/connections", json={"values": {"cwa_api_url": "http://manager:8083", "cwa_user": "admin", "cwa_pass": "pw-1"}})
    app.hub.manager = FakeManager(app.hub.config.library_dir)


def _queue(client, book_id=None):
    """The first tab of the queue, or one book's item from whichever tab (waiting or ready to write) it is in."""
    data = client.get("/api/admin/review").get_json()
    if book_id is None:
        return data
    for tab in data["tabs"]:
        part = client.get("/api/admin/review?tab=%s&limit=2000" % tab["id"]).get_json()
        for entry in part["items"] + [item for group in part["ready"] for item in group["items"]]:
            if entry["book_id"] == book_id:
                return entry
    return None


def _finish(client):
    import time
    job = client.get("/api/admin/review/job").get_json()
    for _ in range(400):
        if not job["running"]:
            break
        time.sleep(0.05)
        job = client.get("/api/admin/review/job").get_json()
    return job


def test_plain_words_patterns_and_credits():
    assert review.name_pattern("Map 01") == r"^Map\ \d+$" and review.shown_pattern(review.name_pattern("Map 01")) == "Map #"
    rule = {"folder": "Pathfinder/2nd Edition/Society",
            "sets": {"game": "Pathfinder", "edition": "Pathfinder 2e", "role": "Adventure", "series": "Pathfinder Society"}}
    assert review.rule_words(rule) == ("Anything arriving in Pathfinder/2nd Edition/Society → Pathfinder › Pathfinder 2e, "
                                      "Adventure, series Pathfinder Society")
    assert "never suggest title “Maps DragonKing”" in review.rule_words({"pattern": "^Maps$", "rejects": {"title": "Maps DragonKing"}})
    text = "Credits Authors: Logan Bonner, Jason Bulmahn and Stephen Radney-MacFarland Development: Erik Mona Editing: Judy Bauer"
    assert review.credits(text) == ["Logan Bonner", "Jason Bulmahn", "Stephen Radney-MacFarland"]
    assert review.credits("A book with no credits page") == []


def test_a_new_game_system_from_its_folder_sorts_the_next_books():
    lines = kinds.GAME_LINES
    assert review.new_game_from("Shadowrun 6e/Core", lines) == {"game": "Shadowrun", "edition": "Shadowrun 6e"}
    assert review.new_game_from("Shadowrun/6th Edition/Core", lines) == {"game": "Shadowrun", "edition": "Shadowrun 6e"}
    assert review.new_game_from("Books/Some Campaign", lines) is None  # a plain folder says nothing
    assert review.new_game_from("Pathfinder/2nd Edition", lines) is None  # a game we know already
    entry = review.game_line_entry("Shadowrun", "Shadowrun 6e")
    later = kinds.sort_book("Firing Squad", formats=["PDF"], origin="Shadowrun 6e/Adventures/Firing Squad.pdf",
                            game_lines=kinds.load_game_lines([entry]))
    assert (later["kind"], later["game"], later["edition"], later["role"]) == ("Tabletop", "Shadowrun", "Shadowrun 6e", "Adventure")


def test_rules_that_differ_by_a_number_are_offered_as_one(app):
    hub = app.hub
    with app.app_context():
        for season in (3, 4, 5):
            hub.review.save_rule("Pathfinder/1st Edition/Society/Season 0%d" % season, "",
                                 {"kind": "Tabletop", "role": "Adventure", "game": "Pathfinder", "edition": "Pathfinder 1e",
                                  "series": "Pathfinder Society Season %d" % season}, {})
        hub.review.save_rule("Books/Elsewhere", "", {"kind": "Fiction"}, {})
        merges = hub.review.merge_suggestions()
        assert len(merges) == 1
        offer = merges[0]
        assert offer["rule"]["folder"] == "Pathfinder/1st Edition/Society" and offer["left_out"] == ["series"]
        assert offer["rule"]["sets"] == {"kind": "Tabletop", "role": "Adventure", "game": "Pathfinder", "edition": "Pathfinder 1e"}
        assert len(hub.learned_rules()) == 4  # only offered: nothing is combined until an admin says so
        hub.review.merge(offer["ids"])
        rules = hub.learned_rules()
        assert len(rules) == 2 and any(rule["folder"] == "Pathfinder/1st Edition/Society" for rule in rules)
        row = hub.con().execute("SELECT confirmations FROM learned_rules WHERE folder=?", ("Pathfinder/1st Edition/Society",)).fetchone()
        assert row["confirmations"] == 3


def test_the_queue_has_suggestions_and_plain_unsorted_books_wait_in_their_own_tab(app, client):
    hub = app.hub
    core = _game_book(app, "PF 2E", "Core Rulebook", "Pathfinder/2nd Edition/Core/PF 2E - Core Rulebook {PZO2101}.pdf", 300)
    plain = _game_book(app, "Some Notes", "Somebody Else", "", 310)
    with app.app_context():
        hub.resort_books(read_pages=False)
    login(client, "You")
    data = _queue(client)
    assert {tab["id"] for tab in data["tabs"]} == {"tabletop", "books", "graphic", "unsorted"}
    item = next(entry for entry in data["items"] if entry["book_id"] == core)
    assert item["media"] == "tabletop"
    assert item["suggest"]["title"] == "Core Rulebook" and item["suggest"]["authors"] == ["Paizo"]
    assert (item["suggest"]["game"], item["suggest"]["edition"], item["suggest"]["role"]) == ("Pathfinder", "Pathfinder 2e", "Rulebook")
    assert {change["field"] for change in item["changes"]} >= {"title", "authors", "publisher", "tags"}
    assert all(entry["book_id"] != plain for entry in data["items"])  # not among the tabletop suggestions...
    unsorted = client.get("/api/admin/review?tab=unsorted&limit=500").get_json()["items"]
    assert plain in [entry["book_id"] for entry in unsorted]  # ...but waiting for a person to look inside it
    login(client, "Nan")
    assert client.get("/api/admin/review").status_code == 403


def test_confirming_writes_to_the_library_learns_a_rule_and_sorts_the_rest(app, client):
    hub = app.hub
    first = _game_book(app, "Age of Ashes 1", "Hellknight Hill",
                       "Pathfinder/2nd Edition/Adventure Paths/Age of Ashes/PF 2E - Age of Ashes 1 - Hellknight Hill.pdf", 320)
    second = _game_book(app, "Age of Ashes 2", "Cult of Cinders",
                        "Pathfinder/2nd Edition/Adventure Paths/Age of Ashes/PF 2E - Age of Ashes 2 - Cult of Cinders.pdf", 330)
    with app.app_context():
        hub.resort_books(read_pages=False)
        nan = next(p for p in hub.profiles() if p["name"] == "Nan")
        old_key = hub.library.get(first)["key"]
        hub.save_reading(nan["id"], old_key, percent=30)
    login(client, "You")
    item = _queue(client, first)
    assert item["batch_size"] == 2 and item["suggest"]["series"] == "Age of Ashes" and item["suggest"]["series_index"] == 1
    fields = dict(item["suggest"], language="")
    # nothing can be written until the library manager's sign-in is saved
    assert client.post("/api/admin/review/%d" % first, json={"action": "confirm", "fields": fields}).status_code == 400
    _ready(app, client)
    r = client.post("/api/admin/review/%d" % first, json={"action": "confirm", "fields": fields})
    assert r.status_code == 200, r.get_json()
    assert list((Path(hub.config.data_dir) / "library-backups").glob("metadata-*.db"))
    fixed = hub.library.get(first)
    assert (fixed["title"], fixed["author"], fixed["own_series"], fixed["series_index"]) == ("Hellknight Hill", "Paizo", "Age of Ashes", 1.0)
    assert fixed["publisher"] == "Paizo" and {"Pathfinder", "Pathfinder 2e", "Adventure"} <= set(fixed["raw_tags"])
    with app.app_context():
        # Family Hub's records followed the book to its new name
        assert hub.con().execute("SELECT percent FROM reading WHERE book_key=?", (fixed["key"],)).fetchone()[0] == 30
        assert hub.kind_index()[fixed["key"]] == "Tabletop" and hub.role_index()[fixed["key"]] == "Adventure"
        kind_source = hub.con().execute("SELECT source FROM book_kind WHERE book_key=?", (fixed["key"],)).fetchone()[0]
        assert kind_source == "manual"
        family = {row["tag"] for row in hub.con().execute("SELECT tag FROM family_tags WHERE book_key=?", (fixed["key"],))}
        assert {"Pathfinder", "Pathfinder 2e"} <= family
    # the rule it learned, in plain words, with the books it covers
    rules = client.get("/api/admin/review/rules").get_json()["rules"]
    assert len(rules) == 1 and rules[0]["folder"] == "Pathfinder/2nd Edition/Adventure Paths/Age of Ashes"
    assert rules[0]["words"].startswith("Anything arriving in Pathfinder/2nd Edition/Adventure Paths/Age of Ashes → Pathfinder › Pathfinder 2e, Adventure")
    assert rules[0]["matches"] == 2
    # the other book from that folder was sorted by the rule, and waits only to be written to the library
    assert _queue(client, first) is None
    waiting = _queue(client, second)
    assert waiting["ready"] == rules[0]["id"] and waiting["suggest"]["title"] == "Cult of Cinders"
    group = next(group for group in client.get("/api/admin/review?tab=tabletop").get_json()["ready"] if group["rule"] == rules[0]["id"])
    assert group["count"] == 1 and group["words"] == rules[0]["words"]
    backups = len(list((Path(hub.config.data_dir) / "library-backups").glob("metadata-*.db")))
    assert client.post("/api/admin/review/ready/%d" % rules[0]["id"]).status_code == 200
    job = _finish(client)
    assert job["problems"] == [] and job["confirmed"] == 1
    assert hub.library.get(second)["title"] == "Cult of Cinders" and "Pathfinder 2e" in hub.library.get(second)["raw_tags"]
    assert _queue(client, second) is None
    assert client.post("/api/admin/review/ready/%d" % rules[0]["id"]).status_code == 400  # nothing left to write
    with app.app_context():
        assert len(hub.learned_rules()) == 1  # writing what a rule sorted teaches no new rule
    assert backups >= 1
    rules = client.get("/api/admin/review/rules").get_json()["rules"]
    # rules travel in the book data file
    data = json.loads(client.get("/api/admin/export").data)
    assert data["learned_rules"][0]["folder"] == rules[0]["folder"]
    assert client.delete("/api/admin/review/rules/%d" % rules[0]["id"]).status_code == 200
    assert client.get("/api/admin/review/rules").get_json()["rules"] == []
    with app.app_context():
        hub.import_data(data)
        assert len(hub.learned_rules()) == 1


def test_confirm_all_like_this_does_the_whole_batch(app, client):
    hub = app.hub
    ids = [_game_book(app, "S03-%02d Mission %d" % (number, number), "elenak",
                      "Pathfinder/1st Edition/Society/Season 03/S03-%02d Mission %d.pdf" % (number, number), 300 + number * 150)
           for number in (1, 2, 3)]
    with app.app_context():
        hub.resort_books(read_pages=False)
    _ready(app, client)
    item = _queue(client, ids[0])
    assert item["batch_size"] == 3
    r = client.post("/api/admin/review/%d" % ids[0], json={"action": "confirm_all", "batch": item["batch"], "fields": dict(item["suggest"])})
    assert r.status_code == 200, r.get_json()
    job = client.get("/api/admin/review/job").get_json()
    for _ in range(200):
        if not job["running"]:
            break
        import time
        time.sleep(0.05)
        job = client.get("/api/admin/review/job").get_json()
    assert job["problems"] == [] and job["confirmed"] == 3
    titles = sorted(hub.library.get(book_id)["title"] for book_id in ids)
    assert titles == ["Mission 1", "Mission 2", "Mission 3"]
    assert [hub.library.get(book_id)["series_index"] for book_id in ids] == [1.0, 2.0, 3.0]
    with app.app_context():
        row = hub.con().execute("SELECT confirmations FROM learned_rules").fetchone()
        assert row["confirmations"] == 3


def test_books_that_share_a_name_come_first_and_part_ways_on_confirm(app, client):
    hub = app.hub
    one = _game_book(app, "Maps", "DragonKing", "Books/Rise of Tiamat/Maps.pdf", 900)
    two = _game_book(app, "Maps", "DragonKing", "Books/Hoard of the Dragon Queen/Maps.pdf", 1100)
    _game_book(app, "Bigger 1", "elenak", "Pathfinder/1st Edition/Extra/Map Pack/Bigger 1.pdf", 1300)
    _game_book(app, "Bigger 2", "elenak", "Pathfinder/1st Edition/Extra/Map Pack/Bigger 2.pdf", 1500)
    with app.app_context():
        hub.resort_books(read_pages=False)
    _ready(app, client)
    items = _queue(client)["items"]
    assert {items[0]["book_id"], items[1]["book_id"]} == {one, two} and items[0]["shares"] == 2
    item = _queue(client, one)
    fields = dict(item["suggest"], title="Rise of Tiamat Maps", authors=["Wizards of the Coast"], game="D&D", edition="D&D 5e")
    assert client.post("/api/admin/review/%d" % one, json={"action": "confirm", "fields": fields}).status_code == 200
    assert hub.library.get(one)["key"] != hub.library.get(two)["key"]
    with app.app_context():
        assert hub.kind_index()[hub.library.get(one)["key"]] == "Tabletop"
        assert hub.kind_index().get(hub.library.get(two)["key"])  # the other entry kept its sort
    assert _queue(client, two) is not None


def test_rejecting_remembers_and_leaves_the_library_alone(app, client):
    hub = app.hub
    book = _game_book(app, "PF 2E", "Bestiary", "Pathfinder/2nd Edition/Core/PF 2E - Bestiary.pdf", 400)
    with app.app_context():
        hub.resort_books(read_pages=False)
    _ready(app, client)
    assert _queue(client, book) is not None
    r = client.post("/api/admin/review/%d" % book, json={"action": "reject"})
    assert r.status_code == 200
    assert hub.manager.calls == [] and hub.library.get(book)["title"] == "PF 2E"
    assert _queue(client, book) is None
    rules = client.get("/api/admin/review/rules").get_json()["rules"]
    assert rules[0]["rejects"]["title"] == "Bestiary" and "never suggest" in rules[0]["words"]
    with app.app_context():
        hub.resort_books(read_pages=False)
    assert _queue(client, book) is None  # and stays out after a re-sort


def test_confirming_a_new_game_system_teaches_the_sorter(app, client):
    hub = app.hub
    book = _game_book(app, "Shadowrun Sixth World Maps", "elenak", "Shadowrun 6e/Extras/Maps/Sixth World Maps.pdf", 410)
    with app.app_context():
        hub.resort_books(read_pages=False)
    _ready(app, client)
    item = _queue(client, book)
    assert item["new_game"] == {"game": "Shadowrun", "edition": "Shadowrun 6e"}
    fields = dict(item["suggest"], authors=["Catalyst Game Labs"], publisher="Catalyst Game Labs")
    r = client.post("/api/admin/review/%d" % book, json={"action": "confirm", "fields": fields, "new_game": item["new_game"]})
    assert r.status_code == 200, r.get_json()
    with app.app_context():
        assert [line for line in hub.game_lines() if line.tag == "Shadowrun"]
        later = kinds.sort_book("Firing Squad", formats=["PDF"], origin="Shadowrun 6e/Adventures/Firing Squad.pdf", game_lines=hub.game_lines())
        assert later["edition"] == "Shadowrun 6e"
    data = json.loads(client.get("/api/admin/export").data)
    assert data["game_lines"][0]["tag"] == "Shadowrun" and data["game_lines"][0]["publisher"] == "Catalyst Game Labs"


def test_the_whole_library_can_be_sorted_again_in_the_background(app, client):
    hub = app.hub
    _game_book(app, "PF 2E", "Gamemastery Guide", "Pathfinder/2nd Edition/Core/PF 2E - Gamemastery Guide.pdf", 420)
    login(client, "You")
    assert client.post("/api/admin/review/resort").status_code == 200
    job = client.get("/api/admin/review/job").get_json()
    import time
    for _ in range(400):
        if not job["running"]:
            break
        time.sleep(0.05)
        job = client.get("/api/admin/review/job").get_json()
    assert job["what"] == "resort" and job["done"] == job["total"] > 0 and job["problems"] == []
    assert any(entry["title"] == "PF 2E" for entry in _queue(client)["items"])
    seen = []
    with app.app_context():
        assert hub.review.resort(progress=lambda done, total: seen.append((done, total))) == seen[-1][1]


def test_a_later_arrival_a_rule_sorts_waits_to_be_written(app, client):
    hub = app.hub
    with app.app_context():
        rule_id = hub.review.save_rule("Pathfinder/2nd Edition/Bounties", "", {"kind": "Tabletop", "role": "Adventure", "game": "Pathfinder",
                                                                              "edition": "Pathfinder 2e"}, {})
    book = _game_book(app, "PF 2E - Bounty 3 - Snallygaster", "elenak", "Pathfinder/2nd Edition/Bounties/PF 2E - Bounty 3 - Snallygaster.pdf", 500)
    with app.app_context():
        hub.sort_new_books(read_pages=False)
    _ready(app, client)
    data = client.get("/api/admin/review?tab=tabletop&limit=1").get_json()
    assert len(data["items"]) <= 1 and data["tab"] == "tabletop"
    group = next(group for group in data["ready"] if group["rule"] == rule_id)
    assert [item["book_id"] for item in group["items"]] == [book]
    assert hub.manager.calls == []  # nothing is written until "Write to the library" is pressed
