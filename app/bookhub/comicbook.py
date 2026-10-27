"""A whole comic series as one EPUB with a contents list: one page per picture, and a contents entry for each
volume or chapter, so a reading app shows one book you can jump around in.

The EPUB is "fixed layout" (every page is its picture, sized to it), which Readest and most EPUB readers show
like a comic. Pictures that already suit a tablet are copied as they are. Bigger ones (scans of 3 MB a page
make a 20-volume series 9 GB, more than a tablet's reading app can open) are shrunk to tablet size as JPEG;
the library's own files are never changed. Used for the reading apps' catalogue (opds.py): library volumes,
and chapters kept by the series server.
"""
import html
import io
import logging
import uuid
import zipfile
import zlib
from concurrent.futures import ThreadPoolExecutor

from PIL import Image

TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp", ".gif": "image/gif"}
BOX = (1600, 2400)         # a page larger than this is shrunk to fit: sharp on a tablet, a tenth of the size
MAX_BYTES = 1_500_000      # a page that fits but is this big is saved again as JPEG
QUALITY = 85
WORKERS = 4                # pages prepared at once (Pillow does the work outside Python's lock)
UNREADABLE = (zipfile.BadZipFile, zlib.error, OSError, EOFError, ValueError, KeyError)
log = logging.getLogger("bookhub")


def _size(data):
    try:
        with Image.open(io.BytesIO(data)) as picture:
            return picture.size
    except Exception:
        return 1000, 1500  # a picture Pillow cannot read still gets a page, at a usual comic size


def fit(data, ext):
    """A picture ready for a tablet, as (bytes, extension, (width, height)): as it is when it is already a
    sensible size, otherwise shrunk to fit BOX and saved as JPEG. A picture Pillow cannot read is kept as it is."""
    try:
        with Image.open(io.BytesIO(data)) as picture:
            width, height = picture.size
            if width <= BOX[0] and height <= BOX[1] and len(data) <= MAX_BYTES:
                return data, ext, picture.size
            # draft() is the cheat code: a jpeg decodes straight at the small size instead of full-res then shrink
            picture.draft("L" if picture.mode == "L" else "RGB", BOX)
            picture = picture.convert("L" if picture.mode in ("L", "LA", "1", "I;16") else "RGB")
            picture.thumbnail(BOX, Image.LANCZOS)
            shrunk = io.BytesIO()
            picture.save(shrunk, "JPEG", quality=QUALITY, optimize=True)
            return shrunk.getvalue(), ".jpg", picture.size
    except Exception:
        return data, ext, _size(data)


def _prepared(pictures):
    """The pictures in order as (bytes, extension, size), shrunk where needed, several at a time. A picture that
    cannot be read (a damaged file) is left out and logged, so one bad page does not cost the whole book."""
    def prepare(item):
        name, read = item
        ext = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ".jpg"
        if ext not in TYPES:
            return None
        try:
            return fit(read(), ext)
        except UNREADABLE as exc:
            log.warning("Left out an unreadable page, %s: %s", name, exc)
            return None
    pictures = list(pictures)
    with ThreadPoolExecutor(WORKERS) as pool:
        # small batches, or a 2000-page series tries to sit in memory all at once. RAM says no
        for start in range(0, len(pictures), 4 * WORKERS):
            for page in pool.map(prepare, pictures[start:start + 4 * WORKERS]):
                if page:
                    yield page


def build(target, title, author, parts, language="en", progress=None):
    """Write the book to ``target``. parts: [(contents entry, [(picture name, read)] ), ...] in reading order,
    where read() gives the picture's bytes. progress(pages done, pages) is called as it goes. Returns how many
    pages it has."""
    parts = [(label, list(pictures)) for label, pictures in parts]
    total = sum(len(pictures) for _, pictures in parts)
    esc = lambda text: html.escape(text or "", quote=True)  # noqa: E731
    manifest, spine, toc = [], [], []
    with zipfile.ZipFile(target, "w") as epub:
        # EPUB rule nobody remembers: "mimetype" goes FIRST and UNCOMPRESSED or readers just refuse the file.
        # https://www.w3.org/TR/epub-33/#sec-zip-container-mime
        epub.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        epub.writestr("META-INF/container.xml",
                   '<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                   '<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>',
                   compress_type=zipfile.ZIP_DEFLATED)
        page_no = 0
        for label, pictures in parts:
            first = None
            # invisible text on each chapter's first page. pages are just pictures, so without this Readest's
            # search has literally nothing to find when you type "Chapter 21"
            words = '<p style="position:absolute;left:0;top:0;margin:0;font-size:1px;line-height:1px;color:transparent">%s</p>' % esc(label)
            for data, ext, (width, height) in _prepared(pictures):
                page_no += 1
                if progress:
                    progress(page_no, total)
                img, page = "images/p%05d%s" % (page_no, ext), "pages/p%05d.xhtml" % page_no
                epub.writestr("OEBPS/" + img, data, compress_type=zipfile.ZIP_STORED)  # pictures are compressed already
                epub.writestr("OEBPS/" + page,
                           '<?xml version="1.0" encoding="UTF-8"?><!DOCTYPE html><html xmlns="http://www.w3.org/1999/xhtml">'
                           '<head><title>%s</title><meta name="viewport" content="width=%d, height=%d"/>'
                           '<style>html,body{margin:0;padding:0}img{display:block;width:%dpx;height:%dpx}</style></head>'
                           '<body>%s<img src="../%s" alt=""/></body></html>' % (esc(label), width, height, width, height, "" if first else words, img),
                           compress_type=zipfile.ZIP_DEFLATED)
                manifest.append('<item id="i%d" href="%s" media-type="%s"%s/>' % (page_no, img, TYPES[ext], ' properties="cover-image"' if page_no == 1 else ""))
                manifest.append('<item id="p%d" href="%s" media-type="application/xhtml+xml"/>' % (page_no, page))
                spine.append('<itemref idref="p%d"/>' % page_no)
                first = first or page
            if first:
                toc.append('<li><a href="%s">%s</a></li>' % (first, esc(label)))
        if not page_no:
            raise ValueError("There are no pictures to put in the book.")
        epub.writestr("OEBPS/nav.xhtml",
                   '<?xml version="1.0" encoding="UTF-8"?><!DOCTYPE html><html xmlns="http://www.w3.org/1999/xhtml" '
                   'xmlns:epub="http://www.idpf.org/2007/ops"><head><title>Contents</title></head><body>'
                   '<nav epub:type="toc" id="toc"><h1>Contents</h1><ol>%s</ol></nav></body></html>' % "".join(toc),
                   compress_type=zipfile.ZIP_DEFLATED)
        epub.writestr("OEBPS/content.opf",
                   '<?xml version="1.0" encoding="UTF-8"?>'
                   '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid" prefix="rendition: http://www.idpf.org/vocab/rendition/#">'
                   '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
                   '<dc:identifier id="uid">urn:uuid:%s</dc:identifier><dc:title>%s</dc:title>%s<dc:language>%s</dc:language>'
                   '<meta property="dcterms:modified">2000-01-01T00:00:00Z</meta>'
                   '<meta property="rendition:layout">pre-paginated</meta><meta property="rendition:spread">none</meta>'
                   '<meta name="cover" content="i1"/></metadata>'
                   '<manifest><item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>%s</manifest>'
                   '<spine>%s</spine></package>'
                   # the id comes from the title (part range included), so a longer copy counts as a different book
                   % (uuid.uuid5(uuid.NAMESPACE_URL, "familyhub:" + title), esc(title),
                      "<dc:creator>%s</dc:creator>" % esc(author) if author else "", esc(language), "".join(manifest), "".join(spine)),
                   compress_type=zipfile.ZIP_DEFLATED)
    return page_no
