"""Manga, manhwa and other series that are read chapter by chapter.

These do not live in the Calibre library: a long series would be hundreds of
"books" there. A Suwayomi server next to Family Hub follows each series on the
reading sites it has sources for, downloads every chapter as a CBZ file and
remembers which chapters have been read. Family Hub asks it over its GraphQL
API and shows the answers; pictures are passed through Family Hub so the
browser never needs the series server's address or password.

Written against Suwayomi-Server v2.4.
"""
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import requests

from . import db

TIMEOUT = 40
LOCAL = "0"            # Suwayomi's built-in source for files put in its "local" folder
PROFILES_KEY = "series:profiles-off"
# Suwayomi's defaults skip a series while it has unread chapters. Someone keeping every chapter wants none of that.
KEEP_EVERYTHING = {
    "downloadAsCbz": True, "autoDownloadNewChapters": True, "autoDownloadNewChaptersLimit": 0,
    "excludeEntryWithUnreadChapters": False, "excludeUnreadChapters": False, "excludeNotStarted": False,
}
STATUS = {"ONGOING": "Ongoing", "COMPLETED": "Complete", "PUBLISHING_FINISHED": "Complete", "LICENSED": "Licensed",
          "CANCELLED": "Cancelled", "ON_HIATUS": "On a break"}
SERIES_FIELDS = """id title author status genre unreadCount downloadCount sourceId
  source{displayName} chapters{totalCount} firstUnreadChapter{id name sourceOrder}"""
CHAPTER_FIELDS = "id name chapterNumber sourceOrder isRead isDownloaded uploadDate scanlator lastPageRead"


class SeriesError(Exception):
    pass


def blocked_ids(hub):
    """Profiles an admin has shut out of the series shelf. Everyone else who is signed in sees it; guests never do."""
    try:
        return {int(i) for i in json.loads(db.kv_get(hub.con(), PROFILES_KEY) or "[]")}
    except (ValueError, TypeError):
        return set()


def allow(hub, profile_id, on):
    ids = blocked_ids(hub)
    ids.discard(profile_id) if on else ids.add(profile_id)
    con = hub.con()
    db.kv_set(con, PROFILES_KEY, json.dumps(sorted(ids)))
    con.commit()


def forget_profile(hub, profile_id):
    allow(hub, profile_id, True)
    con = hub.con()
    con.execute("DELETE FROM kv WHERE key=?", ("series:mine:%d" % profile_id,))
    con.commit()


def my_books(hub, profile_id):
    """Series followed chapter by chapter that a person put in My books, for their reading app (opds.py)."""
    try:
        return [int(i) for i in json.loads(db.kv_get(hub.con(), "series:mine:%d" % profile_id) or "[]")]
    except (ValueError, TypeError):
        return []


def set_my_book(hub, profile_id, series_id, on):
    ids = [i for i in my_books(hub, profile_id) if i != series_id]
    if on:
        ids.insert(0, series_id)
    db.kv_set(hub.con(), "series:mine:%d" % profile_id, json.dumps(ids))


def _series(m):
    total = (m.get("chapters") or {}).get("totalCount") or 0
    unread = m.get("unreadCount") or 0
    nxt = m.get("firstUnreadChapter")
    return {
        "id": m["id"], "title": m.get("title") or "", "author": m.get("author") or "",
        "status": STATUS.get(m.get("status") or "", ""), "genres": (m.get("genre") or [])[:6],
        "source": (m.get("source") or {}).get("displayName") or "", "local": str(m.get("sourceId")) == LOCAL,
        "chapters": total, "read": total - unread, "unread": unread, "downloaded": m.get("downloadCount") or 0,
        "next": {"id": nxt["id"], "name": nxt["name"], "order": nxt["sourceOrder"]} if nxt else None,
    }


def _chapter(c):
    return {"id": c["id"], "name": c.get("name") or "", "number": c.get("chapterNumber"), "order": c["sourceOrder"],
            "read": bool(c.get("isRead")), "downloaded": bool(c.get("isDownloaded")), "group": c.get("scanlator") or "",
            "page": c.get("lastPageRead") or 0, "uploaded": int(c.get("uploadDate") or 0)}


class Series:
    def __init__(self, hub):
        self.hub = hub
        self._prepared = None  # the address whose settings have been checked since Family Hub started

    @property
    def configured(self):
        return bool(self.hub.config.series_url)

    # ------------------------------------------------------------- talking

    def _auth(self, config):
        return (config.series_user, config.series_pass) if config.series_user else None

    def gql(self, query, variables=None, config=None, timeout=TIMEOUT):
        config = config or self.hub.config
        if not config.series_url:
            raise SeriesError("The series server is not set up yet. An admin can add it under Admin, Hub, Connections.")
        try:
            r = requests.post(config.series_url + "/api/graphql", json={"query": query, "variables": variables or {}},
                              auth=self._auth(config), timeout=timeout)
            if r.status_code == 401:
                raise SeriesError("The series server refused the user name or password.")
            r.raise_for_status()
            payload = r.json()
        except (requests.RequestException, ValueError) as exc:
            raise SeriesError("Could not reach the series server: %s" % exc)
        if payload.get("errors"):
            raise SeriesError("The series server said: %s" % (payload["errors"][0].get("message") or "unknown error")[:300])
        return payload.get("data") or {}

    def raw(self, path, stream=False):
        """A picture or chapter file from the series server, as a requests response."""
        config = self.hub.config
        if not config.series_url:
            raise SeriesError("The series server is not set up yet.")
        try:
            r = requests.get(config.series_url + path, auth=self._auth(config), timeout=TIMEOUT, stream=stream)
        except requests.RequestException as exc:
            raise SeriesError("Could not reach the series server: %s" % exc)
        if r.status_code != 200:
            raise SeriesError("The series server does not have that (%s)." % r.status_code)
        return r

    # ------------------------------------------------------------ settings

    def status(self, config=None):
        config = config or self.hub.config
        if not config.series_url:
            return {"configured": False, "ok": False}
        try:
            data = self.gql("{aboutServer{version} sources{nodes{id lang}} settings{%s}}" % " ".join(KEEP_EVERYTHING),
                            config=config, timeout=8)
        except SeriesError as exc:
            return {"configured": True, "ok": False, "error": str(exc)}
        sources = [s for s in data["sources"]["nodes"] if s["id"] != LOCAL]
        keeps = all(data["settings"].get(k) == v for k, v in KEEP_EVERYTHING.items())
        return {"configured": True, "ok": True, "version": data["aboutServer"]["version"], "sources": len(sources), "keeps": keeps}

    def prepare(self, config=None):
        """Set the series server to download every new chapter of every followed series, as CBZ files."""
        config = config or self.hub.config
        self.gql("mutation($s: PartialSettingsTypeInput!){setSettings(input:{settings:$s}){settings{downloadAsCbz}}}",
                 {"s": KEEP_EVERYTHING}, config=config)
        self._prepared = config.series_url

    def _ready(self):
        if self._prepared != self.hub.config.series_url:
            self.prepare()

    def languages(self):
        langs = {x.strip().lower() for x in (self.hub.config.series_langs or "en").split(",") if x.strip()}
        return langs | {"all", "localsourcelang"}

    def sources(self):
        nodes = self.gql("{sources{nodes{id displayName lang isNsfw}}}")["sources"]["nodes"]
        langs = self.languages()
        return [{"id": s["id"], "name": s["displayName"], "local": s["id"] == LOCAL} for s in nodes if s["lang"].lower() in langs]

    # --------------------------------------------------------------- shelf

    def shelf(self):
        self._ready()
        data = self.gql("{mangas(condition:{inLibrary:true}){nodes{%s}} downloadStatus{queue{state}}}" % SERIES_FIELDS)
        items = sorted((_series(m) for m in data["mangas"]["nodes"]), key=lambda s: s["title"].lower())
        return {"series": items, "queued": len(data["downloadStatus"]["queue"])}

    def search(self, query):
        """Ask every source in the chosen languages at once. One slow or broken site must not spoil the rest."""
        sources = self.sources()
        q = "mutation($s: LongString!, $q: String!){fetchSourceManga(input:{source:$s,type:SEARCH,query:$q,page:1}){mangas{id title inLibrary}}}"

        def ask(source):
            try:
                found = self.gql(q, {"s": source["id"], "q": query})["fetchSourceManga"]["mangas"]
            except SeriesError as exc:
                return {"source": source["name"], "error": str(exc)[:200], "results": []}
            return {"source": source["name"], "error": "",
                    "results": [{"id": m["id"], "title": m["title"], "following": m["inLibrary"]} for m in found[:12]]}

        with ThreadPoolExecutor(max_workers=6) as pool:
            groups = list(pool.map(ask, sources))
        groups.sort(key=lambda g: (not g["results"], g["source"].lower()))
        return {"groups": groups, "sources": len([s for s in sources if not s["local"]])}

    def detail(self, series_id, refresh=False):
        if refresh:
            self.gql("mutation($id: Int!){fetchMangaAndChapters(input:{id:$id,fetchManga:true,fetchChapters:true}){manga{id}}}",
                     {"id": series_id}, timeout=120)
        data = self.gql("query($id: Int!){manga(id:$id){%s description inLibrary realUrl} chapters(condition:{mangaId:$id}){nodes{%s}}}"
                        % (SERIES_FIELDS, CHAPTER_FIELDS), {"id": series_id})
        if not data.get("manga"):
            raise SeriesError("That series is not on the series server.")
        chapters = sorted((_chapter(c) for c in data["chapters"]["nodes"]), key=lambda c: c["order"])
        if not chapters and not refresh:
            return self.detail(series_id, refresh=True)  # found by a search but never opened
        info = _series(data["manga"])
        info.update(description=(data["manga"].get("description") or "")[:3000], following=bool(data["manga"]["inLibrary"]),
                    link=data["manga"].get("realUrl") or "", chapters_list=chapters)
        return info

    def follow(self, series_id):
        """Follow a series and start downloading every chapter of it."""
        self._ready()
        self.gql("mutation($id: Int!){updateManga(input:{id:$id,patch:{inLibrary:true}}){manga{id}}}", {"id": series_id})
        return self.download(series_id, refresh=True)

    def unfollow(self, series_id):
        """Stop following. Chapters already downloaded stay on disk."""
        self.gql("mutation($id: Int!){updateManga(input:{id:$id,patch:{inLibrary:false}}){manga{id}}}", {"id": series_id})

    def download(self, series_id, refresh=False):
        chapters = self.detail(series_id, refresh=refresh)["chapters_list"]
        ids = [c["id"] for c in chapters if not c["downloaded"]]
        if ids:
            self.gql("mutation($ids: [Int!]!){enqueueChapterDownloads(input:{ids:$ids}){downloadStatus{state}}}", {"ids": ids})
            self.gql("mutation{startDownloader(input:{}){downloadStatus{state}}}")
        return len(ids)

    def mark(self, chapter_ids, read):
        self.gql("mutation($ids: [Int!]!, $r: Boolean!){updateChapters(input:{ids:$ids,patch:{isRead:$r}}){chapters{id}}}",
                 {"ids": [int(i) for i in chapter_ids], "r": bool(read)})

    def pages(self, chapter_id):
        data = self.gql("mutation($id: Int!){fetchChapterPages(input:{chapterId:$id}){pages}}", {"id": chapter_id}, timeout=120)
        return len(data["fetchChapterPages"]["pages"])

    def check_now(self):
        """Ask the series server to look for new chapters now, without waiting for the answer."""
        threading.Thread(target=lambda: self._quiet("mutation{updateLibrary(input:{}){updateStatus{jobsInfo{isRunning}}}}"),
                         daemon=True).start()

    def _quiet(self, query):
        try:
            self.gql(query, timeout=120)
        except SeriesError:
            pass

    def test(self, config):
        status = self.status(config)
        if not status["ok"]:
            return {"ok": False, "message": status.get("error") or "Enter the address of the series server."}
        try:
            self.prepare(config)
        except SeriesError as exc:
            return {"ok": False, "message": "Connected, but its settings could not be changed: %s" % exc}
        if not status["sources"]:
            note = " It has no sources yet, so there is nothing to search. Add some in the series server itself (see the guide)."
        else:
            note = " It has %d sources." % status["sources"]
        return {"ok": True, "message": "Connected to Suwayomi %s.%s It is now set to keep every new chapter as a CBZ file." % (status["version"], note)}
