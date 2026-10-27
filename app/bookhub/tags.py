"""Tidying tags that come from machines: a book file's own details and outside catalogues.

Files carry all sorts of things in their "subject" field: ISBNs, dates, the
publisher, adverts for the program that made them. Catalogues add shelving
notes of their own. ``tidy`` keeps what describes the book and drops the rest.
Nothing is changed in the library itself; this only decides what Family Hub shows.
Tags people type (family and personal tags) are never touched.
"""
import re

MAX_LENGTH = 40
MAX_WORDS = 5

# "ISBN-13: 978...", "# Publisher: Routledge", "Number Of Pages:"
LABEL_RE = re.compile(
    r"^(isbn|asin|publisher|published|publication date|pub date|number of pages|pages|language|format|"
    r"edition|copyright|author|title|file ?size|source|url|website|keywords?)\b[\w /-]*:", re.I)
NUMBER_RE = re.compile(r"^[\d\s./:xX-]+$")  # ISBNs, dates, page counts
WEB_RE = re.compile(r"https?:|www\.|\.(com|org|net)\b", re.I)
PUBLISHER_RE = re.compile(r"\b(press|publications|publishing|publishers|ptr|verlag)$", re.I)
GROUP_RE = re.compile(r"^team \w+$", re.I)  # names of the groups that scan books
CATALOGUE_RE = re.compile(
    r"^[A-Za-z_]+:\S|new york times|=|accessible book|protected daisy|in library|large type|large print|"
    r"internet archive|overdrive|open ?library|reading level|lending library|long now|"
    r"translations? (into|from)|in english$|^fiction in |works by one author", re.I)

PUBLISHERS = {
    "charles river media", "morgan kaufmann", "routledge", "o'reilly", "o'reilly media", "packt", "apress",
    "wiley", "springer", "elsevier", "addison-wesley", "prentice hall", "mcgraw-hill", "penguin",
    "harpercollins", "random house", "tor books", "course technology", "new riders", "sams", "que",
}
JUNK = {
    "general", "unknown", "none", "n/a", "ebook", "ebooks", "e-book", "book", "books", "pdf", "epub", "mobi",
    "calibre", "docbook", "unregisterd version", "unregistered version", "untitled", "misc", "other",
    "uncategorized", "various", "text", "document", "to-read", "to read", "owned", "default", "etc",
    "american", "english", "british", "authors", "literature", "contemporary",
}
SHORT_OK = {"ai", "ux", "ui", "ya", "sf", "v5", "3d", "2d", "c#"}
MAX_WORDS_WHOLE = 7  # before splitting; longer than this is a sentence
# One spelling for the commonest genres, so "Thrillers" and "Thriller" count as the same tag.
ALIASES = {
    "thrillers": ["Thriller"], "thrillers & suspense": ["Thriller", "Suspense"],
    "thriller & suspense": ["Thriller", "Suspense"], "mystery & detective": ["Mystery"],
    "mysteries": ["Mystery"], "detective and mystery stories": ["Mystery"],
    "international mystery & crime": ["Mystery", "Crime"], "mystery & crime": ["Mystery", "Crime"],
    "short fiction": ["Short stories"], "short stories (single author)": ["Short stories"],
    "sci-fi": ["Science fiction"], "scifi": ["Science fiction"], "sf": ["Science fiction"],
    "science fiction & fantasy": ["Science fiction", "Fantasy"], "ya": ["Young adult"],
    "young adult fiction": ["Young adult"], "juvenile fiction": ["Children's"], "children": ["Children's"],
    "childrens": ["Children's"], "classic": ["Classics"], "classic literature": ["Classics"],
    "computer game design": ["Game design"], "video game design": ["Game design"],
    "fps": ["First-person shooter"], "police procedurals": ["Police procedural"],
    "biography & autobiography": ["Biography"], "biographies": ["Biography"], "memoirs": ["Memoir"],
    "non-fiction": ["Non-fiction"], "nonfiction": ["Non-fiction"], "humour": ["Humor"],
    "horror tales": ["Horror"], "horror fiction": ["Horror"], "fantasy fiction": ["Fantasy"],
    "romance fiction": ["Romance"], "love stories": ["Romance"], "ghost stories": ["Ghost story"],
    "psychological": ["Psychological fiction"], "literary": ["Literary fiction"],
    "historical": ["Historical fiction"], "suspense fiction": ["Suspense"], "thrillers (fiction)": ["Thriller"],
    "comics & graphic novels": ["Graphic novel"], "graphic novels": ["Graphic novel"],
    "role-playing games": ["Roleplaying game"], "roleplaying games": ["Roleplaying game"],
    "art": ["Art"], "comic books strips etc": ["Comics"], "comic books strips": ["Comics"],
    "computer games": ["Video games"], "fiction in english": [], "humorous": ["Humor"], "humorous fiction": ["Humor"],
}
# Shown first when a catalogue offers more tags than are kept.
GENRES = {
    "fiction", "non-fiction", "fantasy", "science fiction", "horror", "mystery", "thriller", "suspense",
    "romance", "crime", "classics", "historical fiction", "literary fiction", "psychological fiction",
    "short stories", "young adult", "children's", "graphic novel", "biography", "memoir", "history",
    "science", "philosophy", "humor", "poetry", "drama", "adventure", "gothic", "dystopian", "satire",
    "game design", "roleplaying game", "true crime", "essays", "ghost story", "police procedural",
}


# Catalogues file subjects under these, as "Fiction, horror".
BROAD = {"fiction", "nonfiction", "non-fiction", "juvenile fiction", "juvenile nonfiction", "young adult fiction",
         "authors", "literature", "history", "science", "games", "computers", "art", "comics & graphic novels"}


def _case(tag):
    """Sentence case for tags written all in capitals or all in small letters."""
    if tag.lower() in GENRES:
        return tag.capitalize()
    if tag.islower() or (tag.isupper() and len(tag) > 4):
        return tag[0].upper() + tag[1:].lower()
    return tag[0].upper() + tag[1:]


def _split(tag):
    """Break "Fiction.Horror", "FICTION / Literary" and "Fiction, psychological" into their parts."""
    tag = re.sub(r"\([^)]*\)", " ", tag)
    parts = re.split(r"\s*[;|]\s*|\s+/\s+|\s+--?\s+|\s+>\s+|(?<=[A-Za-z])\.(?=[A-Za-z])", tag)
    one_word = lambda pieces: all(len(piece.split()) == 1 for piece in pieces)  # noqa: E731
    found = []
    for part in parts:
        # the comma problem: "Fiction, psychological" is two tags, but "Solo, Han" is one guy written backwards.
        # no perfect rule exists, these guesses are just right most of the time
        pieces = [piece.strip() for piece in part.split(",") if piece.strip()]
        if len(pieces) < 2:
            found += pieces
        elif len(pieces) == 3 and pieces[-1].lower() in BROAD and one_word(pieces[:2]) and pieces[0].lower() not in BROAD:
            found += [(pieces[1] + " " + pieces[0]).title(), pieces[2]]  # "Solo, Han, fiction"
        elif pieces[0].lower() in BROAD or pieces[-1].lower() in BROAD:
            found += pieces
        elif len(pieces) == 2 and one_word(pieces):
            found.append((pieces[1] + " " + pieces[0]).title())
        else:
            found.append(" ".join(pieces))
    return found


def _keep(tag, skip):
    low = tag.lower()
    if low in JUNK or low in PUBLISHERS or low in skip:
        return False
    if len(tag) > MAX_LENGTH or len(tag.split()) > MAX_WORDS or tag.endswith("."):
        return False  # a sentence, not a tag
    if len(tag) < 3 and low not in SHORT_OK:
        return False
    if NUMBER_RE.match(tag) or WEB_RE.search(tag) or PUBLISHER_RE.search(tag) or GROUP_RE.match(tag):
        return False
    return not CATALOGUE_RE.search(tag)


def _same(tag):
    """"Self realization" and "Self-realization" are one tag."""
    return re.sub(r"[\W_]+", "", tag.lower())


def tidy(raw, skip=(), limit=None, plain=False):
    """Return the tags worth showing from ``raw``, in order, without repeats.

    ``skip`` holds things that are not tags for this book: its publisher,
    title and author. ``limit`` keeps the best few, genres first. ``plain``
    leaves out tags with accented letters, which in catalogues are nearly
    always the same subject again in another language.
    """
    skip = {thing.strip().lower() for thing in skip if thing and thing.strip()}
    kept, seen = [], set()
    for whole in raw or []:
        whole = re.sub(r"\s+", " ", str(whole or "")).strip().lstrip("#*•- ").strip()
        if not whole or LABEL_RE.match(whole) or WEB_RE.search(whole) or whole.lower() in skip:
            continue
        if len(re.findall(r"[\w']+", whole)) > MAX_WORDS_WHOLE:
            continue
        for part in _split(whole):
            part = part.strip(" .:-_")
            for tag in ALIASES.get(part.lower(), [part]):
                if not _keep(tag, skip) or (plain and not tag.isascii()):
                    continue
                tag = _case(tag)
                if _same(tag) not in seen:
                    seen.add(_same(tag))
                    kept.append(tag)
    if limit and len(kept) > limit:
        # genres jump the queue, everything else keeps its place behind them
        kept = ([tag for tag in kept if tag.lower() in GENRES] + [tag for tag in kept if tag.lower() not in GENRES])[:limit]
    return kept


def dropped(raw, kept):
    """The raw tags that left nothing behind, for the admin's "removed" list."""
    kept = {tag.lower() for tag in kept}
    return [whole for whole in raw or [] if not {tag.lower() for tag in tidy([whole])} & kept]


def apply_rules(tags, rules):
    """Apply an admin's choices: ``rules`` maps a tag (lower case) to its new name, or "" to hide it."""
    if not rules:
        return list(tags)
    renamed, seen = [], set()
    for tag in tags:
        new = rules.get(tag.lower())
        tag = tag if new is None else new  # careful: "" means hide it, None means no rule. not the same thing!
        if tag and tag.lower() not in seen:
            seen.add(tag.lower())
            renamed.append(tag)
    return renamed
