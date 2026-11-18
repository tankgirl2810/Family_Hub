# Watch mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A full-screen Watch mode in Family Hub where the family picks, asks for and watches films and TV in a built-in player.

**Architecture:** `player.py` asks Jellyfin how a title can be played in this browser and the hub passes the result through (the file as it is, or Jellyfin's converted stream), so the Jellyfin key stays on the server. `watch.js` draws Watch mode and the player on top of the existing Films and TV data (`/api/media/library`, `/item`, `/watch`, `/search`, `/request`).

**Tech Stack:** Flask + requests (existing), plain JavaScript and CSS (existing), hls.js kept in `static/vendor/`.

**Spec:** `docs/superpowers/specs/2026-11-18-watch-mode-design.md`

## Global Constraints

- No framework, no build step; Australian English; tool names (Jellyfin, Radarr) only on admin-facing words.
- The Jellyfin key never reaches the browser: not in an address, a playlist, a page or an error message.
- Every play route is `@profile_only` (no guests, no signed-out browsers).
- Every screen makes sense when Jellyfin, Radarr or Sonarr is not connected.
- WCAG 2.2 AA, 44px touch targets on phones, reduced motion respected; colours and type from the design tokens only.
- The repository stays free of household details; tests use the demo library and a pretend Jellyfin.

## Review Focus

1. Two people watching at once: stopping one must not end the other's conversion (stop is scoped to its own play session). Test in Task 2.
2. Leaving the player any way (Back, another page, closing the tab) stops the conversion and keeps the place. Checked by hand in Task 5; the stop route is tested in Task 2.
3. Junk sent as a place (negative, not a number, far past the end) is refused or clamped, never stored as it came. Test in Task 3.
4. An episode Sonarr has but Jellyfin has not listed yet: Play is not offered, and the words say why. Checked by hand in Task 4.
5. Jellyfin slow, refusing, or without an administrator account: a plain message, never a stack trace or the key. Test in Task 1.

---

### Task 1: Choosing how a title plays (`player.py`, `start`)

**Files:** Create `app/bookhub/player.py`, `tests/test_watch_area.py`. Modify `app/bookhub/hub.py` (`self.player = player.Player(self)` beside `self.media`), `app/bookhub/api.py`.

**Interfaces (produces):**
- `player.PlayError(Exception)`; `player.ITEM = re.compile(r"[0-9a-fA-F]{32}")`.
- `Player.start(item_id: str, can: dict, audio: int | None = None) -> dict` returning
  `{"item", "kind": "direct" | "hls", "url", "length": seconds, "session", "resume": seconds, "subtitles": [{"index", "label", "lang", "url"}], "audio": [{"index", "label", "chosen": bool}]}`.
  `can` is `{"video": [...], "audio": [...], "containers": [...]}`, filtered against fixed allowed sets.
- `POST /api/media/play/<item_id>/start` body `{"can": {...}, "audio": n}` -> that dict; bad id 404; `PlayError` -> 502 with its words.

- [ ] Tests (pretend Jellyfin by patching `player.requests.request`): a file the browser can play gives `kind == "direct"` and `url == "/api/media/play/<id>/file?source=<source>"`; one it cannot gives `kind == "hls"` and a url under `/api/media/play/<id>/hls/master.m3u8?` with no `api_key`/`ApiKey` and not containing the key; text subtitles are listed with `/api/media/play/<id>/subs/<source>/<index>.vtt`, picture subtitles are left out; choosing a non-default `audio` sends `EnableDirectPlay: false`; Jellyfin not set up -> 502 "Jellyfin is not set up yet."; no administrator -> 502 with plain words; no `MediaSources` -> 502; guest 403, signed out 401.
- [ ] Run: `pytest tests/test_watch_area.py -q` fails (no module).
- [ ] Implement. The account is the first user with `Policy.IsAdministrator`, remembered for the life of the process. `PlaybackInfo` is sent `SubtitleStreamIndex: -1` so nothing is burnt in. Device profile: direct play for mp4/m4v/mov and webm with the browser's codecs; conversion to HLS (`ts` + h264 + aac, or `mp4` + hevc,h264 when the browser plays hevc); subtitles `vtt` External.
- [ ] Run the tests: pass. Commit.

### Task 2: Passing the video through

**Files:** Modify `app/bookhub/player.py`, `app/bookhub/api.py`, `tests/test_watch_area.py`.

**Interfaces (produces):**
- `Player.file(item_id, source, range_header) -> requests.Response` (streamed), `Player.hls(item_id, path, query: str) -> requests.Response`, `Player.subtitle(item_id, source, index) -> str`, `Player.stop(session: str) -> None`.
- `player.HLS_PATH = re.compile(r"(master|main)\.m3u8|hls1/[A-Za-z0-9_-]+/-?[0-9]+\.(ts|mp4|m4s)")` (fullmatch).
- `GET /api/media/play/<item_id>/file?source=`, `GET /api/media/play/<item_id>/hls/<path:path>`, `GET /api/media/play/<item_id>/subs/<source>/<int:index>.vtt`, `POST /api/media/play/<item_id>/stop` body `{"session"}`.

- [ ] Tests: a ranged request is passed on with its `Range` and comes back 206 with `Content-Range`, `Content-Length`, `Accept-Ranges`; a playlist has every `api_key=`/`ApiKey=` removed and the key appears nowhere in the body; a piece comes back byte for byte with the query passed on; `hls/../../Users`, `hls/main.m3u8/../x`, a 31-character id and a non-hex source are 404 and the pretend Jellyfin is never asked; stop calls `DELETE /Videos/ActiveEncodings` with that session's own `deviceId` and `playSessionId` and nothing for an unknown session; all routes 403 for a guest.
- [ ] Implement: streamed pass-through in 256 KB pieces, upstream closed when the browser goes away; playlists are text-rewritten, everything else is bytes.
- [ ] Run the tests: pass. Commit.

### Task 3: The place in a film

**Files:** Modify `app/bookhub/db.py` (table), `app/bookhub/player.py`, `app/bookhub/api.py`, `tests/test_watch_area.py`.

**Interfaces (produces):**
- Table `watch_places(item_id TEXT PRIMARY KEY, kind TEXT, arr_id INTEGER, season INTEGER, episode INTEGER, title TEXT, at REAL, length REAL, finished INTEGER, updated TEXT, profile_id INTEGER)`.
- `Player.keep_place(item_id, at, length, about: dict, profile_id) -> dict`, `Player.places() -> list[dict]` (newest first), `Player.place(item_id) -> dict | None`; `player.FINISHED = 0.92`.
- `POST /api/media/play/<item_id>/place` body `{"at", "length", "kind", "arr_id", "season", "episode", "title"}`; `GET /api/media/places` -> `{"places": [...]}`.
- `start()`'s `resume` is the hub's place or Jellyfin's `UserData`, whichever was touched last; 0 when finished or under 60 seconds.

- [ ] Tests: a kept place is listed and `start` resumes from it; past 92% it is `finished` and `resume` is 0; Jellyfin is told (`POST /UserItems/<id>/UserData`) and its refusal does not fail the save; Jellyfin's newer place wins; `at` of -5, "abc", or 10 times the length is refused (400) or clamped to the length; a guest cannot save.
- [ ] Implement. Run the tests: pass. Commit.

### Task 4: Watch mode's screens

**Files:** Create `app/bookhub/static/watch.js`, `app/bookhub/static/watch.css`. Modify `index.html` (the two files), `app.js` (route `watch`, guards, `PAGE_SECTION`, "Watch mode" button on Films and TV, Watch buttons in `watchPanel` now play in the hub with "Open in Jellyfin" beside them), `api.py` (`GET /api/media/backdrop/<kind>/<int:arr_id>`).

**Interfaces (produces):** `pageWatch(sub, id)` handling `#/watch`, `#/watch/film/<id>`, `#/watch/show/<id>`, `#/watch/search`, `#/watch/play/film/<id>`, `#/watch/play/show/<id>/<season>/<episode>`; `Watch.playLink(kind, id, season, episode)`.

- [ ] Rows: Carry on watching, New on the server, Films, TV shows, On the way; a title page; search and ask. Arrow keys move between anything that can be pressed; Backspace goes back; F switches full screen.
- [ ] By hand in the browser at desktop and phone width, both colour schemes, with and without the services connected.
- [ ] Commit.

### Task 5: The player

**Files:** Modify `watch.js`, `watch.css`. Add `app/bookhub/static/vendor/hls.min.js` and its licence.

- [ ] Controls: play and pause, back 10, forward 30, seek bar, time, subtitles, audio, mute, next episode, full screen; they hide after 3 seconds of stillness while playing. Keys: Space/K, arrows, F, M, C, N, Backspace.
- [ ] The place is kept every 15 seconds, on pause and on leaving; leaving stops the conversion. "Up next" counts down 10 seconds at the end of an episode.
- [ ] By hand: a file played directly and a converted one; leaving every way; an error from the stream shows words and Try again.
- [ ] Commit.

### Task 6: Guides

**Files:** Create `docs/20-watch-mode.md`. Modify `README.md` (guide list), `docs/14-films-tv-and-the-vpn.md` (pointer), `CONTRIBUTING.md` (where things are).

- [ ] Write, run the whole test suite, commit.
