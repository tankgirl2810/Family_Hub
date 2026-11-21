"""Series followed chapter by chapter: twice-listed chapters, comic file parts and progress in them."""
import io
import json
import re
import zipfile
from pathlib import Path

from test_app import app, client, login, series_server  # noqa: F401

from bookhub import chapterfile, db, joined, series


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


# ------------------------------------------------- chapters in the download queue

def test_chapters_downloading_waiting_and_failed_show_in_the_download_queue(client, series_server):
    series_server.chapters[8] = [row(81, "Ch. 1 - The kettle", 1.0, 1, done=False)]
    series_server.queued = [74, 73, 81, 72]
    series_server.downloads = {74: {"state": "ERROR", "progress": 0.12244898, "tries": 3},
                               73: {"state": "DOWNLOADING", "progress": 0.64, "tries": 1},
                               72: {"state": "FINISHED", "progress": 1.0}}  # done: about to leave the queue
    got = client.get("/api/series/queue").get_json()
    assert got["configured"] and got["running"] and got["more_waiting"] == 0
    assert got["counts"] == {"downloading": 1, "waiting": 1, "failed": 1}
    assert got["chapters"] == [
        {"id": 74, "series_id": 7, "series": "Tower Climber", "chapter": "Chapter 4", "state": "failed", "progress": 12, "tries": 3, "position": None},
        {"id": 73, "series_id": 7, "series": "Tower Climber", "chapter": "Chapter 3", "state": "downloading", "progress": 64, "tries": 1, "position": 1},
        {"id": 81, "series_id": 8, "series": "Quiet Tea Shop", "chapter": "Ch. 1 - The kettle", "state": "waiting", "progress": 0, "tries": 0, "position": 2}]
    # A stopped downloader is said, so chapters that will not move are not shown as on their way.
    series_server.downloader = "STOPPED"
    assert client.get("/api/series/queue").get_json()["running"] is False

    # Anyone who may read series sees the queue; guests and profiles with series switched off do not.
    nan = next(p for p in client.get("/api/state").get_json()["profiles"] if p["name"] == "Nan")
    client.post("/api/logout")
    login(client, "Nan")
    assert client.get("/api/series/queue").get_json()["counts"]["failed"] == 1
    client.post("/api/logout")
    login(client, "You")
    client.patch("/api/profiles/%d" % nan["id"], json={"can_series": False})
    client.post("/api/logout")
    login(client, "Nan")
    assert client.get("/api/series/queue").status_code == 403
    client.post("/api/logout")
    client.post("/api/login", json={"guest": True})
    assert client.get("/api/series/queue").status_code == 403


def test_a_long_queue_lists_the_next_in_line_and_counts_the_rest(app, series_server):
    # The real case: over a thousand chapters queued. Every failed one is listed; of the ones waiting, the next hundred.
    series_server.chapters[7] = [row(1000 + n, "Ch. %d" % n, float(n), n, done=False) for n in range(1, 161)]
    series_server.queued = [1000 + n for n in range(1, 161)]
    series_server.downloads = {1000 + n: {"state": "ERROR", "tries": 3} for n in range(1, 11)}
    series_server.downloads[1011] = {"state": "DOWNLOADING", "progress": 0.5}
    with app.app_context():
        got = app.hub.series.queue()
    assert got["counts"] == {"downloading": 1, "waiting": 149, "failed": 10} and got["more_waiting"] == 49
    assert [c["state"] for c in got["chapters"]].count("waiting") == series.WAITING_SHOWN and len(got["chapters"]) == 111
    assert got["chapters"][-1]["chapter"] == "Ch. 111" and got["chapters"][-1]["position"] == 101  # the downloading one is first in line


def test_the_download_queue_has_no_chapters_without_a_series_server(client):
    login(client, "You")
    assert client.get("/api/series/queue").get_json() == {"configured": False, "running": False, "chapters": [], "more_waiting": 0,
                                                          "counts": {"downloading": 0, "waiting": 0, "failed": 0}}


# ------------------------------------------------------------ deleting a series

def kv(app, pattern):
    with app.app_context():
        return sorted(r["key"] for r in app.hub.con().execute("SELECT key FROM kv WHERE key LIKE ?", (pattern,)))


def test_deleting_a_series_takes_everything_of_it_away_and_it_stays_gone(app, client, series_server, monkeypatch):
    fake_files(monkeypatch)
    me = client.get("/api/state").get_json()["me"]["id"]
    # Everything a followed series gathers: two series in My books, comic files, an app's place in one, queued
    # chapters, and chapters of it set aside in needs-attention from an earlier go.
    assert client.post("/api/series/8/follow", json={"on": True}).get_json()["queued"] == 1
    for series_id in (7, 8):
        assert client.post("/api/series/%d/mine" % series_id, json={"on": True}).status_code == 200
    series_server.queued += [73, 74]
    with app.app_context():
        hub = app.hub
        chapterfile.keep_series(hub, hub.series.detail(7))
        made = list(chapterfile.folder(hub, 7).glob("*.cbz"))
        digest = hub.con().execute("SELECT hash FROM joined_books WHERE kind='chapters' AND name='7'").fetchone()["hash"]
        joined.apply(hub, me, joined.find(hub, digest), 40.0)
        for key in ("series:looked:7", "series:looked:8", "fetched:%d:chapters:7:1" % me, "fetched:%d:chapters:8:1" % me,
                    "fetched:%d:chapters:77:1" % me):
            db.kv_set(hub.con(), key, "1")
        aside = Path(hub.config.attention_dir) / "_old-sources" / "Tower Climber (Reading Site (EN))"
        aside.mkdir(parents=True)
        (aside / "0001 Chapter 1.cbz").write_bytes(b"old")
        other_aside = aside.with_name("Quiet Tea Shop (Reading Site (EN))")
        other_aside.mkdir()
    assert len(made) == 1 and kv(app, "joined:%") == ["joined:%d:%s" % (me, digest)]
    detail = client.get("/api/series/7").get_json()
    assert detail["can_delete"] is True and detail["set_aside"] == 1 and detail["also"] == []

    # Only an admin deletes, as with books and films: someone who may follow series, or a guest, cannot.
    nan = next(p for p in client.get("/api/state").get_json()["profiles"] if p["name"] == "Nan")
    client.patch("/api/profiles/%d" % nan["id"], json={"can_request": True})
    client.post("/api/logout")
    login(client, "Nan")
    assert client.get("/api/series/7").get_json()["can_delete"] is False
    refused = client.delete("/api/series/7")
    assert refused.status_code == 403 and "admin" in refused.get_json()["error"]
    client.post("/api/logout")
    client.post("/api/login", json={"guest": True})
    assert client.delete("/api/series/7").status_code == 403
    assert 7 in series_server.following and series_server.queued == [81, 73, 74] and made[0].is_file() and aside.is_dir()
    assert not any("delete" in q.lower() for q in series_server.asked)

    client.post("/api/logout")
    login(client, "You")
    gone = client.delete("/api/series/7").get_json()
    assert gone["ok"] and (gone["queued"], gone["chapters"], gone["set_aside"], gone["read_carried"]) == (2, 2, 1, [])
    assert gone["message"] == ("Deleted Tower Climber (Reading Site (EN)). It is no longer followed, 2 chapters came off the download "
                               "queue, 2 chapters kept on the server were deleted, 1 set aside in needs-attention went too.")
    # On the series server: not followed, nothing of it queued, no chapter file left. The other series is untouched.
    assert series_server.following == {8} and series_server.queued == [81]
    assert not any(c["isDownloaded"] for c in series_server.chapters[7])
    # In Family Hub: no comic file, no record of one, nobody's My books, nothing set aside. The other series keeps its own.
    with app.app_context():
        assert not chapterfile.folder(app.hub, 7).exists()
        assert app.hub.con().execute("SELECT COUNT(*) FROM joined_books WHERE kind='chapters'").fetchone()[0] == 0
        assert series.my_books(app.hub, me) == [8]
    assert kv(app, "joined:%") == [] and kv(app, "series:looked:%") == ["series:looked:8"]
    assert kv(app, "fetched:%") == ["fetched:%d:chapters:77:1" % me, "fetched:%d:chapters:8:1" % me]
    assert not aside.exists() and other_aside.is_dir()
    assert [s["title"] for s in client.get("/api/series").get_json()["series"]] == ["Quiet Tea Shop"]
    assert "Tower Climber" not in [b["title"] for b in client.get("/api/books?kinds=Graphic novel").get_json()["books"]]

    # And it does not come back: the jobs that look for chapters, make comic files and tidy old copies leave it alone.
    with app.app_context():
        hub = app.hub
        hub.series.rest_finished()
        chapterfile.keep_all(hub)
        assert hub.series.old_sources() == ([], [])
        assert hub.series.retire_old_sources(hub.config.attention_dir) == []
        assert not chapterfile.folder(hub, 7).exists()
    assert series_server.following == {8} and series_server.queued == [81]
    assert not any(c["isDownloaded"] for c in series_server.chapters[7])


def test_deleting_the_spare_copy_of_a_series_followed_twice_keeps_your_place(app, client, series_server):
    # The real case: one title followed from two sites. Each copy says where the other is; deleting the spare
    # marks what was read in it as read in the copy kept, and leaves that copy's chapters alone.
    series_server.titles[9] = "Tower Climber (Official)"
    series_server.sources[9] = ("66", "Other Site (EN)")
    series_server.chapters[9] = [row(90 + n, "Ch. %d" % n, float(n), n, read=n in (2, 3)) for n in (1, 2, 3)]
    series_server.following.add(9)
    with app.app_context():
        app.hub.series._shelf_kept = (0, None)
    shelf = {s["id"]: s for s in client.get("/api/series").get_json()["series"]}
    assert shelf[7]["also"] == [{"id": 9, "source": "Other Site (EN)"}] and shelf[9]["also"] == [{"id": 7, "source": "Reading Site (EN)"}]
    cards = {b["series_id"]: b for b in client.get("/api/books?kinds=Graphic novel").get_json()["books"] if b.get("chapter_series")}
    assert cards[9]["also"] == [{"id": 7, "source": "Reading Site (EN)"}]
    assert client.get("/api/series/7").get_json()["also"] == [{"id": 9, "source": "Other Site (EN)", "chapters": 3, "downloaded": 3, "read": 2}]

    gone = client.delete("/api/series/9").get_json()
    assert gone["read_carried"] == ["Reading Site (EN)"] and gone["chapters"] == 3
    assert gone["message"].endswith("What was read in it is marked read in the copy from Reading Site (EN).")
    assert [c["isRead"] for c in series_server.chapters[7]] == [True, True, True, False]
    assert [c["isDownloaded"] for c in series_server.chapters[7]] == [True, True, False, False] and series_server.following == {7}
    assert client.get("/api/series").get_json()["series"][0]["also"] == []
    assert client.get("/api/series/7").get_json()["also"] == []


def test_a_copy_whose_site_has_gone_can_still_be_deleted(client, series_server):
    # A spare is often the empty copy from a site that stopped answering: deleting it must not go and ask that site.
    series_server.following.add(8)
    series_server.asked.clear()
    assert client.delete("/api/series/8").get_json()["message"] == "Deleted Quiet Tea Shop (Reading Site (EN)). It is no longer followed."
    assert series_server.following == {7} and not any("fetchManga" in q for q in series_server.asked)
    assert client.delete("/api/series/999").status_code == 502


def test_a_series_from_the_series_servers_own_folder_is_not_deleted(client, series_server):
    series_server.sources[7] = ("0", "Local source")
    assert client.get("/api/series/7").get_json()["can_delete"] is False
    refused = client.delete("/api/series/7")
    assert refused.status_code == 502 and "Stop following" in refused.get_json()["error"]
    assert 7 in series_server.following and [c["isDownloaded"] for c in series_server.chapters[7]] == [True, True, False, False]
    assert not any("delete" in q.lower() for q in series_server.asked)
