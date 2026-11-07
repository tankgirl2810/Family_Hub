"""Looking inside a book next to its details: first pages, the Unsorted tab, and Edit details for any book."""
import io
import zipfile

from test_app import app, client, login  # noqa: F401
from bookhub import kinds, peek


def _first(client, fmt):
    return next(b for b in client.get("/api/books").get_json()["books"] if fmt in b["formats"])


def test_a_pdf_shows_its_first_pages_and_an_epub_its_opening(app, client):
    login(client, "You")
    pdf = _first(client, "PDF")
    info = client.get("/api/admin/books/%d/peek" % pdf["id"]).get_json()
    assert info["kind"] == "pdf" and info["pages"] >= 1
    r = client.get("/api/admin/books/%d/peek.pdf?pages=3" % pdf["id"])
    assert r.status_code == 200 and r.mimetype == "application/pdf" and r.data.startswith(b"%PDF")
    assert r.headers["Content-Disposition"] == "inline"
    epub = _first(client, "EPUB")
    info = client.get("/api/admin/books/%d/peek" % epub["id"]).get_json()
    assert info["kind"] == "text" and info["paragraphs"]
    assert not any("<" in line for line in info["paragraphs"])  # words only, never the book's own markup
    assert client.get("/api/admin/books/99999/peek").status_code == 404


def test_only_an_admin_looks_inside(app, client):
    login(client, "Nan")
    book = _first(client, "EPUB")
    assert client.get("/api/admin/books/%d/peek" % book["id"]).status_code == 403
    assert client.get("/api/admin/books/%d/peek.pdf" % book["id"]).status_code == 403
    assert client.get("/api/admin/review/book/%d" % book["id"]).status_code == 403


def test_comic_pictures_in_page_order(tmp_path):
    path = tmp_path / "x.cbz"
    with zipfile.ZipFile(path, "w") as archive:
        for name in ("page10.jpg", "page2.jpg", "page1.jpg", "notes.txt", ".hidden.jpg"):
            archive.writestr(name, b"x")
    assert peek.comic_pictures(path) == ["page1.jpg", "page2.jpg", "page10.jpg"]
    assert peek.comic_picture(path, 2) == (b"x", "image/jpeg")
    assert peek.comic_pictures(tmp_path / "missing.cbz") == []


def test_a_pdf_preview_never_sends_more_than_it_should(tmp_path):
    from pypdf import PdfWriter
    writer = PdfWriter()
    for _ in range(40):
        writer.add_blank_page(width=100, height=100)
    out = io.BytesIO()
    writer.write(out)
    path = tmp_path / "big.pdf"
    path.write_bytes(out.getvalue())
    from pypdf import PdfReader
    data, total = peek.pdf_pages(path, 3)
    assert total == 40 and len(PdfReader(io.BytesIO(data)).pages) == 3
    data, _ = peek.pdf_pages(path, 500)
    assert len(PdfReader(io.BytesIO(data)).pages) == peek.MAX_PAGES


def test_unsorted_books_wait_in_their_own_tab_and_any_book_opens_in_the_editor(app, client):
    hub = app.hub
    login(client, "You")
    book = _first(client, "EPUB")
    with app.app_context():
        hub.set_kind(book["key"], kinds.UNSORTED, source="auto")
        hub.con().commit()
    answer = client.get("/api/admin/review?tab=unsorted&limit=200").get_json()
    assert answer["tab"] == "unsorted"
    assert "unsorted" in [tab["id"] for tab in answer["tabs"]]
    assert book["id"] in [item["book_id"] for item in answer["items"]]
    # a book the sorter is happy with still opens for Edit details
    other = next(b for b in client.get("/api/books").get_json()["books"] if b["id"] != book["id"])
    single = client.get("/api/admin/review/book/%d" % other["id"]).get_json()
    assert single["item"]["book_id"] == other["id"] and single["item"]["batch_size"] == 0
    assert single["kinds"] and single["roles"]
    assert client.get("/api/admin/review/book/99999").status_code == 404
