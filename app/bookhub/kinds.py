"""Guessing what kind of book something is, and which franchise tags it should carry.

Every book has one kind. The guess is made once, when the book first appears;
after that a person's choice always wins.
"""
import re

FICTION, NONFICTION, GRAPHIC, TABLETOP, UNSORTED = "Fiction", "Non-fiction", "Graphic novel", "Tabletop", "Unsorted"
KINDS = [FICTION, NONFICTION, GRAPHIC, TABLETOP, UNSORTED]

COMIC_FORMATS = {"CBZ", "CBR", "CB7", "CBC"}

# Checked against the title, author, series, the book's own tags and the folder
# it arrived in. The first matching kind wins; every matching tag is added.
TABLETOP_RE = re.compile(
    r"vampire.{0,6}the.{0,3}masquerade|\bvtm\b|clanbook|world of darkness|\bwerewolf.{0,6}the.{0,3}apocalypse|"
    r"dungeons?\s*(&|and)\s*dragons|\bad&d\b|\bd&d\b|pathfinder (rpg|roleplaying)|call of cthulhu rpg|"
    r"player'?s handbook|dungeon master'?s guide|monster manual|roleplaying game|role-playing game|"
    r"\btabletop\b|\bsourcebook\b|gamer'?s handbook", re.I)
GRAPHIC_RE = re.compile(r"graphic novel|\bcomics?\b|\bmanga\b", re.I)

TAG_RULES = [
    ("Star Trek", re.compile(r"star\s*tre[kx]", re.I)),
    ("VTM", re.compile(r"vampire.{0,6}the.{0,3}masquerade|\bvtm\b|clanbook", re.I)),
    ("World of Darkness", re.compile(r"world of darkness", re.I)),
    ("D&D", re.compile(r"dungeons?\s*(&|and)\s*dragons|\bad&d\b|\bd&d\b", re.I)),
]
# Edition tags are only looked for on tabletop books, where "Revised" means something.
EDITION_RULES = [
    ("V5", re.compile(r"\bv5\b|masquerade\s*v?5\b|5th edition", re.I)),
    ("V20", re.compile(r"\bv20\b|20th anniversary", re.I)),
    ("Revised", re.compile(r"\brevised\b", re.I)),
    ("AD&D", re.compile(r"\bad&d\b|advanced dungeons", re.I)),
    ("2nd edition", re.compile(r"\b2nd ed(ition)?\b|\b2e\b", re.I)),
    ("3rd edition", re.compile(r"\b3rd ed(ition)?\b|\b3(\.5)?e\b", re.I)),
]

FICTION_WORDS = {
    "fiction", "fantasy", "romance", "mystery", "thriller", "horror", "novel", "novels", "classic", "classics",
    "sci-fi", "scifi", "ghost story", "comedy", "humor", "humour", "children", "juvenile",
    "short stories", "gothic", "adventure", "dystopian", "fairy tales", "crime", "westerns", "literary",
    "science fiction", "historical fiction", "young adult", "poetry", "drama",
}
NONFICTION_WORDS = {
    "nonfiction", "non-fiction", "biography", "autobiography", "memoir", "history", "science", "business",
    "self-help", "computers", "programming", "reference", "philosophy", "essays", "cooking", "travel",
    "true crime", "psychology", "education", "design", "art", "technology", "mathematics", "politics",
    "religion", "health", "economics", "games", "game design", "study aids", "social science",
}


def _tag_kind(tags):
    """Fiction or non-fiction from a book's own subject tags, or None."""
    fiction = nonfiction = False
    for tag in tags:
        low = tag.strip().lower()
        # "Fiction / Fantasy & Magic" -> the whole thing plus each bit on its own
        pieces = {low} | {piece.strip() for piece in re.split(r"[/&,;:>-]", low)}
        # order matters here: "nonfiction" has "fiction" hiding inside it
        if "nonfiction" in low or "non-fiction" in low:
            nonfiction = True
        elif "fiction" in low or pieces & FICTION_WORDS:
            fiction = True
        elif pieces & NONFICTION_WORDS:
            nonfiction = True
    if fiction:
        return FICTION
    return NONFICTION if nonfiction else None


def guess(title, author="", series="", tags=(), formats=(), origin=""):
    """Return (kind, [tags to add]) for a book."""
    text = " ".join([title or "", author or "", series or "", " ".join(tags), origin or ""])
    formats = {fmt.upper() for fmt in formats}
    if TABLETOP_RE.search(text):
        kind = TABLETOP
    elif (formats and formats <= COMIC_FORMATS) or GRAPHIC_RE.search(text):
        kind = GRAPHIC
    else:
        kind = _tag_kind(tags) or UNSORTED
    found = [tag for tag, rule in TAG_RULES if rule.search(text)]
    if kind == TABLETOP:
        found += [tag for tag, rule in EDITION_RULES if rule.search(text)]
    return kind, found
