"""Series followed chapter by chapter: twice-listed chapters, comic file parts and progress in them."""
import io
import json
import re
import zipfile
from pathlib import Path

from test_app import app, client, series_server  # noqa: F401

from bookhub import chapterfile, joined, series


def row(cid, name, number, order, read=False, done=True, uploaded="0", group=""):
    return {"id": cid, "name": name, "chapterNumber": number, "sourceOrder": order, "isRead": read,
            "isDownloaded": done, "uploadDate": uploaded, "scanlator": group, "lastPageRead": 0}


def chapter(cid, name, number, order):
    return {"id": cid, "name": name, "number": number, "order": order, "read": False, "downloaded": True, "group": "", "page": 0, "uploaded": 0}


def fake_files(monkeypatch):
    """Every chapter as a tiny CBZ of two pictures."""
    def chapter_file(cid):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            for page in (1, 2):
                z.writestr("%d.jpg" % page, b"c%d-p%d" % (cid, page))
        return buf.getvalue()

    class Answer:
        def __init__(self, data):
            self.data = data

        def iter_content(self, n):
            yield self.data

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(series.Series, "raw", lambda self, path, stream=False: Answer(
        chapter_file(int(re.match(r"/api/v1/chapter/(\d+)/download", path).group(1)))))


def test_plus_18_written_the_other_way_round_is_the_copy_kept():
    # sites write "(+18)" as often as "(18+)"
    plain = chapter(1, "Ch. 41 - Bonus chapter", 41.0, 1)
    spicy = dict(chapter(2, "Ch. 41 - Bonus chapter (+18)", 41.0, 2), uploaded=0)
    plain["uploaded"] = 999  # newer, so only the 18+ mark can win it
    kept, dropped = series.unique([plain, spicy])
    assert [c["id"] for c in kept] == [2] and [c["id"] for c in dropped] == [1]


def test_continue_skips_a_hidden_second_copy_and_the_copy_is_marked_read(app, client, series_server):
    # The real case: Ch. 2 is listed twice, the copy kept is read, the hidden one never is. The series server's
    # "first unread" is the hidden copy, so "Continue" offered Ch. 2 to someone 98 chapters further on.
    series_server.chapters[7] = [row(71, "Ch. 1", 1.0, 1, read=True), row(72, "Ch. 2", 2.0, 2, uploaded="1"),
                                 row(73, "Ch. 2", 2.0, 3, read=True, uploaded="5"), row(74, "Ch. 3", 3.0, 4)]
    got = client.get("/api/series/7").get_json()
    assert [c["id"] for c in got["chapters_list"]] == [71, 73, 74] and got["doubles"] == 1
    assert got["next"] == {"id": 74, "name": "Ch. 3", "order": 4}
    assert (got["read"], got["unread"]) == (2, 1)
    # Putting the doubles away also marks the hidden copy read, so the shelf's unread count stops counting it.
    with app.app_context():
        app.hub.series.drop_doubles(app.hub.series.detail(7))
    assert series_server.chapters[7][1]["isRead"] is True
    shelf = client.get("/api/series").get_json()["series"][0]
    assert shelf["unread"] == 1 and shelf["next"]["id"] == 74


def test_a_finished_series_with_everything_read_offers_nothing_to_continue(client, series_server):
    series_server.status[7] = "COMPLETED"
    series_server.chapters[7] = [row(72, "Ch. 1", 1.0, 1), row(71, "Ch. 1", 1.0, 2, read=True),
                                 row(73, "Ch. 2", 2.0, 3, read=True)]
    got = client.get("/api/series/7").get_json()
    assert got["status"] == "Finished" and got["unread"] == 0 and got["next"] is None


def test_season_and_decimal_chapters_keep_reading_order_and_names():
    chapters = [chapter(1, "Ch. 46.1 - Bonus chapter (+18)", 46.1, 1), chapter(2, "Ch. 46.3 - Bonus chapter (+18)", 46.3, 2),
                chapter(3, "Ch. 54 - (Season 2 Premiere)", 54.0, 3), chapter(4, "Ch. 123 - S2-123", 123.0, 4),
                chapter(5, "Chapter 0.5", 0.5, 5)]
    kept, dropped = series.unique(chapters)
    assert [c["id"] for c in kept] == [1, 2, 3, 4, 5] and not dropped
    assert [chapterfile.chapter_name(c) for c in kept] == [
        "Chapter 46.1 · Bonus chapter (+18)", "Chapter 46.3 · Bonus chapter (+18)", "Chapter 54 · (Season 2 Premiere)",
        "Chapter 123 · S2-123", "Chapter 0.5"]


def test_chapters_with_the_same_name_stay_apart_in_a_part(app, client, series_server, monkeypatch):
    # Two "Notice" posts with no chapter number: both kept, and each must keep its own place in the comic file,
    # or progress in the first is put down to the second.
    fake_files(monkeypatch)
    series_server.chapters[7] = [row(71, "Ch. 1", 1.0, 1), row(72, "Notice", -1.0, 2), row(73, "Ch. 2", 2.0, 3),
                                 row(74, "Notice", -1.0, 4)]
    with app.app_context():
        info = app.hub.series.detail(7)
        chapterfile.keep_series(app.hub, info)
        path = chapterfile.ready_file(app.hub, info, 1)[0]
        assert path
        _, toc, pages = joined.describe(path)
        assert [label for label, _ in toc] == ["Chapter 1", "Notice", "Chapter 2", "Notice (2)"] and pages == 8
        names = sorted(n for n in zipfile.ZipFile(path).namelist() if n.endswith(".jpg"))
        assert len({n.split("/")[0] for n in names}) == 4
        record = app.hub.con().execute("SELECT parts FROM joined_books WHERE path=?", (str(path),)).fetchone()
        assert [p["key"] for p in json.loads(record["parts"])] == [71, 72, 73, 74]
        found = joined.find(app.hub, app.hub.con().execute("SELECT hash FROM joined_books WHERE path=?", (str(path),)).fetchone()["hash"])
        # half way through the first Notice (page 3 of 8): chapter 1 is finished, the second Notice is not
        states = joined.where(found, 3 / 8 * 100)
        assert [(key, state) for key, state, _ in states] == [(71, "finished"), (72, "reading"), (73, None), (74, None)]


def test_while_the_queue_is_busy_the_shorter_copy_still_downloads_from_the_page(app, client, series_server, monkeypatch):
    fake_files(monkeypatch)
    series_server.chapters[7] = [row(71, "Ch. 1", 1.0, 1), row(72, "Ch. 2", 2.0, 2), row(73, "Ch. 3", 3.0, 3, done=False)]
    with app.app_context():
        chapterfile.keep_all(app.hub)
    first = client.get("/api/series/7").get_json()["files"][0]
    assert first["state"] == "ready" and first["older"] is False
    # chapter 3 arrives while hundreds more are queued: the part isn't made again yet...
    series_server.queued = [99]
    series_server.chapters[7][2]["isDownloaded"] = True
    with app.app_context():
        chapterfile.keep_all(app.hub)
    now = client.get("/api/series/7").get_json()["files"][0]
    assert now["state"] == "waiting" and now["chapters"] == 3
    # ...but the page knows the shorter copy is there to download (the button stays), as the download itself does
    assert now["older"] is True and now["size"] > 0
    got = client.get("/api/series/7/file/1")
    assert got.status_code == 200 and sum(n.endswith(".jpg") for n in zipfile.ZipFile(io.BytesIO(got.data)).namelist()) == 4


def test_titles_from_different_sites_are_matched_without_their_labels():
    assert series.same_series("Please Divorce Me (Official)") == series.same_series("Please Divorce Me")
    assert series.same_series("I Failed to Oust the Villain! [Official][Uncensored]") == series.same_series("I Failed To Oust The Villain")
    assert series.same_series("I Only Need the Duke’s Child") == series.same_series("I Only Need the Duke's Child")
    # a name in brackets can be part of the title: two different series called "You Are My World" stay apart
    assert series.same_series("You Are My World (Bboong)") != series.same_series("You Are My World")


def test_an_old_copy_from_a_site_no_longer_followed_is_set_aside_and_its_read_marks_kept(app, series_server, monkeypatch):
    # The real case: re-found at a site without missing pages and followed there; the old copy's chapters
    # were still on disk, so Komga showed the series twice.
    fake_files(monkeypatch)
    series_server.titles[9] = "Tower Climber [Official]"
    series_server.chapters[9] = [row(90 + n, "Ch. %d" % n, float(n), n, read=n < 3) for n in (1, 2, 3)]
    with app.app_context():
        retire, both = app.hub.series.old_sources()
        assert [(followed["id"], [old["id"] for old in olds]) for followed, olds in retire] == [(7, [9])] and both == []
        done = app.hub.series.retire_old_sources(app.hub.config.attention_dir)
    assert done == [{"title": "Tower Climber [Official]", "source": "Reading Site (EN)", "chapters": 3}]
    aside = Path(app.hub.config.attention_dir) / "_old-sources" / "Tower Climber [Official] (Reading Site (EN))"
    assert sorted(path.name for path in aside.iterdir()) == ["0001 Ch. 1.cbz", "0002 Ch. 2.cbz", "0003 Ch. 3.cbz"]
    assert not any(c["isDownloaded"] for c in series_server.chapters[9])
    # Chapter 2 was read on the old site: now it is read on the followed one too. The followed copy keeps its files.
    assert [c["isRead"] for c in series_server.chapters[7]] == [True, True, False, False]
    assert [c["isDownloaded"] for c in series_server.chapters[7]] == [True, True, False, False]


def test_a_series_followed_at_two_sites_is_left_alone(app, series_server, monkeypatch):
    fake_files(monkeypatch)
    series_server.titles[9] = "Tower Climber (Official)"
    series_server.chapters[9] = [row(91, "Ch. 1", 1.0, 1)]
    series_server.following.add(9)
    with app.app_context():
        assert app.hub.series.old_sources() == ([], ["Tower Climber"])
        assert app.hub.series.retire_old_sources(app.hub.config.attention_dir) == []
    assert series_server.chapters[9][0]["isDownloaded"] is True


def test_a_chapter_that_could_not_be_copied_stays_on_the_series_server(app, series_server, monkeypatch):
    series_server.titles[9] = "Tower Climber"
    series_server.chapters[9] = [row(91, "Ch. 1", 1.0, 1)]

    def refuse(self, path, stream=False):
        raise series.SeriesError("The series server does not have that (404).")
    monkeypatch.setattr(series.Series, "raw", refuse)
    with app.app_context():
        assert app.hub.series.retire_old_sources(app.hub.config.attention_dir) == []
    assert series_server.chapters[9][0]["isDownloaded"] is True
