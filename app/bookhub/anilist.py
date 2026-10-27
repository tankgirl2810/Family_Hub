"""Manga, manhwa and webcomics to try next, from AniList.

AniList (anilist.co) is a free catalogue of manga and manhwa. Every title there has readers' tags, each with how
strongly it applies ("Isekai 92%"), and readers' recommendations ("people who liked this also liked..."). For the
series a person has enjoyed (followed chapter by chapter, or graphic novels in the library), Family Hub asks
AniList what readers recommend, leaves out what they already have, and puts the rest in order: how many readers
recommend it, from how many of the series they liked, and how well its tags match the tags of what they liked.

Only series titles are sent, never who is asking, and only while looking things up outside is switched on
(the same switch as Open Library). Answers are kept for a week, and each look asks about a few series at a time,
because AniList allows only so many questions a minute.
"""
import json
import logging
import re
import time

import requests

from . import db, recommend, search

API = "https://graphql.anilist.co"
TIMEOUT = 20
KEEP_DAYS = 7
SEEDS = 10            # liked series asked about, the best liked first
NEW_PER_LOOK = 4      # of those, at most this many not already kept are asked about in one go
SHOW = 18
COUNTRY = {"KR": "Manhwa", "CN": "Manhua", "TW": "Manhua", "JP": "Manga"}
QUERY = """query($s: String) { Media(search: $s, type: MANGA) { id title { romaji english } countryOfOrigin
  recommendations(sort: RATING_DESC, perPage: 15) { nodes { rating mediaRecommendation {
    id title { romaji english } countryOfOrigin format status averageScore siteUrl genres
    coverImage { medium } tags { name rank isMediaSpoiler } } } } } }"""
log = logging.getLogger("bookhub")


class AniListError(Exception):
    pass


def ask(title):
    """AniList's entry for a title and what readers recommend from it, or None when it does not know the title."""
    try:
        reply = requests.post(API, json={"query": QUERY, "variables": {"s": title}}, timeout=TIMEOUT,
                              headers={"Accept": "application/json", "User-Agent": "FamilyHub (home library)"})
    except requests.RequestException as exc:
        raise AniListError("AniList could not be reached: %s" % str(exc)[:150])
    if reply.status_code == 404:
        return None
    if reply.status_code == 429:
        raise AniListError("AniList asked Family Hub to slow down; the rest are asked about next time.")
    try:
        data = reply.json()
    except ValueError:
        raise AniListError("AniList sent something unexpected (%s)." % reply.status_code)
    media = (data.get("data") or {}).get("Media")
    if not media:
        if reply.status_code >= 500:
            raise AniListError("AniList is having trouble (%s)." % reply.status_code)
        return None
    recs = [_rec(node) for node in (media.get("recommendations") or {}).get("nodes") or []
            if node.get("mediaRecommendation") and (node.get("rating") or 0) > 0]
    return {"id": media["id"], "recs": recs}


def _rec(node):
    """One of AniList's recommendations, trimmed to what the page shows."""
    media = node["mediaRecommendation"]
    titles = media.get("title") or {}
    # top 8 tags readers actually agree on (60%+), minus the spoilery ones
    by_rank = sorted(media.get("tags") or [], key=lambda tag: -(tag.get("rank") or 0))
    tags = [tag["name"] for tag in by_rank if not tag.get("isMediaSpoiler") and (tag.get("rank") or 0) >= 60][:8]
    return {
        "id": media["id"], "title": titles.get("english") or titles.get("romaji") or "",
        "other_title": titles.get("romaji") or "",
        "kind": COUNTRY.get(media.get("countryOfOrigin") or "", "Manga"), "format": media.get("format") or "",
        "status": (media.get("status") or "").replace("_", " ").title(), "score": media.get("averageScore"),
        "link": media.get("siteUrl") or "", "cover": (media.get("coverImage") or {}).get("medium") or "",
        "genres": media.get("genres") or [], "tags": tags, "votes": node.get("rating") or 0,
    }


def plain(title):
    """A title for comparing: no bracketed tags ("[Official]", "(Digital)"), volume numbers or punctuation."""
    title = re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", title or "")
    title = re.sub(r"\b(?:v|vol|volume)\.?\s*\d+\b.*$", " ", title, flags=re.I)
    return search.norm(title)


def _kept(hub, title):
    raw = db.kv_get(hub.con(), "anilist:" + db.slug(title))
    if not raw:
        return False, None
    try:
        got = json.loads(raw)
    except ValueError:
        return False, None
    return time.time() - got.get("at", 0) < KEEP_DAYS * 86400, got.get("answer")


def liked_series(hub, profile_id, index=None):
    """The manga and manhwa this person has enjoyed, the best liked first: [(title, weight, tags)].
    Series followed chapter by chapter that they rated or have read a good way into, and graphic novel
    series in the library they rated or finished volumes of. index: this person's hub.tag_index, if to hand."""
    from . import kinds, series
    con = hub.con()
    ratings = {row["book_key"]: row["rating"] for row in con.execute("SELECT book_key, rating FROM ratings WHERE profile_id=?", (profile_id,))}
    liked = {}
    if hub.series.configured and profile_id not in series.blocked_ids(hub):
        for followed in hub.series.shelf_cached()["series"]:
            rated = ratings.get(db.series_key(followed["title"]))
            # no rating? reading a fifth of it (3 chapters minimum) counts as liking it
            read_lots = followed["read"] >= max(3, followed["chapters"] // 5)
            weight = recommend.RATING_WEIGHT.get(rated, 0) if rated else (1 if read_lots else 0)
            if weight > 0:
                liked[followed["title"].lower()] = (followed["title"], weight, list(followed["genres"]))
    kind_of = hub.kind_index()
    finished = {row["book_key"] for row in hub.reading_rows(profile_id) if row["status"] == "finished"}
    index = hub.tag_index(profile_id) if index is None else index
    by_series = {}
    for book in hub.library.books():
        if kind_of.get(book["key"]) == kinds.GRAPHIC and book.get("series"):
            by_series.setdefault(book["series"], []).append(book)
    for name, volumes in by_series.items():
        rated = ratings.get(db.series_key(name)) or max((ratings.get(volume["key"]) or 0 for volume in volumes), default=0)
        finished_one = any(volume["key"] in finished for volume in volumes)
        weight = recommend.RATING_WEIGHT.get(rated, 0) if rated else (1 if finished_one else 0)
        if weight > 0 and name.lower() not in liked:
            tags = sorted({tag for volume in volumes for group in index.get(volume["key"], {}).values() for tag in group})
            liked[name.lower()] = (name, weight, tags)
    return sorted(liked.values(), key=lambda entry: -entry[1])


def suggestions(hub, profile_id, look=True):
    """{"books": [...], "asked": n, "waiting": n, "error": str}: manga and manhwa to try, the best first.
    look: ask AniList about liked series not kept yet (a few at a time); otherwise only what is kept is used."""
    index = hub.tag_index(profile_id)
    liked = liked_series(hub, profile_id, index)[:SEEDS]
    if not liked:
        return {"books": [], "liked": 0, "asked": 0, "waiting": 0, "error": ""}
    asked, error, answers = 0, "", []
    for title, weight, tags in liked:
        fresh, answer = _kept(hub, title)
        if not fresh and look and hub.config.tag_lookups == "on" and asked < NEW_PER_LOOK and not error:
            try:
                answer = ask(title)
                asked += 1
                db.kv_set(hub.con(), "anilist:" + db.slug(title), json.dumps({"at": time.time(), "answer": answer}))
            except AniListError as exc:
                error = str(exc)
        if answer:
            answers.append((title, weight, tags, answer))
    waiting = sum(1 for title, _, _ in liked if not _kept(hub, title)[0])
    # What they already have: followed series, the library's series and titles, and what they turned down.
    have = {plain(followed["title"]) for followed in (hub.series.shelf_cached()["series"] if hub.series.configured else [])}
    have |= {plain(name) for book in hub.library.books() for name in (book.get("series"), book["title"]) if name}
    have |= {plain(title) for title, _, _ in liked}
    dismissed = set(json.loads(db.kv_get(hub.con(), "anilist-no:%d" % profile_id) or "[]"))
    mine = recommend.taste(hub, profile_id, index)
    dislikes = mine["dislike"]
    liked_tags = {}
    for _, weight, tags, _ in answers:
        for tag in tags:
            liked_tags[tag.lower()] = liked_tags.get(tag.lower(), 0) + weight
    for tag, tag_weight in mine["tags"].items():
        liked_tags[tag] = liked_tags.get(tag, 0) + tag_weight
    picks = {}
    for title, weight, _, answer in answers:
        for rec in answer["recs"]:
            names = {plain(rec["title"]), plain(rec["other_title"])} - {""}
            if rec["id"] in dismissed or names & have or any(dislike.lower() in rec["title"].lower() for dislike in dislikes):
                continue
            pick = picks.setdefault(rec["id"], dict(rec, because=[], points=0.0))
            pick["because"].append(title)
            # votes help, but cap them at 50 so one mega-popular rec doesn't bulldoze everything
            pick["points"] += weight * (1 + min(rec["votes"], 50) / 10)
    for pick in picks.values():
        everything = pick["tags"] + pick["genres"]
        match = sum(max(0, liked_tags.get(tag.lower(), 0)) for tag in everything)
        pick["points"] += 0.5 * match + (pick["score"] or 0) / 20
        pick["matched"] = [tag for tag in everything if liked_tags.get(tag.lower(), 0) > 0][:3]
    books = sorted(_fair_share(sorted(picks.values(), key=lambda pick: -pick["points"]))[:SHOW], key=lambda pick: -pick["points"])
    for pick in books:
        pick["reason"] = _reason(pick["because"])
        pick.pop("points", None)
    return {"books": books, "liked": len(liked), "asked": asked, "waiting": waiting, "error": error}


def _fair_share(ranked):
    """The best first, but no one liked series hogging the list: each gets a share, then the rest by points."""
    share = max(3, SHOW // max(1, len({pick["because"][0] for pick in ranked})))
    taken, per_seed = [], {}
    for pick in ranked:
        seed = pick["because"][0]
        if per_seed.get(seed, 0) < share:
            taken.append(pick)
            per_seed[seed] = per_seed.get(seed, 0) + 1
    return taken + [pick for pick in ranked if pick not in taken]


def _reason(because):
    more = " (and %d more)" % (len(because) - 2) if len(because) > 2 else ""
    return "Readers who liked " + " and ".join(because[:2]) + more + " liked this"


def dismiss(hub, profile_id, anilist_id):
    key = "anilist-no:%d" % profile_id
    dismissed = json.loads(db.kv_get(hub.con(), key) or "[]")
    if anilist_id not in dismissed:
        dismissed.append(int(anilist_id))
        db.kv_set(hub.con(), key, json.dumps(dismissed[-500:]))


def cover_allowed(url):
    """AniList's cover pictures, passed through Family Hub like Open Library's."""
    return isinstance(url, str) and bool(re.match(r"^https://s4\.anilist\.co/file/anilistcdn/media/manga/cover/[A-Za-z0-9/_.-]+$", url)) and ".." not in url
