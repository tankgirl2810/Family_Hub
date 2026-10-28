"""Who may do what: sign-ins, members against admin routes, requests from other sites, files passed through."""
import re

import requests

from test_app import app, client, login  # noqa: F401


def _fill(rule):
    return re.sub(r"<(?:(int|path|string):)?([^>]+)>", lambda m: "1" if m.group(1) == "int" else "x", rule)


def _api_routes(app):
    for rule in app.url_map.iter_rules():
        if rule.rule.startswith("/api"):
            for method in sorted(rule.methods - {"HEAD", "OPTIONS"}):
                yield method, rule.rule


def _no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise requests.ConnectionError("no network in tests")
    monkeypatch.setattr(requests.Session, "request", refuse)


def test_nobody_signed_in_gets_nothing_but_the_sign_in(app, client, monkeypatch):
    _no_network(monkeypatch)
    open_routes = {"/api/state", "/api/login", "/api/logout", "/api/setup"}
    for method, rule in _api_routes(app):
        if rule in open_routes:
            continue
        code = client.open(_fill(rule), method=method, json={}).status_code
        assert code in (401, 403), (method, rule, code)


def test_a_member_is_refused_every_admin_route(app, client, monkeypatch):
    _no_network(monkeypatch)
    login(client, "Nan")
    for method, rule in _api_routes(app):
        if rule.startswith("/api/admin") or (method == "DELETE" and rule in ("/api/books/<int:book_id>", "/api/media/<kind>/<int:arr_id>",
                                                                             "/api/media/torrents/<torrent_hash>", "/api/profiles/<int:profile_id>")):
            code = client.open(_fill(rule), method=method, json={}).status_code
            assert code == 403, (method, rule, code)
    ids = {p["name"]: p["id"] for p in client.get("/api/state").get_json()["profiles"]}
    # a member can't touch another profile, or hand themselves more rights
    assert client.patch("/api/profiles/%d" % ids["You"], json={"passcode": "5555"}).status_code == 403
    client.patch("/api/profiles/%d" % ids["Nan"], json={"role": "admin", "can_request": True, "can_request_media": True})
    me = client.get("/api/state").get_json()["me"]
    assert me["role"] == "member" and not me["can_request"] and not me["can_request_media"]


def test_requests_from_another_site_are_refused(client):
    """SameSite=Lax still sends the cookie from any page on the same host (Radarr's, qBittorrent's...), so a
    state-changing request has to come from Family Hub's own pages."""
    login(client, "You")
    for site in ("cross-site", "same-site"):
        r = client.post("/api/admin/backup", headers={"Sec-Fetch-Site": site})
        assert r.status_code == 403 and "error" in r.get_json()
    # import takes any body (force=True), so a plain form on another page could post it
    assert client.post("/api/admin/import", data='{"format": "bookhub-bookdata", "books": {}}', content_type="text/plain",
                       headers={"Sec-Fetch-Site": "same-site"}).status_code == 403
    assert client.post("/api/admin/backup", headers={"Sec-Fetch-Site": "same-origin"}).status_code == 200
    assert client.post("/api/admin/backup").status_code == 200  # no header: not a browser, so no cookie riding along either
    assert client.get("/api/books", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 200  # reading is fine


def test_a_changed_passcode_signs_out_everywhere_else(app):
    nan_phone, admin = app.test_client(), app.test_client()
    login(nan_phone, "Nan")
    login(admin, "You")
    ids = {p["name"]: p["id"] for p in admin.get("/api/state").get_json()["profiles"]}
    assert nan_phone.get("/api/books").status_code == 200
    assert admin.patch("/api/profiles/%d" % ids["Nan"], json={"passcode": "4321"}).status_code == 200
    assert nan_phone.get("/api/books").status_code == 401  # the old phone has to sign in again
    assert login(nan_phone, "Nan", "4321").status_code == 200
    # changing your own passcode keeps THIS sign-in going
    assert nan_phone.patch("/api/profiles/%d" % ids["Nan"], json={"passcode": "9876"}).status_code == 200
    assert nan_phone.get("/api/books").status_code == 200
    assert admin.get("/api/books").status_code == 200  # nobody else's sign-in is touched


def test_a_sign_in_from_before_passcode_stamps_still_works(app):
    phone = app.test_client()
    login(phone, "Nan")
    with phone.session_transaction() as saved:
        saved.pop("pc", None)  # a cookie handed out before this check existed
    assert phone.get("/api/books").status_code == 200
    with phone.session_transaction() as saved:
        assert saved.get("pc")


def test_series_cover_for_reading_apps_is_never_a_web_page(app, client, monkeypatch):
    import base64

    class Reply:
        headers = {"Content-Type": "text/html; charset=utf-8"}
        content = b"<script>alert(1)</script>"
    monkeypatch.setattr(type(app.hub.series), "raw", lambda self, *args, **kwargs: Reply())
    auth = {"Authorization": "Basic " + base64.b64encode(b"You:demo").decode()}
    r = client.get("/opds/chapters-cover/1", headers=auth)
    assert r.status_code == 200
    assert r.mimetype == "application/octet-stream" and r.headers["X-Content-Type-Options"] == "nosniff"
    Reply.headers = {"Content-Type": "image/webp"}
    assert client.get("/opds/chapters-cover/1", headers=auth).mimetype == "image/webp"


def test_import_never_makes_a_nameless_profile(app, client):
    login(client, "You")
    before = len(client.get("/api/state").get_json()["profiles"])
    data = {"format": "bookhub-bookdata", "books": {"k": {"title": "T", "ratings": {"": 4, "  ": 3, "?": 5},
                                                          "shelves": [""], "reading": {" ": {"status": "finished"}}}}}
    assert client.post("/api/admin/import", json=data).status_code == 200
    assert len(client.get("/api/state").get_json()["profiles"]) == before


def test_the_app_page_cannot_be_framed_by_another_site(client):
    r = client.get("/")
    assert "frame-ancestors 'self'" in r.headers.get("Content-Security-Policy", "")
    assert r.headers.get("X-Content-Type-Options") == "nosniff"


def test_api_replies_say_nosniff(client):
    assert client.get("/api/state").headers.get("X-Content-Type-Options") == "nosniff"
