"""The Kindle diary: a file from the export script, brought in now and then, says how far each book is."""
from datetime import date

from test_app import app, client, login  # noqa: F401

from bookhub import kindle


def export(*books):
    return {"source": "kindle-web", "exported": "2026-11-08T00:00:00Z", "books": [
        {"asin": asin, "title": title, "authors": [author], "origin": origin, "position": position}
        for asin, title, author, origin, position in books]}


def me(app, name="You"):
    return next(profile["id"] for profile in app.hub.profiles() if profile["name"] == name)


def test_each_file_brought_in_is_a_day_in_the_diary(app):
    with app.app_context():
        pid = me(app)
        first = export(("B0BN96R1M5", "Desire in His Blood (Brides of the Kylorr Book 1)", "Draven, Zoey:", "KINDLE_UNLIMITED", 2523),
                       ("B000000001", "Court of Tricksters", "Prater, S. L.:", "PURCHASE", 1))
        counts = kindle.bring_in(app.hub, pid, first, today=date(2026, 11, 1))
        assert counts == {"books": 2, "new": 2, "moved": 0, "started": 1}
        shelf = {book["asin"]: book for book in kindle.library(app.hub, pid)["books"]}
        started = shelf["B0BN96R1M5"]
        # "Draven, Zoey:" is how Amazon writes it
        assert started["author"] == "Zoey Draven" and started["status"] == "reading" and started["unlimited"]
        assert started["days"] == [] and shelf["B000000001"]["status"] == "none"  # nothing to compare the first file with

        # a week later one moved on, the other was opened
        kindle.bring_in(app.hub, pid, export(("B0BN96R1M5", "Desire in His Blood (Brides of the Kylorr Book 1)", "Draven, Zoey:", "", 4100),
                                              ("B000000001", "Court of Tricksters", "Prater, S. L.:", "", 60)), today=date(2026, 11, 8))
        # the same day twice keeps the furthest place, and a file where Amazon did not answer for a book keeps its place
        kindle.bring_in(app.hub, pid, export(("B0BN96R1M5", "Desire in His Blood (Brides of the Kylorr Book 1)", "Draven, Zoey:", "", 3000)),
                        today=date(2026, 11, 8))
        kindle.bring_in(app.hub, pid, export(("B0BN96R1M5", "Desire in His Blood (Brides of the Kylorr Book 1)", "Draven, Zoey:", "", None)),
                        today=date(2026, 11, 9))
        shelf = {book["asin"]: book for book in kindle.library(app.hub, pid)["books"]}
        assert shelf["B0BN96R1M5"]["days"] == ["2026-11-08"]
        assert shelf["B000000001"]["days"] == ["2026-11-08"] and shelf["B000000001"]["status"] == "reading"


def test_a_file_that_is_not_from_the_script_is_refused(client):
    login(client, "You")
    for bad in (None, [], {"books": []}, {"source": "kindle-web", "books": "lots"}, {"source": "kindle-web", "books": [{"asin": "x", "title": "y"}]}):
        assert client.post("/api/me/kindle", json=bad).status_code == 400
    assert client.post("/api/me/kindle/B0BN96R1M5/finished", json={}).status_code == 404


def test_finished_is_pressed_and_the_book_is_in_the_library_search(client):
    login(client, "You")
    got = client.post("/api/me/kindle", json=export(("B0BN96R1M5", "Desire in His Blood", "Draven, Zoey:", "KINDLE_UNLIMITED", 2523),
                                                    ("B000000002", "A Girl Like Her", "Gibson, Ava:", "PURCHASE", 1))).get_json()
    assert got["books"] and got["new"] == 2
    listed = {book["title"]: book for book in client.get("/api/books").get_json()["books"]}
    # both are found in the library search, marked Kindle, the started one as being read
    assert listed["A Girl Like Her"]["kindle"] and not listed["A Girl Like Her"]["in_library"]
    assert listed["Desire in His Blood"]["status"] == "reading"

    reply = client.post("/api/me/kindle/B0BN96R1M5/finished", json={"day": "2026-11-07"}).get_json()
    shelf = reply["books"]
    # it says where the book comes in the year (as Finished on a library book does), but only when that day is this year
    assert ("finished" in reply) == (date.today().year == 2026)
    assert "finished" not in client.post("/api/me/kindle/B000000002/finished", json={"day": "2020-05-01"}).get_json()
    done = next(book for book in shelf if book["asin"] == "B0BN96R1M5")
    assert done["status"] == "finished" and done["finished"] == "2026-11-07"
    # a later file does not undo it
    client.post("/api/me/kindle", json=export(("B0BN96R1M5", "Desire in His Blood", "Draven, Zoey:", "", 2600)))
    again = next(book for book in client.get("/api/me/kindle").get_json()["books"] if book["asin"] == "B0BN96R1M5")
    assert again["status"] == "finished"
    undone = client.post("/api/me/kindle/B0BN96R1M5/finished", json={"finished": False}).get_json()["books"]
    assert next(book for book in undone if book["asin"] == "B0BN96R1M5")["status"] == "reading"
