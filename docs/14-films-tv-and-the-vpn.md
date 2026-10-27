# 14. Films, TV shows, subtitles and the VPN

**Movies and TV Shows** in the sidebar has three tabs:

| Tab | What is in it |
|---|---|
| **Watch** | Every film and series on the list, with what is already on the server and what is still being looked for. Above it, "New on the server" from Jellyfin, with a **Watch** button |
| **Search** | Search for a film or show and request it |
| **Downloads** | Its own section in the sidebar (also a tab here and in Books): every download in qBittorrent, films, TV and books, with **Pause**, **Continue**, buttons to move it up or down the line, and **Delete** for admins; what was added lately |

Family Hub is only the front end. These programs do the work:

| Program | Its job | Its own page |
|---|---|---|
| **Prowlarr** | Searches your indexers. Already used for books (guide 5) | Port 9696 |
| **Radarr** | Keeps the list of films, picks the best release, files the finished film | Port 7878 |
| **Sonarr** | The same for TV, and fetches new episodes as they come out | Port 8989 |
| **qBittorrent** | Downloads. It has no internet of its own: only the VPN's | Port 8080 |
| **gluetun** (the VPN) | Connects to your VPN provider. Torrents only ever go through it | – |
| **Bazarr** | Fetches English subtitles for every film and episode | Port 6767 |
| **Jellyfin** | Where the family watches | Port 8096 |

A show's page lists **Seasons and episodes**: pick a season, and every
episode on the server has a **Watch** button that opens it in Jellyfin. A
film on the server has its own **Watch** button.

## Who can do what

- Everyone signed in (not guests) can see Watch and Downloads.
- **Requesting** needs **Can request films and TV**. Admins always can; an
  admin ticks it for anyone else under **Admin → Hub → Profiles**. A request
  goes straight through: nobody has to approve it. Downloads shows who asked.
- Anyone who can request also gets these on a film or show's page:
  - **Look again now**: search every indexer straight away, for example
    when you hear a better release is out.
  - **Stop looking** / **Start looking again**: stop Radarr or Sonarr
    searching (and swapping in better copies) for that title. Anything
    already downloading still finishes.
  - **Pick a version yourself**: see every version the indexers have and
    choose one you are happy with, even a smaller or lower-quality one
    marked "would not be picked". It may later be swapped for a better
    copy unless you press **Stop looking** once it is on the server.
- On **Downloads**, anyone who can request films and TV can pause, continue
  and reorder those downloads; books follow **Can find new books** instead. qBittorrent downloads three at a time (Connect everything sets
  this up) and the rest wait in line; a download nobody is sharing doesn't
  hold up the line.
- When a download reaches 100%, qBittorrent checks it once more and then
  stops it, so it doesn't keep uploading. The files stay in place, and Radarr,
  Sonarr or the book intake files them as usual. It shows as **Done**.
  Pressing **Continue** on a finished download doesn't make it start sharing again.
- A small card in the bottom-right corner follows the films, shows and books you
  asked for or opened, on every page, and says when each is ready. If the
  browser tab is in the background, its title changes too. Press ✕ to stop
  following one.
- On **Downloads**, admins also get **Delete** on each download. It removes
  the download and its files from qBittorrent and, when it is for a film or
  show, that film or show from the server and Jellyfin too (for a TV
  episode, that means the whole show). It asks first and cannot be undone.
- **Pick a version yourself → Show versions** lists versions as each site
  answers, best first by the quality in their names. Press **Stop
  searching** to pick from what has been found so far; Radarr or Sonarr
  check the one you pick, and if they turn it down they say why and offer
  **Use this anyway**. When their own full search finishes, their checked list
  replaces the early one.
- **Downloads → Speed** (under the list of downloads; press it to open)
  shows the speed now and a slider in 24 small
  steps from **Fastest** (no limit) to **Slowest** (100 KB/s), or type an
  exact speed in MB/s and press **Set**. Slide it right when someone needs
  the internet: downloads keep going, just slower. Uploads follow at a
  quarter of the download limit. Under it, a chart of the last five minutes
  (download, upload, and the limit as a dashed line; hover for the speed at
  any moment) and a line saying how many downloads are downloading, waiting
  their turn, waiting for seed, paused and done. Anyone who may download
  things can change the speed.
- **Waiting for seed.** A book download that nobody has shared for ten minutes
  goes to the back of the line and is paused, so it does not hold up the
  rest. It is listed under **Waiting for seed** with when it was last
  checked. Every half hour it is started for five minutes to look for
  someone sharing it; if it finds someone it carries on by itself from the
  back of the line, otherwise it is paused again. **Continue** starts it
  straight away and takes it out of waiting for seed.
- **Books and graphic novels swap to a shared copy.** A book download that
  had people sharing it and then has nobody for half an hour is looked for
  again. If another copy of the same book has someone sharing it (an EPUB
  first, then the one with most sharers), that copy is downloaded instead and
  the stuck one is deleted with its unfinished files. "The same book" means the
  title and author words match and any volume or issue number is the same. If
  no other copy is shared yet, it looks again an hour later, then three hours
  after that. If there is still none, it stops looking: the download is paused
  and says **Paused · no shared copy found**. **Find a new copy** tries again
  by hand; **Continue** starts it again. A download that never had anyone
  sharing it just waits for seed. Swaps show under **Discover → Recent
  requests**.
- **Find a new copy** (book downloads still coming in) lists other copies of
  the same book that someone is sharing, the best first; **Use this copy**
  downloads it and deletes the stuck one with its unfinished files.
- **Waiting for seed, and the swap, are for books and graphic novels only.**
  Films and TV are left to Radarr and Sonarr, which give up on a stuck
  download and look for another copy themselves.
- **Files are missing.** qBittorrent says this when a download's files were
  moved by hand, or the downloads folder changed. They are listed together on
  the Downloads page; if you already have them, **Forget (files stay)** or
  **Forget all** takes them off qBittorrent's list without touching any file.
- While a version of a film or episode is coming in, its card says
  **Downloading** with how far along it is, instead of "being looked for".
- **Remove from the server** is for admins only. Removing deletes the files
  after a confirmation that names the title and its size.

When requesting a show, choose **Whole series** (the usual), **Latest
season** or **Pick seasons**. New episodes keep coming whichever you choose.

Each film or show's page has **What's happening**, which updates by itself
while you watch: who asked for it, each indexer that was searched and how
many results it gave, the version that was picked, the download's progress,
and when it was filed on the server. If something goes wrong, the line in
red says what, for example that qBittorrent refused the sign-in.

## The quality everyone gets

Connect everything (below) gives Radarr and Sonarr a quality profile called
**Family Hub**:

- 2160p (4K) first, from Blu-ray or web releases;
- then 1080p, then 720p, when there is no 4K;
- if there is nothing in HD at all, the best there is (DVD or 480p), so an
  old or rare film still arrives;
- once something lower is on the server, it keeps looking and swaps in a
  better release when one appears, up to 2160p;
- never "remux" copies (50 to 80 GB a film), disc images, or cinema
  camera recordings.

A 4K film is usually 15 to 30 GB. To change the rule, edit the **Family Hub**
profile in Radarr or Sonarr (Settings → Profiles). Pressing Connect
everything again puts the household rule back.

## Where the files go

Everything lives under one folder, `DATA_ROOT` in the `.env` file
(`./stack/data` on this PC):

```
DATA_ROOT/
  torrents/movies    torrents/tv    torrents/books    ← qBittorrent downloads here
  media/movies       media/tv                          ← Radarr and Sonarr file them here
```

Downloads and the library are on the same disk on purpose. A finished film
is then *linked* into `media/movies`, not copied: it takes up its space once
even though the stopped download is still in `torrents`.

Connect everything also tells qBittorrent to use those folders (its
"automatic torrent management"). Without that, films and TV land in the
books folder, where Radarr and Sonarr cannot see them, and a film's page
says **Downloaded, but Radarr could not file it**. Pressing Connect
everything again puts it right and moves those downloads into their folders,
after which Radarr and Sonarr file them by themselves within a few minutes.

### Films and TV to sort

**Downloads → Films and TV to sort** (admins) lists videos on the disk that
are not on the server yet, from two places:

- `DATA_ROOT/torrents/movies` and `torrents/tv`: finished downloads that
  were not filed (for example ones moved there by hand).
- `DATA_ROOT/unsorted/films` and `unsorted/tv`: films and TV you already
  have, copied in from another disk. Family Hub makes these two folders.

Radarr and Sonarr read each file name and say what it is:

| Heading | What it means | What to do |
|---|---|---|
| Ready to file | They know which film or episode it is, and the server has no copy | **File it**, or **File all ready** |
| Better than the copy you have | The server has it, in a worse quality | **File it**; the old copy is replaced |
| Not recognised | The name did not say which film or show | Search, then **It's this one** |
| Spare copies | The server already has it in as good a copy or better | **Delete spare** |
| Cannot be filed | A sample, a file still being written, an episode it cannot place | The reason is shown |

Filing moves the file into `media/movies` or `media/tv`, named the server's
way, where Jellyfin finds it; the folder it came from is tidied away once no
video is left in it, and its finished download is taken out of qBittorrent.
A film or show picked with **It's this one** is added to the list without
looking for anything to download. Two copies of the same thing in these
folders are marked, with the biggest (usually the best) pointed out.

Family Hub can delete only inside `torrents` and `unsorted`; the media
folder itself stays read-only to it (`docker-compose.yml` mounts just those
two folders writable).

**On the home server**, set `DATA_ROOT` to a folder on the big storage disk,
for example `DATA_ROOT=/mnt/storage/data`, before the first start. Moving it
later means moving the whole folder and pressing Connect everything again.

## The VPN

qBittorrent runs *inside* the VPN container. When the VPN is down, or not
set up yet, qBittorrent has no internet at all: nothing can ever be
downloaded over your own connection. Downloads then says **Waiting for VPN ·
not set up** (or **VPN not connected**).

### Choosing a provider

What matters most for torrents is **port forwarding**: it lets other people's
clients connect to yours, which makes downloads faster and seeding work.

- **ProtonVPN Plus** (the default here). Port forwarding works with
  WireGuard and gluetun sets it up by itself. Family Hub then tells
  qBittorrent which port was given, every five minutes.
- **AirVPN**. Very popular for torrents, with fixed forwarded ports. A bit
  more setting up (see gluetun's wiki for AirVPN).
- Mullvad no longer offers port forwarding, so it is a poor fit.

### Setting up ProtonVPN Plus

1. On account.protonvpn.com open **Downloads → WireGuard configuration**.
2. Give it a name, choose **GNU/Linux**, tick **NAT-PMP (Port Forwarding)**,
   pick a server that supports P2P, and press **Create**.
3. Copy the line that starts `PrivateKey =` (only the key after the `=`).
4. In the `.env` file fill in:

   ```
   VPN_PROVIDER=protonvpn
   VPN_TYPE=wireguard
   VPN_WIREGUARD_KEY=the key you copied
   VPN_COUNTRIES=Switzerland
   ```

   `GLUETUN_API_KEY` must also be set to any long random text; it already is
   on this PC. Docker refuses to start anything while it is empty, so the
   VPN's controls are never left open.
5. Run `docker compose --profile search --profile torrent --profile media up -d`.
6. Open **Admin → Hub → Films and TV**. "VPN for torrents" should say **VPN
   connected · … · port … forwarded**.

### If the VPN container restarts

The VPN reconnecting by itself (it happens now and then, often to another
server) needs nothing from you. But if the VPN *container* itself restarts,
for example after `docker restart gluetun` or a crash, qBittorrent is left
without any network until it is restarted too. That is safe (it still cannot
reach the internet over your own connection), but downloads stop. Downloads
and **Admin → Hub → Films and TV** then say so, and the fix is:

```
docker compose restart qbittorrent
```

Restarting the whole computer or Docker is fine: both start together.

To use another provider, set `VPN_PROVIDER` to its name as gluetun spells it
(see gluetun's wiki) and fill in either the WireGuard key or `VPN_USER` and
`VPN_PASSWORD` with `VPN_TYPE=openvpn`.

## Switching it all on

1. Do the VPN steps above (torrents wait until the VPN is up, but everything
   else works without it).
2. Run:

   ```
   docker compose --profile search --profile torrent --profile media up -d
   ```

   On this PC add `--profile watch` to start a Jellyfin to try. On the home
   server leave that off: it already has Jellyfin.
3. qBittorrent prints a temporary password the first time it starts. Read it
   with `docker logs qbittorrent`, open `http://<the computer>:8080`, sign in
   as `admin`, and set your own password under **Tools → Options → Web UI**.
   Put the user name and password in Family Hub under **Admin → Hub →
   Connections → Download progress (qBittorrent)**. The address is
   `http://gluetun:8080`.
4. In Family Hub open **Admin → Hub → Films and TV** and press **Connect
   everything**. It reads each program's API key from its own settings, then:
   - sets the user name and password from **Connections → Sign-in for the
     programs' own pages** on Radarr, Sonarr, Prowlarr, Bazarr and
     qBittorrent, when one is saved;
   - gives Radarr and Sonarr their folders (`/data/media/movies`, `/data/media/tv`);
   - makes the Family Hub quality profile in both;
   - connects both to qBittorrent;
   - tells Prowlarr to share its indexers with both, and to send books
     from Discover to qBittorrent;
   - gives qBittorrent a folder for films, TV and books;
   - points Bazarr at Radarr and Sonarr and asks for English subtitles
     (forced ones too, and hearing-impaired ones when nothing else exists);
   - adds Films and TV libraries to a Jellyfin started from this project.

   Each step is listed with what happened. Anything already in place is left
   alone, so press it again whenever something was added or changed.
5. For Jellyfin, make an API key in Jellyfin (**Dashboard → API Keys → +**)
   and enter it under **Admin → Hub → Connections → Watching (Jellyfin)**,
   with the address you use in a browser for the Watch buttons. Jellyfin
   asks you to make its own admin account the first time you open it. One
   account is enough: the whole household watches with it.

## Subtitles

Bazarr looks on free sources that need no account (Podnapisi, YIFY
Subtitles, TVsubtitles and subtitles already inside the file). The biggest
source, **OpenSubtitles.com**, needs a free account (about 20 downloads a
day): enter it under **Admin → Hub → Connections → Subtitles (Bazarr)** and
press Connect everything again. Other languages go in the same place as
short codes separated by commas (`en, fr`).

## On the home server

Use the Jellyfin and Prowlarr you already have:

- leave the `watch` profile off;
- under **Admin → Hub → Connections**, put your Jellyfin's address and key
  under Watching, and your Prowlarr's address and key under Book search;
- Connect everything leaves an existing Jellyfin's libraries alone. Point
  them at `DATA_ROOT/media/movies` and `DATA_ROOT/media/tv` yourself.

## What has and has not been tested

Tested against stand-ins that answer like the real programs: Connect
everything, requesting films and shows (with chosen seasons), the downloads
list, removing, and the VPN's "not set up" state. **Tried on the real
programs on 24 October 2026** (on the PC, ProtonVPN Plus): the VPN connects
with a forwarded port, qBittorrent's traffic leaves through the VPN and stops
when the VPN stops, Family Hub passes the forwarded port to qBittorrent, and
Connect everything set up Radarr, Sonarr, Prowlarr, qBittorrent and Bazarr
with every step done. **Not yet tried:** a real download from start to
finish, subtitles arriving, and Jellyfin.
