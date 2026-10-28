"""The book library: reading the Calibre library, copies, spare-copy removal, covers and fixing details."""
import io
import sqlite3
import zipfile
from types import SimpleNamespace

import pytest
from PIL import Image

from test_app import app, client  # noqa: F401
from bookhub import bookfix, calibre, covers, db, duplicates


def test_document_versions_are_not_volumes():
    v = calibre.volume_from_title
    assert v("Character Sheet v1.1") is None
    assert v("Guide to Pathfinder Society Organized Play v4.3") is None
    assert v("Multiclass Archetypes v1.0") is None
    # real volumes still are
    assert v("Cherry Magic! v01 (2020) (Digital)")["label"] == "Volume 1"
    assert v("Art for Storytellers Vol.2")["label"] == "Volume 2"
    assert v("Some Manga v10.5 (2020)", comic=True)["label"] == "Volume 10.5"


def test_a_locked_or_damaged_epub_gets_no_page_estimate_instead_of_a_crash(tmp_path):
    broken = tmp_path / "broken.epub"
    with zipfile.ZipFile(broken, "w", zipfile.ZIP_DEFLATED) as epub:
        epub.writestr("text/ch1.xhtml", "<p>%s</p>" % ("words " * 500))
    with zipfile.ZipFile(broken) as epub:
        info = epub.infolist()[0]
    start = info.header_offset + 30 + len(info.filename.encode())
    data = bytearray(broken.read_bytes())
    data[start:start + 8] = b"\xff" * 8  # a deflate block type that doesn't exist: zlib.error deep inside the read
    broken.write_bytes(bytes(data))
    assert calibre.estimate_pages(broken) is None


def test_books_in_other_alphabets_do_not_share_one_key():
    first = db.make_key("Вампиры Маскарад. Пятая редакция. Книга правил", "Unknown")
    second = db.make_key("Заполняемый лист персонажа", "Unknown")
    assert first != second and not first.startswith("ta:|")
    assert db.series_key("Вампиры") != db.series_key("Оборотни")
    # keys of everything else stay exactly as they were
    assert db.make_key("Dracula", "Bram Stoker") == "ta:dracula|bram-stoker"
    assert db.make_key("Café Story", "Anon") == "ta:cafe-story|anon"
    assert db.series_key("Star Trek (Gold Key)") == "series:star-trek-gold-key"


def _entry(book_id, path, **extra):
    book = {"id": book_id, "title": "Same Book", "author": "A. Writer", "key": "ta:same-book|a-writer", "has_cover": True,
            "own_formats": {"EPUB": path}, "sizes": {"EPUB": 100}, "synopsis": "", "raw_tags": [], "series": "",
            "own_series": "", "publisher": "", "isbn": "", "pubdate": "", "added": "2026-01-01T00:00:00", "series_index": 1}
    book.update(extra)
    return book


@pytest.fixture()
def judge(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    for name in ("a/x.epub", "b/x.epub"):
        (tmp_path / name).write_bytes(b"x" * 100)
    hub = SimpleNamespace(config=SimpleNamespace(library_dir=str(tmp_path)))
    return duplicates.Duplicates(hub)


def test_identical_spares_hand_over_their_details_first(judge):
    keep, spare = _entry(1, "a/x.epub"), _entry(2, "b/x.epub", synopsis="A story someone typed in.", raw_tags=["Horror"])
    plan = judge.plan({"key": keep["key"], "choice": None, "shown": [keep], "books": [keep, spare]})
    assert plan["cls"] == duplicates.IDENTICAL and plan["keep"] == 1
    assert {(step["field"], step["from"]) for step in plan["copy"]} == {("synopsis", 2), ("tags", 2)}
    # an ISBN can't be copied across, so a person decides
    keep, spare = _entry(1, "a/x.epub"), _entry(2, "b/x.epub", isbn="9780141439846")
    plan = judge.plan({"key": keep["key"], "choice": None, "shown": [keep], "books": [keep, spare]})
    assert plan["cls"] == duplicates.REVIEW and plan["lost"] == ["ISBN"]


def test_a_series_guessed_from_the_name_is_not_a_detail_to_copy():
    guessed = _entry(1, "a/x.epub", series="Foo", own_series="")
    assert duplicates.details(guessed)["series"] is False
    assert duplicates.details(_entry(1, "a/x.epub", series="Foo", own_series="Foo"))["series"] is True


def test_the_library_keeps_its_own_series_apart_from_a_guessed_one(app):  # noqa: F811
    for book in app.hub.library.all_books():
        assert "own_series" in book
        if book["part"]:
            assert book["own_series"] == ""


def _sideways_jpeg():
    picture = Image.new("RGB", (200, 300), "red")
    exif = picture.getexif()
    exif[0x0112] = 6  # "turn me 90 degrees", as phones write it
    out = io.BytesIO()
    picture.save(out, "JPEG", exif=exif.tobytes())
    return out.getvalue()


def test_a_sideways_phone_photo_is_turned_the_right_way_up():
    made = Image.open(io.BytesIO(covers._jpeg(_sideways_jpeg())))
    assert made.size == (300, 200)


def test_fixing_details_stops_cleanly_when_the_backup_fails(app, monkeypatch):  # noqa: F811
    hub = app.hub
    monkeypatch.setattr(hub.config, "cwa_api_url", "http://cwa", raising=False)
    monkeypatch.setattr(hub.config, "cwa_user", "admin", raising=False)
    monkeypatch.setattr(hub.config, "cwa_pass", "pw", raising=False)

    def no_backup():
        raise sqlite3.OperationalError("disk I/O error")

    changed = []
    monkeypatch.setattr(hub.duplicates, "backup", no_backup)
    monkeypatch.setattr(hub.manager, "set_field", lambda *args: changed.append(args))
    book = hub.library.books()[0]
    with pytest.raises(bookfix.FixError, match="nothing was changed"):
        hub.bookfix.apply(book["id"], "Real Title", ["Real Author"])
    assert changed == []
