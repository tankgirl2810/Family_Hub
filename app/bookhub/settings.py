"""Connection settings an admin can change from the Admin page.

Every address, key and password lives in ONE file, the household's .env (see .env.example). Docker
reads it when the services start, and docker-compose.yml also lets Family Hub write to it
(HUB_CONFIG_FILE), so a value saved on the Admin page goes into that same file, takes effect straight
away, and travels with the file (and with the setup file, setupfile.py) to another machine.

Without a writable .env (running outside Docker, or the tests) a value saved here is kept in Family
Hub's database instead, as before; clearing it falls back to the environment. Values saved in the
database by an older version are moved into the .env file the first time it is writable.

Secrets can be written but are never sent back to the browser, and none of this is included in the
book-data export or backups.
"""
import copy
import os
import re
from pathlib import Path

from . import db, integrations

# group id, title, help, [(config attribute, label, kind)]   kind: "url", "text", "number", "toggle" or "secret"
GROUPS = [
    ("prowlarr", "Book search (Prowlarr)",
     "In Prowlarr: Settings → General → API Key. Use http://prowlarr:9696 if Prowlarr runs from this project.",
     [("prowlarr_url", "Prowlarr address", "url"), ("prowlarr_api_key", "API key", "secret")]),
    ("qbit", "Download progress (qBittorrent)",
     "Optional. Shows live progress on the Discover page. Downloads work without it.",
     [("qbit_url", "qBittorrent address", "url"), ("qbit_user", "User name", "text"),
      ("qbit_pass", "Password", "secret"), ("qbit_category", "Category for books (optional)", "text")]),
    ("cwa", "Library manager",
     "The first address is the one you type in a browser; it is used for the \"Open in library manager\" links. "
     "The rest is only needed for removing spare copies of a book (Admin, Books, See the copies) and for admins deleting a book: Family Hub signs in to the "
     "library manager with an admin account there and asks it to merge or remove the entries. Use "
     "http://calibre-web-automated:8083 as the second address if it runs from this project.",
     [("cwa_url", "Library manager address (in a browser)", "url"),
      ("cwa_api_url", "Library manager address (for Family Hub)", "url"),
      ("cwa_user", "Admin user name there", "text"), ("cwa_pass", "Password", "secret")]),
    ("mail", "Sending books by email (Send to Kindle)",
     "A mail account Family Hub sends books from. For Gmail: make an address just for this, turn on 2-Step "
     "Verification, create an app password, and enter the address and that app password here. "
     "Each Kindle must also list this address as an approved sender on Amazon.",
     [("smtp_user", "Email address (sign-in name)", "text"), ("smtp_pass", "App password", "secret"),
      ("smtp_host", "Mail server", "text"), ("smtp_port", "Port", "number"),
      ("smtp_from", "Send from, if different (optional)", "text"),
      ("smtp_max_mb", "Largest book to send (MB)", "number")]),
    ("catalogues", "Community tags from book catalogues",
     "Family Hub asks Open Library and Wikidata what each book is about and adds the answers as community tags. "
     "Only the title, author and ISBN are sent. Google Books is used as well if you enter a key: in the Google "
     "Cloud console, create a project, enable the Books API and create an API key (free). Comics are looked up "
     "on Comic Vine if you enter its key: make a free account at comicvine.gamespot.com and open "
     "comicvine.gamespot.com/api to see it. The same lookups fetch what readers at large rate each book; a Google "
     "Books key adds Google's ratings, and a Hardcover key (hardcover.app, Settings, Hardcover API) adds Hardcover's. "
     "The Hardcover key here is for the whole library and is separate from the one a person links for their own reading.",
     [("tag_lookups", "Look up tags for new books", "toggle"),
      ("google_books_key", "Google Books API key (optional)", "secret"),
      ("comic_vine_key", "Comic Vine API key (optional, for comics)", "secret"),
      ("hardcover_key", "Hardcover API key (optional, for readers' ratings)", "secret")]),
    ("series", "Series by chapter (manga, manhwa)",
     "A Suwayomi server that follows series on reading sites and keeps every chapter. Use http://suwayomi:4567 if it "
     "runs from this project. The user name and password are only needed if you gave the series server one. "
     "Languages are the short codes of the sources to search, separated by commas (en, ko, ja).",
     [("series_url", "Series server address", "url"), ("series_user", "User name (optional)", "text"),
      ("series_pass", "Password (optional)", "secret"), ("series_langs", "Languages to search", "text")]),
    ("radarr", "Films (Radarr)",
     "Radarr finds films through Prowlarr and files them on the server. Use http://radarr:7878 if it runs from this "
     "project. Leave the key empty: Connect everything reads it from Radarr's own settings.",
     [("radarr_url", "Radarr address", "url"), ("radarr_api_key", "API key (optional)", "secret")]),
    ("sonarr", "TV shows (Sonarr)",
     "Sonarr does the same for TV, and fetches new episodes as they come out. Use http://sonarr:8989 if it runs from this project.",
     [("sonarr_url", "Sonarr address", "url"), ("sonarr_api_key", "API key (optional)", "secret")]),
    ("bazarr", "Subtitles (Bazarr)",
     "Bazarr fetches subtitles for every film and episode. Languages are short codes separated by commas (en, fr). "
     "It uses free sources that need no account; an OpenSubtitles.com account (free, about 20 a day) adds the biggest one. "
     "Press Connect everything after changing these.",
     [("bazarr_url", "Bazarr address", "url"), ("bazarr_api_key", "API key (optional)", "secret"),
      ("subtitle_langs", "Subtitle languages", "text"),
      ("opensubtitles_user", "OpenSubtitles.com user name (optional)", "text"), ("opensubtitles_pass", "OpenSubtitles.com password", "secret")]),
    ("jellyfin", "Watching (Jellyfin)",
     "Where the family watches. The first address is how Family Hub reaches it (http://jellyfin:8096 when it runs from this "
     "project, or your server's Jellyfin); the second is the one you type in a browser, for the Watch buttons. "
     "Make the key in Jellyfin: Dashboard, API Keys, +.",
     [("jellyfin_url", "Jellyfin address (for Family Hub)", "url"), ("jellyfin_link", "Jellyfin address (in a browser)", "url"),
      ("jellyfin_api_key", "API key", "secret")]),
    ("visuals", "Visuals on Home",
     "The animated pictures at the top of Home. Family Hub ships with a set; to add your own, keep them in a public "
     "GitHub repository (the visuals README says how) and give its name here as your-name/repository-name. It is "
     "checked every hour. The home town is for the weather visual and the globe; find its latitude and longitude on a "
     "map (south and west are negative). It is only ever sent to Open-Meteo for the forecast.",
     [("visuals_repo", "GitHub repository (optional)", "text"), ("visuals_branch", "Branch (optional)", "text"),
      ("home_place", "Home town", "text"), ("home_lat", "Latitude", "coordinate"), ("home_lon", "Longitude", "coordinate")]),
    ("logins", "Sign-in for the programs' own pages",
     "One user name and password for opening Radarr, Sonarr, Prowlarr, Bazarr and qBittorrent in a browser (the links under "
     "Admin, Hub, Programs). Family Hub itself uses their API keys. Press Connect everything after changing these: it sets "
     "them on each program, and qBittorrent's sign-in above follows.",
     [("services_user", "User name", "text"), ("services_pass", "Password", "secret")]),
    ("vpn", "VPN for torrents (gluetun)",
     "qBittorrent only reaches the internet through the VPN. The VPN's own account details are in the .env file, because "
     "Docker needs them before anything starts (guide 14). Here is only how Family Hub asks it which port is forwarded.",
     [("gluetun_url", "VPN control address", "url"), ("gluetun_api_key", "VPN control key", "secret")]),
    ("copy", "Nightly copy to a second disk",
     "Every night Family Hub copies its own data, the other services' settings, the book library and the chapters to a "
     "second disk. The folder is named in the .env file as BACKUP_PATH, because Docker has to be told about it; "
     "this switch turns the copying on and off. Nothing on the second disk is ever deleted.",
     [("second_copy", "Copy every night", "toggle")]),
]
# Used when neither the Admin page nor the environment gives a value.
# Gmail takes messages up to 25 MB, and a book grows by a third when attached, hence 18.
DEFAULTS = {"subtitle_langs": "en", "smtp_host": "smtp.gmail.com", "smtp_port": "587", "smtp_max_mb": "18", "tag_lookups": "on", "series_langs": "en", "second_copy": "on"}
FIELDS = {name: kind for _, _, _, fields in GROUPS for name, _, kind in fields}
# The name each setting has in the .env file (and so in the environment docker-compose.yml passes on).
ENV_NAMES = {
    "prowlarr_url": "PROWLARR_URL", "prowlarr_api_key": "PROWLARR_API_KEY",
    "qbit_url": "QBITTORRENT_URL", "qbit_user": "QBITTORRENT_USER", "qbit_pass": "QBITTORRENT_PASS", "qbit_category": "QBITTORRENT_CATEGORY",
    "cwa_url": "CWA_URL", "cwa_api_url": "CWA_INTERNAL_URL", "cwa_user": "CWA_USER", "cwa_pass": "CWA_PASS",
    "smtp_user": "SMTP_USER", "smtp_pass": "SMTP_PASS", "smtp_host": "SMTP_HOST", "smtp_port": "SMTP_PORT",
    "smtp_from": "SMTP_FROM", "smtp_max_mb": "SMTP_MAX_MB",
    "tag_lookups": "TAG_LOOKUPS", "google_books_key": "GOOGLE_BOOKS_KEY", "comic_vine_key": "COMIC_VINE_KEY", "hardcover_key": "HARDCOVER_KEY",
    "series_url": "SUWAYOMI_URL", "series_user": "SUWAYOMI_USER", "series_pass": "SUWAYOMI_PASS", "series_langs": "SERIES_LANGUAGES",
    "radarr_url": "RADARR_URL", "radarr_api_key": "RADARR_API_KEY", "sonarr_url": "SONARR_URL", "sonarr_api_key": "SONARR_API_KEY",
    "bazarr_url": "BAZARR_URL", "bazarr_api_key": "BAZARR_API_KEY", "subtitle_langs": "SUBTITLE_LANGUAGES",
    "opensubtitles_user": "OPENSUBTITLES_USER", "opensubtitles_pass": "OPENSUBTITLES_PASS",
    "jellyfin_url": "JELLYFIN_URL", "jellyfin_link": "JELLYFIN_LINK", "jellyfin_api_key": "JELLYFIN_API_KEY",
    "gluetun_url": "GLUETUN_URL", "gluetun_api_key": "GLUETUN_API_KEY", "second_copy": "SECOND_COPY",
    "services_user": "SERVICES_USER", "services_pass": "SERVICES_PASSWORD",
    "visuals_repo": "VISUALS_REPO", "visuals_branch": "VISUALS_BRANCH",
    "home_place": "HOME_PLACE", "home_lat": "HOME_LAT", "home_lon": "HOME_LON",
}
SAVED_HEADER = "# --- Saved from Family Hub's Admin page ----------------------------------"
PLAIN_VALUE = re.compile(r"[A-Za-z0-9_./:@+,=%-]*")


class EnvFile:
    """The household's .env file: read the way Docker Compose reads it, and changed one line at a time so its
    comments and layout stay as they are."""

    def __init__(self, path):
        self.path = Path(path)

    def usable(self):
        return self.path.is_file() and os.access(self.path, os.W_OK)

    @staticmethod
    def parse_value(raw):
        raw = raw.strip()
        if raw[:1] == "'":
            end = raw.find("'", 1)
            return raw[1:end] if end > 0 else raw[1:]
        if raw[:1] == '"':
            out, i = [], 1
            while i < len(raw) and raw[i] != '"':
                if raw[i] == "\\" and i + 1 < len(raw):
                    i += 1
                    out.append({"n": "\n"}.get(raw[i], raw[i]))
                elif raw.startswith("$$", i):
                    out.append("$")
                    i += 1
                else:
                    out.append(raw[i])
                i += 1
            return "".join(out)
        return re.split(r"\s+#", raw, maxsplit=1)[0].strip()

    @staticmethod
    def format_value(value):
        """Written so Docker Compose reads back exactly this text: a $ or # in a password stays itself."""
        if PLAIN_VALUE.fullmatch(value):
            return value
        if "'" not in value and "\n" not in value:
            return "'%s'" % value
        return '"%s"' % value.replace("\\", "\\\\").replace('"', '\\"').replace("$", "$$").replace("\n", "\\n")

    def read(self):
        values = {}
        try:
            text = self.path.read_text(encoding="utf-8-sig")
        except OSError:
            return values
        for line in text.splitlines():
            m = re.match(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$", line)
            if m:
                values[m.group(1)] = self.parse_value(m.group(2))
        return values

    def write(self, changes):
        """Set each NAME to its value: on its own line where the file has one, otherwise added at the end."""
        with open(self.path, "r+", encoding="utf-8", newline="") as f:
            text = f.read()
            newline = "\r\n" if "\r\n" in text else "\n"
            lines = text.splitlines()
            for name, value in changes.items():
                line = "%s=%s" % (name, self.format_value(value))
                at = next((i for i, l in enumerate(lines) if re.match(r"\s*(?:export\s+)?%s\s*=" % re.escape(name), l)), None)
                if at is None:
                    if SAVED_HEADER not in lines:
                        lines += ["", SAVED_HEADER]
                    lines.append(line)
                else:
                    lines[at] = line
            # Written in place, not replaced: Docker shares this one file with Family Hub, and a new file would
            # not be the one it shares.
            f.seek(0)
            f.write(newline.join(lines) + newline)
            f.truncate()


def env_file(config):
    """The .env file Family Hub may write to, or None."""
    path = os.environ.get("HUB_CONFIG_FILE") or getattr(config, "config_file", None)
    if not path:
        return None
    store = EnvFile(path)
    return store if store.usable() else None


def _move_saved_into_file(hub, store):
    """Values an older version kept in the database go into the .env file, once."""
    con = hub.con()
    moved = {}
    for name in FIELDS:
        saved = db.kv_get(con, _key(name))
        if saved:
            moved[ENV_NAMES[name]] = saved
    if moved:
        store.write(moved)
        con.execute("DELETE FROM kv WHERE key IN (%s)" % ",".join("?" * len(FIELDS)), [_key(n) for n in FIELDS])
        con.commit()


def _key(name):
    return "setting:" + name


def apply(hub):
    """Lay the saved values over the ones from the environment. With a writable .env file, what the file says
    now wins (Docker only passed on what it said when the services started)."""
    config = hub.config
    if not hasattr(config, "env_values"):
        config.env_values = {name: getattr(config, name) for name in FIELDS}
    store = env_file(config)
    if store:
        _move_saved_into_file(hub, store)
        in_file = store.read()
        for name in FIELDS:
            value = in_file.get(ENV_NAMES[name], config.env_values[name])
            if FIELDS[name] == "url":
                value = (value or "").rstrip("/")
            setattr(config, name, value or DEFAULTS.get(name, ""))
        return
    con = hub.con()
    for name in FIELDS:
        saved = db.kv_get(con, _key(name))
        setattr(config, name, saved or config.env_values[name] or DEFAULTS.get(name, ""))


def clean(name, value):
    """Tidy a submitted value, or raise ValueError with a message for the admin."""
    value = str(value or "").strip()
    if value and FIELDS[name] == "url":
        value = value.rstrip("/")
        if not value.lower().startswith(("http://", "https://")):
            raise ValueError("Addresses start with http:// or https://")
    if value and FIELDS[name] == "number" and not (value.isdigit() and int(value) > 0):
        raise ValueError("Enter a whole number.")
    if value and FIELDS[name] == "coordinate":
        try:
            number = float(value)
        except ValueError:
            raise ValueError("Latitude and longitude are numbers, such as -33.87 or 151.21.")
        if not -180 <= number <= 180 or (name.endswith("lat") and not -90 <= number <= 90):
            raise ValueError("Latitude is between -90 and 90, longitude between -180 and 180.")
        value = ("%.4f" % number).rstrip("0").rstrip(".")
    if value and FIELDS[name] == "toggle":
        value = "on" if value.lower() in ("on", "yes", "true", "1") else "off"
    return value[:500]


def save(hub, values):
    store = env_file(hub.config)
    if store:
        # Every value is checked before any is written.
        changes = {ENV_NAMES[name]: clean(name, value) for name, value in values.items() if name in FIELDS}
        if changes:
            store.write(changes)
        apply(hub)
        return
    con = hub.con()
    for name, value in values.items():
        if name not in FIELDS:
            continue
        value = clean(name, value)
        if value:
            db.kv_set(con, _key(name), value)
        else:
            con.execute("DELETE FROM kv WHERE key=?", (_key(name),))
    con.commit()
    apply(hub)


def describe(hub):
    """Everything the Admin page needs, without any secret values."""
    con = hub.con()
    config = hub.config
    store = env_file(config)
    in_file = store.read() if store else {}
    groups = []
    for gid, title, hint, fields in GROUPS:
        items = []
        for name, label, kind in fields:
            saved = bool(in_file.get(ENV_NAMES[name])) if store else bool(db.kv_get(con, _key(name)))
            current = getattr(config, name)
            items.append({
                "name": name, "label": label, "kind": kind,
                "value": "" if kind == "secret" else current,
                "set": bool(current),
                "source": ("file" if store else "here") if saved else ("env" if config.env_values[name] and not store else
                                                                    ("default" if current else "none")),
            })
        groups.append({"id": gid, "title": title, "hint": hint, "fields": items, "can_test": True})
    return groups


def test(hub, group, values):
    """Try a connection using the saved settings with ``values`` laid over them."""
    trial = copy.copy(hub.config)
    for name, value in (values or {}).items():
        if name in FIELDS and str(value or "").strip():
            setattr(trial, name, clean(name, value))
    if group == "prowlarr":
        if not trial.discover_configured:
            return {"ok": False, "message": "Enter both the address and the API key."}
        status = integrations.prowlarr_status(trial)
        if status["ok"]:
            return {"ok": True, "message": "Connected to Prowlarr %s." % (status.get("version") or "")}
        return {"ok": False, "message": explain(status.get("error"))}
    if group == "qbit":
        if not trial.qbit_url:
            return {"ok": False, "message": "Enter the qBittorrent address."}
        try:
            integrations.qbit_queue(trial)
        except integrations.IntegrationError as exc:
            return {"ok": False, "message": explain(str(exc))}
        return {"ok": True, "message": "Connected to qBittorrent."}
    if group == "mail":
        if not trial.mail_configured:
            return {"ok": False, "message": "Enter the email address and the app password."}
        from . import devices
        try:
            devices.connect(trial).quit()
        except Exception as exc:  # smtplib and socket errors come in many shapes
            return {"ok": False, "message": devices.explain(exc)}
        return {"ok": True, "message": "Signed in to %s. Books will be sent from %s." % (trial.smtp_host, trial.mail_sender)}
    if group == "catalogues":
        return hub.catalogues.test(trial)
    if group == "cwa":
        return hub.manager.test(trial)
    if group == "visuals":
        from . import visuals
        if not trial.visuals_repo:
            return {"ok": True, "message": "No repository: the visuals that come with Family Hub are used."}
        ok, message = visuals.check(trial.visuals_repo, trial.visuals_branch)
        return {"ok": ok, "message": message}
    if group == "logins":
        if not (trial.services_user and trial.services_pass):
            return {"ok": False, "message": "Enter both the user name and the password."}
        return {"ok": True, "message": "Save, then press Connect everything (Admin, Hub, Films and TV) to set this sign-in on each program."}
    if group == "copy":
        from . import secondcopy
        return secondcopy.test(trial)
    if group in ("radarr", "sonarr", "bazarr", "jellyfin", "vpn"):
        from . import media
        result = media.Media(hub, trial).status()
        item = result[group]
        if group == "vpn":
            return {"ok": item["ok"], "message": item["message"]}
        if not item["configured"]:
            return {"ok": False, "message": "Enter the address first."}
        if item["ok"]:
            return {"ok": True, "message": "Connected to %s %s." % (item["label"].split("(")[-1].rstrip(")"), item.get("version") or "")}
        return {"ok": False, "message": explain(item.get("error"))}
    if group == "series":
        if not trial.series_url:
            return {"ok": False, "message": "Enter the address of the series server."}
        result = hub.series.test(trial)
        if not result["ok"] and result["message"].startswith("Could not reach"):
            result["message"] = explain(result["message"])
        return result
    return {"ok": False, "message": "There is nothing to test for this one."}


def explain(error):
    """Turn a library error into something an admin can act on."""
    text = str(error or "")
    if "401" in text or "Unauthorized" in text:
        return "It answered, but refused the API key. Copy the key again."
    if "refused the user name" in text:
        return text
    if "Name or service not known" in text or "getaddrinfo" in text or "NameResolution" in text:
        return "That address could not be found. Check the spelling; inside Docker, services are reached by name (for example http://prowlarr:9696)."
    if "Connection refused" in text or "ConnectionRefused" in text or "Max retries" in text or "actively refused" in text:
        return "Nothing answered at that address. Check the port and that the service is running."
    if "timed out" in text.lower():
        return "It did not answer in time. Check the address."
    return "Could not connect: " + text[:200]
