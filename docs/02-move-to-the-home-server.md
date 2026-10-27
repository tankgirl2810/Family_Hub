# 2. Move it to the home server

Everything Family Hub and its companions store lives in one place: the
project folder, and inside it the `stack` folder. Moving means copying that
folder and starting the containers again.

| Folder | What is in it | Move it? |
|---|---|---|
| `stack/bookhub` | Profiles, tags, ratings, reading history, daily backups | Yes, this is the important one |
| `stack/cwa-config` | Library manager settings and users | Yes |
| `stack/prowlarr-config` | Indexers and download client settings | Yes |
| `stack/qbittorrent-config` | Torrent client settings (if you use the bundled one) | Yes |
| `stack/radarr-config`, `stack/sonarr-config`, `stack/bazarr-config` | Film, TV and subtitle settings, and their lists of what is wanted | Yes |
| `stack/gluetun`, `stack/jellyfin-config` | VPN state, and the trial Jellyfin (PC only) | Not needed |
| `stack/data` (or your `DATA_ROOT`) | Films, TV shows and their downloads | On the server, point `DATA_ROOT` at the storage disk instead |
| `stack/suwayomi` | Series server settings, sources, followed series and read marks (if you use it) | Yes |
| `stack/series` | Manga and manhwa chapters (if you use the series server) | Yes |
| `stack/komga-config` | Reading-app accounts and read marks for series (if you use the series server) | Yes |
| `stack/library` | The book files and their details | Yes, or point the server at the library it already has |
| `stack/inbox`, `stack/ingest` | Temporary | No |
| `stack/needs-attention` | Files Family Hub could not add, and originals it has already unpacked | Only if you still want them |
| `.env` | Every setting, address, password and key | Yes |

**The quickest way:** make a setup file under **Admin → Hub → Setup file**.
It holds `.env`, the profiles and each program's setup (everything marked
"Yes" above except the books and chapters) in one locked zip. On the server,
clone the project, unpack the setup file into it (guide 7 has the steps),
copy or point at the library, then carry on from step 6 below. The steps
below copy the whole folder instead, books included.

## Steps

**On the PC**

1. Make a safety copy first: in Family Hub go to **Admin → Books → Back up
   now**, then **Download book data** and keep that file somewhere safe.
2. Stop the containers:

   ```bash
   docker compose --profile search --profile torrent --profile media --profile watch --profile series stop
   ```

3. Copy the whole project folder to the server (USB drive, network share, or
   `scp`). On macOS or Linux, `scripts/pack-for-server.sh` makes one archive
   for you; add `--with-library` to include the books.

**On the server**

4. Install Docker if the server does not have it yet (a Jellyfin set up
   with Docker means it is there already).
5. Put the folder where the other services live, for example
   `/opt/book-hub`.
6. Open `.env` and check:
   - `PUID` and `PGID` match the user that owns the files (run `id`).
   - `DATA_ROOT` points at the big storage disk (see "Films, TV and their
     storage" below).
   - `LIBRARY_PATH` points at the server's real library if it is not inside
     `stack`.
   - `DOWNLOADS_PATH` is empty, so finished book downloads land in
     `DATA_ROOT/torrents/books` on the storage disk too. A path left over
     from the PC would keep them in `stack`.
   - `CWA_URL` is the address people will type, for example
     `http://192.168.1.10:8083`.
7. Fix ownership if you copied from Windows:

   ```bash
   sudo chown -R 1000:1000 stack
   ```

8. Start it:

   ```bash
   docker compose --profile search --profile torrent --profile media up -d --build
   ```

   Leave out `--profile search` if the server's own Prowlarr is used (see
   below). Add `--profile series` if you follow manga by chapter
   (guide 13). `watch` stays off: the server has its own Jellyfin.

9. Open `http://<server address>:8090` and sign in. Your profiles and data
   should be exactly as you left them.

## If the ports clash with Jellyfin or Prowlarr

Change the left-hand numbers in `.env` (`BOOKHUB_PORT`, `CWA_PORT`,
`PROWLARR_PORT`, `QBITTORRENT_PORT`, `RADARR_PORT`, `SONARR_PORT`,
`BAZARR_PORT`, `JELLYFIN_PORT`, `SUWAYOMI_PORT`, `KOMGA_PORT`; the end of
`.env.example` lists them).

**Already running Prowlarr?** Do not start a second one. Leave out
`--profile search`, then in Family Hub, **Admin → Hub → Connections → Book
search (Prowlarr)**, enter your existing Prowlarr's address, for example
`http://192.168.1.10:9696`, and its API key, and press **Test connection**
([guide 5](05-finding-new-books.md), step E). Connect everything then shares
its indexers with Radarr and Sonarr.

**Already running a torrent client?** Use this project's qBittorrent anyway
(`--profile torrent`). It is the one locked inside the VPN, so nothing is
ever downloaded over your own connection; a client outside the VPN would
break that promise. If the old client is still needed for other things, give
the new one a different port (`QBITTORRENT_PORT`).

## Bringing in what you already own

When the files move, or the server already has books, films or shows of its own, Family Hub checks what
is already there before adding anything. Put things in through these doors, not straight into the library
or media folders, and the checks happen by themselves.

| What | Put it in | What is checked |
|---|---|---|
| Book files from another disk or computer | `stack/inbox` (guide 8) | A file the library already has, byte for byte, is not added; it goes to `needs-attention` with "Already in the library". A Kindle file beside an EPUB of the same name is held back |
| Book downloads | The finished-downloads folder (guide 5) | The same check, and a file still downloading is left alone |
| Films and TV from another disk | `DATA_ROOT/unsorted/films` and `unsorted/tv` | **Downloads → Films and TV to sort**: Radarr and Sonarr say which film or episode each file is and whether the server already has as good a copy. Spare copies can be deleted from there (guide 14) |
| Chapters of followed series | Copy `stack/series` and `stack/suwayomi` across (the table above) | The series server already knows them; nothing is downloaded twice |

The byte-for-byte check only catches the very same file. The same book as a different file (another
edition, an EPUB beside a PDF) is added, and then shown as **one book**. To find and remove such spares
afterwards: **Admin → Books → Copies of the same book → See the copies**, then **Remove spare copies from
the library** (guide 8). The library's database is copied first, and spares are set aside rather than
deleted unless they are identical.

When searching for something new, **Discover** marks a result **In library** when you already have it,
and Radarr and Sonarr do the same for films and shows.

## Starting from a fresh install instead

If you would rather install cleanly on the server and bring only the book
data: set up the server from scratch (guide 1, Level 2), create the first
admin, then **Admin → Books → Import** the book-data file from step 1.
Tags, ratings and history are matched to books by ISBN or by title and
author. Profiles named in the file are created if missing, without
passcodes; set those afterwards.

## Films, TV and their storage

Before the first start on the server, set `DATA_ROOT` in `.env` to a folder
on the big storage disk (for example `DATA_ROOT=/mnt/storage/data`). Films,
TV shows and their downloads all live under it; keeping them on one disk lets
finished downloads be linked into place instead of copied. Leave the `watch`
profile off, because the server already has Jellyfin, and enter that
Jellyfin's address and key under **Admin → Hub → Connections**. Guide 14
has the rest, including the VPN.

## Managing the programs behind it

**Admin → Hub → Manage the programs** lists Radarr, Sonarr, Prowlarr,
qBittorrent, Bazarr, Jellyfin, the library manager, Suwayomi and Komga,
says which are running, and has an **Open** button for each one's own page.
The links use the address you opened Family Hub with and each program's
port (the `*_PORT` values in `.env`), so they work from any computer or
phone on the home network. From outside the house you need a VPN into home,
such as Tailscale; none of these pages are on the internet by themselves.

## Adding it to your home dashboard

In Homepage, Homarr or whichever dashboard the server uses, add a link
tile to `http://<server address>:8090`. Family Hub answers at `/healthz` if
the dashboard wants a status check.

## Updating later

```bash
git pull            # if you cloned from GitHub
docker compose up -d --build
```

Your data is untouched by updates; it is all in `stack`.
