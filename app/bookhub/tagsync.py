"""Writing the tags Family Hub shows back into the library, so Calibre-Web, KOReader and every reading app see them too.

What a book's library entry should carry, worked out fresh each time:

  * its own tags, tidied (tags.tidy: no ISBNs, web addresses or "Accessible book") and with an admin's rules
    applied (renamed, or hidden);
  * the best few community tags from the catalogues: the ones most catalogues agree on, at most MAX_COMMUNITY;
  * the family tags people put on it.

Personal tags never leave Family Hub. Tags this module wrote and that are no longer wanted (a family tag taken
off, a community tag an admin hid) are taken off again; tags somebody typed in Calibre-Web are only tidied, never
dropped for being unknown to Family Hub.

Changes go through the library manager (librarymanager.py), a few books a minute, after a copy of the library's
database. Every change is listed with an Undo, which also stops the book being changed again by itself.
"""
import json
from collections import Counter

from . import db, librarymanager, tags

MAX_COMMUNITY = 6
BATCH = 10   # books a minute; Calibre-Web takes one request per book


def _lower(items):
    return {item.lower() for item in items}


class TagSync:
    def __init__(self, hub):
        self.hub = hub

    @property
    def auto(self):
        return (db.kv_get(self.hub.con(), "tagsync:auto") or "1") == "1"

    def set_auto(self, on):
        db.kv_set(self.hub.con(), "tagsync:auto", "1" if on else "0")
        if on:
            db.kv_set(self.hub.con(), "tagsync:stopped", "")

    def _state(self):
        """{book id: {"written": [...], "held": bool}}"""
        return {row["book_id"]: {"written": json.loads(row["written"]), "held": bool(row["held"])}
                for row in self.hub.con().execute("SELECT * FROM tag_sync")}

    def _community(self):
        """{book key: [best community tags]}: tags more catalogues agree on first, then as the catalogues gave them."""
        found = {}
        for row in self.hub.con().execute("SELECT book_key, tag, source FROM community_tags ORDER BY rowid"):
            found.setdefault(row["book_key"], []).append(row["tag"])
        best = {}
        for key, rows in found.items():
            agree = Counter(tag.lower() for tag in rows)
            order, seen = [], set()
            for tag in rows:
                if tag.lower() not in seen:
                    seen.add(tag.lower())
                    order.append(tag)
            order.sort(key=lambda tag: -agree[tag.lower()])  # stable: ties keep the catalogue's order
            best[key] = order
        return best

    def _family(self):
        found = {}
        for row in self.hub.con().execute("SELECT book_key, tag FROM family_tags ORDER BY tag"):
            found.setdefault(row["book_key"], []).append(row["tag"])
        return found

    def plan(self, book, rules, community, family, written):
        """{"now": [...], "to": [...], "add": [...], "remove": [...]} for one library entry."""
        raw = list(book["raw_tags"])
        mine_before = _lower(written)
        wanted_extra = tags.apply_rules(tags.tidy(community, skip=[book["title"], book["author"], book["publisher"]] + book["authors"]),
                                        rules)[:MAX_COMMUNITY] + list(family)
        wanted_lower = _lower(wanted_extra)
        to, seen = [], set()

        def keep(tag):
            tag = db.clean_tag(tag.replace(",", " "))
            if tag and tag.lower() not in seen:
                seen.add(tag.lower())
                to.append(tag)

        for tag in raw:
            if tag.lower() in mine_before and tag.lower() not in wanted_lower:
                continue  # written by Family Hub earlier and no longer wanted here
            # kept as it was typed: tidying would split "Arbuckle; Roscoe" into two names. Only clutter (an ISBN,
            # a web address, "Accessible book") goes, and what an admin renamed or hid
            if not tags.tidy([tag]):
                continue
            for ruled in tags.apply_rules([tag], rules):
                keep(ruled)
        for tag in wanted_extra:
            keep(tag)
        now_lower, to_lower = _lower(raw), _lower(to)
        return {"now": raw, "to": to, "add": [tag for tag in to if tag.lower() not in now_lower],
                "remove": [tag for tag in raw if tag.lower() not in to_lower]}

    def plans(self, include_held=False):
        """[(book, plan)] for every entry whose tags would change."""
        hub = self.hub
        rules, community, family, state = hub.tag_rules(), self._community(), self._family(), self._state()
        found = []
        for book in hub.library.all_books():
            entry = state.get(book["id"], {"written": [], "held": False})
            if entry["held"] and not include_held:
                continue
            plan = self.plan(book, rules, community.get(book["key"], []), family.get(book["key"], []), entry["written"])
            if plan["add"] or plan["remove"]:
                found.append((book, plan))
        return found

    def run(self, limit=BATCH):
        """Write a few entries' tags. Returns how many were written."""
        if not self.auto or not librarymanager.configured(self.hub.config):
            return 0
        waiting = self.plans()[:limit]
        if not waiting:
            return 0
        self.hub.duplicates.backup()  # once for the batch: an OSError here stops the batch before anything is written
        con, done = self.hub.con(), []
        for book, plan in waiting:
            self.hub.manager.set_field(book["id"], "tags", ", ".join(plan["to"]))
            ours = [tag for tag in plan["to"] if tag.lower() not in _lower(plan["now"])]
            before = self._state().get(book["id"], {"written": []})["written"]
            mine = sorted(set(before) | set(ours), key=str.lower)
            con.execute("INSERT INTO tag_sync(book_id, written, held, at) VALUES(?,?,0,?) ON CONFLICT(book_id) DO UPDATE SET "
                        "written=excluded.written, at=excluded.at", (book["id"], json.dumps(mine), db.now()))
            log = con.execute("INSERT INTO tag_sync_log(at, book_id, title, before, after) VALUES(?,?,?,?,?)",
                              (db.now(), book["id"], book["shown_title"], json.dumps(plan["now"]), json.dumps(plan["to"]))).lastrowid
            con.commit()
            done.append((book["id"], plan, log))
        self.hub.library.refresh(force=True)
        ignored = 0
        for book_id, plan, log in done:
            fresh = self.hub.library.get(book_id)
            if fresh and _lower(fresh["raw_tags"]) != _lower(plan["to"]):
                ignored += 1
                # Calibre-Web said nothing useful and changed nothing: stop asking every minute
                con.execute("UPDATE tag_sync SET held=1 WHERE book_id=?", (book_id,))
                con.execute("UPDATE tag_sync_log SET undone=1, note=? WHERE id=?",
                            ("Calibre-Web did not make the change. Does its account have permission to edit books?", log))
        if done and ignored == len(done):
            # not one change took: something is wrong with Calibre-Web, so stop rather than mark the whole library
            self.set_auto(False)
            db.kv_set(con, "tagsync:stopped", "Writing stopped by itself: Calibre-Web did not make any of the last %d changes. "
                      "Check its account may edit books (Admin, Hub, Connections), then switch this on again." % len(done))
            for book_id, _, _ in done:
                con.execute("UPDATE tag_sync SET held=0 WHERE book_id=?", (book_id,))  # not the books' fault
        con.commit()
        return len(done) - ignored

    def undo(self, change_id):
        """Put an entry's tags back as they were, and leave its tags alone from now on."""
        con = self.hub.con()
        row = con.execute("SELECT * FROM tag_sync_log WHERE id=?", (change_id,)).fetchone()
        if not row:
            raise LookupError("There is no such change.")
        if not row["undone"]:
            if not librarymanager.configured(self.hub.config):
                raise librarymanager.ManagerError("Calibre-Web's sign-in is not set up yet.")
            self.hub.duplicates.backup()
            self.hub.manager.set_field(row["book_id"], "tags", ", ".join(json.loads(row["before"])))
            con.execute("UPDATE tag_sync_log SET undone=1 WHERE id=?", (change_id,))
            con.execute("INSERT INTO tag_sync(book_id, written, held, at) VALUES(?,'[]',1,?) ON CONFLICT(book_id) DO UPDATE SET held=1, at=excluded.at",
                        (row["book_id"], db.now()))
            con.commit()
            self.hub.library.refresh(force=True)
        return {"title": row["title"]}

    def release(self, book_id):
        """Let Family Hub look after an entry's tags again after an Undo."""
        con = self.hub.con()
        con.execute("UPDATE tag_sync SET held=0 WHERE book_id=?", (book_id,))
        con.commit()

    def summary(self, preview=5):
        rows = self.hub.con().execute("SELECT * FROM tag_sync_log ORDER BY id DESC LIMIT 50").fetchall()
        waiting = self.plans()
        stopped = db.kv_get(self.hub.con(), "tagsync:stopped") or None
        return {"auto": self.auto, "blocked": stopped or (None if librarymanager.configured(self.hub.config) else
                "Calibre-Web's sign-in is not set up yet. Add it under Admin, Hub, Connections, Calibre-Web."),
                "waiting": len(waiting),
                "next": [{"book_id": book["id"], "title": book["shown_title"], "add": plan["add"], "remove": plan["remove"]}
                         for book, plan in waiting[:preview]],
                "changes": [{"id": row["id"], "at": row["at"], "book_id": row["book_id"], "title": row["title"],
                             "added": [tag for tag in json.loads(row["after"]) if tag.lower() not in _lower(json.loads(row["before"]))],
                             "removed": [tag for tag in json.loads(row["before"]) if tag.lower() not in _lower(json.loads(row["after"]))],
                             "undone": bool(row["undone"])} for row in rows]}
