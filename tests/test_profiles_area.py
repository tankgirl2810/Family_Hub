"""Profiles, settings and setup: edge cases found while testing."""
import zipfile
from pathlib import Path

import pytest

from test_app import app, client, login  # noqa: F401
from bookhub import db, goodreads, integrations, settings, setupfile, visuals
from bookhub.config import Config


def test_clean_tag_treats_non_strings_as_blank(app):
    assert db.clean_tag(5) == "" and db.clean_tag(["Horror"]) == "" and db.clean_tag(None) == ""
    assert db.clean_tag("  Slow   burn ") == "Slow burn"
    with app.app_context():
        key = app.hub.library.books()[0]["key"]
        app.hub.add_tag("family", key, 42, None)  # used to blow up in re.sub
        app.hub.set_tag_rule(["x"], {"y": 1})


def test_backup_keep_zero_still_prunes(app, monkeypatch):
    monkeypatch.setenv("BACKUP_KEEP", "0")
    assert Config().backup_keep == 1
    monkeypatch.setenv("BACKUP_KEEP", "lots")
    assert Config().backup_keep == 30
    folder = app.hub.config.backups_dir
    folder.mkdir(parents=True, exist_ok=True)
    for day in ("2020-01-01", "2020-01-02", "2020-01-03"):
        (folder / ("bookdata-%s.json" % day)).write_text("{}")
    app.hub.config.backup_keep = 0
    with app.app_context():
        app.hub.backup_now()
    assert len(list(folder.glob("bookdata-*.json"))) == 1  # [:-0] used to keep every file


def test_percent_read_is_kept_between_0_and_100(app, client):
    login(client, "You")
    book = client.get("/api/books").get_json()["books"][0]
    r = client.post("/api/books/%d/reading" % book["id"], json={"status": "reading", "percent": -40})
    assert r.status_code == 200
    with app.app_context():
        row = app.hub.con().execute("SELECT percent FROM reading WHERE book_key=? AND profile_id=1", (book["key"],)).fetchone()
    assert row["percent"] == 0
    client.post("/api/books/%d/reading" % book["id"], json={"status": "reading", "percent": 250})
    with app.app_context():
        row = app.hub.con().execute("SELECT percent FROM reading WHERE book_key=? AND profile_id=1", (book["key"],)).fetchone()
    assert row["percent"] == 100


def test_env_file_value_cannot_smuggle_in_another_setting(app, client, tmp_path):
    env = tmp_path / ".env"
    env.write_text("TZ=Etc/UTC\n")
    app.hub.config.config_file = str(env)
    login(client, "You")
    r = client.post("/api/admin/connections", json={"values": {"qbit_user": "me\rCWA_PASS=stolen"}})
    assert r.status_code == 200
    values = settings.EnvFile(env).read()
    assert "CWA_PASS" not in values and values["QBITTORRENT_USER"] == "meCWA_PASS=stolen"
    assert client.post("/api/admin/connections", json={"values": {"smtp_port": "²"}}).get_json()["error"] == "Enter a whole number."
    assert client.post("/api/admin/connections", json={"values": {"qbit_user": ["a"]}}).status_code == 400


def test_connection_test_with_a_list_of_values(client):
    login(client, "You")
    r = client.post("/api/admin/connections/test", json={"group": "logins", "values": ["x"]})
    assert r.status_code == 200 and r.get_json()["ok"] is False


def test_new_profile_passcode_needs_four_characters(client):
    login(client, "You")
    assert client.post("/api/profiles", json={"name": "Kid", "passcode": "12"}).status_code == 400
    assert client.post("/api/profiles", json={"name": "Kid", "passcode": "1234"}).status_code == 200


def test_home_town_nan_is_not_sent_as_json(app, client):
    app.hub.config.home_lat, app.hub.config.home_lon = "nan", "151.2"
    login(client, "You")
    r = client.get("/api/state")
    assert b"NaN" not in r.data and r.get_json()["visuals"]["place"] is None


def test_goodreads_link_sent_as_a_number_or_list(app, client):
    login(client, "You")
    r = client.post("/api/profiles/1/goodreads", json={"link": ["x"]})
    assert r.status_code == 400
    assert goodreads.user_id("²³") == ""
    with app.app_context():
        assert goodreads.set_link(app.hub, 1, 12345678) == "12345678"


def test_restore_skips_folder_entries_and_counts_files(tmp_path):
    pyzipper = pytest.importorskip("pyzipper")
    setup = tmp_path / "setup.zip"
    with pyzipper.AESZipFile(setup, "w", compression=zipfile.ZIP_DEFLATED, encryption=pyzipper.WZ_AES) as z:
        z.setpassword(b"long enough phrase")
        z.writestr("README.txt", "hi")
        z.writestr(setupfile.NOTES_NAME, "notes")
        z.writestr(".env", "TZ=Etc/UTC\n")
        z.writestr("stack/", "")  # 7-Zip and others write folders as entries of their own
        z.writestr("stack/bookhub/x.txt", "x")
    target = tmp_path / "hub"
    target.mkdir()
    assert setupfile.restore(setup, target, "long enough phrase") == 2
    assert (target / "stack" / "bookhub" / "x.txt").read_text() == "x"


def test_failed_setup_file_leaves_nothing_behind(app, tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("TZ=Etc/UTC\n")
    app.hub.config.config_file = str(env)

    def broken(*args):
        raise OSError("disk full")
    monkeypatch.setattr(setupfile, "notes", broken)
    with app.app_context(), pytest.raises(OSError):
        setupfile.build(app.hub, "long enough phrase")
    assert not list((Path(app.hub.config.data_dir) / "setup-files").glob("*.zip"))


class _Reply:
    def __init__(self, status=200, body=None, error=None):
        self.status_code, self._body, self._error = status, body, error

    def json(self):
        if self._error:
            raise self._error
        return self._body

    def raise_for_status(self):
        pass


def test_visuals_check_survives_an_odd_github_answer(app, monkeypatch):
    app.hub.config.visuals_repo = "someone/visuals"
    for reply in (_Reply(error=ValueError("not json")), _Reply(body=["a list"])):
        monkeypatch.setattr(visuals.requests, "get", lambda *a, **k: reply)
        with app.app_context():
            state = visuals.sync(app.hub, force=True)
        assert state["error"] == "GitHub's answer had no commit in it."


def test_hardcover_answer_with_no_books(app, monkeypatch):
    with app.app_context():
        profile = app.hub.profile(1)
        profile = dict(profile, hardcover_token="abc")
        monkeypatch.setattr(integrations.requests, "post", lambda *a, **k: _Reply(body={"data": {"me": {"user_books": None}}}))
        assert integrations.hardcover_sync(app.hub, profile) == 0
        monkeypatch.setattr(integrations.requests, "post", lambda *a, **k: _Reply(body=["odd"]))
        with pytest.raises(integrations.IntegrationError):
            integrations.hardcover_sync(app.hub, profile)


def test_prowlarr_answer_that_is_not_a_list(app, monkeypatch):
    app.hub.config.prowlarr_url, app.hub.config.prowlarr_api_key = "http://prowlarr:9696", "k"
    monkeypatch.setattr(integrations.requests, "get", lambda *a, **k: _Reply(body={"message": "oops"}))
    with pytest.raises(integrations.IntegrationError):
        integrations.prowlarr_search(app.hub.config, "dracula")
