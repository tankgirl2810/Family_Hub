"""The library search: a list of terms, each a word or phrase looked for in one or more fields.

A term is {"fields": ["title", "author", "series", "tag"], "value": "cafe", "source": None, "sign": "+"}.
  fields  where to look; "tag" looks in the book's tags (and its series' tags), only those from ``source``
          ("epub", "community", "family" or "mine") when one is given
  value   each word found at the start of a word in those fields, in any order, ignoring case, accents and
          punctuation ("saviors" finds "Savior's"); "*" stands for any letters ("mag*" finds magic, magician
          and Magus). With "phrase": true (typed in quotes) the words must come together, in order. A tag
          must match whole, and a tag with others nested under it ("Pathfinder") finds those too.
  sign    "+" the book must match, "-" it must not, "or" one of the "or" terms will do

How they combine: every "+" term must match, except that "+" words of the same kind (two authors) mean
"either"; two "+" tags must both be there; any "-" term rules a book out; when there are "or" terms, at least
one must match.
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
    cleaned = []
    for term in terms if isinstance(terms, list) else []:
        if not isinstance(term, dict):
            continue
        fields = [field for field in (term.get("fields") or []) if field in FIELDS]
        value = str(term.get("value") or "").strip()[:200]
        if not fields or not norm(value):
            continue
        source = term.get("source") if term.get("source") in SOURCES else None
        sign = term.get("sign") if term.get("sign") in ("+", "-", "or") else "+"
        cleaned.append({"fields": fields, "value": value, "source": source, "sign": sign, "phrase": bool(term.get("phrase"))})
    return cleaned[:40]


def matcher(terms, nested=None):
    """A function item, series_tags -> bool for these terms (already cleaned). nested is {tag in lower case: tags
    under it} (tabletop.descendants): a tag term finds books with any tag nested under it as well."""
    compiled = []
    nested = nested or {}
    for term in terms:
        words = [term["value"]] if term["phrase"] else norm(term["value"]).split(" ")
        loose = [_pattern(word, False) for word in words]
        whole = [_pattern(tag, True) for tag in {term["value"]} | set(nested.get(term["value"].lower(), ()))]
        # the "kind" is the bucket a + term lands in. picked tags each get their own bucket (all must match),
        # typed words share one per field (so two authors = either one). subtle, but it matters
        kind = (tuple(term["fields"]), term["source"], term["value"].lower() if term["fields"] == ["tag"] else "")
        compiled.append((term, loose, whole, kind))

    def text(item, field):
        if field == "title":
            return " ".join([item["title"], item.get("subtitle") or "", item.get("part") or ""])
        if field == "author":
            return item["author"]
        return " ".join([item.get("series") or "", item.get("collection") or ""])

    def tags_of(item, series_tags, source):
        for group in [source] if source else SOURCES:
            yield from item["tags"].get(group, []) + series_tags.get(group, [])

    def hits(term, loose, whole, item, series_tags):
        word_fields = [field for field in term["fields"] if field != "tag"]
        if word_fields:
            haystack = norm(" | ".join(text(item, field) for field in word_fields))
            if all(pattern.search(haystack) for pattern in loose):
                return True
        if "tag" not in term["fields"]:
            return False
        return any(pattern and pattern.search(norm(tag)) for tag in tags_of(item, series_tags, term["source"]) for pattern in whole)

    def keep(item, series_tags=None):
        series_tags = series_tags or {}
        must, either = {}, []
        for term, loose, whole, kind in compiled:
            hit = hits(term, loose, whole, item, series_tags)
            if term["sign"] == "-" and hit:
                return False
            if term["sign"] == "+":
                must[kind] = must.get(kind, False) or hit
            elif term["sign"] == "or":
                either.append(hit)
        return all(must.values()) and (not either or any(either))

    return keep
