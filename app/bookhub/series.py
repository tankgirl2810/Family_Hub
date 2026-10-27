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
import re
import threading
import time
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
STATUS = {"ONGOING": "Ongoing", "COMPLETED": "Finished", "PUBLISHING_FINISHED": "Finished", "LICENSED": "Licensed",
          "CANCELLED": "Cancelled", "ON_HIATUS": "Hiatus"}
DONE = ("Finished", "Cancelled")  # publication is over: no new chapters to look for
DONE_CATEGORY = "Finished or cancelled (not checked for new chapters)"
CHAPTER_TAG = "Chapter by chapter"  # the tag every series followed chapter by chapter carries in the library
SERIES_FIELDS = """id title author status genre unreadCount downloadCount sourceId
  source{displayName} chapters{totalCount} firstUnreadChapter{id name sourceOrder}"""
CHAPTER_FIELDS = "id name chapterNumber sourceOrder isRead isDownloaded uploadDate scanlator lastPageRead"
# A copy of a chapter with one of these in its name is the one kept when a site lists a chapter twice.
EXTRAS = re.compile(r"18\s*\+|\br-?18\b|\buncensored\b|\buncut\b|\bunedited\b|\bmature\b|\bexplicit\b|\bnsfw\b|\bextended\b", re.I)


class SeriesError(Exception):
    pass


def blocked_ids(hub):
    """Profiles an admin has shut out of the series shelf. Everyone else who is signed in sees it; guests never do."""
    try:
        return {int(profile_id) for profile_id in json.loads(db.kv_get(hub.con(), PROFILES_KEY) or "[]")}
    except (ValueError, TypeError):
        return set()


def allow(hub, profile_id, on):
    ids = blocked_ids(hub)
    if on:
        ids.discard(profile_id)
    else:
        ids.add(profile_id)
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
        return [int(series_id) for series_id in json.loads(db.kv_get(hub.con(), "series:mine:%d" % profile_id) or "[]")]
    except (ValueError, TypeError):
        return []


def set_my_book(hub, profile_id, series_id, on):
    ids = [kept for kept in my_books(hub, profile_id) if kept != series_id]
    if on:
        ids.insert(0, series_id)  # newest first
    db.kv_set(hub.con(), "series:mine:%d" % profile_id, json.dumps(ids))


def _genres(given):
    """Every tag the reading site gives the series, each once, tidied; they become its tags in the library."""
    genres, seen = [], set()
    for genre in given or []:
        genre = " ".join(str(genre).split())
        if genre and genre.lower() not in seen and len(genre) <= 60:
            seen.add(genre.lower())
            genres.append(genre)
    return genres[:40]


def _series(manga):
    total = (manga.get("chapters") or {}).get("totalCount") or 0
    unread = manga.get("unreadCount") or 0
    next_up = manga.get("firstUnreadChapter")
    return {
        "id": manga["id"], "title": manga.get("title") or "", "author": manga.get("author") or "",
        "status": STATUS.get(manga.get("status") or "", ""), "genres": _genres(manga.get("genre")),
        "source": (manga.get("source") or {}).get("displayName") or "", "local": str(manga.get("sourceId")) == LOCAL,
        "chapters": total, "read": total - unread, "unread": unread, "downloaded": manga.get("downloadCount") or 0,
        "next": {"id": next_up["id"], "name": next_up["name"], "order": next_up["sourceOrder"]} if next_up else None,
    }


def _chapter(chapter):
    return {"id": chapter["id"], "name": chapter.get("name") or "", "number": chapter.get("chapterNumber"),
            "order": chapter["sourceOrder"], "read": bool(chapter.get("isRead")), "downloaded": bool(chapter.get("isDownloaded")),
            "group": chapter.get("scanlator") or "", "page": chapter.get("lastPageRead") or 0,
            "uploaded": int(chapter.get("uploadDate") or 0)}


def _has_title(chapter):
    """Does the chapter's name say more than its number ("Ch. 3 - Light (3)" rather than "Ch. 3")?"""
    return bool(re.sub(r"^\s*(?:ch(?:apter)?|ep(?:isode)?)?\.?\s*[\d.]+\s*", "", chapter["name"] or "", flags=re.I).strip(" -:–·"))


def unique(chapters, among_downloaded=False):
    """(kept, dropped): one copy of each chapter number, in reading order. Sites sometimes list a chapter twice
    (two translations, or "Ch. 21" and "Ch. 21 (18+)"). The copy kept is the one marked 18+, uncensored and so
    on, then the one whose name says more, then the newest. among_downloaded: choose among the copies already
    downloaded when there are any (for the comic files, which can only hold what is downloaded). Chapters with
    no number are all kept."""
    best = {}
    for chapter in chapters:
        number = chapter.get("number")
        if number is None or number < 0:
            continue
        # tuples compare left to right, so this is a whole ranking in one line: downloaded (if asked) beats
        # 18+/uncensored beats has-a-title beats newer upload beats later in the list
        rank = (among_downloaded and chapter["downloaded"], bool(EXTRAS.search("%s %s" % (chapter["name"], chapter.get("group") or ""))),
                _has_title(chapter), chapter.get("uploaded") or 0, chapter["order"])
        if number not in best or rank > best[number][0]:
            best[number] = (rank, chapter["id"])
    keep_ids = {chapter_id for _, chapter_id in best.values()}
    kept = [chapter for chapter in chapters if chapter.get("number") is None or chapter["number"] < 0 or chapter["id"] in keep_ids]
    # compare by object identity: checking dict == dict for 500 chapters, 500 times, crawls
    kept_ids = {id(chapter) for chapter in kept}
    return kept, [chapter for chapter in chapters if id(chapter) not in kept_ids]


class Series:
    def __init__(self, hub):
        self.hub = hub
        self._prepared = None  # the address whose settings have been checked since Family Hub started
        self._shelf_kept = (0, None)  # (when, shelf): the library asks for the shelf on every search

    def shelf_cached(self, seconds=30):
        """The followed series, asked again at most every half minute; none when the series server is away."""
        when, kept = self._shelf_kept
        if kept is None or time.time() - when > seconds:
            try:
                kept = self.shelf()
            except SeriesError:
                kept = kept or {"series": [], "queued": 0}
            self._shelf_kept = (time.time(), kept)
        return kept

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
            reply = requests.post(config.series_url + "/api/graphql", json={"query": query, "variables": variables or {}},
                                  auth=self._auth(config), timeout=timeout)
            if reply.status_code == 401:
                raise SeriesError("The series server refused the user name or password.")
            reply.raise_for_status()
            payload = reply.json()
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
            reply = requests.get(config.series_url + path, auth=self._auth(config), timeout=TIMEOUT, stream=stream)
        except requests.RequestException as exc:
            raise SeriesError("Could not reach the series server: %s" % exc)
        if reply.status_code != 200:
            raise SeriesError("The series server does not have that (%s)." % reply.status_code)
        return reply

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
        sources = [source for source in data["sources"]["nodes"] if source["id"] != LOCAL]
        keeps = all(data["settings"].get(setting) == wanted for setting, wanted in KEEP_EVERYTHING.items())
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
        langs = {lang.strip().lower() for lang in (self.hub.config.series_langs or "en").split(",") if lang.strip()}
        return langs | {"all", "localsourcelang"}  # multi-language sources and the local folder always count

    def sources(self):
        nodes = self.gql("{sources{nodes{id displayName lang isNsfw}}}")["sources"]["nodes"]
        langs = self.languages()
        return [{"id": source["id"], "name": source["displayName"], "local": source["id"] == LOCAL}
                for source in nodes if source["lang"].lower() in langs]

    # --------------------------------------------------------------- shelf

    def shelf(self):
        self._ready()
        data = self.gql("{mangas(condition:{inLibrary:true}){nodes{%s}} downloadStatus{queue{state}}}" % SERIES_FIELDS)
        items = sorted((_series(manga) for manga in data["mangas"]["nodes"]), key=lambda followed: followed["title"].lower())
        return {"series": items, "queued": len(data["downloadStatus"]["queue"])}

    def search(self, query):
        """Ask every source in the chosen languages at once. One slow or broken site must not spoil the rest."""
        sources = self.sources()
        mutation = "mutation($s: LongString!, $q: String!){fetchSourceManga(input:{source:$s,type:SEARCH,query:$q,page:1}){mangas{id title inLibrary}}}"

        def ask(source):
            try:
                found = self.gql(mutation, {"s": source["id"], "q": query})["fetchSourceManga"]["mangas"]
            except SeriesError as exc:
                return {"source": source["name"], "error": str(exc)[:200], "results": []}
            return {"source": source["name"], "error": "",
                    "results": [{"id": manga["id"], "title": manga["title"], "following": manga["inLibrary"]} for manga in found[:12]]}

        # all sources at once, 6 at a time. one slowpoke site shouldn't make everyone wait in line
        with ThreadPoolExecutor(max_workers=6) as pool:
            groups = list(pool.map(ask, sources))
        groups.sort(key=lambda group: (not group["results"], group["source"].lower()))
        return {"groups": groups, "sources": len([source for source in sources if not source["local"]])}

    def detail(self, series_id, refresh=False):
        if refresh:
            self.gql("mutation($id: Int!){fetchMangaAndChapters(input:{id:$id,fetchManga:true,fetchChapters:true}){manga{id}}}",
                     {"id": series_id}, timeout=120)
        data = self.gql("query($id: Int!){manga(id:$id){%s description inLibrary realUrl} chapters(condition:{mangaId:$id}){nodes{%s}}}"
                        % (SERIES_FIELDS, CHAPTER_FIELDS), {"id": series_id})
        if not data.get("manga"):
            raise SeriesError("That series is not on the series server.")
        chapters = sorted((_chapter(chapter) for chapter in data["chapters"]["nodes"]), key=lambda chapter: chapter["order"])
        if not chapters and not refresh:
            return self.detail(series_id, refresh=True)  # found by a search but never opened
        info = _series(data["manga"])
        kept, dropped = unique(chapters)
        if dropped:  # the server counts doubles twice; recount with one copy each
            read = sum(chapter["read"] for chapter in kept)
            info.update(chapters=len(kept), read=read, unread=len(kept) - read,
                        downloaded=sum(chapter["downloaded"] for chapter in unique(chapters, among_downloaded=True)[0]))
        # chapters_list has one copy of each chapter; every_chapter is everything the site lists, twice-listed ones too.
        info.update(description=(data["manga"].get("description") or "")[:3000], following=bool(data["manga"]["inLibrary"]),
                    link=data["manga"].get("realUrl") or "", chapters_list=kept, every_chapter=chapters, doubles=len(dropped))
        return info

    def follow(self, series_id):
        """Follow a series and start downloading every chapter of it."""
        self._ready()
        self.gql("mutation($id: Int!){updateManga(input:{id:$id,patch:{inLibrary:true}}){manga{id}}}", {"id": series_id})
        self._shelf_kept = (0, None)
        return self.download(series_id, refresh=True)

    def unfollow(self, series_id):
        """Stop following. Chapters already downloaded stay on disk."""
        self.gql("mutation($id: Int!){updateManga(input:{id:$id,patch:{inLibrary:false}}){manga{id}}}", {"id": series_id})
        self._shelf_kept = (0, None)

    def download(self, series_id, refresh=False):
        chapters = self.detail(series_id, refresh=refresh)["chapters_list"]
        ids = [chapter["id"] for chapter in chapters if not chapter["downloaded"]]
        if ids:
            self.gql("mutation($ids: [Int!]!){enqueueChapterDownloads(input:{ids:$ids}){downloadStatus{state}}}", {"ids": ids})
            self.gql("mutation{startDownloader(input:{}){downloadStatus{state}}}")
        return len(ids)

    def drop_doubles(self, info):
        """Delete the downloaded copies of twice-listed chapters once the copy kept is downloaded. Returns how many.
        Copies not downloaded yet are taken off the download queue (the series server queues every new chapter,
        second copies too), so they are not downloaded only to be deleted."""
        kept, dropped = unique(info.get("every_chapter") or info["chapters_list"])
        waiting = [chapter["id"] for chapter in dropped if not chapter["downloaded"]]
        if waiting:
            try:  # ids not in the queue are passed over by the series server
                self.gql("mutation($ids: [Int!]!){dequeueChapterDownloads(input:{ids:$ids}){downloadStatus{state}}}", {"ids": waiting})
            except SeriesError:
                pass  # tried again next time; the deleting below does not depend on it
        # only bin a spare once its replacement is safely downloaded. never leave a chapter with zero copies
        done = {chapter["number"] for chapter in kept if chapter["downloaded"]}
        ids = [chapter["id"] for chapter in dropped if chapter["downloaded"] and chapter["number"] in done]
        if ids:
            self.gql("mutation($ids: [Int!]!){deleteDownloadedChapters(input:{ids:$ids}){chapters{id}}}", {"ids": ids})
        return len(ids)

    def mark(self, chapter_ids, read):
        self.gql("mutation($ids: [Int!]!, $r: Boolean!){updateChapters(input:{ids:$ids,patch:{isRead:$r}}){chapters{id}}}",
                 {"ids": [int(chapter_id) for chapter_id in chapter_ids], "r": bool(read)})

    def pages(self, chapter_id):
        data = self.gql("mutation($id: Int!){fetchChapterPages(input:{chapterId:$id}){pages}}", {"id": chapter_id}, timeout=120)
        return len(data["fetchChapterPages"]["pages"])

    def rest_finished(self):
        """Series whose publication is over (Finished or Cancelled) go in a category the series server leaves out
        when it looks for new chapters, and every chapter already found is downloaded. A series that starts
        again comes out of it. Returns how many are resting."""
        followed = self.shelf()["series"]
        categories = self.gql("{categories{nodes{id name}}}")["categories"]["nodes"]
        category_id = next((category["id"] for category in categories if category["name"] == DONE_CATEGORY), None)
        if category_id is None:
            # includeInUpdate:EXCLUDE is the magic bit: the server's "check for new chapters" skips this category
            category_id = self.gql("mutation($n: String!){createCategory(input:{name:$n, includeInUpdate:EXCLUDE}){category{id}}}",
                                   {"n": DONE_CATEGORY})["createCategory"]["category"]["id"]
        done = [followed_series for followed_series in followed if followed_series["status"] in DONE]
        going = [followed_series["id"] for followed_series in followed if followed_series["status"] not in DONE]
        mutation = "mutation($ids: [Int!]!, $p: UpdateMangaCategoriesPatchInput!){updateMangasCategories(input:{ids:$ids, patch:$p}){mangas{id}}}"
        if done:
            self.gql(mutation, {"ids": [finished["id"] for finished in done], "p": {"addToCategories": [category_id]}})
        if going:
            self.gql(mutation, {"ids": going, "p": {"removeFromCategories": [category_id]}})
        for finished in done:
            if finished["downloaded"] < finished["chapters"]:  # grab what is there
                self.download(finished["id"])
        return len(done)

    def check_now(self):
        """Ask the series server to look for new chapters now, without waiting for the answer. Finished and
        cancelled series are put aside first, so they are not asked about."""
        def run():
            try:
                self.rest_finished()
            except SeriesError:
                pass
            self._quiet("mutation{updateLibrary(input:{}){updateStatus{jobsInfo{isRunning}}}}")
        threading.Thread(target=run, daemon=True).start()

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
