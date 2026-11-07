"""Game books in the library: the Tabletop view, nested tags, and what each person hides from their own feed.

The Tabletop view puts every game book (and every novel or comic carrying a game's tag) under
Game › Edition › Product line. The game and edition are the book's family tags ("Pathfinder", "Pathfinder 2e");
the product line is the folders the book arrived in below the game's ("Adventure Paths › Age of Ashes", from the
sorter's verdict), else its series, else a shelf for its role ("Rulebooks"). Inside a line: core books, then
general player's guides, then other rulebooks; an adventure path's parts in order, then its Player's Guide and maps.

Nested tags: a tag can sit under another ("Pathfinder 2e" under "Pathfinder"), to any depth. Asking for a tag
always means it and everything under it.

Hiding: each profile can hide tags (and roles, "GM aid") from its own library feed. Hidden books still turn up in
searches and in the Tabletop view; the feed is only what shows before anything is searched for.

Everything here works on the item lists api.books() already built, so nothing asks the database book by book.
"""
import json
import re

from . import db, kinds

NO_GAME = "Game not known"
ROLE_SHELF = {kinds.RULEBOOK: "Rulebooks", kinds.ADVENTURE: "Adventures", kinds.PLAYER_AID: "Player aids", kinds.GM_AID: "GM aids"}
KIND_SHELF = {kinds.FICTION: "Novels and stories", kinds.GRAPHIC: "Comics", kinds.NONFICTION: "Other books", kinds.UNSORTED: "Other books"}
ROLE_RANK = {kinds.RULEBOOK: 0, kinds.ADVENTURE: 1, kinds.PLAYER_AID: 2, kinds.GM_AID: 3}
# The core books in the order a table reads them: the rules, the GM's book, the monsters. First match wins.
CORE_ORDER = [
    (1, re.compile(r"\bgm core\b|dungeon master'?s guide|game ?master'?s? core", re.I)),
    (2, re.compile(r"\bbestiary\b|monster (manual|core)", re.I)),
    (0, re.compile(r"\bcore\b|corebook|core ?rule ?book|player'?s handbook|basic rules|beginner|starter", re.I)),
]
ENGLISH = {"", "en", "eng", "und"}
HIDDEN_KEY = "hidden-tags:%d"


# ------------------------------------------------------------------ nested tags

def descendants(parents):
    """{tag in lower case: {itself and every tag under it, as named}} from hub.tag_parents()."""
    children = {}
    for tag, parent in parents.items():
        children.setdefault(parent.lower(), []).append(tag)
    found = {}
    for top in set(children) | {tag.lower() for tag in parents}:
        seen, todo = set(), [top]
        while todo:  # set_tag_parent refuses loops, but an imported file might not have
            name = todo.pop()
            if name.lower() in seen:
                continue
            seen.add(name.lower())
            todo += children.get(name.lower(), [])
        found[top] = seen
    return found


def under(nested, tag):
    """The tag and everything nested under it, in lower case."""
    return nested.get((tag or "").lower(), {(tag or "").lower()})


def short_name(tag, parent):
    """How a nested tag reads under its parent: "Pathfinder 2e" under "Pathfinder" is "2e"."""
    if parent and tag.lower().startswith(parent.lower()) and len(tag) > len(parent):
        rest = tag[len(parent):].lstrip(" :-–·")
        if rest:
            return rest
    return tag


def roll_up(counts, parents):
    """For /api/tags (merged): a parent tag counts the books of every tag under it, and each entry says its parent."""
    nested = descendants(parents)
    lower_parents = {tag.lower(): parent for tag, parent in parents.items()}
    for low, entry in counts.items():
        if not isinstance(low, str):
            continue
        entry["parent"] = lower_parents.get(low, "")
        for child in nested.get(low, ()):
            if child != low and child in counts:
                entry["keys"] |= counts[child]["keys"]


# ------------------------------------------------------------------ hiding

def hidden(hub, profile_id):
    """The tags and roles this profile hides from its own feed."""
    if not profile_id:
        return []
    try:
        found = json.loads(db.kv_get(hub.con(), HIDDEN_KEY % profile_id) or "[]")
    except ValueError:
        return []
    return [tag for tag in found if isinstance(tag, str)]


def set_hidden(hub, profile_id, names):
    names = names if isinstance(names, list) else []
    kept, seen = [], set()
    for name in names:
        name = db.clean_tag(name)
        if name and name.lower() not in seen:
            seen.add(name.lower())
            kept.append(name)
    db.kv_set(hub.con(), HIDDEN_KEY % profile_id, json.dumps(kept[:100]))
    return kept[:100]


def feed(hub, profile_id, items, nested):
    """(items left, how many were hidden) once this profile's hidden tags and roles are taken out."""
    names = hidden(hub, profile_id)
    if not names:
        return items, 0
    gone = set()
    for name in names:
        gone |= under(nested, name)
    roles = {role.lower() for role in kinds.ROLES} & gone

    def hide(item):
        if item.get("role") and item["role"].lower() in roles:
            return True
        return any(tag.lower() in gone for group in item["tags"].values() for tag in group)

    kept = [item for item in items if not hide(item)]
    return kept, len(items) - len(kept)


# ------------------------------------------------------------------ what each book is

def _game_tags(hub):
    """({game tag lower: (tag, parent)}, {edition tag lower: (tag, game)}): from the game lines and any nesting
    people added (an edition is a tag nested straight under a game)."""
    games, editions = {}, {}
    for line in hub.game_lines():
        games[line.tag.lower()] = (line.tag, line.parent)
        if line.parent and line.parent.lower() not in games:
            games[line.parent.lower()] = (line.parent, "")
        for tag, _ in line.editions:
            editions[tag.lower()] = (tag, line.tag)
    for tag, parent in hub.tag_parents().items():
        if parent.lower() in games and tag.lower() not in games and tag.lower() not in editions:
            editions[tag.lower()] = (tag, games[parent.lower()][0])
    return games, editions


def _verdicts(hub):
    found = {}
    for row in hub.con().execute("SELECT book_key, verdict FROM sort_verdicts"):
        try:
            found[row["book_key"]] = json.loads(row["verdict"])
        except ValueError:
            continue
    return found


def annotate(hub, items):
    """Add what the library page needs to group and badge game books: game, game_parent, edition, role,
    game_series, game_index, product_line and language. Three queries for the whole list."""
    games, editions = _game_tags(hub)
    verdicts = _verdicts(hub)
    roles = hub.role_index()
    languages = {book["key"]: book.get("language") or "" for book in hub.library.books()}
    for item in items:
        family = item["tags"].get("family", [])
        found = [games[tag.lower()] for tag in family if tag.lower() in games]
        # the most exact game wins: "Vampire: The Masquerade" over "World of Darkness"
        game, parent = next(((tag, up) for tag, up in found if up), found[0] if found else ("", ""))
        edition = next((tag for tag in family if tag.lower() in editions and editions[tag.lower()][1].lower() == game.lower()), "")
        verdict = verdicts.get(item["key"]) or {}
        role = roles.get(item["key"], "") if item["kind"] == kinds.TABLETOP else ""
        series = item.get("series") or ""
        index = item.get("series_index") if series else None
        if not series and item["kind"] == kinds.TABLETOP:
            series, index = verdict.get("series") or "", verdict.get("series_index")
        language = (verdict.get("language") or languages.get(item["key"]) or "").lower()
        item.update(game=game, game_parent=parent if game else "", edition=edition, role=role, game_series=series,
                    game_index=index, product_line=[part for part in verdict.get("product_line") or [] if isinstance(part, str)],
                    language="" if language in ENGLISH else language)
    return items


def is_game_book(item):
    return item["kind"] == kinds.TABLETOP or bool(item.get("game"))


# ------------------------------------------------------------------ the Tabletop view

def line_of(item):
    """The product line a game book sits in, as one name."""
    if item["kind"] != kinds.TABLETOP:
        return KIND_SHELF.get(item["kind"], "Other books")
    parts = list(item.get("product_line") or [])
    series = item.get("game_series") or ""
    # "Adventure Paths" alone says little: the series inside it is the line. "Society › Season 03" already says it
    if series and (not parts or kinds.FOLDER_ROLES[2][1].match(parts[-1])) and series.lower() not in (part.lower() for part in parts):
        parts.append(series)
    if parts:
        return " › ".join(parts)
    return ROLE_SHELF.get(item.get("role"), "Rulebooks")


def order_key(item):
    """Where a book goes inside its line: core books, general player's guides, other rulebooks; adventures by
    their number; then player aids (an adventure path's Player's Guide first) and GM aids (maps and the like)."""
    role = item.get("role") or ""
    title = item.get("title") or ""
    rank = ROLE_RANK.get(role, 4) if item["kind"] == kinds.TABLETOP else 5
    if role == kinds.RULEBOOK:
        sub = next((rank for rank, rule in CORE_ORDER if rule.search(title)), 3 if kinds.PLAYERS_GUIDE_RE.search(title) else 4)
    elif role == kinds.PLAYER_AID:
        sub = 0 if kinds.PLAYERS_GUIDE_RE.search(title) else 1
    else:
        sub = 0
    index = item.get("game_index")
    return (rank, sub, index if isinstance(index, (int, float)) and index else 9999, title.lower())


def tree(items):
    """Games › editions › lines, each line a list of positions in ``items``, in reading order.
    [{"game", "parent", "count", "editions": [{"edition", "label", "count", "lines": [{"name", "items", "roles"}]}]}]"""
    games = {}
    for at, item in enumerate(items):
        game = item.get("game") or NO_GAME
        entry = games.setdefault(game.lower(), {"game": game, "parent": item.get("game_parent") or "", "editions": {}})
        edition = entry["editions"].setdefault((item.get("edition") or "").lower(), {
            "edition": item.get("edition") or "", "label": short_name(item.get("edition") or "", game) if item.get("edition") else "No edition given",
            "lines": {}})
        edition["lines"].setdefault(line_of(item), []).append(at)

    def rank_of(positions):
        return min(order_key(items[at])[:2] for at in positions)

    out = []
    for entry in games.values():
        editions = []
        for edition in entry["editions"].values():
            lines = []
            for name, positions in edition["lines"].items():
                positions.sort(key=lambda at: order_key(items[at]))
                roles = {}
                for at in positions:
                    role = items[at].get("role") or items[at]["kind"]
                    roles[role] = roles.get(role, 0) + 1
                lines.append({"name": name, "items": positions, "roles": roles})
            lines.sort(key=lambda line: (rank_of(line["items"]), line["name"].lower()))
            editions.append({"edition": edition["edition"], "label": edition["label"], "lines": lines,
                             "count": sum(len(line["items"]) for line in lines)})
        editions.sort(key=lambda edition: (not edition["edition"], edition["edition"].lower()))
        out.append({"game": entry["game"], "parent": entry["parent"], "editions": editions,
                    "count": sum(edition["count"] for edition in editions)})
    out.sort(key=lambda entry: (entry["game"] == NO_GAME, (entry["parent"] or entry["game"]).lower(), bool(entry["parent"]), entry["game"].lower()))
    return out
