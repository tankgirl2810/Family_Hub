# Watch mode: design

Agreed 2026-11-18.

## Why

Films and TV can already be picked and asked for in Family Hub, but watching means leaving it: every Watch
button opens Jellyfin in a new tab, and nothing is made for a big screen. The household wants one lean-back
place to switch to full screen, pick a film or show, ask for it if it is not on the server, and watch it.

## Goals

- A **Watch mode** inside Family Hub (`#/watch`): the sidebar goes, big poster rows, a full-screen switch.
- **Select**: rows for Carry on watching, New on the server, Films, TV shows and On the way; a title page with
  Play, or seasons and episodes. Works with arrow keys (a TV remote or keyboard), a mouse and touch.
- **Download**: search and ask for a film or show from the same screen, with its progress on the poster. Same
  permission as today ("Can request films and TV"). Download means onto the server.
- **Watch**: a built-in player: play and pause, skip back and forward, a seek bar, subtitles, audio language,
  next episode, full screen. It carries on where the household left off.
- The Watch buttons on the ordinary film and show pages play in the hub too; "Open in Jellyfin" stays as a
  second link.

## Not in this change

- Saving a film onto a phone or laptop to watch offline.
- Each person's own place in a film. The hub uses one Jellyfin account, so the place is the household's.
- Picture-based subtitles (from discs). Text subtitles, which is what Bazarr fetches, are shown.
- A separate app, or a casting button.

## Must not happen

- The Jellyfin key reaching the browser, in an address, a playlist or a page.
- A guest or a signed-out browser being able to play anything.
- Watch mode looking broken when Jellyfin, Radarr or Sonarr is not connected: it says what is missing and who
  can add it.

## Approaches

1. **Keep sending people to Jellyfin's own pages, framed inside the hub.** Least work, but Jellyfin needs its
   own sign-in, cannot be styled, and blocks framing by default. Rejected.
2. **Play the file straight off the disk.** The hub would need the media folder mounted, and browsers cannot
   play most of the 2160p files, so every one of those would fail. Rejected.
3. **The hub's own player, with Jellyfin doing the work behind it (chosen).** The hub asks Jellyfin how a title
   can be played in this browser. A file the browser can play is passed through as it is; anything else Jellyfin
   converts on the fly and the hub passes that through in pieces. The key stays on the server.

## How (the builder's calls unless marked)

- `app/bookhub/player.py`: the Jellyfin side of playing. Finds the household account (the first Jellyfin
  administrator), asks `PlaybackInfo` with a device profile built from what the browser says it can play, and
  answers with one of two stream kinds: `direct` (a ranged pass-through of the file) or `hls` (Jellyfin's
  playlist and pieces, passed through with the key taken out of every address).
- Routes under `/api/media/play/…`, all for signed-in profiles only, ids and paths checked against a strict
  pattern before anything is fetched.
- The place in a film is kept in the hub's database (table `watch_places`, one row per film or episode, shared
  by the household) and also passed to Jellyfin when it accepts it, so its own apps agree. Whichever of the two
  was touched last wins when playing starts. Over 92% watched counts as finished.
- `app/bookhub/static/watch.js` and `watch.css`: Watch mode's screens and the player, in the hub's plain
  JavaScript and its design tokens. `hls.js` (Apache 2.0) is kept in `static/vendor/` for browsers that cannot
  play Jellyfin's converted stream by themselves; Safari plays it natively.
- Full screen is asked of the whole page, so it stays on while moving between the rows, a title and the player.
  On an iPhone, where only a video can go full screen, the button does that instead.
- Guide 20 explains Watch mode for the family; guide 14 points to it.

## Testing

- Automated: a pretend Jellyfin in the tests covers the stream choice, the key never appearing in what the
  browser is sent, ranged pass-through, the place being kept, and who may play.
- By hand in the demo: the screens, arrow-key moving, full screen and the player's controls against a pretend
  Jellyfin serving a short test clip.
- Real Jellyfin: tried if Docker is running on the test PC; said plainly if not.
