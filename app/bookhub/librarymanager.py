"""Asking the library manager (Calibre-Web Automated) to change the library.

Family Hub never writes to the Calibre library itself. The three jobs that change
the library (removing spare copies of a book, an admin deleting a book, and an
admin giving a book its real title and author, see bookfix.py) are
done by signing in to the library manager as an admin and using its own merge,
edit and delete pages, so its database and its files stay in step.

Written against Calibre-Web Automated v4.0.8.
"""
import re
import threading

import requests

TIMEOUT = 120
TOKEN_RE = (re.compile(r'name="csrf_token"[^>]*value="([^"]+)"'), re.compile(r'value="([^"]+)"[^>]*name="csrf_token"'))
# Fields the library manager can change one at a time: Family Hub's name for each -> the library manager's.
FIELDS = {"synopsis": "comments", "tags": "tags", "series": "series", "series_index": "series_index", "publisher": "publishers",
          "title": "title", "authors": "authors"}


class ManagerError(Exception):
    pass


def configured(config):
    return bool(config.cwa_api_url and config.cwa_user and config.cwa_pass)


class LibraryManager:
    def __init__(self, hub):
        self.hub = hub
        self._lock = threading.Lock()
        self._session = None
        self._token = None
        self._signed_in_as = None  # (address, user) the session belongs to

    def _find_token(self, html):
        for pattern in TOKEN_RE:
            found = pattern.search(html)
            if found:
                return found.group(1)
        return None

    def sign_in(self, config=None):
        """Start a session. The library manager allows three tries a minute, so one session is kept and reused."""
        config = config or self.hub.config
        if not configured(config):
            raise ManagerError("The library manager's sign-in is not set up. An admin can add it under Admin, Hub, Connections.")
        session = requests.Session()
        try:
            page = session.get(config.cwa_api_url + "/login", timeout=30)
            token = self._find_token(page.text)
            reply = session.post(config.cwa_api_url + "/login", timeout=30, allow_redirects=False,
                                 data={"username": config.cwa_user, "password": config.cwa_pass, "csrf_token": token or "", "next": "/"})
            if reply.status_code == 429:
                raise ManagerError("The library manager allows three sign-ins a minute. Wait a minute and try again.")
            home = session.get(config.cwa_api_url + "/", timeout=30)
        except requests.RequestException as exc:
            raise ManagerError("Could not reach the library manager: %s" % exc)
        # a good login answers with a redirect, and the home page after it has a logout link. no logout link = not in
        if reply.status_code not in (301, 302, 303) or "/logout" not in home.text:
            raise ManagerError("The library manager refused the user name or password.")
        self._session, self._token = session, self._find_token(home.text) or token
        self._signed_in_as = (config.cwa_api_url, config.cwa_user)
        return session

    def _post(self, path, **kw):
        config = self.hub.config
        with self._lock:
            for attempt in (1, 2):
                if self._session is None or self._signed_in_as != (config.cwa_api_url, config.cwa_user):
                    self.sign_in()
                try:
                    reply = self._session.post(config.cwa_api_url + path, timeout=TIMEOUT, allow_redirects=False,
                                               headers={"X-CSRFToken": self._token or "", "X-Requested-With": "XMLHttpRequest"}, **kw)
                except requests.RequestException as exc:
                    raise ManagerError("Could not reach the library manager: %s" % exc)
                if reply.status_code in (301, 302, 303, 400, 401, 403) and attempt == 1:
                    self._session = None  # session went stale, log in again. but only once, we're not hammering a 3-per-minute limit
                    continue
                return reply
        raise ManagerError("The library manager would not accept the request.")

    def set_field(self, book_id, field, value):
        """Change one detail of one entry. The library manager's answer is not relied on; the caller reads the result back."""
        self._post("/ajax/editbooks/" + FIELDS[field], data={"pk": str(book_id), "value": value})

    def merge(self, keep_id, spare_ids):
        """Move any format only a spare has onto the kept entry, then remove the spares and their files."""
        reply = self._post("/ajax/mergebooks", json={"Merge_books": [keep_id] + list(spare_ids)})
        # it answers with python-ish 'single quotes' sometimes, so flip them before looking
        if reply.status_code != 200 or '"success": true' not in reply.text.replace("'", '"'):
            raise ManagerError("The library manager did not confirm the change (%s). Does this account have permission to edit and delete books?" % reply.status_code)

    def delete(self, book_id):
        """Remove one entry and its files from the library, the way the library manager's own Delete button does."""
        reply = self._post("/ajax/delete/%d" % book_id)
        if reply.status_code != 200 or "success" not in reply.text:
            raise ManagerError("The library manager did not confirm the deletion (%s). Does this account have permission to delete books?" % reply.status_code)

    def test(self, config):
        if not configured(config):
            return {"ok": False, "message": "Enter the address, the user name and the password."}
        try:
            self.sign_in(config)
        except ManagerError as exc:
            return {"ok": False, "message": str(exc)}
        return {"ok": True, "message": "Signed in to the library manager as %s." % config.cwa_user}
