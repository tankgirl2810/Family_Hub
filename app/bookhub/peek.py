"""A look inside a book for the admin pages: its first pages next to the boxes for its details.

  PDF        the first pages as a small PDF of their own, which the browser's own PDF viewer shows and scrolls.
             A 300 MB rulebook isn't sent whole to look at its title page.
  EPUB       the text of its first chapters, as plain paragraphs (never the book's own markup or scripts).
  CBZ, CBR   the first pictures. A CBR that is really a zip (most are) works too; a real RAR or 7z doesn't,
             and gets the cover instead.

The library's files are only ever read.
"""
import html
import io
import logging
import re
import zipfile
from pathlib import PurePosixPath

PAGES = 3          # pages shown at first
MAX_PAGES = 30     # "show more" stops here; past that, download the book
PICTURES = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
ORDER = ["PDF", "EPUB", "CBZ", "CBR", "CB7"]  # which format to look inside when a book has several
logging.getLogger("pypdf").setLevel(logging.ERROR)


class PeekError(Exception):
    pass


def pick(formats):
    """The format worth looking inside, or None."""
    have = {fmt.upper() for fmt in formats}
    return next((fmt for fmt in ORDER if fmt in have), None)


def pdf_pages(path, count=PAGES):
    """(the first ``count`` pages as PDF bytes, how many pages the whole book has)."""
    from pypdf import PdfReader, PdfWriter
    try:
        reader = PdfReader(str(path))
        total = len(reader.pages)
        writer = PdfWriter()
        for page in reader.pages[:max(1, min(count, MAX_PAGES))]:
            writer.add_page(page)
        out = io.BytesIO()
        writer.write(out)
    except Exception as exc:  # encrypted, damaged or not really a PDF
        raise PeekError("This PDF can't be opened here: %s" % exc) from exc
    return out.getvalue(), total


# ---------------------------------------------------------------- marking places on a PDF's pages
# An admin draws a box round the title or the author on a page picture. A box is [left, top, right, bottom] as
# fractions of the page as shown (0..1 from the top left), so it fits any picture size, and a learned rule can
# read the same place in the next book like it. pypdfium2 draws the pages and reads the text inside a box; a
# scanned page has no text to read, and there is no OCR.

LABEL_FIELDS = ("title", "authors")


def _open_page(path, number):
    import pypdfium2 as pdfium
    try:
        doc = pdfium.PdfDocument(str(path))
    except Exception as exc:  # encrypted, damaged or not really a PDF
        raise PeekError("This PDF can't be opened here: %s" % exc) from exc
    if not 0 <= number < min(len(doc), MAX_PAGES):
        doc.close()
        raise PeekError("There is no such page.")
    return doc, doc[number]


def pdf_page_picture(path, number, width=1000):
    """(PNG bytes, page count) of page ``number`` (from 0), about ``width`` pixels wide."""
    doc, page = _open_page(path, number)
    try:
        shown_width = page.get_width() if page.get_rotation() in (0, 180) else page.get_height()
        image = page.render(scale=max(0.2, min(4.0, width / max(shown_width, 1)))).to_pil()
        out = io.BytesIO()
        image.convert("RGB").save(out, "PNG", optimize=True)
        return out.getvalue(), len(doc)
    finally:
        doc.close()


def clean_box(box):
    """A box from the browser as four fractions [left, top, right, bottom], or None when it won't do."""
    try:
        left, top, right, bottom = (min(1.0, max(0.0, float(part))) for part in box)
    except (TypeError, ValueError):
        return None
    left, right = sorted((left, right))
    top, bottom = sorted((top, bottom))
    return [round(left, 4), round(top, 4), round(right, 4), round(bottom, 4)] if right - left > 0.002 and bottom - top > 0.002 else None


def _page_rect(page, box):
    """A box in fractions of the page as shown, in the page's own PDF units (left, bottom, right, top), taking
    the page's crop and turn into account."""
    crop_left, crop_bottom, crop_right, crop_top = page.get_cropbox()
    width, height = crop_right - crop_left, crop_top - crop_bottom
    turn = page.get_rotation() % 360

    def place(fx, fy):
        if turn == 90:
            return crop_left + fy * width, crop_bottom + fx * height
        if turn == 180:
            return crop_right - fx * width, crop_bottom + fy * height
        if turn == 270:
            return crop_right - fy * width, crop_top - fx * height
        return crop_left + fx * width, crop_top - fy * height

    (x1, y1), (x2, y2) = place(box[0], box[1]), place(box[2], box[3])
    return min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)


def pdf_box_text(path, number, box):
    """The text inside ``box`` on page ``number``, on one line. "" when there is none (a scanned page)."""
    box = clean_box(box)
    if not box:
        return ""
    doc, page = _open_page(path, number)
    try:
        text = page.get_textpage().get_text_bounded(*_page_rect(page, box))
    except Exception:  # a page pdfium can't read the text of
        return ""
    finally:
        doc.close()
    return " ".join((text or "").split())


def clean_labels(labels):
    """{"title": {"page": n, "box": [...]}, "authors": {...}} from the browser, with anything odd dropped."""
    found = {}
    for field in LABEL_FIELDS:
        mark = (labels or {}).get(field) if isinstance(labels, dict) else None
        if not isinstance(mark, dict):
            continue
        box = clean_box(mark.get("box"))
        try:
            page = int(mark.get("page"))
        except (TypeError, ValueError):
            continue
        if box and 0 <= page < MAX_PAGES:
            found[field] = {"page": page, "box": box}
    return found


def read_labels(path, labels):
    """{"title": text, "authors": text} read from the marked places of another PDF. Fields with nothing there
    are left out."""
    found = {}
    for field, mark in clean_labels(labels).items():
        try:
            text = pdf_box_text(path, mark["page"], mark["box"])
        except PeekError:  # a shorter book, or one that won't open
            continue
        if text:
            found[field] = text
    return found


CREDIT_WORDS = re.compile(r"^(?:(?:written|designed|created|edited)\s+by|by|authors?|writers?|writing|design(?:ers?)?|credits?)\s*[:\-–]?\s+", re.I)


def as_title(text):
    """Text from a title box as a title: one line, and "DUNGEON DELVERS" as "Dungeon Delvers"."""
    text = " ".join((text or "").split()).strip(" -–:|")
    if text.isupper() and len(text) > 3:
        text = " ".join(word if len(word) <= 3 and word in ("RPG", "GM", "NPC", "D&D", "II", "III", "IV") else word.capitalize()
                        for word in text.split())
    return text[:200]


def as_authors(text):
    """Text from an author box as names: "Written by Ana Ruiz, Tom Ng and Li Wei" is three people."""
    text = CREDIT_WORDS.sub("", " ".join((text or "").split()))
    names = []
    for name in re.split(r"\s*(?:&|;|,|\band\b|\bwith\b)\s*", text):
        name = name.strip(" .-–:|")
        if name and not name.isdigit():
            name = as_title(name) if name.isupper() else name
            if name.lower() not in [known.lower() for known in names]:
                names.append(name[:120])
    return names[:12]


def _natural(name):
    """"page 10" after "page 9", the way a person counts."""
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", name)]


def comic_pictures(path):
    """The picture names inside a comic archive, in page order. [] when it isn't a zip."""
    try:
        with zipfile.ZipFile(path) as archive:
            names = [info.filename for info in archive.infolist()
                     if not info.is_dir() and PurePosixPath(info.filename).suffix.lower() in PICTURES
                     and not PurePosixPath(info.filename).name.startswith(".")]
    except (zipfile.BadZipFile, OSError):
        return []
    return sorted(names, key=_natural)


def comic_picture(path, number):
    """(bytes, mime type) of picture ``number`` (from 0)."""
    names = comic_pictures(path)
    if not 0 <= number < len(names):
        raise PeekError("There is no such page.")
    with zipfile.ZipFile(path) as archive:
        data = archive.read(names[number])
    ext = PurePosixPath(names[number]).suffix.lower().lstrip(".")
    return data, "image/" + ("jpeg" if ext == "jpg" else ext)


def epub_text(path, chars=12000):
    """The first ``chars`` characters of an EPUB's chapters, in reading order, as a list of paragraphs."""
    try:
        with zipfile.ZipFile(path) as epub:
            names = epub.namelist()
            order = _spine(epub, names) or sorted(name for name in names if name.lower().endswith((".xhtml", ".html", ".htm")))
            paragraphs, used = [], 0
            for name in order:
                raw = epub.read(name)[:400000].decode("utf-8", "replace")
                raw = re.sub(r"(?is)<(script|style|head)\b.*?</\1>", " ", raw)
                for block in re.split(r"(?i)</(p|div|h[1-6]|li|br)\s*>|<br\s*/?>", raw):
                    if not block or block.lower() in ("p", "div", "li", "br") or re.fullmatch(r"h[1-6]", block, re.I):
                        continue
                    text = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", block)).split())
                    if text:
                        paragraphs.append(text)
                        used += len(text)
                    if used >= chars:
                        return paragraphs
            return paragraphs
    except (zipfile.BadZipFile, OSError, KeyError) as exc:
        raise PeekError("This EPUB can't be opened here.") from exc


def _spine(epub, names):
    """The chapters in the order the EPUB says to read them."""
    try:
        container = epub.read("META-INF/container.xml").decode("utf-8", "replace")
        opf_name = re.search(r'full-path="([^"]+)"', container).group(1)
        opf = epub.read(opf_name).decode("utf-8", "replace")
    except (KeyError, AttributeError):
        return []
    base = str(PurePosixPath(opf_name).parent)
    hrefs = {}
    for item in re.finditer(r"<item\b[^>]*>", opf):
        tag = item.group(0)
        found_id, found_href = re.search(r'\bid="([^"]+)"', tag), re.search(r'\bhref="([^"]+)"', tag)
        if found_id and found_href:
            hrefs[found_id.group(1)] = found_href.group(1)
    order = []
    for ref in re.finditer(r'<itemref\b[^>]*\bidref="([^"]+)"', opf):
        href = hrefs.get(ref.group(1))
        if href:
            name = str(PurePosixPath(base) / href) if base not in ("", ".") else href
            name = re.sub(r"%20", " ", name)
            if name in names:
                order.append(name)
    return order


def own(book):
    """A library entry's own files: a copy's entry would otherwise lend it the other copy's formats."""
    return book.get("own_formats") or book["formats"]


def file_of(library, book, fmt):
    return library.file_path(dict(book, formats=own(book)), fmt) if fmt else None


def describe(library, book):
    """What the look-inside pane shows for a book: {"format", "kind": "pdf" | "text" | "pictures" | "none", ...}."""
    fmt = pick(own(book))
    path = file_of(library, book, fmt)
    if not path:
        return {"format": fmt, "kind": "none", "reason": "There is no file to look inside."}
    if fmt == "PDF":
        try:
            from pypdf import PdfReader
            total = len(PdfReader(str(path)).pages)
        except Exception:
            return {"format": fmt, "kind": "none", "reason": "This PDF can't be opened here."}
        return {"format": fmt, "kind": "pdf", "pages": total}
    if fmt == "EPUB":
        try:
            return {"format": fmt, "kind": "text", "paragraphs": epub_text(path)}
        except PeekError as exc:
            return {"format": fmt, "kind": "none", "reason": str(exc)}
    pictures = comic_pictures(path)
    if pictures:
        return {"format": fmt, "kind": "pictures", "pages": len(pictures)}
    return {"format": fmt, "kind": "none", "reason": "This kind of comic archive can't be opened here; the cover shows instead."}
