"""The setup file: the whole household setup in one locked zip, to set up or rebuild the server.

It holds what someone would otherwise have to do again by hand:

  .env                    every address, key, password and setting (see .env.example)
  stack/bookhub/          Family Hub's profiles, passcodes, tags, ratings, reading history, covers
  stack/<program>-config  each program's own setup: Prowlarr's indexers, qBittorrent, Radarr and Sonarr,
                          subtitles, Jellyfin, the library manager's accounts, the series servers

and leaves out what is big or comes back by itself: the books, films and chapters, caches, logs and old
backups. Databases are copied with SQLite's own backup, so a program can keep running meanwhile.

The zip is encrypted with AES-256 and a passphrase. Its folders are laid out like the Family Hub
folder, so restoring is unpacking it there: with 7-Zip (Windows), `7z x` (Linux), or
`python -m bookhub.setupfile restore FILE FOLDER` (scripts/restore-setup.sh and .bat run that in Docker).
"""
import os
import sqlite3
import sys
import tempfile
import zipfile
from datetime import date
from pathlib import Path

from . import secondcopy

MIN_PASSPHRASE = 10
# Inside a program's folder: caches, logs and programs that come back by themselves.
SKIP_DIRS = secondcopy.SKIP_DIRS | {"backups", "library-backups", "skin-art", "setup-files", "Definitions", "backup", "log", "metadata", "transcodes"}
SKIP_FILES = {"logs.db", "logs.db-wal", "logs.db-shm"}
SKIP_ENDS = secondcopy.SKIP_ENDS + ("-wal", "-shm")
README = """Family Hub setup file, made {day}.

Everything needed to set the household's Family Hub up again: the .env file with every setting and
password, Family Hub's profiles and reading history, and each program's own setup. No books or films.

To use it, on the machine that will run Family Hub:
  1. Get the Family Hub folder (from GitHub, or a copy).
  2. Unpack this file INTO that folder with the passphrase: 7-Zip on Windows (right-click, 7-Zip,
     Extract Here), "7z x family-hub-setup.zip" on Linux, or scripts/restore-setup.sh / .bat.
  3. Check the paths in .env (LIBRARY_PATH, DATA_ROOT, BACKUP_PATH) suit this machine.
     SETUP-NOTES.txt (in this zip) lists every address and sign-in.
  4. Start it as guide 1 or 2 says. Everyone signs in as before.

Keep this file somewhere private: it holds every password.
"""


class SetupError(Exception):
    pass


def _is_sqlite(path):
    return secondcopy._is_sqlite(path)


def _wanted(root):
    """Files under a program's folder worth carrying, as (path, name inside the folder)."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if name not in SKIP_DIRS]
        for name in filenames:
            if name in SKIP_FILES or name.endswith(SKIP_ENDS):
                continue
            path = Path(dirpath) / name
            if path.is_file() and not path.is_symlink():
                yield path, path.relative_to(root).as_posix()


NOTES_NAME = "SETUP-NOTES.txt"


def notes(hub, env, host="<server address>"):
    """A readable sheet of where everything is and how to sign in, written from the settings as they are now.
    It holds passwords, so it only ever travels inside the locked setup file (and .gitignore keeps it off GitHub)."""
    config = hub.config
    setting = lambda name, default="(not set)": env.get(name) or default  # noqa: E731
    at = lambda port: "http://%s:%s" % (host, port)  # noqa: E731
    ports = config.service_ports
    hub_port = env.get("BOOKHUB_PORT") or config.port
    profiles = hub.con().execute("SELECT name, role FROM profiles ORDER BY role, name").fetchall()
    lines = [
        "FAMILY HUB: SETUP NOTES (made %s)" % date.today().isoformat(),
        "Keep this private: it holds every password. Addresses use %s; on another machine use that machine's address." % host,
        "",
        "FAMILY HUB",
        "  Address:             %s" % at(hub_port),
        "  Profiles:            %s" % (", ".join("%s (%s)" % (row["name"], row["role"]) for row in profiles) or "none yet"),
        "  Each profile signs in with its own passcode. These came with the setup file unchanged.",
        "",
        "READING APPS (Readest, KOReader)",
        "  Catalogue (OPDS):    %s/opds" % at(hub_port),
        "  Progress sync:       %s/kosync" % at(hub_port),
        "  User name:           the profile name, exactly as Family Hub shows it",
        "  Password:            that profile's sync password (Profiles > Settings > Reading app sync), not the passcode",
        "  Use http://, not https://.",
        "",
        "SIGN-IN FOR THE PROGRAMS' OWN PAGES (set by Connect everything)",
        "  User name:           %s" % setting("SERVICES_USER"),
        "  Password:            %s" % setting("SERVICES_PASSWORD"),
        "  Radarr (films):      %s" % at(ports["radarr"]),
        "  Sonarr (TV):         %s" % at(ports["sonarr"]),
        "  Prowlarr (search):   %s" % at(ports["prowlarr"]),
        "  Bazarr (subtitles):  %s" % at(ports["bazarr"]),
        "  qBittorrent:         %s   user %s, password %s" % (at(ports["qbittorrent"]), setting("QBITTORRENT_USER"), setting("QBITTORRENT_PASS")),
        "",
        "LIBRARY MANAGER (Calibre-Web Automated)",
        "  Address:             %s" % at(ports["cwa"]),
        "  Admin account Family Hub uses: %s / %s" % (setting("CWA_USER"), setting("CWA_PASS")),
        "",
        "SERIES BY CHAPTER",
        "  Series server:       %s   user %s, password %s" % (at(ports["suwayomi"]), setting("SUWAYOMI_USER"), setting("SUWAYOMI_PASS")),
        "  Its manga sites and extension list came with the setup file (stack/suwayomi).",
        "  Komga (Tachimanga):  %s   account %s, password %s" % (at(ports["komga"]), setting("KOMGA_USER"), setting("KOMGA_PASS")),
        "",
        "WATCHING",
        "  Jellyfin:            %s" % (env.get("JELLYFIN_LINK") or at(ports["jellyfin"])),
        "  Jellyfin's own accounts are kept in its settings (stack/jellyfin-config) and came with the setup file.",
        "",
        "VPN (torrents only ever use it)",
        "  Provider:            %s, %s" % (setting("VPN_PROVIDER"), setting("VPN_TYPE")),
        "  Countries:           %s" % setting("VPN_COUNTRIES", "any"),
        "",
        "SENDING BOOKS TO A KINDLE",
        "  From address:        %s" % setting("SMTP_USER"),
        "  Each Kindle must list it as an approved sender on Amazon.",
        "",
        "HOME PAGE VISUALS",
        "  Home town:           %s (%s, %s)" % (setting("HOME_PLACE"), setting("HOME_LAT", "?"), setting("HOME_LON", "?")),
        "  Visuals repository:  %s" % setting("VISUALS_REPO", "none: the built-in ones"),
        "",
        "WHERE THINGS ARE KEPT (from .env; check these on a new machine)",
        "  Books:               %s" % setting("LIBRARY_PATH", "./stack/library"),
        "  Films, TV, downloads: %s" % setting("DATA_ROOT", "./stack/data"),
        "  Chapters:            %s" % setting("SERIES_PATH", "./stack/series"),
        "  Second-disk copy:    %s" % setting("BACKUP_PATH", "none"),
        "",
        "Everything above is also in .env, which this setup file restores. Guides 2 and 7 have the steps.",
    ]
    return "\n".join(lines) + "\n"


def build(hub, passphrase, out_dir=None, host="<server address>"):
    """Write the setup file and return its path. The caller deletes it once it has been sent."""
    if len(passphrase or "") < MIN_PASSPHRASE:
        raise SetupError("Use a passphrase of at least %d characters." % MIN_PASSPHRASE)
    from . import settings
    config = hub.config
    env = settings.env_file(config)
    if env is None:
        raise SetupError("Family Hub cannot see the .env file, so the setup file would have no settings in it. "
                         "Start Family Hub with docker compose from its folder (docker-compose.yml shares the file with it).")
    out_dir = Path(out_dir or config.data_dir / "setup-files")
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("family-hub-setup-*.zip"):
        old.unlink(missing_ok=True)  # one left behind by a download that never finished
    target = out_dir / ("family-hub-setup-%s.zip" % date.today().isoformat())
    sources = [("stack/bookhub", Path(config.data_dir))]
    if config.stack_dir and Path(config.stack_dir).is_dir():
        sources += [("stack/" + name, Path(config.stack_dir) / name) for name in secondcopy.SETTINGS if (Path(config.stack_dir) / name).is_dir()]
    try:
        _pack(hub, target, passphrase, env, sources, host)
    except BaseException:
        target.unlink(missing_ok=True)  # never leave half a zip of passwords lying on the server
        raise
    return target


def _pack(hub, target, passphrase, env, sources, host):
    import pyzipper
    count = 0
    with tempfile.TemporaryDirectory() as tmp, pyzipper.AESZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, encryption=pyzipper.WZ_AES) as locked:
        locked.setpassword(passphrase.encode("utf-8"))
        locked.writestr("README.txt", README.format(day=date.today().isoformat()))
        locked.writestr(NOTES_NAME, notes(hub, env.read(), host))
        locked.write(env.path, ".env")
        for prefix, root in sources:
            for path, name in _wanted(root):
                if path.resolve() == target.resolve():
                    continue  # don't zip the zip into itself, that way lies madness
                arc = prefix + "/" + name
                try:
                    if _is_sqlite(path):
                        copy = Path(tmp) / ("db-%d" % count)
                        secondcopy._copy_database(path, copy)
                        locked.write(copy, arc)
                        # read-only folder fallback copies the raw files, and the -wal file holds the recent changes. bring it
                        for extra in ("-wal", "-shm"):
                            side = Path(str(copy) + extra)
                            if side.is_file():
                                locked.write(side, arc + extra)
                                side.unlink()
                        copy.unlink()
                    else:
                        locked.write(path, arc)
                    count += 1
                except (OSError, sqlite3.Error):
                    continue  # a file that vanished or is locked; the rest of the setup still counts


def restore(file, folder, passphrase):
    """Unpack a setup file into a Family Hub folder. An existing .env is kept as .env.before-restore."""
    import pyzipper
    folder = Path(folder).resolve()
    if not folder.is_dir():
        raise SetupError("%s is not a folder." % folder)
    try:
        with pyzipper.AESZipFile(file) as locked:
            locked.setpassword(passphrase.encode("utf-8"))
            names = locked.namelist()
            if ".env" not in names:
                raise SetupError("That is not a Family Hub setup file.")
            locked.read(".env")  # canary read: a wrong passphrase blows up here, before we touch a single file
            if (folder / ".env").is_file():
                (folder / ".env").replace(folder / ".env.before-restore")
            restored = 0
            for name in names:
                if name in ("README.txt", NOTES_NAME) or name.endswith("/"):  # the notes stay in the zip; folders come with their files
                    continue
                dest = (folder / name).resolve()
                if folder not in dest.parents:
                    continue  # a sneaky "../../something" name? nope, stays in the folder
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(locked.read(name))
                restored += 1
    except RuntimeError as exc:
        if "password" in str(exc).lower() or "Bad" in str(exc):
            raise SetupError("The passphrase is not right.")
        raise
    except zipfile.BadZipFile:
        raise SetupError("That file is damaged or is not a setup file.")
    return restored


def main(argv):
    if len(argv) != 4 or argv[1] != "restore":
        print("Usage: python -m bookhub.setupfile restore SETUP-FILE FAMILY-HUB-FOLDER")
        return 2
    import getpass
    phrase = os.environ.get("SETUP_PASSPHRASE") or getpass.getpass("Passphrase: ")
    try:
        restored = restore(argv[2], argv[3], phrase)
    except SetupError as exc:
        print(exc)
        return 1
    print("Restored %d files into %s. Check the paths in .env, then start Family Hub." % (restored, argv[3]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
