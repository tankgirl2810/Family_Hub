"""Finding out what a book really is: PDF clues, how sure the catalogues make us, fixing clear cases by
themselves (with Undo), and offering doubtful ones in the review queue."""
from test_app import app, client, login  # noqa: F401
from bookhub import bookfix, catalogues, identify


def _pdf(path, lines, info_title=""):
    """A one-page PDF with each (size, text) line in Helvetica, and an optional junk title in its own details."""
    stream = "".join("BT /F1 %d Tf 40 %d Td (%s) Tj ET\n" % (size, 520 - n * 60, text) for n, (size, text) in enumerate(lines)).encode()
    objects = [b"<</Type/Catalog/Pages 2 0 R>>", b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
               b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 400 600]/Resources<</Font<</F1 4 0 R>>>>/Contents 5 0 R>>",
               b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
               b"<</Length %d>>stream\n" % len(stream) + stream + b"endstream",
               ("<</Title(%s)/Author(elenak)>>" % info_title).encode()]
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1) + b"".join(b"%010d 00000 n \n" % at for at in offsets)
    out += b"trailer\n<</Size %d/Root 1 0 R/Info 6 0 R>>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    path.write_bytes(bytes(out))
    return path


def test_the_biggest_words_on_the_first_page_are_the_title(tmp_path):
    path = _pdf(tmp_path / "PNW0397.pdf", [(36, "The Long Way Home"), (10, "Copyright 2019 Some Press. doi:10.1234/abcd.5678.")],
                info_title="Microsoft Word - draft3.docx")
    assert identify.cover_title(path) == "The Long Way Home"
    isbns, title, author, dois, cover = bookfix._pdf_clues(path)
    assert title == ""  # the program's leftovers are not a clue (the user name is dropped later, in clues())
    assert dois == ["10.1234/abcd.5678"] and cover == "The Long Way Home"
    assert identify.cover_title(tmp_path / "missing.pdf") == ""
    # a page whose biggest thing is a page number has no title to give
    assert identify.cover_title(_pdf(tmp_path / "n.pdf", [(40, "12"), (10, "Some small words here")])) == ""


def test_a_byline_on_the_cover_is_an_author():
    split = identify.split_byline
    assert split("The Asylum Stone By James L. Sutter") == ("The Asylum Stone", "James L. Sutter")
    assert split("By Jerome Virnich The Darkest Abduction") == ("The Darkest Abduction", "Jerome Virnich")
    assert split("Travels by Sea and Land by Ann Lee and Bob Ray") == ("Travels by Sea and Land", "Ann Lee and Bob Ray")
    assert split("Stand by Me") == ("Stand by Me", "") and split("Dune") == ("Dune", "")
    assert bookfix.tidy_title("Microsoft Word - DDEX18 Tales Trees Tell.docx") == "DDEX18 Tales Trees Tell"


def test_junk_details_and_dois():
    junk = identify.junk_meta
    assert junk("Microsoft Word - Chapter1.doc") and junk("untitled") and junk("layout 1") and junk("Book1") and junk("1234")
    assert junk("Adobe InDesign CS6") and junk("final.indd")
    assert not junk("The Name of the Wind") and not junk("Patrick Rothfuss")
    assert identify.dois_in("see https://doi.org/10.1000/XYZ123. and 10.1000/xyz123") == ["10.1000/xyz123"]


def _clues(titles=(), isbns=(), dois=()):
    return {"titles": list(titles), "isbns": list(isbns), "dois": list(dois)}


def _candidate(title, authors, sources, via=()):
    return identify.Candidate(title=title, authors=authors, sources=list(sources), via=list(via), year=None, publisher="",
                              isbn="", cover="", synopsis="", why="")


def _judge(book, clues, candidates):
    for candidate in candidates:
        identify.score(candidate, clues)
    candidates.sort(key=lambda candidate: -candidate["score"])
    return identify.verdict(book, candidates)


BOOK = {"title": "PNW0397", "author": "elenak"}


def test_an_isbn_that_agrees_with_the_cover_is_clear():
    clues = _clues([("The Long Way Home", "", "the cover")], [("9780141439518", "inside the PDF")])
    status, best, why = _judge(BOOK, clues, [_candidate("The Long Way Home", ["Ann Writer"], ["Open Library"], ["isbn"])])
    assert status == identify.CLEAR and best["title"] == "The Long Way Home" and "ISBN" in why


def test_an_author_tacked_on_the_title_still_agrees():
    clues = _clues([("Designing Virtual Worlds, Richard Bartle", "", "the title, tidied")], [("0131018167", "inside the PDF")])
    assert _judge(BOOK, clues, [_candidate("Designing Virtual Worlds", ["Richard Bartle"], ["Open Library"], ["isbn"])])[0] == identify.CLEAR


def test_an_isbn_alone_or_a_search_alone_is_only_a_suggestion():
    alone = _clues([], [("9780141439518", "inside the PDF")])
    assert _judge(BOOK, alone, [_candidate("Something Else", ["B. Body"], ["Open Library"], ["isbn"])])[0] == identify.DOUBT
    searched = _clues([("The Long Way Home", "", "the cover")])
    assert _judge(BOOK, searched, [_candidate("The Long Way Home", ["Ann Writer"], ["Open Library"])])[0] == identify.DOUBT


def test_two_catalogues_and_the_file_agreeing_is_clear_but_a_close_second_is_not():
    clues = _clues([("The Long Way Home", "Ann Writer", "the PDF's own details")])
    sure = _candidate("The Long Way Home", ["Ann Writer"], ["Open Library", "Google Books"])
    assert _judge(BOOK, clues, [sure])[0] == identify.CLEAR
    # an ISBN that brings back two books with the same title by different people: a person has to choose
    shared = _clues([("The Long Way Home", "", "the cover")], [("9780141439518", "inside the PDF")])
    status, _, why = _judge(BOOK, shared, [_candidate("The Long Way Home", ["Ann Writer"], ["Open Library"], ["isbn"]),
                                           _candidate("The Long Way Home", ["Bob Other"], ["Open Library"], ["isbn"])])
    assert status == identify.DOUBT and "nearly as likely" in why


def test_nothing_found_or_already_right():
    assert identify.verdict(BOOK, [])[0] == identify.UNKNOWN
    right = {"title": "The Long Way Home", "author": "Ann Writer"}
    clue = _clues([("The Long Way Home", "Ann Writer", "the title, tidied")])
    assert _judge(right, clue, [_candidate("The Long Way Home", ["Ann Writer"], ["Open Library"])])[0] == identify.FINE


def test_a_loose_search_result_is_not_offered():
    clues = _clues([("Heroes Handbook", "", "the title, tidied")])
    assert _judge(BOOK, clues, [_candidate("Greek Mythology", ["Liv Albert"], ["Open Library"])])[0] == identify.UNKNOWN
    nameless = _clues([("Assamita", "", "the title, tidied")])
    assert _judge(BOOK, nameless, [_candidate("Assamita", [], ["Open Library"])])[0] == identify.UNKNOWN


def _set_up(app, monkeypatch, status):
    hub = app.hub
    book = hub.library.books()[0]
    monkeypatch.setattr(hub.bookfix, "needing", lambda: [{"id": book["id"], "key": book["key"]}])
    monkeypatch.setattr(hub, "kind_index", lambda: {})
    best = _candidate("The Long Way Home", ["Ann Writer"], ["Open Library"], ["isbn"])
    monkeypatch.setattr(hub.identify, "look", lambda b: (status, best, "Sure because its ISBN matched.", [best], _clues(), []))
    monkeypatch.setattr(hub.bookfix, "ready", lambda: None)
    applied = []

    def apply(book_id, title, authors, *rest):
        applied.append((book_id, title, list(authors)))
        return {"title": title, "author": " & ".join(authors), "key": book["key"]}

    monkeypatch.setattr(hub.bookfix, "apply", apply)
    return hub, book, applied


def test_a_clear_case_is_put_right_by_itself_and_can_be_undone(app, monkeypatch):  # noqa: F811
    hub, book, applied = _set_up(app, monkeypatch, identify.CLEAR)
    assert hub.identify.run() == [(book["id"], "fixed")]
    assert applied == [(book["id"], "The Long Way Home", ["Ann Writer"])]
    assert hub.identify.pending() == []  # looked at once, not again every minute
    change = hub.identify.changes()[0]
    assert change["before"]["title"] == book["title"] and change["after"]["title"] == "The Long Way Home"
    hub.identify.undo(change["id"])
    assert applied[-1] == (book["id"], book["title"], list(book["authors"]))
    assert hub.identify.changes()[0]["undone"] and hub.identify.summary()["counts"]["undone"] == 1
    assert str(book["id"]) in hub.bookfix._ok()  # a person said the old details stand


def test_with_the_switch_off_a_clear_case_waits_in_the_review_queue(app, monkeypatch):  # noqa: F811
    hub, book, applied = _set_up(app, monkeypatch, identify.CLEAR)
    hub.identify.set_auto(False)
    assert hub.identify.run() == [(book["id"], identify.DOUBT)] and applied == []
    assert hub.identify.doubts()[book["id"]]["title"] == "The Long Way Home"


def test_a_doubtful_match_is_filled_in_on_the_review_queue(app, monkeypatch):  # noqa: F811
    hub, book, applied = _set_up(app, monkeypatch, identify.DOUBT)
    hub.identify.run()
    assert applied == []
    monkeypatch.undo()  # the real kind index again, so the book is sorted the usual way
    hub.sort_new_books(read_pages=False)
    entry = hub.review.item_for(book["id"])
    assert entry["suggest"]["kind"] != "Tabletop"  # game books are the sorter's; this one must be a plain book
    assert entry["suggest"]["title"] == "The Long Way Home" and entry["suggest"]["authors"] == ["Ann Writer"]
    assert any("ISBN" in line for line in entry["why"])
    assert any(change["field"] == "title" for change in entry["changes"])


def test_the_switch_and_undo_are_for_admins(app, client):  # noqa: F811
    login(client, "Nan")
    assert client.get("/api/admin/identify").status_code == 403
    assert client.post("/api/admin/identify", json={"auto": False}).status_code == 403
    client.post("/api/logout")
    login(client, "You")
    data = client.get("/api/admin/identify").get_json()
    assert data["auto"] is True and data["changes"] == []
    assert client.post("/api/admin/identify", json={"auto": "no"}).status_code == 400
    assert client.post("/api/admin/identify", json={"auto": False}).get_json()["auto"] is False
    assert client.get("/api/admin/identify").get_json()["auto"] is False
    assert client.post("/api/admin/identify/undo/999").status_code == 404


def test_crossref_answers_become_candidates(monkeypatch):
    def fake(url, params, headers=None):
        if not url.endswith("/10.1234/abcd"):
            return {}  # Open Library did not know it
        return {"message": {"title": ["Deep Things"], "author": [{"given": "Ada", "family": "Lovelace"}], "publisher": "ACM",
                            "issued": {"date-parts": [[2001, 3]]}, "ISBN": ["9780000000002"]}}

    monkeypatch.setattr(catalogues, "_get", fake)
    config = type("C", (), {"google_books_key": ""})()
    found, errors = identify.gather(config, _clues([("Deep Things", "", "the cover")], dois=[("10.1234/abcd", "inside the PDF")]))
    assert errors == [] and found[0]["title"] == "Deep Things" and found[0]["authors"] == ["Ada Lovelace"]
    assert identify.verdict(BOOK, found)[0] == identify.CLEAR
