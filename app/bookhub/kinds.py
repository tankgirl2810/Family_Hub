"""Guessing what kind of book something is, and which tags it should carry.

Every book has one kind. The guess is made once, when the book first appears;
after that a person's choice always wins.

Tabletop books (game books for playing a role-playing game) get more: the game and edition they are for, as two
family tags ("Pathfinder" and "Pathfinder 2e", the second nested under the first), a role (rulebook, adventure,
player aid or GM aid) and, where the name says so, a series and number. The clues, most trusted first:

  1. Rules an admin taught Family Hub by confirming books in the review queue (learned rules).
  2. The folder the file arrived in ("Pathfinder/2nd Edition/Adventure Paths/Age of Ashes/...").
  3. The book's name: its title, author and series in the library, and the file's own name.
  4. The text of a PDF's first pages, where the credits say "Pathfinder Second Edition" or "Fifth Edition".

When two clues disagree (the folder says 1st edition, the credits say second), the verdict says so and the book
waits for a person in the review queue instead of being sorted quietly.

Fiction and comics set in a game's world (Pathfinder Tales, a Clan Novel, a Vampire comic) keep their kind and get
the game's tag, so they turn up with the rest of the game without being mistaken for rulebooks.
"""
import json
import re
from pathlib import Path

FICTION, NONFICTION, GRAPHIC, TABLETOP, UNSORTED = "Fiction", "Non-fiction", "Graphic novel", "Tabletop", "Unsorted"
KINDS = [FICTION, NONFICTION, GRAPHIC, TABLETOP, UNSORTED]
RULEBOOK, ADVENTURE, PLAYER_AID, GM_AID = "Rulebook", "Adventure", "Player aid", "GM aid"
ROLES = [RULEBOOK, ADVENTURE, PLAYER_AID, GM_AID]

COMIC_FORMATS = {"CBZ", "CBR", "CB7", "CBC"}
ARCHIVES = {".zip", ".rar", ".7z", ".tar", ".gz"}
GAMELINES_FILE = Path(__file__).with_name("gamelines.json")

# A game book with no game named: "Core Rulebook", "Character Sheet", a sourcebook from a line we don't know yet.
TABLETOP_RE = re.compile(
    r"roleplaying game|role-playing game|\bttrpg\b|\btabletop\b|\bsourcebook\b|player'?s handbook|dungeon master'?s guide|"
    r"monster manual|game ?master'?s? (guide|screen)|\bgm screen|storyteller'?s (guide|screen|toolkit|handbook|vault)|"
    r"character ?sheet|pre-?generated characters|\bpregens?\b|core ?rule ?book|adventure path|gamer'?s handbook", re.I)
GRAPHIC_RE = re.compile(r"graphic novel|\bcomics?\b|\bmanga\b", re.I)
# Set in a game's world but a story, not rules
FICTION_RE = re.compile(r"\btales\b|\bnovels?\b|clan novel|\bfiction\b", re.I)

# What a game book is for, from its name. GM aids are looked for first: "Bestiary Pawn Collection" is pawns,
# not a bestiary, and a "Tracking Sheet" is the GM's, not a character sheet.
GM_AID_RE = re.compile(
    r"\bgm screen|\bdm screen|game ?master'?s? screen|storyteller'?s screen|\bscreen\b|\bmaps?\b|\bmaps?\.|\bpawns?\b|"
    r"flip-?(mat|tiles?)|\btiles\b|battle cards|chase cards|condition cards|monster cards|\bencounters?\b|"
    r"tracking sheet|initiative|\bhandouts?\b|\btokens?\b|\bnpcs?\b|\bminis\b|miniatures|map ?pack", re.I)
PLAYER_AID_RE = re.compile(
    r"character ?sheet|\bsheets?\b|sheet\b|pre-?gen|pregenerated|\biconics?\b|spell ?cards?|spell ?deck|"
    r"log ?sheet|character (options|background|folio|creation guide)|characterbackground|\blvl ?\d|\blevel \d|"
    r"gear deck|armaments deck|item cards|\bdeck\b|\bcards\b|worksheet|\bcerts?\b|"
    r"\b(barbarian|bard|cleric|druid|fighter|monk|paladin|ranger|rogue|sorcerer|warlock|wizard|alchemist|champion|"
    r"oracle|swashbuckler|witch|magus|gunslinger|inquisitor|summoner)( lvl| level)? ?\d\b", re.I)
FANZINE_RE = re.compile(r"\bwayfinder\b|\bfanzine\b|\bzine\b", re.I)
PLAYERS_GUIDE_RE = re.compile(r"players?'?s? ?guide", re.I)
ADVENTURE_RE = re.compile(
    r"adventure path|\bap\b|\bpart [0-9ivx]+\b|\bmodule\b|one[- ]shot|\bscenario|\bbount(y|ies)\b|\bddal|\bddex|"
    r"curse ?of ?strahd|tomb ?of ?annihilation|out ?of ?the ?abyss|hoard ?of ?the ?dragon ?queen|rise ?of ?tiamat|"
    r"princes ?of ?the ?apocalypse|lost ?mine ?of ?phandelver|storm ?king'?s ?thunder|"
    r"adventurers league|\bquest\b|\bs\d\d-\d\d\b|starter adventure|\badventures?\b(?! gear)|"
    # last: module codes, "D1 - Crown of the Kobold King", but not an edition up front ("H5 - Corebook")
    r"\bchronicle\b|by night\b|^(?![vhwm](5|20)\b)[a-z]{1,2}\d{1,2} ?[-–:] ", re.I)
# Folder names that say what everything in them is for
FOLDER_ROLES = [
    (GM_AID, re.compile(r"^(pawns?|maps?|map ?packs?|screens?|gm aids?|tokens?|tiles|flip[- ]?(mats?|tiles?)|cards|paper minis|minis)$", re.I)),
    (PLAYER_AID, re.compile(r"^(pre-?gens?|pregenerated.*|character sheets?|sheets|player aids?)$", re.I)),
    (ADVENTURE, re.compile(r"^(adventure paths?|adventures?|modules?|scenarios?|society|one[- ]shots?|bount(y|ies))$", re.I)),
    (RULEBOOK, re.compile(r"^(core|core ?books?|core rules|rule ?books?|source ?books?|rules|supplements?|lost omens|companions?|campaign( settings?)?|chronicles|player companions?)$", re.I)),
]
# Folder names that say nothing about the books inside
PLAIN_FOLDER = re.compile(r"^(books?|pdfs?|extras?|misc|other|downloads?|rpgs?|ttrpgs?|tabletop|games?|\d+)$", re.I)

TAG_RULES = [
    ("Star Trek", re.compile(r"star\s*tre[kx]", re.I)),
]
# Tags the sorter put on books before it knew game lines; a re-sort takes them off again where they no longer fit
OLD_AUTO_TAGS = {"VTM", "World of Darkness", "D&D", "Star Trek", "V5", "V20", "Revised", "AD&D", "2nd edition", "3rd edition"}

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

SCRIPTS = [  # a title written in one of these is very likely in that language (codes as the library keeps them)
    ("rus", re.compile(r"[Ѐ-ӿ]")), ("ell", re.compile(r"[Ͱ-Ͽ]")),
    ("jpn", re.compile(r"[぀-ヿ]")), ("kor", re.compile(r"[가-힯]")), ("zho", re.compile(r"[一-鿿]")),
]


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


# ------------------------------------------------------------------ game lines

class GameLine:
    def __init__(self, entry):
        self.tag = entry["tag"]
        self.parent = entry.get("parent") or ""
        self.match = [re.compile(pattern, re.I) for pattern in entry.get("match") or []]
        self.needs = [re.compile(pattern, re.I) for pattern in entry.get("needs") or []]
        self.editions = [(edition["tag"], [re.compile(pattern, re.I) for pattern in edition.get("match") or []])
                         for edition in entry.get("editions") or []]

    def found_in(self, text, folder=False):
        if not any(rule.search(text) for rule in self.match):
            return False
        # a folder named "Pathfinder" is a game folder; a book called "Pathfinder" might be Orson Scott Card's
        return folder or not self.needs or any(rule.search(text) for rule in self.needs)

    def edition_in(self, text):
        return next((tag for tag, rules in self.editions if any(rule.search(text) for rule in rules)), "")


def load_game_lines(extra=()):
    """The game lines in gamelines.json, then any an admin added (dicts in the same shape)."""
    try:
        entries = json.loads(GAMELINES_FILE.read_text(encoding="utf-8"))["games"]
    except (OSError, ValueError, KeyError):
        entries = []
    lines = [GameLine(entry) for entry in list(entries) + list(extra)]
    # the most specific first: "Vampire: The Masquerade" before its parent "World of Darkness"
    parents = {line.parent for line in lines}
    return sorted(lines, key=lambda line: line.tag in parents)


GAME_LINES = load_game_lines()


def _game(text, lines, folder=False):
    """(game tag, edition tag) found in text, either may be "". In a folder path the deepest folder that names a
    game wins: "World Of Darkness V5/Hunter The Reckoning 5ed" is Hunter, not the Vampire a bare "V5" suggests."""
    if folder and "/" in text:
        parts = text.split("/")
        for depth in range(len(parts), 0, -1):
            game, _ = _game(parts[depth - 1], lines, folder=True)
            if game:
                line = next(line for line in lines if line.tag == game)
                # the edition may be in a folder above ("Pathfinder/2nd Edition/Core") or below
                return game, line.edition_in("/".join(parts[depth - 1:])) or line.edition_in(text)
        return "", ""
    for line in lines:
        if line.found_in(text, folder):
            return line.tag, line.edition_in(text)
    return "", ""


# ------------------------------------------------------------------ names

def split_origin(origin):
    """(folder, file name, archive name) from where a file arrived: ("Pathfinder/2nd Edition/Core",
    "PF 2E - Core Rulebook", ""). A file that came out of an archive has only the archive's name to go on (the
    delivery log keeps no more), and the archive counts as its last folder too."""
    parts = [part for part in re.split(r"[\\/]+", origin or "") if part]
    if not parts:
        return "", "", ""
    last = Path(parts[-1])
    if last.suffix.lower() in ARCHIVES:
        return "/".join(parts[:-1] + [last.stem]), "", last.stem
    return "/".join(parts[:-1]), last.stem, ""


def clean_title(name):
    """A file name as a person would write the title: "PF 2E - Bounty #08 The Tireless Path {PZOPFB0008}.pdf"
    becomes "Bounty #08 The Tireless Path"."""
    name = re.sub(r"\.(pdf|epub|cb[zr7]|mobi|azw3?|zip|rar|7z)$", "", name or "", flags=re.I)
    name = re.sub(r"\{[^}]*\}|\[[^\]]*\]|\((scanned|digital|\d{5,})[^)]*\)", " ", name, flags=re.I)
    # release prefixes: "PF 2E - ", "PF2 ", "PZO90163 ", "WW2822 - ", "VtM - ", "V5 - ", "DnD "
    name = re.sub(r"^\s*((pf|sf) ?[12]e?|pzo ?\w+|ww ?\d+|vtm|v5|v20|dnd|d&d|ad&d|5e)\b\s*[-–:]?\s*", "", name, flags=re.I)
    name = re.sub(r"^\s*((pf|sf) ?[12]e?|pzo ?\w+|ww ?\d+|vtm)\b\s*[-–:]?\s*", "", name, flags=re.I)  # "PZO90163 Pathfinder 2E - "
    name = re.sub(r"^\s*pathfinder ?2e?\s*[-–:]\s*", "", name, flags=re.I)
    name = re.sub(r"[_]+|(?<=[a-z])\.(?=[A-Z])", " ", name)
    name = re.sub(r"\s*[-–]\s*$|^\s*[-–]\s*", "", re.sub(r"\s{2,}", " ", name)).strip()
    return name


SERIES_PATTERNS = [
    # fanzines: "Wayfinder #06", "Wayfinder 18 - Tian Xia"
    re.compile(r"^(?P<series>wayfinder)\s*#?\s*(?P<index>\d{1,3})\b\s*[-–:]?\s*(?P<title>.*)$", re.I),
    # "DDAL04-07 The Innocent (5e)", "S02-11 The Pathfinder Trials"
    re.compile(r"^(?P<series>ddal|ddex|s|scenario ?#?)(?P<season>\d\d?)-(?P<index>\d\d?)\s*(?P<title>.+?)(?:\s*\(5e\))?$", re.I),
    # "Age of Ashes 5 - Against the Scarlet Triad", "Extinction Curse AP - Part 4 of 6 - Siege of the Dinosaurs"
    re.compile(r"^(?P<series>[A-Z][A-Za-z' ]+?)(?: AP)?\s*[-–,:]?\s*(?:part|book|vol\.?|volume)?\s*(?P<index>\d{1,2})(?: of \d+)?\s*[-–:]\s*(?P<title>.+)$", re.I),
    # "Kingmaker, part 4: Blood for Blood"
    re.compile(r"^(?P<series>[A-Z][\w' ]+?),? part (?P<index>\d{1,2}|[ivx]+)\s*[-–:]\s*(?P<title>.+)$", re.I),
    # "Bounty #08 The Tireless Path"
    re.compile(r"^(?P<series>bounty)\s*#?(?P<index>\d{1,3})\s*[-–:]?\s*(?P<title>.+)$", re.I),
]
ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6, "vii": 7, "viii": 8, "ix": 9, "x": 10}


def series_from_name(name, game=""):
    """(series, index, title) from a game book's name, or None."""
    for pattern in SERIES_PATTERNS:
        found = pattern.match(name or "")
        if not found:
            continue
        parts = found.groupdict()
        index = parts["index"].lower()
        index = int(index) if index.isdigit() else ROMAN.get(index, 0)
        series = parts["series"].strip()
        if series.lower() == "wayfinder":
            series = "Wayfinder"
        elif series.lower() == "bounty":
            series = (game + " Bounties").strip() if game else "Bounties"
        elif parts.get("season") and series.lower() in ("ddal", "ddex"):
            series = "Adventurers League Season %d" % int(parts["season"])
        elif parts.get("season"):
            series = ("%s Society Season %d" % (game, int(parts["season"]))).strip()
        return series, index, parts["title"].strip() or "%s #%d" % (series, index)
    return None


def language_of(text):
    return next((code for code, script in SCRIPTS if script.search(text or "")), "")


# ------------------------------------------------------------------ the verdict

def _role(name, folder_parts, adventure_series=False):
    """(role, where it was seen) for a game book."""
    if GM_AID_RE.search(name):
        return GM_AID, "name"
    if PLAYER_AID_RE.search(name):
        return PLAYER_AID, "name"
    if PLAYERS_GUIDE_RE.search(name):
        # an adventure path's player's guide goes with its adventure; a general one sits with the rulebooks
        if adventure_series or ADVENTURE_RE.search(name) or any(FOLDER_ROLES[2][1].match(part) for part in folder_parts):
            return PLAYER_AID, "name"
        return RULEBOOK, "name"
    for part in reversed(folder_parts):  # the nearest folder says most
        role = next((role for role, rule in FOLDER_ROLES if rule.match(part.strip())), None)
        if role:
            return role, "folder"
    if ADVENTURE_RE.search(name):
        return ADVENTURE, "name"
    return RULEBOOK, ""


def _folder_series(folder_parts, game=""):
    """The series a folder of adventures names: the folder inside "Adventure Paths" (or "Society", "Modules"...),
    tidied: "PF115-120 - Ironfang Invasion" is Ironfang Invasion, "Season 04" a Society season."""
    for above, part in zip(folder_parts, folder_parts[1:]):
        if not FOLDER_ROLES[2][1].match(above.strip()):
            continue
        part = re.sub(r"^\s*(pf|pzo)\s*\d+(-\d+)?\s*[-–:]?\s*", "", part, flags=re.I).strip()
        season = re.match(r"^season ?(\d+)$", part, re.I)
        if season:
            return ("%s Society Season %d" % (game, int(season.group(1)))).strip()
        return part
    return ""


def _product_line(folder_parts, lines):
    """The folders below the game's and edition's: ["Adventure Paths", "Age of Ashes"]."""
    left = []
    for part in folder_parts:
        game, edition = _game(part, lines, folder=True)
        if game or edition or PLAIN_FOLDER.match(part.strip()) or re.search(r"\b(\d(st|nd|rd|th)|first|second|third|fifth) ?ed(ition)?\b|\b[12]e\b", part, re.I):
            continue
        left.append(part.strip())
    return left


def _matches(rule, folder, name):
    if rule.get("folder") and not (folder.lower() + "/").startswith(rule["folder"].lower().rstrip("/") + "/"):
        return False
    if rule.get("pattern"):
        try:
            return bool(re.search(rule["pattern"], name, re.I))
        except re.error:
            return False
    return bool(rule.get("folder"))


def sort_book(title, author="", series="", tags=(), formats=(), origin="", pages="", learned=(), game_lines=None,
              catalogue_tags=()):
    """Everything the sorter can tell about one book, as a dict (the verdict). ``tags`` are the book file's own;
    ``catalogue_tags`` (from outside catalogues) only help tell fiction from non-fiction, because a catalogue
    often has a different book of the same name.


    kind, role ("" unless tabletop), game, edition, tags (to add as family tags), parents ({tag: parent tag}),
    series, series_index, title (a cleaner title, when the name looks like a file name), language,
    product_line (folders below the game's), sources ({"game": "folder", ...}), conflicts (what disagreed, in
    words), unsure (why a person should look), learned (ids of learned rules that matched).
    """
    lines = GAME_LINES if game_lines is None else game_lines
    folder, file_name, archive = split_origin(origin)
    file_name = re.sub(r"_+", " ", file_name)  # "HoardDragonQueen_Encounters": underscores hide word edges
    folder_parts = [part for part in folder.split("/") if part]
    formats = {fmt.upper() for fmt in formats}
    tags = list(tags)
    subjects = tags + list(catalogue_tags)
    name_text = " ".join([title or "", author or "", series or "", file_name, " ".join(tags)])
    # the library sometimes splits "PF 2E - Core Rulebook" into a title and an "author", so both count
    name = file_name or " ".join(part for part in (title, author) if part and part.lower() != "unknown")
    role_name = name + (" | " + archive if archive else "")  # "Merisiel - Level 5 | Iconic Pregens"
    verdict = {"kind": UNSORTED, "role": "", "game": "", "edition": "", "tags": [], "parents": {}, "series": "",
               "series_index": None, "title": "", "language": "", "product_line": [], "sources": {},
               "conflicts": [], "unsure": [], "learned": []}

    # game and edition: folder, then name, then the first pages
    found = [(where, _game(text, lines, folder=(where == "folder")))
             for where, text in (("folder", folder), ("name", name_text), ("pages", pages or "")) if text]
    games = [(where, game) for where, (game, _) in found if game]
    if games:
        verdict["game"], verdict["sources"]["game"] = games[0][1], games[0][0]
        line = next(line for line in lines if line.tag == verdict["game"])
        # a parent game ("World of Darkness") gives way to a more exact one found elsewhere
        exact = [(where, game) for where, game in games if game != line.tag and next(
            (other for other in lines if other.tag == game), line).parent == line.tag]
        if exact:
            verdict["game"], verdict["sources"]["game"] = exact[0][1], exact[0][0]
            line = next(line for line in lines if line.tag == verdict["game"])
        for where, game in games:
            other = next(other for other in lines if other.tag == game)
            # a book's first pages name other games all the time (the Open Game License mentions D&D), so they
            # only disagree with a folder or name that said nothing
            if where == "pages" and verdict["sources"]["game"] != "pages":
                continue
            if game != line.tag and line.parent != game and other.parent != line.tag:
                verdict["conflicts"].append("The %s says %s, the %s says %s." % (
                    verdict["sources"]["game"], line.tag, where, game))
                break
        editions = [(where, line.edition_in(text)) for where, text in
                    (("folder", folder), ("name", name_text), ("pages", pages or "")) if text]
        editions = [(where, edition) for where, edition in editions if edition]
        if editions:
            (first, edition), others = editions[0], editions[1:]
            verdict["edition"], verdict["sources"]["edition"] = edition, first
            other = next(((where, said) for where, said in others if said != edition), None)
            if other:
                verdict["conflicts"].append("The %s says %s, the %s says %s." % (first, edition, other[0], other[1]))

    comic = bool(formats) and formats <= COMIC_FORMATS
    ebook = bool(formats) and formats <= {"EPUB", "MOBI", "AZW3", "AZW", "KEPUB"}
    own_name = " ".join([title or "", author or "", series or "", file_name])
    # catalogues tag novels "Role playing games" too, so only the book's own name counts here
    game_words = TABLETOP_RE.search(own_name) or TABLETOP_RE.search(folder)
    role, role_seen = _role(role_name, folder_parts)
    # a sheet or map in its name, or a folder of them ("Flip Mats"): a picture pack of those is not a comic
    aid_words = role_seen in ("name", "folder") and role in (GM_AID, PLAYER_AID)
    story = FICTION_RE.search(own_name) or FICTION_RE.search(folder)
    # a catalogue saying "fiction" is believed for an ebook; for a PDF it has often mixed the book up with a novel
    # of the same name ("Children of the Blood" is a V5 sourcebook and a novel)
    told_fiction = _tag_kind(subjects) == FICTION and (ebook or not folder)

    if (verdict["game"] or game_words) and told_fiction and ebook:
        verdict["kind"] = FICTION  # "Hell's Pawns" by Dave Gross is a novel, not a box of pawns
    elif verdict["game"] or game_words:
        if (story or told_fiction) and not game_words and not aid_words:
            verdict["kind"] = GRAPHIC if comic else FICTION
        elif comic and not aid_words and not game_words:
            verdict["kind"] = GRAPHIC  # a Vampire comic: a comic set in the game's world
        elif formats and formats <= {"EPUB", "MOBI", "AZW3", "AZW"} and not game_words and role_seen != "name" and verdict["game"]:
            verdict["kind"] = FICTION  # rulebooks come as PDFs; a game's name on an ebook is nearly always a novel
            verdict["unsure"].append("An ebook with a game's name: taken to be a novel set in its world.")
        else:
            verdict["kind"] = TABLETOP
    elif aid_words and (folder or game_words):
        verdict["kind"] = TABLETOP  # maps, pawns, sheets from a game folder we don't know yet
        verdict["unsure"].append("Looks like a game book, but which game is not known.")
    elif comic or GRAPHIC_RE.search(name_text):
        verdict["kind"] = GRAPHIC
    else:
        verdict["kind"] = _tag_kind(subjects) or UNSORTED

    if verdict["kind"] == TABLETOP:
        product = _product_line(folder_parts, lines)
        named = series_from_name(clean_title(name), verdict["game"])
        from_folder = _folder_series(folder_parts, verdict["game"])
        role, role_seen = _role(role_name, folder_parts, bool(named or series or from_folder))
        verdict["role"], verdict["product_line"] = role, product
        if role_seen:
            verdict["sources"]["role"] = role_seen
        if named:
            verdict["series"], verdict["series_index"] = named[0], named[1]
        elif not series:
            verdict["series"] = from_folder
        if not verdict["game"]:
            if "Looks like a game book, but which game is not known." not in verdict["unsure"]:
                verdict["unsure"].append("Looks like a game book, but which game is not known.")
        # a map or flip-mat suits every edition, and a fanzine like Wayfinder writes for whichever is current
        elif line.editions and not verdict["edition"] and role != GM_AID and not FANZINE_RE.search(own_name + " " + folder):
            verdict["unsure"].append("Which edition of %s is not known." % verdict["game"])
        elif verdict["sources"].get("game") == "pages":
            verdict["unsure"].append("Only the book's first pages name the game.")

    # a cleaner title, when the library took the file's name (or split it into title and "author")
    if verdict["kind"] == TABLETOP or (file_name and verdict["kind"] == UNSORTED):
        better = clean_title(file_name or name)
        named = series_from_name(better, verdict["game"])
        if named:
            better = named[2]
        if better and better.lower() != (title or "").strip().lower():
            verdict["title"] = better
    verdict["language"] = language_of(" ".join([title or "", file_name]))

    for rule in sorted(learned, key=lambda rule: (len(rule.get("folder") or ""), bool(rule.get("pattern")))):
        if not _matches(rule, folder, name):
            continue
        verdict["learned"].append(rule.get("id"))
        sets = rule.get("sets") or {}
        for field in ("kind", "role", "game", "edition", "series"):
            if sets.get(field) is not None:
                verdict[field] = sets[field]
                verdict["sources"][field] = "learned"
        if sets.get("tags"):
            verdict["tags"] += [tag for tag in sets["tags"] if tag not in verdict["tags"]]
        verdict["conflicts"], verdict["unsure"] = [], []  # a person has already said what books like this are
        for field, value in (rule.get("rejects") or {}).items():
            if verdict.get(field) == value:
                verdict[field] = "" if field != "series_index" else None

    if verdict["game"]:
        verdict["tags"].insert(0, verdict["game"])
        line = next((line for line in lines if line.tag == verdict["game"]), None)
        while line and line.parent:
            verdict["parents"][line.tag] = line.parent
            if line.parent not in verdict["tags"]:
                verdict["tags"].insert(0, line.parent)
            line = next((other for other in lines if other.tag == line.parent), None)
    if verdict["edition"]:
        # fiction gets an edition only when the book itself names one; a folder full of rulebooks doesn't count
        game_book = verdict["kind"] in (TABLETOP, UNSORTED) or verdict["sources"].get("edition") == "learned"
        if game_book or (verdict["game"] and next(line for line in lines if line.tag == verdict["game"]).edition_in(own_name)):
            verdict["tags"].append(verdict["edition"])
            if verdict["game"]:
                verdict["parents"][verdict["edition"]] = verdict["game"]
        else:
            verdict["edition"] = ""
    text = " ".join([name_text, folder])
    verdict["tags"] += [tag for tag, rule in TAG_RULES if rule.search(text) and tag not in verdict["tags"]]
    if verdict["kind"] != TABLETOP:
        verdict["role"] = ""
    return verdict


def needs_pages(verdict, formats):
    """Whether reading the first pages of the PDF could settle or check something the names left open: a game
    book without its game or edition, or one whose game only its name gives (the folder being the surer clue)."""
    if "PDF" not in {fmt.upper() for fmt in formats} or "learned" in verdict["sources"].values():
        return False
    if verdict["kind"] == UNSORTED:
        return True
    return verdict["kind"] == TABLETOP and (not verdict["game"] or not verdict["edition"] or verdict["sources"].get("game") != "folder")


def guess(title, author="", series="", tags=(), formats=(), origin=""):
    """Return (kind, [tags to add]) for a book."""
    verdict = sort_book(title, author, series, tags, formats, origin)
    return verdict["kind"], verdict["tags"]
