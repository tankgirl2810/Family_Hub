"""Marking where a PDF names its title and author: page pictures, the text in a drawn box, and rules that read
the same places in the next book like it."""
from types import SimpleNamespace

from test_app import app, client, login  # noqa: F401
from bookhub import hub as hub_module, peek, review


def _pdf(path, lines, rotate=0):
    """A one-page 600x800 PDF with each (x, y, size, text) written where it says (y from the bottom)."""
    stream = "".join("BT /F1 %d Tf %d %d Td (%s) Tj ET\n" % (size, x, y, text) for x, y, size, text in lines).encode()
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>",
               b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
               b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 600 800] /Rotate %d /Contents 4 0 R "
               b"/Resources << /Font << /F1 5 0 R >> >> >>" % rotate,
               b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"endstream",
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for n, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % n + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    path.write_bytes(bytes(out))
    return path


COVER = [(60, 700, 36, "DUNGEON DELVERS"), (60, 650, 18, "Core Rulebook"), (60, 100, 12, "Written by Ana Ruiz & Tom Ng")]
TITLE_BOX = [0.05, 0.08, 0.95, 0.15]     # round y=700 (from the top: 100/800 = 0.125)
AUTHOR_BOX = [0.05, 0.85, 0.95, 0.9]     # round y=100 (from the top: 700/800 = 0.875)


def test_the_text_inside_a_box_is_only_what_is_in_the_box(tmp_path):
    path = _pdf(tmp_path / "a.pdf", COVER)
    assert peek.pdf_box_text(path, 0, TITLE_BOX) == "DUNGEON DELVERS"
    assert peek.pdf_box_text(path, 0, AUTHOR_BOX) == "Written by Ana Ruiz & Tom Ng"
    assert peek.pdf_box_text(path, 0, [0.5, 0.4, 0.9, 0.6]) == ""  # an empty part of the page


def test_a_turned_page_is_read_where_it_shows(tmp_path):
    # turned a quarter clockwise, the bottom line (the credits) shows down the left side
    path = _pdf(tmp_path / "turned.pdf", COVER, rotate=90)
    assert "Ana Ruiz" in peek.pdf_box_text(path, 0, [0.0, 0.0, 0.2, 1.0])
    assert "DUNGEON" not in peek.pdf_box_text(path, 0, [0.0, 0.0, 0.2, 1.0])


def test_a_page_picture(tmp_path):
    path = _pdf(tmp_path / "a.pdf", COVER)
    data, total = peek.pdf_page_picture(path, 0, width=300)
    assert data.startswith(b"\x89PNG") and total == 1
    try:
        peek.pdf_page_picture(path, 4)
        raise AssertionError("page 5 of a one-page book")
    except peek.PeekError:
        pass


def test_box_text_as_a_title_and_as_authors():
    assert peek.as_title("DUNGEON DELVERS") == "Dungeon Delvers"
    assert peek.as_title("The Lost Mine") == "The Lost Mine"
    assert peek.as_authors("Written by Ana Ruiz, Tom Ng and Li Wei") == ["Ana Ruiz", "Tom Ng", "Li Wei"]
    assert peek.as_authors("Authors: ANA RUIZ & Ana Ruiz") == ["Ana Ruiz"]


def test_odd_marks_from_the_browser_are_dropped():
    assert peek.clean_box([0.9, 0.5, 0.1, 0.2]) == [0.1, 0.2, 0.9, 0.5]  # dragged up and left
    assert peek.clean_box([0.1, 0.1, 0.1, 0.1]) is None
    assert peek.clean_box("nope") is None
    assert peek.clean_labels({"title": {"page": 0, "box": TITLE_BOX}, "series": {"page": 0, "box": TITLE_BOX},
                              "authors": {"page": "x", "box": AUTHOR_BOX}}) == {"title": {"page": 0, "box": TITLE_BOX}}


def test_confirmed_marks_are_kept_on_the_rule_and_said_in_plain_words():
    fields = review.Review.clean_fields({"title": "Dungeon Delvers", "authors": "Ana Ruiz", "kind": "Tabletop",
                                         "labels": {"title": {"page": 0, "box": TITLE_BOX}, "authors": {"page": 1, "box": AUTHOR_BOX}}})
    assert set(fields["labels"]) == {"title", "authors"}
    words = review.rule_words({"folder": "Delvers", "pattern": "", "sets": {"kind": "Tabletop", "labels": fields["labels"]}})
    assert "title and author read from the places marked on page 1 and 2" in words


def test_a_rule_with_marks_reads_the_next_book_like_it(tmp_path):
    other = _pdf(tmp_path / "b.pdf", [(60, 700, 36, "DEEPER DELVES"), (60, 100, 12, "Written by Li Wei")])
    rules = [{"id": 1, "sets": {"kind": "Tabletop"}},
             {"id": 2, "sets": {"labels": {"title": {"page": 0, "box": TITLE_BOX}, "authors": {"page": 0, "box": AUTHOR_BOX}}}}]
    fake = SimpleNamespace(library=SimpleNamespace(file_path=lambda book, fmt: other))
    verdict = {"learned": [1, 2], "title": "deeper_delves_v2", "sources": {}}
    hub_module.Hub._read_marked_places(fake, {"formats": {"PDF": "b.pdf"}}, verdict, rules)
    assert verdict["title"] == "Deeper Delves" and verdict["marked_authors"] == ["Li Wei"]
    assert verdict["sources"] == {"title": "marked", "authors": "marked"}
    # a scanned book (nothing in the boxes) keeps what the sorter had
    scanned = _pdf(tmp_path / "c.pdf", [])
    fake.library.file_path = lambda book, fmt: scanned
    verdict = {"learned": [2], "title": "from the name", "sources": {}}
    hub_module.Hub._read_marked_places(fake, {"formats": {"PDF": "c.pdf"}}, verdict, rules)
    assert verdict == {"learned": [2], "title": "from the name", "sources": {}}


def test_marking_on_a_real_library_pdf_through_the_api(app, client):
    login(client, "You")
    pdf = next(b for b in client.get("/api/books").get_json()["books"] if "PDF" in b["formats"])
    r = client.get("/api/admin/books/%d/peek/page/0.png" % pdf["id"])
    assert r.status_code == 200 and r.mimetype == "image/png"
    answer = client.post("/api/admin/books/%d/peek/box" % pdf["id"], json={"page": 0, "box": [0, 0, 1, 1]}).get_json()
    assert set(answer) >= {"text", "title", "authors", "page", "box"} and answer["box"] == [0, 0, 1, 1]
    assert client.post("/api/admin/books/%d/peek/box" % pdf["id"], json={"page": 0, "box": [0.2, 0.2, 0.2, 0.2]}).status_code == 400
    login(client, "Nan")
    assert client.get("/api/admin/books/%d/peek/page/0.png" % pdf["id"]).status_code == 403
    assert client.post("/api/admin/books/%d/peek/box" % pdf["id"], json={"page": 0, "box": [0, 0, 1, 1]}).status_code == 403
