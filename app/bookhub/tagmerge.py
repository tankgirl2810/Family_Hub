"""Merging two spellings of a tag into one ("Yaoi" into "Boy love").

A merge covers every sort of tag at once, each in its own way:

- Family tags are rewritten: every book's "Yaoi" becomes "Boy love" (a book
  that had both keeps one). The merge is also kept as a rule, so a "Yaoi"
  typed later, added by the sorter or brought in by an import is stored as
  "Boy love" too. Nesting moves across with it.
- Book file and community tags get the admin's ordinary rename rule
  (tag_rules), which changes how they show, not where they come from.
- Personal tags are never touched: they are private.

Undo puts the old name back on the family tags that had it when the merge was
made (each merge keeps a list of them), and stops the renaming. Tags given
the old name since then keep the new one, and nesting is not moved back.

The admin page also suggests merges it can spot by itself: spellings that
differ only in capitals, a plural, punctuation, spaces or "&" for "and".
Synonyms (Yaoi and BL) are for a person to merge.
"""
import json
import re
import unicodedata

from . import db

DISMISSED = "tags:merge-dismissed"  # suggestions an admin said no to, as signatures (see signature())


def rules(con):
    """{merged-away name in lower case: name kept}, for family tags."""
    return {row["tag"].lower(): row["becomes"] for row in con.execute("SELECT tag, becomes FROM tag_merges")}


def resolve(tag, merged):
    """The name a family tag is stored under once merges apply. Follows a chain, and can't go round in circles."""
    seen = set()
    while tag.lower() in merged and tag.lower() not in seen:
        seen.add(tag.lower())
        tag = merged[tag.lower()]
    return tag


# ------------------------------------------------------------------ spotting near-duplicates

def _singular(word):
    """A rough singular, the same for both spellings: boys -> boy, zombies -> zomby, zombie -> zomby, witches -> witch."""
    if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        word = word[:-1]
    if len(word) > 3 and word.endswith("ie"):
        word = word[:-2] + "y"
    elif len(word) > 3 and word.endswith("e") and word[-2] in "chsxz":
        word = word[:-1]  # so "horse" and "horses" both end up "hors". ugly, but the same, which is all that matters
    return word


def merge_key(tag):
    """What's left of a tag once capitals, accents, plurals, punctuation and spaces are taken away.

    Two tags with the same key are likely the same tag: "Boys' love" and "Boy love", "Sci-fi" and "Sci fi",
    "Sword & sorcery" and "Sword and Sorcery"."""
    text = unicodedata.normalize("NFKD", tag or "").casefold()
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = re.sub(r"['’]", "", text).replace("&", " and ")
    return "".join(_singular(word) for word in re.findall(r"[^\W_]+", text))


def signature(names):
    """One suggestion, written down so a dismissed one stays dismissed."""
    return "|".join(sorted({name.lower() for name in names}))


def usage(family_hub):
    """{tag as shown: {"tag", "family", "file", "community"}}, each a set of book keys. Book file and community tags
    are counted as shown, after the admin's rules, so a tag already renamed isn't offered again."""
    used = {}
    for key, groups in family_hub.tag_index().items():
        for group, field in (("family", "family"), ("epub", "file"), ("community", "community")):
            for tag in groups[group]:
                entry = used.setdefault(tag, {"tag": tag, "family": set(), "file": set(), "community": set()})
                entry[field].add(key)
    return used


def _books(entry):
    return entry["family"] | entry["file"] | entry["community"]


def _summary(entry):
    return {"tag": entry["tag"], "books": len(_books(entry)), "family": len(entry["family"]),
            "file": len(entry["file"]), "community": len(entry["community"])}


def all_tags(used):
    """Every family, book file and community tag with its counts, for picking tags to merge."""
    rows = [_summary(entry) for entry in used.values()]
    rows.sort(key=lambda row: (-row["books"], row["tag"].lower()))
    return rows


def suggestions(con, used):
    """Groups of tags that look like one tag spelled differently, the most used first. Each says which name
    would be kept: the one on the most books."""
    dismissed = set(json.loads(db.kv_get(con, DISMISSED) or "[]"))
    groups = {}
    for entry in used.values():
        key = merge_key(entry["tag"])
        if key:
            groups.setdefault(key, []).append(entry)
    found = []
    for entries in groups.values():
        if len(entries) < 2 or signature(entry["tag"] for entry in entries) in dismissed:
            continue
        names = sorted((_summary(entry) for entry in entries), key=lambda row: (-row["books"], row["tag"]))
        found.append({"names": names, "keep": names[0]["tag"], "signature": signature(row["tag"] for row in names),
                      "books": len(set().union(*(_books(entry) for entry in entries)))})
    found.sort(key=lambda item: (-item["books"], item["keep"].lower()))
    return found


def dismiss(con, names):
    """Don't suggest these tags as one again."""
    dismissed = set(json.loads(db.kv_get(con, DISMISSED) or "[]"))
    dismissed.add(signature(names))
    db.kv_set(con, DISMISSED, json.dumps(sorted(dismissed)))


# ------------------------------------------------------------------ merging

def _names(names, keep):
    """The tags being merged away: tidied, without blanks or the kept name itself (a different case of it stays)."""
    picked = []
    for name in names or []:
        name = db.clean_tag(name)
        if name and name != keep and name.lower() not in [other.lower() for other in picked]:
            picked.append(name)
    return picked


def preview(used, names, keep):
    """What merging would do, before anything changes: how many books carry one of the tags, and how many of
    those already have the kept name."""
    keep = db.clean_tag(keep)
    names = _names(names, keep)
    changing = set()
    counts = {"family": set(), "file": set(), "community": set()}
    for name in names:
        entry = used.get(name) or {"family": set(), "file": set(), "community": set()}
        changing |= _books(entry)
        for field in counts:
            counts[field] |= entry[field]
    kept = _books(used[keep]) if keep in used else set()
    return {"keep": keep, "names": names, "books": len(changing), "already": len(changing & kept),
            "family": len(counts["family"]), "file": len(counts["file"]), "community": len(counts["community"]),
            "new_name": keep not in used}


def _rewrite_family(con, name, keep):
    """Every book's family tag `name` becomes `keep`. Returns what's needed to undo it."""
    history = []
    case_only = name.lower() == keep.lower()
    rows = con.execute("SELECT book_key, tag, added_by, created FROM family_tags WHERE tag=?", (name,)).fetchall()
    for row in rows:
        if case_only:
            # the table ignores case, so a book can't have both spellings. just fix the capitals
            if row["tag"] != keep:
                con.execute("UPDATE family_tags SET tag=? WHERE book_key=? AND tag=?", (keep, row["book_key"], name))
                history.append({"key": row["book_key"], "tag": row["tag"], "by": row["added_by"], "created": row["created"], "both": False})
            continue
        both = con.execute("SELECT 1 FROM family_tags WHERE book_key=? AND tag=?", (row["book_key"], keep)).fetchone() is not None
        if both:  # it has the kept name already; that row stays as it was
            con.execute("DELETE FROM family_tags WHERE book_key=? AND tag=?", (row["book_key"], name))
        else:  # same row, new name: who added it and when stay the same
            con.execute("UPDATE family_tags SET tag=? WHERE book_key=? AND tag=?", (keep, row["book_key"], name))
        history.append({"key": row["book_key"], "tag": row["tag"], "by": row["added_by"], "created": row["created"], "both": both})
    return history


def _move_nesting(con, name, keep):
    """The old name's place in the nesting goes to the kept name: its parent (unless the kept name has one) and its children."""
    parent = con.execute("SELECT parent FROM tag_parents WHERE tag=?", (name,)).fetchone()
    con.execute("DELETE FROM tag_parents WHERE tag=?", (name,))
    if parent and parent["parent"].lower() != keep.lower():
        con.execute("INSERT OR IGNORE INTO tag_parents(tag, parent) VALUES(?,?)", (keep, parent["parent"]))
    con.execute("UPDATE tag_parents SET parent=? WHERE parent=?", (keep, name))
    con.execute("DELETE FROM tag_parents WHERE tag=parent")  # "Boy love" was under "Yaoi"? not any more, it IS it now


def merge(con, names, keep):
    """Merge `names` into `keep` across family, book file and community tags. Returns how many family tags changed."""
    keep = db.clean_tag(keep)
    names = _names(names, keep)
    if not keep or not names:
        return None
    changed = 0
    # the kept name is a name again: if it had been merged or renamed into something else, that stops
    con.execute("DELETE FROM tag_merges WHERE tag=? AND becomes!=? COLLATE NOCASE", (keep, keep))
    con.execute("DELETE FROM tag_rules WHERE tag=? AND becomes!=? COLLATE NOCASE", (keep, keep))
    for name in names:
        history = _rewrite_family(con, name, keep)
        changed += len(history)
        _move_nesting(con, name, keep)
        # anything that used to become the old name becomes the kept one, so there are no chains to follow
        con.execute("UPDATE tag_merges SET becomes=? WHERE becomes=? COLLATE NOCASE", (keep, name))
        con.execute("UPDATE tag_rules SET becomes=? WHERE becomes=? COLLATE NOCASE", (keep, name))
        # the same rename for book file and community tags (straight in: set_tag_rule drops a change of capitals)
        con.execute("INSERT INTO tag_rules(tag, becomes) VALUES(?,?) ON CONFLICT(tag) DO UPDATE SET becomes=excluded.becomes",
                    (name, keep))
        before = con.execute("SELECT history FROM tag_merges WHERE tag=?", (name,)).fetchone()
        kept = json.loads(before["history"]) if before else []
        con.execute("INSERT INTO tag_merges(tag, becomes, created, history) VALUES(?,?,?,?) ON CONFLICT(tag) DO UPDATE SET "
                    "becomes=excluded.becomes, created=excluded.created, history=excluded.history",
                    (name, keep, db.now(), json.dumps(kept + history)))
    con.commit()
    return changed


def merges(con):
    """The merges made so far, newest first."""
    rows = []
    for row in con.execute("SELECT * FROM tag_merges ORDER BY created DESC, tag"):
        history = json.loads(row["history"] or "[]")
        rows.append({"tag": row["tag"], "becomes": row["becomes"], "created": row["created"],
                     "restorable": len(history)})
    return rows


def undo(con, tag):
    """Stop merging `tag`, and put it back on the family tags that had it when it was merged.
    Returns how many were put back, or None when there's no such merge."""
    row = con.execute("SELECT * FROM tag_merges WHERE tag=?", (tag,)).fetchone()
    if not row:
        return None
    name, keep = row["tag"], row["becomes"]
    restored = 0
    for old in json.loads(row["history"] or "[]"):
        if old["tag"].lower() == keep.lower():
            restored += con.execute("UPDATE family_tags SET tag=? WHERE book_key=? AND tag=?", (old["tag"], old["key"], keep)).rowcount
            continue
        if not old.get("both"):  # the kept name came from this merge, so it goes again
            con.execute("DELETE FROM family_tags WHERE book_key=? AND tag=?", (old["key"], keep))
        restored += con.execute("INSERT OR IGNORE INTO family_tags(book_key, tag, added_by, created) VALUES(?,?,?,?)",
                                (old["key"], old["tag"], old.get("by"), old.get("created") or db.now())).rowcount
    con.execute("DELETE FROM tag_rules WHERE tag=? AND becomes=? COLLATE NOCASE", (name, keep))
    con.execute("DELETE FROM tag_merges WHERE tag=?", (name,))
    con.commit()
    return restored


# ------------------------------------------------------------------ backups

def export(con):
    return {"tag_merges": [{"tag": row["tag"], "becomes": row["becomes"], "created": row["created"],
                            "history": json.loads(row["history"] or "[]")}
                           for row in con.execute("SELECT * FROM tag_merges ORDER BY tag")],
            "tag_merge_dismissed": json.loads(db.kv_get(con, DISMISSED) or "[]")}


def import_merges(con, data):
    """Bring merges back from a book-data file. One we haven't got is applied to the family tags already here."""
    for entry in data.get("tag_merges") or []:
        if not isinstance(entry, dict):
            continue
        name, keep = db.clean_tag(entry.get("tag")), db.clean_tag(entry.get("becomes"))
        if not name or not keep or con.execute("SELECT 1 FROM tag_merges WHERE tag=?", (name,)).fetchone():
            continue
        # profile numbers from another install would point at the wrong people, so those are left out
        history = [dict(old, by=None) for old in entry.get("history") or [] if isinstance(old, dict) and old.get("key") and old.get("tag")]
        history += _rewrite_family(con, name, keep) if name != keep else []
        con.execute("INSERT INTO tag_merges(tag, becomes, created, history) VALUES(?,?,?,?)",
                    (name, keep, entry.get("created") or db.now(), json.dumps(history)))
    dismissed = [item for item in data.get("tag_merge_dismissed") or [] if isinstance(item, str)]
    if dismissed:
        dismissed = sorted(set(json.loads(db.kv_get(con, DISMISSED) or "[]")) | set(dismissed))
        con.execute("INSERT INTO kv(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (DISMISSED, json.dumps(dismissed)))
