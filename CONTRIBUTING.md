# Contributing to Family Hub

Family Hub is built and looked after by two people, for their own household
first. Only they push to the `master` branch. Everyone is welcome to use it,
fork it and change their own copy.

The most useful help from anyone else is an **issue**: something that does
not work, a guide that is unclear, or an idea (the **Issues** tab, then
**New issue**). Never paste your `.env`, passwords, API keys or a setup file
into an issue; issues are public.

Pull requests are read, but please open an issue first so nobody spends time
on something that will not be merged. Small, focused changes are the easiest
to review.

## Ways to help

- **Test on real hardware and report back.** The README lists what has not
  been tested yet (real devices, Prowlarr, Docker, Hardcover). A short issue
  saying "works with X" or "fails with Y, here is the log" is valuable.
- **Add a tag pack.** See [tagpacks/README.md](tagpacks/README.md).
- **Fix a bug or add a feature.** Open an issue first for anything large so
  nobody builds the same thing twice.

## Setting up

```bash
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r app/requirements.txt pytest
pytest -q                            # all tests should pass
cd app && python -m bookhub --demo --demo-dir ../demo
```

The demo library is rebuilt whenever the `demo` folder is missing.

## Where things are

| Path | What it is |
|---|---|
| `app/bookhub/hub.py` | Tags, ratings, reading history, stats, recommendations, backup |
| `app/bookhub/api.py` | Every web address the pages call, and who may call it |
| `app/bookhub/calibre.py` | Read-only access to the Calibre library |
| `app/bookhub/kosync.py` | Device sync (KOReader protocol) |
| `app/bookhub/integrations.py` | Prowlarr, qBittorrent, Hardcover |
| `app/bookhub/media.py` | Films and TV: Radarr, Sonarr, Bazarr, Jellyfin, the VPN, Connect everything |
| `app/bookhub/mediasort.py` | Films and TV to sort: recognising videos already on the disk, filing them, deleting spare copies |
| `app/bookhub/looks.py` | The design's colour schemes, accent and visual on Home, and the household's choice |
| `app/bookhub/visuals.py` | The visuals on Home: the built-in ones, checking a GitHub repository for new ones, and the sealed frame they run in |
| `app/bookhub/librarymanager.py` | Signing in to the library manager to merge or delete books |
| `app/bookhub/duplicates.py` | Spare copies of a book, and the library backup before changes |
| `app/bookhub/bookfix.py` | Books without real details: clues from the files, catalogue matches, the change through the library manager |
| `app/bookhub/secondcopy.py` | The nightly copy to a second disk |
| `app/bookhub/series.py` | Series by chapter (Suwayomi) |
| `app/bookhub/settings.py` | Admin → Hub → Connections: values saved in the app |
| `app/bookhub/static/` | The web pages: plain HTML, CSS and JavaScript, no build step. `looks/minimalist/` holds the design's stylesheets, `setpieces.js` the screen-change scan, `visuals/` the built-in visuals, their libraries and `frame.js` |
| `tests/test_app.py` | Tests that run the whole app against the demo library |

## Ground rules

- **Never write to the Calibre library.** Family Hub reads it; the library
  manager owns it. The only two exceptions, removing spare copies and an
  admin deleting a book, go through the library manager's own pages
  (`librarymanager.py`) after a backup of its database.
- **User data is keyed by `book_key`**, never by Calibre's book number.
- **Add or update a test** for behaviour you change, and keep `pytest` green.
- **Permissions are enforced in `api.py`,** not just hidden in the page.
  Guest, restricted, member and admin must each be checked on the server.
- **Keep it readable for non-specialists.** Plain words in the interface;
  no new dependencies or build tools without discussion.
- **Accessibility:** real buttons and labels, keyboard reachable, readable
  contrast. Colours come from the design's tokens (`static/looks/minimalist/`);
  check a change in all four colour schemes, and with a custom accent.
- **No indexers, trackers or download sources in the repository.** Users add
  their own in Prowlarr.
- **No private data in issues or pull requests:** no API keys, `.env`
  files, databases or backups.

## Pull requests

1. Fork, create a branch, make the change.
2. Run `pytest -q`.
3. Open a pull request describing what changed and how you tested it. If it
   touches something listed as untested in the README, say what you tried
   it against.
