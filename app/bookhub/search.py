"""The library search: a list of terms, each a word or phrase looked for in one or more fields.

A term is {"fields": ["title", "author", "series", "tag"], "value": "cafe", "source": None, "sign": "+"}.
  fields  where to look; "tag" looks in the book's tags (and its series' tags), only those from ``source``
          ("epub", "community", "family" or "mine") when one is given
  value   each word found at the start of a word in those fields, in any order, ignoring case, accents and
          punctuation ("saviors" finds "Savior's"); "*" stands for any letters ("mag*" finds magic, magician
          and Magus). With "phrase": true (typed in quotes) the words must come together, in order. A tag
          must match whole.
  sign    "+" the book must match, "-" it must not, "or" one of the "or" terms will do

How they combine: every "+" term must match, except that "+" terms of the same kind (two authors, two family
tags) mean "either"; any "-" term rules a book out; when there are "or" terms, at least one must match.
"""
import re
import unicodedata

FIELDS = ("title", "author", "series", "tag")
SOURCES = ("epub", "community", "family", "mine")


def norm(text):
    """Lower case, no accents, apostrophes dropped, other punctuation a space."""
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    text = re.sub(r"['’`]", "", text)
    return re.sub(r"[^a-z0-9*]+", " ", text).strip()


def _pattern(value, whole):
    words = norm(value)
    if not words:
        return None
    body = r"\s*".join(re.escape(part).replace(r"\*", r"[a-z0-9]*") for part in words.split(" "))
    return re.compile((r"^%s$" if whole else r"(?:^|\s)%s") % body)


def clean(terms):
    """The terms as sent by the page, checked: unknown fields, sources and signs are dropped."""
    out = []
    for t in terms if isinstance(terms, list) else []:
        if not isinstance(t, dict):
            continue
        fields = [f for f in (t.get("fields") or []) if f in FIELDS]
        value = str(t.get("value") or "").strip()[:200]
        if not fields or not norm(value):
            continue
        source = t.get("source") if t.get("source") in SOURCES else None
        sign = t.get("sign") if t.get("sign") in ("+", "-", "or") else "+"
        out.append({"fields": fields, "value": value, "source": source, "sign": sign, "phrase": bool(t.get("phrase"))})
    return out[:40]


def matcher(terms):
    """A function item, series_tags -> bool for these terms (already cleaned)."""
    compiled = []
    for t in terms:
        words = [t["value"]] if t["phrase"] else norm(t["value"]).split(" ")
        loose, whole = [_pattern(w, False) for w in words], _pattern(t["value"], True)
        kind = (tuple(t["fields"]), t["source"])
        compiled.append((t, loose, whole, kind))

    def text(item, field):
        if field == "title":
            return " ".join([item["title"], item.get("subtitle") or "", item.get("part") or ""])
        if field == "author":
            return item["author"]
        return " ".join([item.get("series") or "", item.get("collection") or ""])

    def hits(t, loose, whole, item, series_tags):
        words = [f for f in t["fields"] if f != "tag"]
        if words:
            hay = norm(" | ".join(text(item, f) for f in words))
            if all(p.search(hay) for p in loose):
                return True
        for field in t["fields"]:
            if field == "tag":
                groups = [t["source"]] if t["source"] else list(SOURCES)
                for g in groups:
                    for tag in item["tags"].get(g, []) + series_tags.get(g, []):
                        if whole.search(norm(tag)):
                            return True
        return False

    def keep(item, series_tags=None):
        series_tags = series_tags or {}
        groups, either = {}, []
        for t, loose, whole, kind in compiled:
            hit = hits(t, loose, whole, item, series_tags)
            if t["sign"] == "-" and hit:
                return False
            if t["sign"] == "+":
                groups[kind] = groups.get(kind, False) or hit
            elif t["sign"] == "or":
                either.append(hit)
        return all(groups.values()) and (not either or any(either))

    return keep
