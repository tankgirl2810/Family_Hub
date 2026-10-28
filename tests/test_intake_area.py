"""Getting books in: the inbox, downloads, book search and swapping stuck downloads."""
import shutil
import zipfile
from pathlib import Path

from test_app import FakeResponse, app, client, login  # noqa: F401


def test_a_comic_archive_with_a_release_note_is_still_a_comic(app, tmp_path):
    # the note is a .txt, which the inbox counts as a book, but 20 pages and a note is a comic
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    with zipfile.ZipFile(inbox / "Saga 01.zip", "w") as z:
        for n in range(20):
            z.writestr("page%02d.jpg" % n, b"jpg")
        z.writestr("readme.txt", b"scanned by someone")
    with app.app_context():
        result = app.hub.intake.tidy_inbox(min_age=0)
    assert result["delivered"] == ["Saga 01.cbz"]
    assert {p.name for p in (tmp_path / "ingest").iterdir()} == {"Saga 01.cbz"}


def test_an_archive_with_an_epub_and_a_kindle_copy_brings_in_one_book(app, tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    with zipfile.ZipFile(inbox / "Emma bundle.zip", "w") as z:
        z.writestr("Emma.epub", b"PK emma")
        z.writestr("Emma.mobi", b"mobi emma")
        z.writestr("Other.azw3", b"azw3 other")
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    shutil.copyfile(inbox / "Emma bundle.zip", downloads / "release.zip")
    with app.app_context():
        assert sorted(app.hub.intake.tidy_inbox(min_age=0)["delivered"]) == ["Emma.epub", "Other.azw3"]
        assert sorted(app.hub.import_downloads(min_age=0)) == ["Emma (2).epub", "Other (2).azw3"]


def test_a_book_in_an_archive_the_library_already_has_is_not_added_twice(app, tmp_path):
    book = next(b for b in app.hub.library.books() if "EPUB" in b["formats"])
    original = Path(app.hub.library.file_path(book, "EPUB"))
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    with zipfile.ZipFile(downloads / "again.zip", "w") as z:
        z.write(original, "Again.epub")
        z.writestr("New.epub", b"PK new")
    with app.app_context():
        assert app.hub.import_downloads(min_age=0) == ["New.epub"]
        log = app.hub.con().execute("SELECT action, reason FROM intake_log WHERE name='Again.epub'").fetchone()
    assert tuple(log) == ("skipped", "Already in the library: the very same file")


def test_a_book_that_cannot_be_handed_over_stays_in_the_inbox(app, tmp_path, monkeypatch):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "Stuck.epub").write_bytes(b"PK stuck")
    real_replace = Path.replace

    def refuse(self, target):
        if self.name.endswith(".part"):
            raise OSError("disk full")
        return real_replace(self, target)
    monkeypatch.setattr(Path, "replace", refuse)
    with app.app_context():
        assert app.hub.intake.tidy_inbox(min_age=0)["delivered"] == []
    # not left hidden as a .part file the library manager never looks at
    assert (inbox / "Stuck.epub").read_bytes() == b"PK stuck"
    assert list((tmp_path / "ingest").iterdir()) == []


def _search_as_admin(app, client, monkeypatch, releases):
    from bookhub import integrations
    cfg = app.hub.config
    cfg.prowlarr_url, cfg.prowlarr_api_key = "http://prowlarr:9696", "k"
    monkeypatch.setattr(integrations, "prowlarr_search", lambda config, query, limit=60: [dict(r) for r in releases])
    login(client, "You")


def test_search_ranks_by_every_typed_word(app, client, monkeypatch):
    # four plain words: the extra spelling asked for drops the first word, but the ranking must not
    releases = [{"guid": "b", "title": "Potter Fire", "format": "PDF", "seeders": 50},
                {"guid": "a", "title": "Harry Potter Collection", "format": "EPUB", "seeders": 1}]
    _search_as_admin(app, client, monkeypatch, releases)
    got = client.get("/api/discover/search?q=Harry Potter Goblet Fire").get_json()["results"]
    assert [r["guid"] for r in got] == ["a", "b"]  # both share two words; the EPUB first


def test_search_marks_books_already_in_the_library_by_whole_words(app, client, monkeypatch):
    releases = [{"guid": "1", "title": "Emmanuel Carrere - Limonov", "format": "EPUB", "seeders": 1},
                {"guid": "2", "title": "Jane Austen - Emma (1815) EPUB", "format": "EPUB", "seeders": 1},
                {"guid": "3", "title": "Persuasion - Jane Austen", "format": "EPUB", "seeders": 1}]
    _search_as_admin(app, client, monkeypatch, releases)
    got = {r["guid"]: r["library_id"] for r in client.get("/api/discover/search?q=whatever").get_json()["results"]}
    ids = {b["title"]: b["id"] for b in app.hub.library.books()}
    assert got == {"1": None, "2": ids["Emma"], "3": ids["Persuasion"]}


def test_a_swap_prowlarr_will_not_send_is_not_retried_every_minute(app, monkeypatch):
    from bookhub import bookswap, integrations
    torrent = {"hash": "abc", "name": "Emma - Jane Austen (2003) [EPUB]", "progress": 0.4, "num_seeds": 3, "dlspeed": 0}
    looks = []

    class Session:
        def get(self, url, params=None, timeout=0):
            return FakeResponse(200, [dict(torrent)])

        def post(self, url, data=None, timeout=0):
            return FakeResponse(200, {})

    def refuse(config, guid, indexer):
        raise integrations.IntegrationError("no download client")
    cfg = app.hub.config
    cfg.qbit_url, cfg.prowlarr_url, cfg.prowlarr_api_key = "http://gluetun:8080", "http://prowlarr:9696", "k"
    monkeypatch.setattr(integrations, "qbit_session", lambda config: Session())
    monkeypatch.setattr(integrations, "prowlarr_search", lambda config, q: looks.append(q) or [
        {"guid": "good", "indexer_id": 2, "indexer": "B", "title": "Jane Austen - Emma.epub", "seeders": 4, "protocol": "torrent", "format": "EPUB"}])
    monkeypatch.setattr(integrations, "prowlarr_grab", refuse)
    minute = lambda m: 10000 + m * 60  # noqa: E731
    with app.app_context():
        bookswap.run(app.hub, now=minute(0))
        torrent.update(num_seeds=0)
        for m in range(1, 45):
            bookswap.run(app.hub, now=minute(m))
    assert len(looks) == 1  # looked once at half an hour, then waits like when no copy was found


def test_release_tags_in_brackets_do_not_pick_the_comic_run():
    from bookhub import catalogues
    series = catalogues.comic_series
    assert series("Watchmen 01 (of 12)") == ("Watchmen", "")
    assert series("Saga 001 (Digital) (2012)") == ("Saga", "2012")
    assert series("Star Trek (Gold Key) 07") == ("Star Trek", "Gold Key")
