"""What to read next, worked out from what each person has finished and rated.

Two things come from the same picture of a person's taste:

* an order for the unread books in the library ("Similar to what I've read"), and
* once a week, a short list of books the library does not have, found on
  Open Library by the authors and subjects the person rates highest.

Nothing is worked out until a person has finished or rated a handful of
books; before that there is too little to go on. A person can also say which
authors they like (counted as strongly as a five-star book, and asked about
first) and which they do not (never suggested). Those lists are theirs alone. What is sent to Open Library
is an author's name or a subject, never who is asking.
"""
import json
import math
import random
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from . import catalogues, db, goodreads

MIN_BOOKS = 5            # books finished or rated before any of this starts
PER_WEEK = 12
DAYS = 7
AUTHORS, SUBJECTS = 4, 4  # how many favourite authors and subjects are asked about
SUBJECT_TRIES = 8        # family tags such as "Comfort re-read" are not subjects anywhere else, so try a few more
RATING_WEIGHT = {5: 3, 4: 2, 3: 1, 2: -1, 1: -2}
TOO_BROAD = {"fiction", "non-fiction", "nonfiction", "general", "novels", "literature", "books"}
SEARCH = "https://openlibrary.org/search.json"
FIELDS = "key,title,author_name,first_publish_year,cover_i,language"


LIKED_WEIGHT = 6  # an author someone says they like counts as two five-star books


def _authors(text):
    return [name.strip() for name in (text or "").split("&") if catalogues.author_known(name.strip())]


def author_prefs(hub, profile_id):
    """{"like": [names], "dislike": [names]}: authors this person has said they like or do not."""
    kept = db.kv_get(hub.con(), "authors:%d" % profile_id)
    try:
        got = json.loads(kept) if kept else {}
    except ValueError:
        got = {}
    return {"like": list(got.get("like") or []), "dislike": list(got.get("dislike") or [])}


def set_author_pref(hub, profile_id, author, how):
    """how: "like", "dislike" or "none" (take it off both lists)."""
    name = " ".join(str(author or "").split())[:120]
    if not name:
        return author_prefs(hub, profile_id)
    prefs = author_prefs(hub, profile_id)
    for which in ("like", "dislike"):
        prefs[which] = [listed for listed in prefs[which] if listed.lower() != name.lower()]
    if how in ("like", "dislike"):
        prefs[how].append(name)
        prefs[how].sort(key=str.lower)
    db.kv_set(hub.con(), "authors:%d" % profile_id, json.dumps(prefs))
    return prefs


def _disliked(author_text, dislikes):
    """Is any of a book's authors one this person does not want suggested?"""
    # checked both ways round, so "Rowling" catches "J. K. Rowling" and vice versa
    return any(_wrote(author, [dislike]) or _wrote(dislike, [author])
               for author in (author_text or "").split("&") for dislike in dislikes if author.strip())


def _tags_of(index, key, extra=()):
    """A book's tags from every group, each once whatever its case: {lower: as written}."""
    return {tag.lower(): tag for tag in list(extra) + [tag for group in index.get(key, {}).values() for tag in group]}


def taste(hub, profile_id, index=None):
    """{"have": books finished or rated, "ready": bool, "tags": {tag: weight}, "authors": {name: weight}}
    index: this person's hub.tag_index, when the caller has it already."""
    con = hub.con()
    ratings = {row["book_key"]: row["rating"] for row in con.execute(
        "SELECT book_key, rating FROM ratings WHERE profile_id=?", (profile_id,))}
    reading = hub.reading_rows(profile_id)
    finished = {row["book_key"] for row in reading if row["status"] == "finished"}
    index = hub.tag_index(profile_id) if index is None else index
    meta = {row["book_key"]: row["author"] for row in con.execute("SELECT book_key, author FROM book_meta")}
    keys = hub.library.keys()
    tags, authors, names = defaultdict(float), defaultdict(float), {}
    followed = _followed(hub, profile_id, ratings)
    followed_keys = {key for key, *_ in followed}

    def count(found_tags, author_text, weight):
        for lower, tag in found_tags.items():
            tags[lower] += weight
            names.setdefault(lower, tag)
        for name in _authors(author_text):
            authors[name] += weight

    for key in (set(ratings) | finished) - followed_keys:
        weight = RATING_WEIGHT.get(ratings.get(key), 1)  # finished but never rated = "yeah it was fine"
        count(_tags_of(index, key), keys[key]["author"] if key in keys else meta.get(key, ""), weight)
    # Series followed chapter by chapter count too, with the reading site's tags and the series' own.
    for key, author, weight, site_tags in followed:
        count(_tags_of(index, key, site_tags), author, weight)
    prefs = author_prefs(hub, profile_id)
    for name in prefs["like"]:
        authors[name] += LIKED_WEIGHT
    for name in prefs["dislike"]:
        authors.pop(name, None)
    # Authors someone likes count towards having enough to go on.
    have = len(set(ratings) | finished | followed_keys) + len(prefs["like"])
    return {"have": have, "need": MIN_BOOKS, "ready": have >= MIN_BOOKS, "tags": dict(tags), "authors": dict(authors),
            "names": names, "dislike": prefs["dislike"], "seen": set(ratings) | {row["book_key"] for row in reading}}


def _followed(hub, profile_id, ratings):
    """Series followed chapter by chapter this person has enjoyed: [(series key, author, weight, site tags)].
    A rated series counts by its rating; an unrated one as mildly liked once a fifth of it (at least three
    chapters) has been read."""
    from . import series
    if not hub.series.configured or profile_id in series.blocked_ids(hub):
        return []
    enjoyed = []
    for followed in hub.series.shelf_cached()["series"]:
        key = db.series_key(followed["title"])
        rated = ratings.get(key)
        read_lots = followed["read"] >= max(3, followed["chapters"] // 5)
        weight = RATING_WEIGHT.get(rated, 0) if rated else (1 if read_lots else 0)
        if weight:
            enjoyed.append((key, followed["author"], weight, list(followed["genres"])))
    return enjoyed


def _rarity(hub, index):
    """A tag on every other book says little; one on a handful says a lot."""
    books = hub.library.books()
    counts = defaultdict(int)
    for book in books:
        for tag in _tags_of(index, book["key"]):
            counts[tag] += 1
    total = max(1, len(books))
    # basically TF-IDF's "IDF" half: log(books / books with the tag). "Fiction" ~ 0, "Cozy cat detective" ~ big
    return lambda tag: math.log(1 + total / counts[tag]) if counts.get(tag) else math.log(1 + total)


def for_you(hub, profile_id, index=None):
    """Unread library books, scored against this person's taste: {book key: {"score", "why"}}.
    index: this person's hub.tag_index, when the caller has it already."""
    index = hub.tag_index(profile_id) if index is None else index
    mine = taste(hub, profile_id, index)
    result = {"have": mine["have"], "need": mine["need"], "ready": mine["ready"], "books": {}}
    if not mine["ready"]:
        return result
    rarity = _rarity(hub, index)
    # The words of each author in their taste, worked out once rather than for every book (see _wrote).
    liked_words = [(author, weight, set(catalogues._words(author))) for author, weight in mine["authors"].items()]
    for book in hub.library.books():
        if book["key"] in mine["seen"] or _disliked(book["author"], mine["dislike"]):
            continue
        tag_scores = [(mine["tags"][lower] * rarity(lower), tag) for lower, tag in _tags_of(index, book["key"]).items()
                      if mine["tags"].get(lower, 0)]
        author_weights = {}
        for name in _authors(book["author"]):
            words = {word for word in catalogues._words(name) if len(word) > 2}
            author_weights.update((author, weight) for author, weight, theirs in liked_words if words and words <= theirs)
        liked = [author for author, weight in author_weights.items() if weight > 0]
        # a favourite author is worth 3x a matching tag. a rule of thumb, fiddle if it feels off
        score = sum(points for points, _ in tag_scores) + 3 * sum(author_weights.values())
        if score <= 0:
            continue
        why = (["More by " + liked[0]] if liked else []) + [tag for points, tag in sorted(tag_scores, reverse=True) if points > 0][:3]
        result["books"][book["key"]] = {"score": round(score, 2), "why": why[:3]}
    return result


# -------------------------------------------------- books the library lacks

def favourites(hub, profile_id, mine=None, index=None):
    """The authors and subjects worth asking Open Library about, best first."""
    index = hub.tag_index(profile_id) if index is None else index
    mine = mine or taste(hub, profile_id, index)
    rarity = _rarity(hub, index)
    by_weight = sorted(mine["authors"].items(), key=lambda pair: (-pair[1], pair[0]))
    authors = [author for author, weight in by_weight if weight >= 2][:AUTHORS]
    author_names = {author.lower() for author in mine["authors"]}
    scored = sorted(((weight * rarity(tag), mine["names"][tag]) for tag, weight in mine["tags"].items()
                     if weight > 0 and tag not in TOO_BROAD and tag not in author_names), reverse=True)
    return authors, [name for _, name in scored[:SUBJECT_TRIES]]


def _wrote(author, names):
    """Is ``author`` one of ``names``, give or take initials and spacing?"""
    # words of 3+ letters only, so initials ("J. K.") don't get a vote
    mine = {word for word in catalogues._words(author) if len(word) > 2}
    return bool(mine) and mine <= {word for name in names for word in catalogues._words(name)}


def _english(doc):
    return not doc.get("language") or "eng" in doc["language"]


def candidates(authors, subjects):
    """Ask Open Library. Yields (title, author, year, cover id, reason)."""
    for author in authors:
        docs = catalogues._get(SEARCH, {"author": author, "fields": FIELDS, "sort": "editions", "limit": 15}).get("docs") or []
        for doc in docs:
            names = doc.get("author_name") or []
            # Their own books, not anthologies with a story of theirs in.
            if len(names) <= 2 and _english(doc) and _wrote(author, names):
                yield doc.get("title"), names[0], doc.get("first_publish_year"), doc.get("cover_i"), "More by " + author
    since, useful = date.today().year - 35, 0
    for subject in subjects:
        if useful >= SUBJECTS:
            break
        query = 'subject:"%s" first_publish_year:[%d TO %d] language:eng' % (subject.replace('"', ""), since, date.today().year + 1)
        docs = catalogues._get(SEARCH, {"q": query, "fields": FIELDS, "sort": "readinglog", "limit": 20}).get("docs") or []
        useful += bool(docs)
        for doc in docs:
            names = doc.get("author_name") or []
            if names and _english(doc):
                yield doc.get("title"), names[0], doc.get("first_publish_year"), doc.get("cover_i"), "Because you like " + subject


def refresh(hub, profile_id):
    """Make this week's list for one person. Returns how many books are on it, or None
    when there is nothing to go on yet or outside lookups are switched off."""
    con = hub.con()
    tagged = hub.tag_index(profile_id)
    mine = taste(hub, profile_id, tagged)
    if not mine["ready"] or hub.config.tag_lookups != "on":
        return None
    authors, subjects = favourites(hub, profile_id, mine, tagged)
    index = goodreads.library_index(hub)
    keys = hub.library.keys()
    skip = {row["book_key"] for row in con.execute(
        "SELECT book_key FROM recommendations WHERE profile_id=? AND dismissed=1", (profile_id,))} | mine["seen"]
    by_reason, taken = defaultdict(list), set()
    for title, author, year, cover, reason in candidates(authors, subjects):
        if not title or not author or _disliked(author, mine["dislike"]):
            continue
        key = goodreads.key_for(hub, title, author, index=index)
        # "Villette, a novel" is the "Villette" they have already read.
        names = {db.make_key(variant, author) for variant in goodreads._titles(title)} | {key}
        if key in keys or names & skip or names & taken:
            continue
        taken |= names
        by_reason[reason].append((key, title, author, year, cover, reason))
    # round robin: one from each author/subject in turn, so it's not twelve books by whoever answered first.
    # the random seed is person + week, so the list stays put all week instead of reshuffling every refresh
    picks, rng = [], random.Random("%s-%s" % (profile_id, date.today().isocalendar()[:2]))
    lists = list(by_reason.values())
    for group in lists:
        head, tail = group[:6], group[6:]
        rng.shuffle(head)
        group[:] = head + tail
    while lists and len(picks) < PER_WEEK:
        for group in list(lists):
            if group and len(picks) < PER_WEEK:
                picks.append(group.pop(0))
            if not group:
                lists.remove(group)
    con.execute("DELETE FROM recommendations WHERE profile_id=? AND dismissed=0", (profile_id,))
    for key, title, author, year, cover, reason in picks:
        con.execute("INSERT OR IGNORE INTO recommendations(profile_id, book_key, title, author, year, cover_id, reason, at, dismissed) "
                    "VALUES(?,?,?,?,?,?,?,?,0)", (profile_id, key, title, author, year, cover, reason, db.now()))
    db.kv_set(con, "recommended:%d" % profile_id, db.now())
    con.commit()
    return len(picks)


def refresh_due(hub, log=None):
    """Once a week for everyone who has read enough."""
    con = hub.con()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    for profile in hub.profiles():
        last = db.kv_get(con, "recommended:%d" % profile["id"])
        if last and last > cutoff:
            continue
        try:
            refresh(hub, profile["id"])
        except catalogues.LookupError_ as exc:
            if log:
                log("suggestions for %s could not be fetched: %s", profile["name"], exc)


def listing(hub, profile_id):
    """This week's list, without anything that has since arrived in the library or been started."""
    con = hub.con()
    keys = hub.library.keys()
    index = goodreads.library_index(hub)
    seen = {row["book_key"] for row in hub.reading_rows(profile_id)}
    dislikes = author_prefs(hub, profile_id)["dislike"]
    books = []
    for row in con.execute("SELECT * FROM recommendations WHERE profile_id=? AND dismissed=0 ORDER BY rowid", (profile_id,)):
        arrived = goodreads.key_for(hub, row["title"], row["author"], index=index) in keys
        if row["book_key"] in seen or _disliked(row["author"], dislikes) or arrived:
            continue
        books.append({"key": row["book_key"], "title": row["title"], "author": row["author"], "year": row["year"],
                      "cover_id": row["cover_id"], "reason": row["reason"]})
    return {"books": books, "updated": db.kv_get(con, "recommended:%d" % profile_id)}


def dismiss(hub, profile_id, book_key):
    con = hub.con()
    con.execute("UPDATE recommendations SET dismissed=1 WHERE profile_id=? AND book_key=?", (profile_id, book_key))
    con.commit()


def forget_profile(hub, profile_id):
    hub.con().execute("DELETE FROM recommendations WHERE profile_id=?", (profile_id,))
    hub.con().execute("DELETE FROM kv WHERE key=?", ("authors:%d" % profile_id,))
