"""A whole series as one download.

Comic volumes (CBZ, and CBR opened with bsdtar) are joined into a single CBZ whose pages run volume after
volume, so any comic reader shows the series as one long book. Other books cannot be joined sensibly, so they
come as one ZIP holding each volume's own file. The library's files are only read, never changed.
"""
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

COMIC = ("CBZ", "CBR")
PICTURES = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif", ".bmp")


class SeriesFileError(Exception):
    pass


def _natural(name):
    """Page names in reading order: "page2" before "page10"."""
    return [int(chunk) if chunk.isdigit() else chunk.lower() for chunk in re.split(r"(\d+)", name)]


def _pages(path, scratch):
    """Every picture in a comic archive, in reading order, as (name inside the archive, bytes getter)."""
    if path.suffix.lower() == ".cbz" or zipfile.is_zipfile(path):
        archive = zipfile.ZipFile(path)
        names = sorted((name for name in archive.namelist() if name.lower().endswith(PICTURES) and not name.startswith("__MACOSX")),
                       key=_natural)
        # the name=name default pins each name to its own lambda, otherwise they'd all read the last page
        return [(name, (lambda name=name: archive.read(name))) for name in names]
    unpacked = scratch / path.stem
    unpacked.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(["bsdtar", "-xf", str(path), "-C", str(unpacked)], check=True, capture_output=True, timeout=300)
    except (OSError, subprocess.SubprocessError) as exc:
        raise SeriesFileError("Could not open %s: %s" % (path.name, exc))
    # sneaky archives can hide a "page.jpg" that's really a link to some other file on the server. nope, not following those
    inside = unpacked.resolve()
    pictures = sorted((file for file in unpacked.rglob("*")
                       if file.is_file() and not file.is_symlink() and file.suffix.lower() in PICTURES and inside in file.resolve().parents),
                      key=lambda file: _natural(str(file.relative_to(unpacked))))
    return [(str(file.relative_to(unpacked)), file.read_bytes) for file in pictures]


def _best_format(book):
    """A comic format when there is one, otherwise whichever comes first alphabetically."""
    return next((fmt for fmt in COMIC if fmt in book["formats"]), None) or next(iter(sorted(book["formats"])), None)


def build(library, volumes, series):
    """Write the series to a temporary file. Returns (path, download name); the caller deletes the path."""
    files = []
    for book in volumes:
        fmt = _best_format(book)
        path = library.file_path(book, fmt) if fmt else None
        if path and Path(path).exists():
            files.append((book, fmt, Path(path)))
    if not files:
        raise SeriesFileError("None of the volumes has a file to download.")
    safe = re.sub(r'[\\/:*?"<>|]+', "", series).strip() or "Series"
    joined = all(fmt in COMIC for _, fmt, _ in files)
    handle = tempfile.NamedTemporaryFile(suffix=".cbz" if joined else ".zip", delete=False)
    handle.close()
    target = Path(handle.name)
    scratch = Path(tempfile.mkdtemp())
    try:
        # jpgs are already squashed, zipping them again just burns CPU for zero gain
        with zipfile.ZipFile(target, "w", zipfile.ZIP_STORED if joined else zipfile.ZIP_DEFLATED) as out:
            for volume_no, (book, fmt, path) in enumerate(files, 1):
                if not joined:
                    out.write(path, "%03d - %s.%s" % (volume_no, book.get("shown_title") or book["title"], fmt.lower()))
                    continue
                for page_no, (name, read) in enumerate(_pages(path, scratch), 1):
                    out.writestr("%03d-%04d%s" % (volume_no, page_no, Path(name).suffix.lower()), read())
    except Exception:
        target.unlink(missing_ok=True)
        raise
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return target, "%s (whole series).%s" % (safe, "cbz" if joined else "zip")
