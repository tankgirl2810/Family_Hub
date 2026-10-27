"""Devices, each person's "My books" shelf, and sending books to a Kindle.

A profile lists the devices and apps it reads on. Readest and KOReader fetch
books themselves from the profile's private catalogue (see opds.py). A Kindle
cannot do that, so books are emailed to its Send-to-Kindle address instead.

Family Hub can put a book on a device but cannot take one off again: removing a
book from the shelf stops offering it, and the copy on the device has to be
deleted there.
"""
import re
import smtplib
import ssl
import threading
from email.message import EmailMessage

from . import db

KINDS = [("kindle", "Kindle"), ("readest", "Readest"), ("koreader", "KOReader"), ("kobo", "Kobo"), ("other", "Other")]
KIND_IDS = {kind for kind, _ in KINDS}
# Where "open my library" goes when a device has no link of its own.
DEFAULT_LINKS = {"kindle": "https://read.amazon.com/kindle-library", "readest": "https://web.readest.com",
                 "kobo": "https://www.kobo.com/library"}
# What Amazon accepts by email, best first. Comics and old Kindle formats are not on the list.
KINDLE_FORMATS = ["EPUB", "PDF", "DOCX", "DOC", "RTF", "TXT", "HTML", "HTM"]
AMAZON_LIMIT_MB = 50
MIME = {"EPUB": "epub+zip", "PDF": "pdf"}
NOT_SET_UP = "Sending by email is not set up yet. An admin can add it under Admin, Hub, Connections."


def connect(config, timeout=30):
    """Open a signed-in connection to the mail server."""
    port = int(config.smtp_port or 587)
    context = ssl.create_default_context()
    if port == 465:
        smtp = smtplib.SMTP_SSL(config.smtp_host, port, timeout=timeout, context=context)
    else:
        smtp = smtplib.SMTP(config.smtp_host, port, timeout=timeout)
        smtp.starttls(context=context)
    smtp.login(config.smtp_user, config.smtp_pass)
    return smtp


def explain(error):
    """Turn a mail error into something a person can act on."""
    if isinstance(error, smtplib.SMTPAuthenticationError):
        return "The mail service refused the address or app password. With Gmail, use an app password, not the account password."
    if isinstance(error, smtplib.SMTPRecipientsRefused):
        return "The mail service refused the Kindle address. Check its spelling."
    if isinstance(error, (smtplib.SMTPSenderRefused, smtplib.SMTPDataError)):
        return "The mail service would not take the message: %s" % str(error)[:160]
    text = str(error)
    if "getaddrinfo" in text or "Name or service not known" in text:
        return "The mail server could not be found. Check its name."
    if "timed out" in text.lower() or "refused" in text.lower():
        return "The mail server did not answer. Check the server name and port."
    return "Could not send: " + text[:200]


def safe_filename(title, author, ext):
    name = "%s - %s" % (title, author) if author else title
    name = re.sub(r'[\\/:*?"<>|\r\n\t]+', " ", name).strip()[:120] or "book"
    return "%s.%s" % (name, ext.lower())


class Devices:
    def __init__(self, hub):
        self.hub = hub
        self._lock = threading.Lock()

    # ------------------------------------------------------------ devices

    def describe(self, row):
        return {
            "id": row["id"], "profile_id": row["profile_id"], "name": row["name"], "kind": row["kind"],
            "address": row["address"], "link": row["link"] or DEFAULT_LINKS.get(row["kind"], ""),
            "custom_link": row["link"], "auto_send": bool(row["auto_send"]),
        }

    def rows(self, profile_id):
        return self.hub.con().execute("SELECT * FROM devices WHERE profile_id=? ORDER BY id", (profile_id,)).fetchall()

    def get(self, device_id):
        return self.hub.con().execute("SELECT * FROM devices WHERE id=?", (device_id,)).fetchone()

    def _clean(self, data, old=None):
        """Checked values for a new or changed device, or ValueError with a message."""
        kind = data.get("kind", old["kind"] if old else "other")
        if kind not in KIND_IDS:
            raise ValueError("Choose what kind of device or app this is.")
        name = db.clean_tag(data.get("name", old["name"] if old else ""))
        if not name:
            raise ValueError("Give the device a name, such as Nan's Kindle.")
        address = str(data.get("address", old["address"] if old else "") or "").strip()[:200]
        if kind != "kindle":
            address = ""
        elif not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", address):
            raise ValueError("Enter the Kindle's own email address. It ends in @kindle.com and is listed on Amazon "
                             "under Manage Your Content and Devices, Preferences, Personal Document Settings.")
        link = str(data.get("link", old["link"] if old else "") or "").strip()[:500]
        if link and not link.lower().startswith(("http://", "https://")):
            raise ValueError("Links start with http:// or https://")
        auto = data.get("auto_send", old["auto_send"] if old else False)
        return name, kind, address, link, 1 if (auto and kind == "kindle") else 0

    def add(self, profile_id, data):
        values = self._clean(data)
        con = self.hub.con()
        cur = con.execute("INSERT INTO devices(profile_id, name, kind, address, link, auto_send, created) VALUES(?,?,?,?,?,?,?)",
                          (profile_id, *values, db.now()))
        con.commit()
        return cur.lastrowid

    def update(self, device, data):
        values = self._clean(data, device)
        con = self.hub.con()
        con.execute("UPDATE devices SET name=?, kind=?, address=?, link=?, auto_send=? WHERE id=?", (*values, device["id"]))
        con.commit()

    def remove(self, device_id):
        con = self.hub.con()
        con.execute("DELETE FROM deliveries WHERE device_id=?", (device_id,))
        con.execute("DELETE FROM devices WHERE id=?", (device_id,))
        con.commit()

    def forget_profile(self, profile_id):
        con = self.hub.con()
        for table in ("deliveries", "devices", "shelf"):
            con.execute("DELETE FROM %s WHERE profile_id=?" % table, (profile_id,))

    # -------------------------------------------------------------- shelf

    def shelf(self, profile_id):
        """{book_key: date added} for one profile's "My books"."""
        return {row["book_key"]: row["added"] for row in
                self.hub.con().execute("SELECT book_key, added FROM shelf WHERE profile_id=?", (profile_id,))}

    def set_shelf(self, profile_id, book, on, by=None):
        """Add or remove a book. Returns the device names it is being sent to, or is still on."""
        con = self.hub.con()
        key = book["key"]
        kindles = [device for device in self.rows(profile_id) if device["kind"] == "kindle"]
        result = {"on": bool(on), "sending": [], "still_on": []}
        if on:
            self.hub.ensure_meta(key)
            added = con.execute("INSERT OR IGNORE INTO shelf(profile_id, book_key, added_by, added) VALUES(?,?,?,?)",
                                (profile_id, key, by, db.now())).rowcount
            con.commit()
            # rowcount is 0 when it was already on the shelf, so pressing the button twice doesn't email it twice
            for device in kindles if added else []:
                if not device["auto_send"]:
                    continue
                try:
                    self.queue(device, book)
                    result["sending"].append(device["name"])
                except ValueError as exc:
                    self._record(device, key, "failed", str(exc))
        else:
            sent = {row["device_id"] for row in con.execute(
                "SELECT device_id FROM deliveries WHERE profile_id=? AND book_key=? AND status='sent'", (profile_id, key))}
            result["still_on"] = [device["name"] for device in kindles if device["id"] in sent]
            con.execute("DELETE FROM shelf WHERE profile_id=? AND book_key=?", (profile_id, key))
            # forget it was ever sent, so putting it back on the shelf later sends it again
            con.execute("DELETE FROM deliveries WHERE profile_id=? AND book_key=?", (profile_id, key))
            con.commit()
        self.hub.touch()
        return result

    # ------------------------------------------------------------ sending

    def limit_mb(self):
        try:
            return min(AMAZON_LIMIT_MB, int(self.hub.config.smtp_max_mb or 18))
        except ValueError:
            return 18

    def sendable(self, book):
        """(format, path) of the copy to email, or (None, reason it cannot be sent)."""
        library = self.hub.library
        choices = []
        if "EPUB" not in book["formats"]:
            converted = self.hub.config.converted_dir / ("%d.epub" % book["id"])
            if converted.is_file():
                choices.append(("EPUB", converted))
        for fmt in KINDLE_FORMATS:
            path = library.file_path(book, fmt) if fmt in book["formats"] else None
            if path:
                choices.append((fmt, path))
        choices.sort(key=lambda choice: KINDLE_FORMATS.index(choice[0]))
        if not choices:
            have = ", ".join(sorted(book["formats"])) or "no files"
            if set(book["formats"]) & {"CBZ", "CBR", "CB7"}:
                return None, "Comics cannot be sent to a Kindle by email."
            return None, "A Kindle cannot take this book by email (it has %s)." % have
        limit = self.limit_mb()
        for fmt, path in choices:
            if path.stat().st_size <= limit * 1048576:  # 1048576 = one MB in bytes
                return fmt, path
        size = min(path.stat().st_size for _, path in choices) / 1048576
        return None, "Too big to email: %.0f MB, and the limit is %d MB." % (size, limit)

    def _record(self, device, book_key, status, reason="", fmt=""):
        con = self.hub.con()
        con.execute(
            "INSERT INTO deliveries(device_id, book_key, profile_id, status, reason, fmt, at) VALUES(?,?,?,?,?,?,?) "
            "ON CONFLICT(device_id, book_key) DO UPDATE SET status=excluded.status, reason=excluded.reason, "
            "fmt=excluded.fmt, at=excluded.at",
            (device["id"], book_key, device["profile_id"], status, reason, fmt, db.now()))
        con.commit()

    def queue(self, device, book):
        """Line a book up to be emailed. Raises ValueError with the reason if it cannot be."""
        if device["kind"] != "kindle":
            raise ValueError("Only a Kindle is sent books. Other apps fetch them from your catalogue.")
        fmt, why = self.sendable(book)
        if not fmt:
            raise ValueError(why)
        if not self.hub.config.mail_configured:
            raise ValueError(NOT_SET_UP)
        self._record(device, book["key"], "queued", fmt=fmt)

    def send_queued(self):
        """Email everything that is waiting. Returns how many were sent."""
        if not self._lock.acquire(blocking=False):
            return 0  # someone else is already on it
        try:
            con = self.hub.con()
            waiting = con.execute("SELECT * FROM deliveries WHERE status='queued' ORDER BY at").fetchall()
            if not waiting:
                return 0
            config = self.hub.config
            if not config.mail_configured:
                return 0
            try:
                smtp = connect(config)
            except Exception as exc:
                for row in waiting:
                    self._finish(row, "failed", explain(exc))
                return 0
            sent = 0
            try:
                for row in waiting:
                    device = self.get(row["device_id"])
                    book = self.hub.library.by_key(row["book_key"])
                    if not device or not book:
                        self._finish(row, "failed", "The book is no longer in the library.")
                        continue
                    fmt, path = self.sendable(book)
                    if not fmt:
                        self._finish(row, "failed", path)
                        continue
                    message = EmailMessage()
                    message["From"], message["To"], message["Subject"] = config.mail_sender, device["address"], book["title"]
                    message.set_content("Sent from the family library by Family Hub.")
                    message.add_attachment(path.read_bytes(), maintype="application", subtype=MIME.get(fmt, "octet-stream"),
                                           filename=safe_filename(book["title"], book["author"], fmt))
                    try:
                        smtp.send_message(message)
                    except Exception as exc:
                        self._finish(row, "failed", explain(exc))
                        continue
                    self._finish(row, "sent", fmt=fmt)
                    sent += 1
            finally:
                try:
                    smtp.quit()
                except Exception:
                    pass  # it's goodbye either way
            return sent
        finally:
            self._lock.release()

    def _finish(self, row, status, reason="", fmt=None):
        con = self.hub.con()
        con.execute("UPDATE deliveries SET status=?, reason=?, fmt=?, at=? WHERE device_id=? AND book_key=?",
                    (status, reason or "", fmt or row["fmt"], db.now(), row["device_id"], row["book_key"]))
        con.commit()

    def kick(self, app):
        """Send what is waiting now, without holding up the page that asked."""
        def work():
            with app.app_context():
                try:
                    self.send_queued()
                except Exception as exc:
                    app.logger.warning("sending to a device failed: %s", exc)
        threading.Thread(target=work, name="bookhub-send", daemon=True).start()

    # --------------------------------------------------------------- view

    def book_view(self, profile_id, book):
        """Whether a book is on a profile's shelf, and where it stands on each of their Kindles."""
        con = self.hub.con()
        key = book["key"]
        sent = {row["device_id"]: row for row in con.execute(
            "SELECT * FROM deliveries WHERE profile_id=? AND book_key=?", (profile_id, key))}
        fmt, why = self.sendable(book)
        devices = []
        for device in self.rows(profile_id):
            if device["kind"] != "kindle":
                continue
            row = sent.get(device["id"])
            devices.append({
                "id": device["id"], "name": device["name"], "can_send": bool(fmt), "why": "" if fmt else why,
                "state": row["status"] if row else "none", "reason": row["reason"] if row else "",
                "at": row["at"] if row else None,
            })
        on = con.execute("SELECT 1 FROM shelf WHERE profile_id=? AND book_key=?", (profile_id, key)).fetchone()
        return {"on": bool(on), "devices": devices}
