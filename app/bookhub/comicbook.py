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
        with Image.open(io.BytesIO(data)) as im:
            return im.size
    except Exception:
        return 1000, 1500  # a picture Pillow cannot read still gets a page, at a usual comic size


def fit(data, ext):
    """A picture ready for a tablet, as (bytes, extension, (width, height)): as it is when it is already a
    sensible size, otherwise shrunk to fit BOX and saved as JPEG. A picture Pillow cannot read is kept as it is."""
    try:
        with Image.open(io.BytesIO(data)) as im:
            size = im.size
            if size[0] <= BOX[0] and size[1] <= BOX[1] and len(data) <= MAX_BYTES:
                return data, ext, size
            im.draft("L" if im.mode == "L" else "RGB", BOX)  # JPEG pages decode straight at the smaller size
            im = im.convert("L" if im.mode in ("L", "LA", "1", "I;16") else "RGB")
            im.thumbnail(BOX, Image.LANCZOS)
            out = io.BytesIO()
            im.save(out, "JPEG", quality=QUALITY, optimize=True)
            return out.getvalue(), ".jpg", im.size
    except Exception:
        return data, ext, _size(data)


def _prepared(pictures):
    """The pictures in order as (bytes, extension, size), shrunk where needed, several at a time. A picture that
    cannot be read (a damaged file) is left out and logged, so one bad page does not cost the whole book."""
    def one(item):
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
        for start in range(0, len(pictures), 4 * WORKERS):  # a few at a time, so memory stays small
            for done in pool.map(one, pictures[start:start + 4 * WORKERS]):
                if done:
                    yield done


def build(target, title, author, parts, language="en", progress=None):
    """Write the book to ``target``. parts: [(contents entry, [(picture name, read)] ), ...] in reading order,
    where read() gives the picture's bytes. progress(pages done, pages) is called as it goes. Returns how many
    pages it has."""
    parts = [(label, list(pictures)) for label, pictures in parts]
    total = sum(len(pictures) for _, pictures in parts)
    esc = lambda t: html.escape(t or "", quote=True)  # noqa: E731
    manifest, spine, toc = [], [], []
    with zipfile.ZipFile(target, "w") as z:
        # The mimetype entry must come first and be stored uncompressed, or readers refuse the file.
        z.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml",
                   '<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                   '<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>',
                   compress_type=zipfile.ZIP_DEFLATED)
        n = 0
        for label, pictures in parts:
            first = None
            for data, ext, (width, height) in _prepared(pictures):
                n += 1
                if progress:
                    progress(n, total)
                img, page = "images/p%05d%s" % (n, ext), "pages/p%05d.xhtml" % n
                z.writestr("OEBPS/" + img, data, compress_type=zipfile.ZIP_STORED)  # pictures are compressed already
                z.writestr("OEBPS/" + page,
                           '<?xml version="1.0" encoding="UTF-8"?><!DOCTYPE html><html xmlns="http://www.w3.org/1999/xhtml">'
                           '<head><title>%s</title><meta name="viewport" content="width=%d, height=%d"/>'
                           '<style>html,body{margin:0;padding:0}img{display:block;width:%dpx;height:%dpx}</style></head>'
                           '<body><img src="../%s" alt=""/></body></html>' % (esc(label), width, height, width, height, img),
                           compress_type=zipfile.ZIP_DEFLATED)
                manifest.append('<item id="i%d" href="%s" media-type="%s"%s/>' % (n, img, TYPES[ext], ' properties="cover-image"' if n == 1 else ""))
                manifest.append('<item id="p%d" href="%s" media-type="application/xhtml+xml"/>' % (n, page))
                spine.append('<itemref idref="p%d"/>' % n)
                first = first or page
            if first:
                toc.append('<li><a href="%s">%s</a></li>' % (first, esc(label)))
        if not n:
            raise ValueError("There are no pictures to put in the book.")
        z.writestr("OEBPS/nav.xhtml",
                   '<?xml version="1.0" encoding="UTF-8"?><!DOCTYPE html><html xmlns="http://www.w3.org/1999/xhtml" '
                   'xmlns:epub="http://www.idpf.org/2007/ops"><head><title>Contents</title></head><body>'
                   '<nav epub:type="toc" id="toc"><h1>Contents</h1><ol>%s</ol></nav></body></html>' % "".join(toc),
                   compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("OEBPS/content.opf",
                   '<?xml version="1.0" encoding="UTF-8"?>'
                   '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid" prefix="rendition: http://www.idpf.org/vocab/rendition/#">'
                   '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
                   '<dc:identifier id="uid">urn:uuid:%s</dc:identifier><dc:title>%s</dc:title>%s<dc:language>%s</dc:language>'
                   '<meta property="dcterms:modified">2000-01-01T00:00:00Z</meta>'
                   '<meta property="rendition:layout">pre-paginated</meta><meta property="rendition:spread">none</meta>'
                   '<meta name="cover" content="i1"/></metadata>'
                   '<manifest><item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>%s</manifest>'
                   '<spine>%s</spine></package>'
                   % (uuid.uuid5(uuid.NAMESPACE_URL, "familyhub:" + title), esc(title),
                      "<dc:creator>%s</dc:creator>" % esc(author) if author else "", esc(language), "".join(manifest), "".join(spine)),
                   compress_type=zipfile.ZIP_DEFLATED)
    return n
