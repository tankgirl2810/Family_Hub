# Family Hub

A family front end for the home server: a book library with profiles,
passcodes, shared and personal tags, ratings, reading stats and
recommendations; films and TV shows requested through Radarr and Sonarr and
watched in Jellyfin, with torrents locked inside a VPN; and one design,
Minimalist Family Hub, in four colour schemes. Photos and Files are in the menu
marked "Coming soon".

Family Hub sits on top of a Calibre library (managed by
[Calibre-Web Automated](https://github.com/crocodilestick/Calibre-Web-Automated))
and does not change your book files itself: removing spare copies and an
admin deleting a book go through the library manager, after a backup. Everything the family adds is kept in its
own small database and backed up to a plain file every day.

> **Status: version 0.1, a first working version.** Read
> [What has and has not been tested](#what-has-and-has-not-been-tested)
> before relying on it.

## What it does

| Area | What you get |
|---|---|
| Tidying | Books without real details: reads files for an ISBN, finds the real title and author in Open Library and Google Books. Films and TV to sort: recognises videos already on the disk, files them, deletes spare copies |
| Look | The Minimalist Family Hub design in four colour schemes (Dark, Light, Paper, Blueprint), an accent colour, a different animated visual on Home each day (built in, or from your own GitHub repository) and a screen-change scan. An admin picks them for the whole household |
| Films and TV | Browse what is on the server, request films and shows (whole series, latest season or chosen seasons), see downloads with who asked, admins can pick a release by hand or remove a title. One button connects Radarr, Sonarr, Prowlarr, qBittorrent, Bazarr and Jellyfin. Guide 14 |
| VPN | qBittorrent has no internet except through the VPN (gluetun, ProtonVPN by default), so nothing downloads over your own connection |
| Subtitles | Bazarr fetches English subtitles, forced ones too |
| Profiles | Admin, member and restricted profiles; passcodes; admin override; a guest mode that browses and downloads without tracking |
| Library | Search, filters, covers, synopsis, download; books read elsewhere (such as Kindle) listed alongside |
| Copies | A book that is in the library twice is shown once. Spare copies can be removed for good from the Admin page, with every non-identical spare set aside first. Until then, nothing is removed |
| Covers | Give a book a different cover: upload a picture, pick one from Open Library, or have a plain one made from the title. Kept in Family Hub; the library is not changed |
| Kinds | Each book is fiction, non-fiction, a graphic novel or tabletop; guessed on arrival, filterable, and fixable in bulk. The Library is split into Books and Graphic novels |
| Manga and manhwa by chapter | Follow a series on reading sites; every chapter is downloaded and kept, and read in the browser. Optional, needs the series server |
| Inbox | Drop in files, folders or archives; books are passed to the library and anything unusable is set aside with a reason |
| Tags | Tags from the book file with the clutter removed, community tags from Open Library, Wikidata, Comic Vine (for comics) and shared tag packs, family tags, private personal tags; one search across all four; admins can hide or rename tags |
| Ratings | 1 to 5 stars per profile, with the rest of the family's ratings shown, and the average from readers at large (Goodreads or Open Library) on every card |
| Dashboard | Books and graphic novels read (counted apart), this month, pages, authors, a monthly trend you can filter by tag or author; an admin view of the library and family |
| Recommendations | "Similar books" from shared tags and authors; unread library books ordered by what you have enjoyed; a weekly list of books the library lacks, with a button to find each one |
| Discover | Search your Prowlarr indexers, send a result to your torrent client, follow authors and tags |
| Connections | Enter and test every address, key and password (Prowlarr, qBittorrent, Radarr, Sonarr, Bazarr, Jellyfin, the VPN, mail, catalogues) under Admin → Hub → Connections; no file editing or restart. Guide 15 lists them all |
| Devices | Each person lists their Kindles and reading apps and keeps a "My books" list; reading apps fetch it from a private catalogue, a Kindle is sent books by email, and admins can do it for someone else |
| Sync | Reading position from Readest or KOReader arrives automatically; Kindle reading can be pulled from Hardcover |
| Goodreads | Each person can link their Goodreads profile: read and currently-reading shelves and ratings come in, and books the family has only on Kindle are listed, anonymously, with a button to find the EPUB |
| Backup | Daily export of all book data (tags, ratings, history) to JSON; import to restore or move |

## Quick start

Try it in two minutes with a sample library (needs Python 3.10+):

```bash
scripts/run-demo.sh          # macOS / Linux
scripts\run-demo.bat         # Windows
```

Then open <http://localhost:8090>. Profiles: **You** and **Partner**
(admins) and **Nan** (restricted), all with passcode `1234`.

## Your own settings

Everything personal to a household lives in one file, `.env`, which never
goes to GitHub: addresses, passwords, API keys and settings. Copy
[`.env.example`](.env.example) to `.env` and fill it in (guide 1); settings
saved later on the Admin page are written into the same file. To set
someone else up with your household's setup (settings, profiles and each
program's setup, no books or films), make a locked **setup file** under
Admin → Hub → Setup file. [Guide 7](docs/07-sharing-on-github.md) explains
both, and how two people can work on the code together.

## Guides

1. [Try it on your PC](docs/01-try-it-on-your-pc.md)
2. [Move it to the home server](docs/02-move-to-the-home-server.md)
3. [Profiles: full and restricted](docs/03-profiles.md)
4. [Syncing devices and Kindle reading](docs/04-syncing.md)
5. [Finding and downloading new books](docs/05-finding-new-books.md)
6. [Your data and backups](docs/06-your-data-and-backups.md)
7. [Sharing: GitHub, your settings and the setup file](docs/07-sharing-on-github.md)
8. [Adding your own files and sorting the library](docs/08-adding-and-sorting-books.md)
9. [Devices and "My books"](docs/09-devices-and-my-books.md)
10. [Tags](docs/10-tags.md)
11. [Goodreads and books that are only on Kindle](docs/11-goodreads.md)
12. [What to read next](docs/12-what-to-read-next.md)
13. [Graphic novels, manga and series by chapter](docs/13-manga-and-series-by-chapter.md)
14. [Films, TV shows, subtitles and the VPN](docs/14-films-tv-and-the-vpn.md)
15. [What you need to get, and where it goes](docs/15-what-you-need.md)
16. [Visuals on Home](docs/16-visuals-on-home.md)
17. [The watchlist](docs/17-watchlist.md)

## How the pieces fit

```
 Readest / KOReader ──reading position──▶ ┌──────────┐ ◀──reads only── Calibre library
 Web browser ───────────────────────────▶ │ Family Hub │                 (folder of books)
 Hardcover (Kindle reading) ────────────▶ └──────────┘                       ▲
                                             │   ▲                           │ adds, converts
                                    search   │   │ finished downloads        │
                                             ▼   │                  Calibre-Web Automated
                                         Prowlarr ──▶ torrent client ──▶ import folder
```

## What has and has not been tested

Being upfront, because this decides how much checking you should do:

| Part | State |
|---|---|
| Profiles, passcodes, admin override, guest mode | Tested (automated tests and in a browser) |
| Library, tags, tag search, ratings, stats, recommendations | Tested against the demo library |
| Copies of the same book shown as one | Tested, and run on a real library of 445 entries (84 books were in it more than once) |
| Removing spare copies | Tested end to end against a real library manager working on a copy of that library: 47 sets removed or merged with no problems. **Not yet run on the live library**; that waits for the library manager's sign-in |
| Nightly copy to a second disk | Tested on the real stack against a scratch folder: 3 GB copied, the databases in the copy open cleanly, a second run copies only what changed |
| Dashboard "My books" tab | Tested |
| Changing covers | Tested, including real covers fetched from Open Library |
| Series by chapter | Tested against a real series server and one real reading site. Not tested on a real iPad or with a long series |
| "Similar to what I've read" and weekly suggestions | Tested, including real lists from Open Library. How good the suggestions are has only been judged on the sample library |
| Backup export and import | Tested, including restoring into an empty install |
| Device sync endpoint (KOReader protocol) | Protocol and book matching tested with simulated requests. **Not yet tested with a real Readest, KOReader or Kobo device** |
| Community tags from catalogues | Open Library and Wikidata tested with real books. **Google Books and Comic Vine not yet tested with a real key** |
| Goodreads shelves | Tested against a real public Goodreads profile, and with an export file made to Goodreads' column layout. **Not yet tested with an export file downloaded from Goodreads, or with a private profile** |
| "My books", devices, Send to Kindle, the reading-app catalogue | Tested with a stand-in mail server and simulated catalogue requests. **Not yet tested with a real Kindle, Gmail account or Readest** |
| Book search and "send to client" | Tested against a stand-in for Prowlarr built from its published API. **Not yet tested against a real Prowlarr** |
| Import of finished downloads | File copying tested. **Not yet tested with a real torrent client and Calibre-Web Automated** |
| Docker setup (`docker-compose.yml`, `Dockerfile`) | Every container has run on the PC (Docker Desktop on Windows). **Not yet run on the Linux home server** |
| Reading a real Calibre library | Uses Calibre's standard database layout; **only tried on the demo library** |
| Design and colour schemes | All four colour schemes and a custom accent checked in a browser (Edge), on a desktop and a phone-sized screen |
| Films and TV, Connect everything | Run on the real Radarr, Sonarr, Prowlarr, qBittorrent, Bazarr and ProtonVPN (through gluetun) on the PC: every Connect everything step done, torrents only through the VPN, forwarded port passed on. **Not yet: a real download start to finish, subtitles arriving, Jellyfin** |
| Deleting a book (admins) | Tested against a stand-in for the library manager. **Not yet run on the live library** |
| Hardcover import (Kindle reading) | **Experimental and untested** with a live account |
| PDF to EPUB | Needs Calibre's converter in the image (`INSTALL_CALIBRE=true`); **untested** |

Not built yet: an in-browser reader, automatic downloading for followed
authors (it suggests, you click), and new-release tracking by genre.

## A note on downloads

Discover, and film and TV requests, search whatever indexers **you** add to
Prowlarr; Family Hub ships with none. What is legal to download depends on the book and where
you live. Plenty of books are free to share, including everything at
[Standard Ebooks](https://standardebooks.org) and
[Project Gutenberg](https://www.gutenberg.org).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Tag packs are the easiest way to
help: see [tagpacks/README.md](tagpacks/README.md).

## Licence

MIT. See [LICENSE](LICENSE).
