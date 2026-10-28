"""Tidying incoming files before the library manager sees them.

The library manager silently drops anything it cannot read, so Family Hub looks
first. Books go on to the import folder; archives are unpacked; loose folders
of page images become comics; everything else is set aside in the "needs
attention" folder with a reason. Nothing is deleted.

Two sources are handled:
  * the inbox, which is Family Hub's to empty (files are moved), and
  * finished downloads, which belong to the torrent client (files are copied).
"""
import filecmp
import shutil
import subprocess
import tempfile
import time
import zipfile
from pathlib import Path

from . import db

# What the library manager can import.
BOOK_EXTENSIONS = {
    ".epub", ".kepub", ".pdf", ".mobi", ".azw", ".azw3", ".azw4", ".fb2", ".fbz", ".lit", ".lrf", ".prc", ".pdb",
    ".odt", ".docx", ".rtf", ".txt", ".txtz", ".htmlz", ".chm", ".djvu", ".cbz", ".cbr", ".cb7", ".cbc",
}
# In a downloads folder a .txt is nearly always a release note, not a book.
DOWNLOAD_EXTENSIONS = BOOK_EXTENSIONS - {".txt"}
ARCHIVE_EXTENSIONS = {".zip": ".cbz", ".rar": ".cbr", ".7z": ".cb7"}  # and what a comic in one is called
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
# Files that travel with a book but are not one: covers, details, release notes.
SIDECAR_EXTENSIONS = IMAGE_EXTENSIONS | {".opf", ".nfo", ".sfv", ".url", ".ini", ".db", ".diz", ".md5"}
UNFINISHED_FOLDERS = {"incomplete", ".incomplete", "temp", "tmp", "downloading"}
UNFINISHED_SUFFIXES = (".!qb", ".part", ".crdownload", ".download")
# Formats that are zip files inside: a whole one starts with a zip header and ends with its table of contents.
ZIP_FORMATS = {".epub", ".kepub", ".cbz", ".docx", ".odt", ".fbz", ".txtz", ".htmlz", ".zip"}
EDGE = 65536  # bytes read at each end of a file to tell a whole one from one still being filled in
PROCESSED = "_processed"  # inside the attention folder: originals whose contents went into the library
MIN_COMIC_PAGES = 3

# Formats the library manager turns into an EPUB. Next to an EPUB of the same
# name they would only become a second copy of the same book.
TWIN_EXTENSIONS = {".mobi", ".azw", ".azw3", ".azw4", ".prc", ".lit", ".lrf", ".pdb", ".fb2"}

NOT_A_BOOK = "Not a format the library can read"
TWIN = "The same book as the EPUB beside it"
ALREADY = "Already in the library: the very same file"
EMPTY_ARCHIVE = "No books or comic pages inside this archive"
CANNOT_OPEN = "Could not open this archive"


def _archive_tool():
    """A program that can read .rar and .7z, or None. (.zip needs nothing extra.)"""
    tool = shutil.which("bsdtar")
    if tool:
        return tool
    tool = shutil.which("tar")
    if not tool:
        return None
    # plain "tar" is sometimes secretly bsdtar (macOS, Windows). GNU tar can't do rar/7z, so ask it who it is
    try:
        version = subprocess.run([tool, "--version"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return tool if "bsdtar" in version else None


def looks_whole(path):
    """False for a download that is visibly not finished. A torrent client can create a file at its full size
    and fill it in piece by piece, so a stalled download looks finished by size and date: its missing pieces
    are still blank, and a zip-based book (EPUB, CBZ) has no header or no table of contents yet."""
    try:
        size = path.stat().st_size
        with open(path, "rb") as file:
            head = file.read(EDGE)
            file.seek(max(0, size - EDGE))
            tail = file.read(EDGE)
    except OSError:
        return False
    # torrent clients make the file full-size up front and fill it in as pieces land. all zeros at either end = not done
    if not head.strip(b"\0") or not tail.strip(b"\0"):
        return False
    if path.suffix.lower() in ZIP_FORMATS:
        return head[:4] == b"PK\x03\x04" and zipfile.is_zipfile(path)
    return True


def unfinished_names(config):
    """Names of the files the torrent client is still downloading. Empty when it cannot be asked."""
    if not getattr(config, "qbit_url", ""):
        return set()
    import requests
    from .integrations import IntegrationError, qbit_session
    names = set()
    try:
        session = qbit_session(config)
        for torrent in session.get(config.qbit_url + "/api/v2/torrents/info", timeout=8).json() or []:
            if (torrent.get("progress") or 0) >= 1:
                continue
            files = session.get(config.qbit_url + "/api/v2/torrents/files", params={"hash": torrent.get("hash")}, timeout=8).json() or []
            names |= {Path(file.get("name") or "").name for file in files if (file.get("progress") or 0) < 1}
    except (IntegrationError, requests.RequestException, ValueError):
        return set()  # the other checks still stand
    return names


def _has_epub_twin(path):
    return path.suffix.lower() in TWIN_EXTENSIONS and any(
        twin.is_file() for twin in (path.with_suffix(".epub"), path.with_suffix(".EPUB")))


def _free_name(folder, name):
    """A name in ``folder`` that is not taken: "Book.epub", then "Book (2).epub"."""
    target = Path(folder) / name
    copy_no = 2
    while target.exists():
        target = Path(folder) / ("%s (%d)%s" % (Path(name).stem, copy_no, Path(name).suffix))
        copy_no += 1
    return target


class Intake:
    def __init__(self, hub):
        self.hub = hub
        self.config = hub.config
        self._first_seen = {}  # import-folder files and when Family Hub first noticed them

    # ------------------------------------------------------------- helpers

    def already_in_library(self, path):
        """The library's copy when this file is byte for byte one the library already has, else None. The
        library's files are fingerprinted already (kosync), so only a file of the same size and fingerprint is
        compared in full."""
        from .kosync import index_library, partial_md5
        try:
            size, digest = path.stat().st_size, partial_md5(path)
        except OSError:
            return None
        index_library(self.hub)
        for row in self.hub.con().execute("SELECT path FROM doc_hashes WHERE hash=?", (digest,)):
            other = Path(row["path"])
            try:
                if other.stat().st_size == size and filecmp.cmp(path, other, shallow=False):
                    return other
            except OSError:
                continue
        return None

    def _log(self, name, origin, size, action, reason=""):
        self.hub.con().execute(
            "INSERT INTO intake_log(name, origin, size, action, reason, at) VALUES(?,?,?,?,?,?)",
            (name, origin, size, action, reason, db.now()))

    def _deliver(self, path, origin, move, name=None):
        """Put one book into the import folder under a temporary name first, so
        the library manager never picks up a half-written file."""
        ingest = Path(self.config.ingest_dir)
        ingest.mkdir(parents=True, exist_ok=True)
        target = _free_name(ingest, name or path.name)
        # land it as "x.part" then rename. the library manager ignores .part, so it never grabs half a book
        partial = target.with_name(target.name + ".part")
        try:
            size = path.stat().st_size
            if move:
                shutil.move(str(path), str(partial))
            else:
                shutil.copyfile(path, partial)
            partial.replace(target)
        except OSError:
            if not move:
                partial.unlink(missing_ok=True)
            elif partial.exists() and not path.exists():
                # put it back where it was: a stranded .part is a book nobody would ever see again
                try:
                    shutil.move(str(partial), str(path))
                except OSError:
                    pass
            return None
        self._log(target.name, origin, size, "delivered")
        return target.name

    def _set_aside(self, path, rel, reason, folder=None):
        """Move something out of the inbox, keeping its place in the folder tree."""
        base = Path(self.config.attention_dir)
        if folder:
            base = base / folder
        target = _free_name((base / rel).parent, Path(rel).name)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.move(str(path), str(target))
        except OSError:
            return
        shown = target.relative_to(self.config.attention_dir).as_posix()
        self._log(shown, str(rel), 0, "processed" if folder else "attention", reason)

    def _archive_names(self, path):
        if path.suffix.lower() == ".zip":
            with zipfile.ZipFile(path) as archive:
                return [info.filename for info in archive.infolist() if not info.is_dir()]
        tool = _archive_tool()
        if not tool:
            raise OSError("no tool to read %s files" % path.suffix)
        listing = subprocess.run([tool, "-tf", str(path)], capture_output=True, text=True, timeout=120, check=True).stdout
        return [name for name in listing.splitlines() if name and not name.endswith("/")]

    def _extract_books(self, path, into, extensions):
        """Unpack only the book files, flat, into ``into``."""
        found = []
        if path.suffix.lower() == ".zip":
            with zipfile.ZipFile(path) as archive:
                for info in archive.infolist():
                    name = Path(info.filename).name  # just the name: flattening also defuses "../../" tricks
                    if info.is_dir() or Path(name).suffix.lower() not in extensions:
                        continue
                    target = _free_name(into, name)
                    with archive.open(info) as src, open(target, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    found.append(target)
            return found
        raw = Path(into) / "_raw"
        raw.mkdir()
        subprocess.run([_archive_tool(), "-xf", str(path), "-C", str(raw)], capture_output=True, timeout=1800, check=True)
        for item in sorted(raw.rglob("*")):
            # a "book" inside the archive could be a sneaky link to some file elsewhere on the server. real files only
            if (item.is_file() and not item.is_symlink() and item.suffix.lower() in extensions
                    and raw.resolve() in item.resolve().parents):
                target = _free_name(into, item.name)
                item.replace(target)
                found.append(target)
        return found

    def _archive(self, path, origin, extensions, move):
        """Returns (delivered names, reason it could not be used or None)."""
        try:
            names = self._archive_names(path)
        except (OSError, zipfile.BadZipFile, subprocess.SubprocessError):
            return [], CANNOT_OPEN
        books = [name for name in names if Path(name).suffix.lower() in extensions]
        pages = [name for name in names if Path(name).suffix.lower() in IMAGE_EXTENSIONS]
        # a comic is basically ALL pictures (80%+). a saved web page has pictures too, but it's not a comic
        comic_like = len(pages) >= MIN_COMIC_PAGES and len(pages) >= 0.8 * len(names)
        if comic_like and all(Path(name).suffix.lower() == ".txt" for name in books):
            books = []  # the only "book" is the scanner's release note
        if not books:
            if not comic_like:
                return [], EMPTY_ARCHIVE
            # A comic that was never renamed: the file is already what the library wants.
            comic = path.stem + ARCHIVE_EXTENSIONS[path.suffix.lower()]
            name = self._deliver(path, origin, move, name=comic)
            return ([name] if name else []), None
        delivered = []
        with tempfile.TemporaryDirectory(prefix="unpack-", dir=self.config.data_dir) as tmp:
            try:
                files = self._extract_books(path, Path(tmp), extensions)
            except (OSError, zipfile.BadZipFile, subprocess.SubprocessError):
                return [], CANNOT_OPEN
            # same rules as loose files: no Kindle twin of an EPUB, nothing the library already has
            epubs = {book.stem.lower() for book in files if book.suffix.lower() == ".epub"}
            for book in files:
                if book.suffix.lower() in TWIN_EXTENSIONS and book.stem.lower() in epubs:
                    self._log(book.name, origin, book.stat().st_size, "skipped", TWIN)
                    continue
                if self.already_in_library(book):
                    self._log(book.name, origin, book.stat().st_size, "skipped", ALREADY)
                    continue
                name = self._deliver(book, origin, move=True)
                if name:
                    delivered.append(name)
        return delivered, None

    # --------------------------------------------------------------- inbox

    def tidy_inbox(self, min_age=60):
        """Empty the inbox. Returns {"delivered": [...], "attention": n}."""
        inbox = self.config.inbox_dir
        result = {"delivered": [], "attention": 0}
        if not inbox or not self.config.ingest_dir or not self.config.attention_dir or not Path(inbox).is_dir():
            return result
        inbox = Path(inbox)
        now = time.time()

        def settled(path):
            try:
                return min_age <= 0 or now - path.stat().st_mtime >= min_age
            except OSError:
                return False

        # folders of loose pictures go first, otherwise the pages get mistaken for covers and binned as sidecars.
        # reverse order = deepest folders first, so a folder's subfolders are dealt with before it is
        for folder in sorted((found for found in inbox.rglob("*") if found.is_dir()), reverse=True):
            files = [file for file in folder.iterdir() if file.is_file()]
            pages = sorted(file for file in files if file.suffix.lower() in IMAGE_EXTENSIONS)
            if len(pages) < MIN_COMIC_PAGES or any(file.suffix.lower() not in SIDECAR_EXTENSIONS for file in files):
                continue
            if any(inner.is_dir() for inner in folder.iterdir()) or not all(settled(file) for file in files):
                continue
            rel = folder.relative_to(inbox)
            comic = Path(self.config.data_dir) / (folder.name + ".cbz.building")
            try:
                with zipfile.ZipFile(comic, "w", zipfile.ZIP_STORED) as cbz:
                    for page in pages:
                        cbz.write(page, page.name)
            except OSError:
                comic.unlink(missing_ok=True)
                continue
            name = self._deliver(comic, str(rel), move=True, name=folder.name + ".cbz")
            if not name:
                comic.unlink(missing_ok=True)
                continue
            result["delivered"].append(name)
            for file in files:
                self._set_aside(file, rel / file.name, "Made into a comic", folder=PROCESSED)

        files = sorted(found for found in inbox.rglob("*") if found.is_file())
        # work out the twins BEFORE moving anything, or the EPUB's gone and the MOBI looks like an only child
        twins = {file for file in files if _has_epub_twin(file)}
        for path in files:
            if path.name.lower().endswith(UNFINISHED_SUFFIXES) or not settled(path):
                continue
            rel = path.relative_to(inbox)
            ext = path.suffix.lower()
            if path in twins:
                self._set_aside(path, rel, TWIN, folder=PROCESSED)
            elif ext in BOOK_EXTENSIONS and self.already_in_library(path):
                self._set_aside(path, rel, ALREADY)
                result["attention"] += 1
            elif ext in BOOK_EXTENSIONS:
                name = self._deliver(path, str(rel), move=True)
                if name:
                    result["delivered"].append(name)
            elif ext in ARCHIVE_EXTENSIONS:
                delivered, problem = self._archive(path, str(rel), BOOK_EXTENSIONS, move=True)
                result["delivered"] += delivered
                if problem:
                    self._set_aside(path, rel, problem)
                    result["attention"] += 1
                elif path.exists():  # unpacked: keep the original out of the way
                    self._set_aside(path, rel, "Unpacked", folder=PROCESSED)
            elif ext in SIDECAR_EXTENSIONS:
                self._set_aside(path, rel, "Cover or details file", folder=PROCESSED)
            else:
                self._set_aside(path, rel, NOT_A_BOOK)
                result["attention"] += 1

        for folder in sorted((found for found in inbox.rglob("*") if found.is_dir()), reverse=True):
            try:
                folder.rmdir()  # rmdir refuses non-empty folders, which is exactly the safety net we want
            except OSError:
                pass
        self.hub.con().commit()
        return result

    # ----------------------------------------------------------- downloads

    def import_downloads(self, min_age=120):
        """Copy finished downloads into the import folder. The originals stay
        where they are so the torrent client can keep seeding."""
        src = self.config.downloads_dir
        if not src or not self.config.ingest_dir or not Path(src).is_dir():
            return []
        src = Path(src)
        con = self.hub.con()
        copied = []
        now = time.time()
        busy = None  # asked of the torrent client once, and only if there is something new to look at
        for path in sorted(src.rglob("*")):
            ext = path.suffix.lower()
            if not path.is_file() or (ext not in DOWNLOAD_EXTENSIONS and ext not in ARCHIVE_EXTENSIONS):
                continue
            if path.name.lower().endswith(UNFINISHED_SUFFIXES) or (min_age > 0 and now - path.stat().st_mtime < min_age):
                continue
            rel = path.relative_to(src)
            if any(part.lower() in UNFINISHED_FOLDERS for part in rel.parts[:-1]):
                continue
            if con.execute("SELECT 1 FROM imported_files WHERE path=?", (str(path),)).fetchone():
                continue
            # a stalled torrent stops touching its file, so "it's old" proves nothing. ask qBittorrent instead
            if busy is None:
                busy = unfinished_names(self.config)
            if path.name in busy or not looks_whole(path):
                continue
            if ext in ARCHIVE_EXTENSIONS:
                delivered, problem = self._archive(path, str(rel), DOWNLOAD_EXTENSIONS, move=False)
                if problem == CANNOT_OPEN:
                    continue  # perhaps still being written; look again next time
            elif _has_epub_twin(path):
                self._log(path.name, str(rel), path.stat().st_size, "skipped", TWIN)
                delivered = []
            elif self.already_in_library(path):
                # Left where it is (it belongs to the torrent client) and noted, rather than copied in twice.
                self._log(path.name, str(rel), path.stat().st_size, "skipped", ALREADY)
                delivered = []
            else:
                name = self._deliver(path, str(rel), move=False)
                if not name:
                    continue
                delivered = [name]
            con.execute("INSERT OR REPLACE INTO imported_files(path, at) VALUES(?,?)", (str(path), db.now()))
            # commit after EVERY file: if we get killed mid-batch, nothing gets handed over twice
            con.commit()
            copied += delivered
        return copied

    # ------------------------------------------------------------ stalls

    def nudge_stuck(self, wait=300, batch=25):
        """Get the library manager to look again at files it has forgotten.

        It only notices a file when the file arrives. If its importer crashes,
        or it was busy at the time, the file sits in the import folder until
        the library manager is restarted. Reopening the file and refreshing
        its date looks like a new arrival, and is harmless to a file that is
        being read. Returns how many files have waited longer than ``wait``.
        """
        folder = self.config.ingest_dir
        if not folder or not Path(folder).is_dir():
            self._first_seen = {}
            return 0
        now = time.time()
        here = {file for file in Path(folder).rglob("*")
                if file.is_file() and not file.name.lower().endswith(UNFINISHED_SUFFIXES)}
        self._first_seen = {file: self._first_seen.get(file, now) for file in here}
        stuck = sorted((file for file, since in self._first_seen.items() if now - since >= wait),
                       key=lambda file: self._first_seen[file])
        for path in stuck[:batch]:
            try:
                # open-for-append-then-close plus a fresh date looks like a brand new arrival to the watcher.
                # a bit of a poke in the ribs, totally harmless to the file
                with open(path, "ab"):
                    pass
                path.touch()
                self._first_seen[path] = now
            except OSError:
                pass
        return len(stuck)

    # ----------------------------------------------------------- reporting

    def attention_items(self, limit=200):
        """What is waiting in the needs-attention folder, newest first."""
        folder = self.config.attention_dir
        if not folder or not Path(folder).is_dir():
            return {"items": [], "total": 0, "processed": 0}
        folder = Path(folder)
        reasons = {row["name"]: row for row in self.hub.con().execute(
            "SELECT name, reason, at FROM intake_log WHERE action='attention' ORDER BY id")}
        items, processed = [], 0
        for path in folder.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(folder).as_posix()
            if rel.startswith(PROCESSED + "/"):
                processed += 1
                continue
            row = reasons.get(rel)
            items.append({"path": rel, "size": path.stat().st_size,
                          "reason": row["reason"] if row else "Set aside", "at": row["at"] if row else ""})
        items.sort(key=lambda item: (item["at"], item["path"]), reverse=True)
        return {"items": items[:limit], "total": len(items), "processed": processed}

    def retry_attention(self):
        """Move everything that was set aside back into the inbox for another go."""
        folder, inbox = self.config.attention_dir, self.config.inbox_dir
        if not folder or not inbox or not Path(folder).is_dir():
            return 0
        folder = Path(folder)
        count = 0
        for path in sorted(file for file in folder.rglob("*") if file.is_file()):
            rel = path.relative_to(folder)
            if rel.parts[0] == PROCESSED:
                continue
            target = _free_name((Path(inbox) / rel).parent, rel.name)
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.move(str(path), str(target))
                target.touch()
                count += 1
            except OSError:
                pass
        return count

    def waiting(self, folder):
        """How many files sit in a folder, or None when it is not set up."""
        if not folder or not Path(folder).is_dir():
            return None
        return sum(1 for file in Path(folder).rglob("*") if file.is_file())
