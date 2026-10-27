# 15. What you need to get, and where it goes

One list of everything Family Hub's films, TV and book downloads depend on:
what to buy or sign up for, what to install, and exactly where each
address, key and password goes. There are two places to put things:

- **The `.env` file** (back end) in the project folder. Docker reads it
  before anything starts, so anything a container needs to *start* lives
  here. After changing it run `docker compose ... up -d` again (the same
  command as in step 4 below).
- **Admin → Hub → Connections** (front end), signed in as an admin. Press
  a service's name to open its box; each has a **Test connection** button.
  Pressing **Save** writes the value into the same `.env` file and it takes
  effect straight away, with no restart.

Most API keys you never type at all: **Connect everything** (Admin → Hub →
Films and TV) reads them from each program's own settings.

## The shopping list

| # | What | Cost | Needed for |
|---|---|---|---|
| 1 | **Docker** (Docker Desktop on this PC; Docker Engine on the home server) | Free | Running everything |
| 2 | **ProtonVPN Plus** | Paid subscription (the free plan has no torrenting or port forwarding) | Any torrent download. Without it qBittorrent stays offline on purpose |
| 3 | **Indexers** to add to Prowlarr | Public ones free; private trackers need an invite | Finding anything: films, TV and books |
| 4 | **A big storage disk** on the home server | Hardware | Where films and TV are kept (`DATA_ROOT`) |
| 5 | **OpenSubtitles.com account** | Free (about 20 downloads a day) | Optional: the biggest subtitle source |

Everything else is free and comes with this project: Prowlarr, qBittorrent,
the VPN container (gluetun), Radarr, Sonarr, Bazarr and, on this PC,
Jellyfin. Your home server already has its own Jellyfin and Prowlarr.

## Where each value goes

| Value | Where you get it | `.env` (back end) | Family Hub (front end) |
|---|---|---|---|
| VPN WireGuard key | ProtonVPN website (step 2) | `VPN_WIREGUARD_KEY` | – (only Docker needs it) |
| VPN countries | Your choice, e.g. `Switzerland` | `VPN_COUNTRIES` | – |
| VPN control key | Any long random text. **Already set on this PC.** Required: Docker will not start while it is empty | `GLUETUN_API_KEY` | Connections → VPN for torrents (gluetun) → VPN control key (filled in from `.env`) |
| Storage folder | The big disk's path | `DATA_ROOT` | – |
| qBittorrent user name and password | You choose them in qBittorrent (step 5) | `QBITTORRENT_USER`, `QBITTORRENT_PASS` | Connections → Download progress (qBittorrent). Address `http://gluetun:8080` |
| Prowlarr API key | Read for you by Connect everything. By hand: Prowlarr → Settings → General → API Key | `PROWLARR_API_KEY` | Connections → Book search (Prowlarr). Address `http://prowlarr:9696` |
| Indexers | Prowlarr's own page → Indexers → Add Indexer | – | – (Radarr and Sonarr get them from Prowlarr) |
| Radarr API key | Read for you by Connect everything | `RADARR_API_KEY` | Connections → Films (Radarr). Address `http://radarr:7878` |
| Sonarr API key | Read for you by Connect everything | `SONARR_API_KEY` | Connections → TV shows (Sonarr). Address `http://sonarr:8989` |
| Bazarr API key | Read for you by Connect everything | `BAZARR_API_KEY` | Connections → Subtitles (Bazarr). Address `http://bazarr:6767` |
| Subtitle languages | `en` (already the default) | – | Connections → Subtitles (Bazarr) → Subtitle languages |
| OpenSubtitles.com sign-in | opensubtitles.com → Sign up | – | Connections → Subtitles (Bazarr) → OpenSubtitles.com user name and password |
| Jellyfin API key | Jellyfin → Dashboard → API Keys → + | `JELLYFIN_API_KEY` | Connections → Watching (Jellyfin) → API key |
| Jellyfin addresses | `http://jellyfin:8096` for Family Hub; the one you type in a browser for the Watch buttons, e.g. `http://192.168.1.20:8096` | `JELLYFIN_URL` | Connections → Watching (Jellyfin), both addresses |
| Who may request | Your choice per person | – | Admin → Hub → Profiles → **Can request films and TV** |

The addresses above are already the defaults when everything runs from this
project. Change them only for programs that run elsewhere (the home
server's own Jellyfin and Prowlarr).

## Step by step

### 1. Docker

- **This PC:** install Docker Desktop from docker.com and start it. Leave
  it running whenever you use the stack.
- **Home server:** install Docker Engine and the compose plugin
  (docs.docker.com → Install → your Linux). Guide 2 covers moving over.

### 2. ProtonVPN Plus

1. Sign up at protonvpn.com and choose **Plus** (or a bundle that includes
   it). Longer plans cost less per month.
2. Sign in at **account.protonvpn.com → Downloads → WireGuard
   configuration**.
3. Give it a name (e.g. `family-hub`), choose platform **GNU/Linux**, turn
   on **NAT-PMP (Port Forwarding)**, pick a server marked for **P2P** (the
   two-arrow icon) and press **Create**.
4. A text file appears. Copy only the text after `PrivateKey = `.
5. Open `.env` in the project folder and fill in:

   ```
   VPN_PROVIDER=protonvpn
   VPN_TYPE=wireguard
   VPN_WIREGUARD_KEY=the key you copied
   VPN_COUNTRIES=Switzerland
   ```

   Keep the key private: `.env` is never uploaded (it is in `.gitignore`).

To use a different provider later, see "The VPN" in guide 14.

### 3. Storage (home server only)

Before the first start on the server, point `DATA_ROOT` in `.env` at the big
disk, for example `DATA_ROOT=/mnt/storage/data`. Family Hub's folders
(`torrents/…` and `media/…`) are made inside it. On this PC it stays
`./stack/data`. Moving it later means moving the whole folder and pressing
Connect everything again.

### 4. Start everything

```
docker compose --profile search --profile torrent --profile media --profile watch up -d
```

| Profile | Starts |
|---|---|
| `search` | Prowlarr |
| `torrent` | the VPN (gluetun) and qBittorrent inside it |
| `media` | Radarr, Sonarr, Bazarr |
| `watch` | Jellyfin. **This PC only**: leave it off on the home server, which has its own |

The first start downloads each program, which takes a few minutes. If the
VPN key is still empty, Docker reports that qBittorrent could not start:
that is the lock working, not a fault.

### 5. qBittorrent

1. Read the temporary password: `docker logs qbittorrent` (look for "A
   temporary password is provided").
2. Open `http://localhost:8080` (on the server, its address instead of
   localhost) and sign in as `admin` with that password.
3. **Tools → Options → Web UI**: set your own user name and password, and
   press **Save**.
4. In Family Hub: **Admin → Hub → Connections → Download progress
   (qBittorrent)**. Address `http://gluetun:8080`, plus the user name and
   password. Press **Test connection**, then **Save**.

Connect everything gives that same sign-in to Radarr and Sonarr, so set it
up before pressing the button.

### 6. Prowlarr and indexers

1. Open `http://localhost:9696`. The first time, it asks you to make a
   sign-in for Prowlarr's own page: choose **Forms** and any user name and
   password.
2. **Indexers → Add Indexer**: add the ones you use. Family Hub comes with
   none. Some public sites sit behind a "checking your browser" page
   (Prowlarr says "blocked by CloudFlare Protection"). For those, a helper
   called FlareSolverr starts with Prowlarr: in Prowlarr open **Settings →
   Indexers → +**, choose **FlareSolverr**, set the host to
   `http://flaresolverr:8191` (not localhost) and a tag such as `flare`, then
   give each of those indexers the same tag. It works by opening the page in
   a hidden browser, so it breaks now and then when the sites change their
   checks; indexers that need no such helper are more dependable.
3. Nothing to type into Family Hub: Connect everything reads Prowlarr's key
   and shares the indexers with Radarr and Sonarr.

**Home server:** use the Prowlarr you already have. Put its address and key
in **Admin → Hub → Connections → Book search (Prowlarr)** (key from
Prowlarr → Settings → General → API Key).

Guide 5 covers using Prowlarr for books.

### 7. Radarr, Sonarr and Bazarr

The first time you open each one's page (ports 7878, 8989, 6767) it may ask
you to make a sign-in for that page; choose **Forms** and any user name and
password. Or save one user name and password under **Admin → Hub →
Connections → Sign-in for the programs' own pages**, and step 8 sets it on
Radarr, Sonarr, Prowlarr, Bazarr and qBittorrent for you. Nothing else to do
by hand: step 8 sets them up.

### 8. Connect everything

1. In Family Hub, sign in as an admin and open **Admin → Hub → Films and
   TV**.
2. Press **Connect everything**. Every step is listed with what happened:
   API keys, the programs' sign-ins, folders, the quality profile,
   qBittorrent, Prowlarr (for films, TV and books), subtitles, Jellyfin's
   libraries and the VPN.
3. A step marked as failed says why in words. Fix that and press the button
   again: whatever is already in place is left alone.
4. "VPN for torrents" on the same page should say **VPN connected · … ·
   port … forwarded**.

### 9. Jellyfin

**This PC:**

1. Open `http://localhost:8096` and follow its first-time setup. Make one
   admin account: the whole household uses it. When it asks for libraries,
   skip that step: Connect everything adds Films (`/media/movies`) and TV
   shows (`/media/tv`).
2. **Dashboard → API Keys → +**, name it `Family Hub`, copy the key.
3. In Family Hub: **Admin → Hub → Connections → Watching (Jellyfin)**.
   Address for Family Hub `http://jellyfin:8096`; address in a browser
   `http://localhost:8096` (or the PC's network address so other devices'
   Watch buttons work); and the key. Press **Test connection**, then **Save**.

**Home server:** do steps 2 and 3 with your existing Jellyfin, and point its
Films and TV libraries at `DATA_ROOT/media/movies` and `DATA_ROOT/media/tv`
yourself. Connect everything leaves an existing Jellyfin's libraries alone.

### 10. Subtitles (optional extra source)

English subtitles work straight away from free sources. For more:

1. Sign up at opensubtitles.com (free).
2. **Admin → Hub → Connections → Subtitles (Bazarr)**: enter the user name
   and password. Other languages go in **Subtitle languages** as short
   codes (`en, fr`).
3. Press **Connect everything** again.

### 11. Who may request

**Admin → Hub → Profiles**: tick **Can request films and TV** for each
person who may ask for films and shows. Admins always can.

## Extras you may already have set up

These are the book side and the look of Home, each in its own guide:

| What | Where it goes | Guide |
|---|---|---|
| Library manager admin sign-in (removing spare copies, deleting books) | Connections → Library manager | 8 |
| Gmail app password (Send to Kindle) | Connections → Sending books by email | 9 |
| Google Books, Comic Vine, Hardcover keys (tags and ratings) | Connections → Community tags from book catalogues | 10 |
| Series server sign-in (manga) | Connections → Series by chapter | 13 |
| A folder on a second disk (nightly copy) | `.env`: `BACKUP_PATH`, then Connections → Nightly copy | 6 |
| Home town and your own visuals repository | Connections → Visuals on Home | 16 |

## Checklist

- [ ] Docker installed and running
- [ ] ProtonVPN Plus bought; WireGuard key in `.env`
- [ ] (Home server) `DATA_ROOT` points at the storage disk
- [ ] `docker compose --profile search --profile torrent --profile media --profile watch up -d`
- [ ] qBittorrent password set, and entered in Connections
- [ ] Indexers added in Prowlarr
- [ ] **Connect everything** pressed, every step done, VPN says connected
- [ ] Jellyfin set up; its key and addresses in Connections
- [ ] (Optional) OpenSubtitles.com account in Connections
- [ ] "Can request films and TV" ticked for the right people
